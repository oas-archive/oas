"""共用驗證層：決定這次請求要不要擋下。security.install 把 require_access 掛成 middleware（在 Host 檢查之內、跨站檢查之外）。

規則依序：
1. 手機版（設了 OAS_API_TOKEN）：除了 OPEN_PATHS，沒有帶正確通行碼 cookie 的請求一律拒絕（擋同一支手機上的其他 App 讀取或清空典藏）。
   手機版不會開區網模式（settings.detect_remote）。
2. 區網模式沒開（預設）：不檢查，和沒有這個功能時完全相同（電腦版只綁本機）。
3. 區網模式（OAS_REMOTE=1）：
   - 連線來源不是私人／連結本機／本機位址 → 403。
   - 電腦本機（127.0.0.1／::1 且沒有反向代理標頭）→ 全部放行。
   - 路由級別（route_access.py）是公開的 → 放行（健康檢查、配對）。
   - 其他要帶有效的裝置 cookie（devices.py），沒有 → 401；級別不夠或只限本機 → 403。
"""
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from . import devices, route_access, settings

OPEN_PATHS = {"/api/health"}  # 手機版不用通行碼的路徑：App 等服務就緒時用
COOKIE_MAX_AGE = int(devices.DEVICE_TTL.total_seconds())


def phone_token_ok(request: Request) -> bool:
    return hmac.compare_digest(request.cookies.get(settings.TOKEN_COOKIE, "").encode(), settings.API_TOKEN.encode())


def set_device_cookie(response, token: str) -> None:
    """HttpOnly（網頁程式讀不到）、SameSite=Strict（其他網站帶不過來）、Path=/。HTTP 不能加 Secure，也就不能用 __Host- 前綴。"""
    response.set_cookie(settings.DEVICE_COOKIE, token, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="strict")


def clear_device_cookie(response) -> None:
    response.delete_cookie(settings.DEVICE_COOKIE, path="/", httponly=True, samesite="strict")


async def require_access(request: Request, call_next):
    """路徑要用 scope["path"]（實際路由用的值）；request.url 是用 Host 標頭組出來的，可以被偽造。"""
    if settings.API_TOKEN:
        if request.scope["path"] not in OPEN_PATHS and not phone_token_ok(request):
            return JSONResponse({"detail": "缺少典藏館的通行碼"}, status_code=401)
        return await call_next(request)
    if not devices.remote_active():
        return await call_next(request)
    return await _remote_access(request, call_next)


async def _remote_access(request: Request, call_next):
    ip = devices.client_host(request.scope)
    if not devices.allowed_source(ip):
        return JSONResponse({"detail": "只接受家裡區網內的連線"}, status_code=403)
    if devices.is_local_request(request.scope, request.headers):
        return await call_next(request)
    level = route_access.classify(request.scope)
    if level == route_access.PUBLIC:
        return await call_next(request)
    token = request.cookies.get(settings.DEVICE_COOKIE, "")
    device, refreshed = await run_in_threadpool(devices.authenticate, token, ip)
    if device is None:
        await run_in_threadpool(devices.note_unauthorized, ip, request.scope["path"])
        response = JSONResponse({"detail": "這台裝置還沒配對，或已經失效", "pair": True}, status_code=401)
        if token:
            clear_device_cookie(response)
        return response
    if level not in route_access.DEVICE_LEVELS.get(device["level"], ()):
        message = "這件事只能在電腦本機操作" if level == route_access.LOCAL else "這台裝置是唯讀，不能修改"
        return JSONResponse({"detail": message}, status_code=403)
    request.scope.setdefault("state", {})["oas_device"] = device
    response = await call_next(request)
    if refreshed:  # 有用就順延 90 天（最多每分鐘一次）
        set_device_cookie(response, token)
    return response


def current_access(request: Request) -> dict:
    """給 GET /api/devices/me：這次請求以什麼身分通過。"""
    if settings.API_TOKEN or not devices.remote_active() or devices.is_local_request(request.scope, request.headers):
        return {"remote": devices.remote_active(), "access": "local", "device": None}
    device = request.scope.get("state", {}).get("oas_device")
    return {"remote": True, "access": device["level"] if device else "none", "device": device}
