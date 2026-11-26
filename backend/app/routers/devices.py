"""區網模式：配對（公開）、目前身分，以及只限電腦本機的裝置管理與開關。規則見 devices.py、access.py。

只限本機的 API 另外在這裡檢查一次「真的是電腦本機」（不只靠 middleware）：區網模式沒開時 middleware 不檢查，
但這些 API 本來就不該給別台用。手機 App 沒有這些功能（回 404）。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import access, devices, lan_checks, settings
from ..database import Device, utcnow
from ..deps import db_session
from ..schemas import DevicePatch, FirewallAction, PairingCreate, PairRequest, RemoteToggle


def desktop_only() -> None:
    if settings.PLATFORM == "android":
        raise HTTPException(404, "手機 App 沒有區網模式")


def local_machine_only(request: Request) -> None:
    desktop_only()
    if not devices.is_local_request(request.scope, request.headers):
        raise HTTPException(403, "這件事只能在電腦本機操作")


public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api/devices", dependencies=[Depends(local_machine_only)])


# ---------- 公開：配對、目前身分 ----------

@public.post("/pair")
def pair(payload: PairRequest, request: Request, db: Session = Depends(db_session)):
    """新裝置輸入電腦上顯示的配對碼。成功時設定 oas_device cookie；錯誤一律回同一句話（不透露是碼錯、過期還是沒有碼）。"""
    desktop_only()
    if not devices.remote_active():
        raise HTTPException(404, "區網模式沒有開啟")
    ip = devices.client_host(request.scope)
    result, extra = devices.check_code(payload.code, ip)
    if result == "blocked":
        devices.record_noise(db, "pair_blocked", ip)  # 暫停期間同一位址 10 分鐘只記一筆
        devices.commit_quietly(db)
        return JSONResponse({"detail": "嘗試太多次，請稍後再試"}, status_code=429, headers={"Retry-After": str(extra)})
    if result != "ok":
        devices.record_noise(db, "pair_failed", ip, counted=True)  # 同一位址 10 分鐘一筆，detail 是次數
        if result == "voided":
            devices.record(db, "code_voided", ip)
        devices.commit_quietly(db)
        return JSONResponse({"detail": "配對碼錯誤或已失效，請在電腦上重新產生"}, status_code=403)
    device, token = devices.issue(db, payload.name, extra, ip)
    devices.commit_quietly(db)
    response = JSONResponse({"device": {"id": device.id, "name": device.name, "level": device.level}})
    access.set_device_cookie(response, token)
    return response


@public.get("/devices/me")
def me(request: Request):
    """這次請求的身分：access 是 local（電腦本機、或區網模式沒開）、standard、readonly。前端用來決定顯示哪些按鈕。"""
    return access.current_access(request)


# ---------- 只限電腦本機：區網模式開關 ----------

def _remote_state(request: Request) -> dict:
    port = (request.scope.get("server") or ("", 8080))[1]
    return {
        "enabled": devices.remote_active(),  # 這次啟動有沒有開
        "enabled_next": devices.remote_next(),  # 下次啟動要不要開（啟動器讀 remote.json）
        "restart_needed": devices.remote_active() != devices.remote_next(),
        "addresses": devices.lan_addresses(), "port": port, "hostnames": sorted(devices.machine_names()),
    }


@router.get("/remote")
def remote_state(request: Request):
    return _remote_state(request)


@router.put("/remote")
def set_remote(payload: RemoteToggle, request: Request, db: Session = Depends(db_session)):
    """只寫下「下次啟動」的設定；要關掉典藏館視窗再開才生效（啟動器改綁位址）。"""
    devices.set_remote_next(payload.enabled)
    devices.record(db, "remote_on" if payload.enabled else "remote_off", devices.client_host(request.scope))
    devices.commit_quietly(db)
    return _remote_state(request)


# ---------- 只限電腦本機：開啟前的檢查清單、防火牆設定 ----------

@router.get("/checks")
def lan_checks_state(request: Request):
    """檢查清單：網路是私人、防火牆規則、已用區網模式重開、埠轉送。只讀系統狀態（lan_checks.py），不改任何設定。
    status 是 ok／fail／unknown；unknown（查不到、不是 Windows）畫面顯示「無法偵測」，不擋新增裝置。"""
    port = _remote_state(request)["port"]
    result = lan_checks.run_checks(port)
    active, upcoming = devices.remote_active(), devices.remote_next()
    result["restart"] = {"status": lan_checks.OK if active and upcoming else lan_checks.FAIL, "enabled": active, "enabled_next": upcoming,
                         "can_restart": False}  # 啟動器沒有重開機制（tools/launch.ps1），只提示使用者自己關掉視窗再開
    result["windows"] = lan_checks.is_windows()
    return result


@router.post("/firewall")
def firewall_setup(payload: FirewallAction, request: Request, db: Session = Depends(db_session)):
    """以系統管理員身分開 tools/enable-lan-firewall.ps1（會跳 UAC，由使用者按「是」）。只有電腦本機能觸發（router 的 local_machine_only）。"""
    if not lan_checks.is_windows():
        raise HTTPException(409, "只有 Windows 能從這裡設定防火牆")
    result = lan_checks.launch_firewall_script(payload.remove)
    if result == "missing":
        raise HTTPException(404, "找不到防火牆設定腳本 tools\\enable-lan-firewall.ps1")
    if result == "failed":
        raise HTTPException(500, "沒辦法開啟防火牆設定視窗，請到 tools 資料夾手動執行 enable-lan-firewall.ps1")
    return {"result": result}


# ---------- 只限電腦本機：配對碼 ----------

@router.get("/pairing")
def pairing_status():
    """目前有沒有有效的配對碼（不回傳碼本身）。"""
    return devices.code_status()


@router.post("/pairing")
def create_pairing(payload: PairingCreate, request: Request, db: Session = Depends(db_session)):
    """產生新的配對碼（舊的立刻失效），只在這個回應出現一次。"""
    if not devices.remote_active():
        raise HTTPException(409, "區網模式還沒開啟（開啟後要重新啟動典藏館）")
    code, expires = devices.new_code(payload.level)
    devices.record(db, "code_created", devices.client_host(request.scope), detail=payload.level)
    devices.commit_quietly(db)
    state = _remote_state(request)
    return {"code": code, "expires_at": devices.code_status()["expires_at"], "seconds": devices.PAIR_CODE_SECONDS,
            "level": payload.level, "addresses": state["addresses"], "port": state["port"]}


@router.delete("/pairing")
def cancel_pairing(request: Request, db: Session = Depends(db_session)):
    cancelled = devices.cancel_code()
    if cancelled:
        devices.record(db, "code_cancelled", devices.client_host(request.scope))
        devices.commit_quietly(db)
    return {"cancelled": cancelled}


# ---------- 只限電腦本機：裝置清單 ----------

@router.get("")
def list_devices(db: Session = Depends(db_session)):
    rows = db.scalars(select(Device).order_by(Device.revoked_at.is_not(None), Device.id.desc())).all()
    return {"items": [devices.serialise(row) for row in rows]}


@router.get("/events")
def device_events(db: Session = Depends(db_session)):
    return {"items": devices.events(db)}


def _device(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(404, "找不到這台裝置")
    return device


@router.patch("/{device_id}")
def patch_device(device_id: int, payload: DevicePatch, request: Request, db: Session = Depends(db_session)):
    device = _device(db, device_id)
    if payload.name is not None:
        device.name = devices.clean_name(payload.name)
    if payload.level is not None and payload.level != device.level:
        device.level = payload.level
        devices.record(db, "level_changed", devices.client_host(request.scope), device.id, payload.level)
    devices.commit_quietly(db)
    return devices.serialise(device)


@router.post("/{device_id}/revoke")
def revoke_device(device_id: int, request: Request, db: Session = Depends(db_session)):
    device = _device(db, device_id)
    if device.revoked_at is None:
        device.revoked_at = utcnow()
        devices.record(db, "revoked", devices.client_host(request.scope), device.id, device.name)
        devices.commit_quietly(db)
    return devices.serialise(device)


@router.post("/revoke-all")
def revoke_all(request: Request, db: Session = Depends(db_session)):
    count = db.execute(update(Device).where(Device.revoked_at.is_(None)).values(revoked_at=utcnow())).rowcount
    devices.record(db, "revoked_all", devices.client_host(request.scope), detail=str(count))
    devices.commit_quietly(db)
    return {"revoked": count}
