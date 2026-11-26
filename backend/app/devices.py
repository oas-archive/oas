"""區網模式的裝置：來源位址與 Host 規則、配對碼、裝置 token、事件紀錄。使用說明與風險見 README「家裡其他裝置也能看」。

- 配對碼：電腦本機按「新增裝置」才產生；8 位數字、5 分鐘、一次有效，同時只有一組。只放記憶體（存雜湊，重啟就沒了），
  比對用常數時間。這組碼累計猜錯 5 次就作廢；另外同一個來源位址 10 分鐘內失敗 5 次就暫停 10 分鐘。
- token：配對成功才發，secrets.token_urlsafe(32)；資料庫只存 SHA-256 雜湊。放在 oas_device cookie（HttpOnly、SameSite=Strict、
  Path=/、90 天，有用就順延；HTTP 不能加 Secure）。90 天沒用就失效，撤銷立即失效。
- 本機：連線來源是 127.0.0.1／::1，而且沒有任何反向代理標頭，才算電腦本機（免登入、可做只限本機的事）。
- 不把 token、配對碼寫進記錄檔、事件或錯誤訊息。
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import cache, settings
from .database import Device, DeviceEvent, SessionLocal, utcnow

PAIR_CODE_DIGITS = 8
PAIR_CODE_SECONDS = 300
PAIR_CODE_MAX_FAILURES = 5  # 這組碼累計猜錯幾次就作廢
IP_FAILURE_LIMIT = 5  # 同一個來源位址在 IP_FAILURE_WINDOW 秒內失敗幾次就暫停
IP_FAILURE_WINDOW = 600
IP_BLOCK_SECONDS = 600
DEVICE_TTL = timedelta(days=90)  # 多久沒用就失效
SEEN_INTERVAL = 60  # 秒：最後連線時間最多多久寫一次（不要每個請求都寫資料庫）
NOISE_EVENT_INTERVAL = 600  # 秒：同一個位址的雜訊事件（未配對的嘗試、配對失敗、被暫停）多久才記一筆（掃描、亂猜時不灌爆事件表）
EVENT_KEEP = 200  # 重要事件、雜訊事件各留最近幾筆：雜訊再多也擠不掉配對、撤銷這些紀錄
LEVELS = ("readonly", "standard")
EVENT_KINDS = (
    "code_created", "code_cancelled", "code_voided", "paired", "pair_failed", "pair_blocked",
    "unauthorized", "revoked", "revoked_all", "level_changed", "remote_on", "remote_off",
)
NOISE_KINDS = ("pair_failed", "pair_blocked", "unauthorized")  # 區網裡任何人都能製造的事件，和重要事件分開保留

# 有這些標頭就表示請求經過反向代理（Tailscale Serve、其他代理）：來源看起來是 127.0.0.1，其實可能是別台裝置，不給本機免登入。
PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-forwarded-port", "x-forwarded-server",
                 "x-real-ip", "x-client-ip", "true-client-ip", "cf-connecting-ip", "via")

# 區網模式接受的連線來源：私人網段、連結本機、本機。Tailscale 的 100.64.0.0/10 不在內（要用時另外處理，見計畫第 4 階段）。
SOURCE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "127.0.0.0/8",
    "::1/128", "fe80::/10", "fc00::/7",
))
# Host 標頭可以是這些網段的 IPv4 字面值（IP 形式的 Host 不可能是 DNS rebinding）
HOST_NETWORKS = tuple(ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"))


# ---------- 位址 ----------

def remote_active() -> bool:
    """區網模式這次有沒有開（環境變數，手機一律否）。每次都讀 settings，測試可以直接改。"""
    return bool(settings.REMOTE) and settings.PLATFORM != "android"


def parse_ip(host: str | None):
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])  # fe80::1%eth0 這種帶網卡編號的
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def client_host(scope) -> str:
    client = scope.get("client")
    return client[0] if client else ""


def allowed_source(host: str) -> bool:
    address = parse_ip(host)
    return address is not None and any(address in network for network in SOURCE_NETWORKS)


def has_proxy_headers(headers) -> bool:
    return any(name in headers for name in PROXY_HEADERS)


def is_local_request(scope, headers) -> bool:
    """電腦本機：來源是 loopback，而且沒有反向代理標頭。"""
    address = parse_ip(client_host(scope))
    return address is not None and address.is_loopback and not has_proxy_headers(headers)


def machine_names() -> set[str]:
    try:
        name = socket.gethostname().strip().lower()
    except OSError:
        return set()
    if not name or not all(ch.isalnum() or ch == "-" for ch in name):
        return set()
    return {name, f"{name}.local"}


def remote_host_allowed(hostname: str) -> bool:
    """區網模式另外允許的 Host：私人網段的 IPv4 字面值、這台電腦的名稱（及 .local）。不開萬用字元。"""
    try:
        address = ipaddress.IPv4Address(hostname)
    except ValueError:
        return hostname in machine_names()
    return any(address in network for network in HOST_NETWORKS)


def lan_addresses() -> list[str]:
    """這台電腦的區網 IPv4 位址（給設定頁顯示網址與 QR）。只查本機名稱，不連外。"""
    found = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = ipaddress.IPv4Address(info[4][0])
            if any(address in network for network in HOST_NETWORKS):
                found.add(str(address))
    except (OSError, ValueError):
        pass
    return sorted(found, key=ipaddress.IPv4Address)


# ---------- 區網模式開關（下次啟動生效） ----------

def remote_next() -> bool:
    try:
        return json.loads(settings.REMOTE_FILE.read_text(encoding="utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def set_remote_next(enabled: bool) -> None:
    path = settings.REMOTE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"enabled": bool(enabled)}), encoding="utf-8")
    os.replace(temporary, path)


# ---------- 配對碼（只在記憶體） ----------

_lock = threading.Lock()
_key = secrets.token_bytes(32)  # 配對碼的雜湊金鑰，每次啟動不同
_code: dict | None = None  # {"hash", "expires", "failures", "level"}
_ip_failures: dict[str, list[float]] = {}
_ip_blocked: dict[str, float] = {}
_noise_lock = threading.Lock()
_noted: dict[tuple[str, str], list] = {}  # (種類, 位址) → [這一筆的開始時間, 事件編號, 次數]：雜訊事件的節流


def _code_hash(code: str) -> bytes:
    return hmac.new(_key, code.encode(), hashlib.sha256).digest()


def reset_memory() -> None:
    """清掉配對碼與限速狀態（測試用；重啟服務也是這個效果）。"""
    global _code
    with _lock:
        _code = None
        _ip_failures.clear()
        _ip_blocked.clear()
    with _noise_lock:
        _noted.clear()


def new_code(level: str) -> tuple[str, float]:
    """產生新的配對碼（舊的那組立刻失效），回傳（碼, 到期的 Unix 秒）。"""
    global _code
    if level not in LEVELS:
        raise ValueError(level)
    code = f"{secrets.randbelow(10 ** PAIR_CODE_DIGITS):0{PAIR_CODE_DIGITS}d}"
    expires = time.time() + PAIR_CODE_SECONDS
    with _lock:
        _code = {"hash": _code_hash(code), "expires": expires, "failures": 0, "level": level}
    return code, expires


def cancel_code() -> bool:
    global _code
    with _lock:
        active = _code is not None and _code["expires"] > time.time()
        _code = None
    return active


def code_status() -> dict:
    with _lock:
        if _code is None or _code["expires"] <= time.time():
            return {"active": False, "expires_at": None, "level": None}
        return {"active": True, "expires_at": _epoch_iso(_code["expires"]), "level": _code["level"]}


def _note_ip_failure(ip: str, now: float) -> None:
    recent = [stamp for stamp in _ip_failures.get(ip, []) if stamp > now - IP_FAILURE_WINDOW]
    recent.append(now)
    if len(recent) >= IP_FAILURE_LIMIT:
        _ip_blocked[ip] = now + IP_BLOCK_SECONDS
        _ip_failures.pop(ip, None)
    else:
        _ip_failures[ip] = recent


def check_code(code: str, ip: str) -> tuple[str, object]:
    """檢查新裝置輸入的配對碼。回傳：
    ("ok", 級別)／("wrong", None)（碼錯、沒有碼、過期，對外一律同一句話）／("voided", None)（這次猜錯讓碼作廢）／("blocked", 剩幾秒)。
    """
    global _code
    now = time.time()
    normalised = "".join(ch for ch in code if ch not in " -")
    with _lock:
        for stale in [key for key, until in _ip_blocked.items() if until <= now]:
            del _ip_blocked[stale]
        for stale in [key for key, stamps in _ip_failures.items() if not stamps or stamps[-1] <= now - IP_FAILURE_WINDOW]:
            del _ip_failures[stale]
        if ip in _ip_blocked:
            return "blocked", int(_ip_blocked[ip] - now) + 1
        current = _code if _code is not None and _code["expires"] > now else None
        if current is None:
            _code = None
            _note_ip_failure(ip, now)
            return "wrong", None
        valid = len(normalised) == PAIR_CODE_DIGITS and normalised.isdigit()
        if valid and hmac.compare_digest(_code_hash(normalised), current["hash"]):
            _code = None  # 一次有效
            return "ok", current["level"]
        current["failures"] += 1
        _note_ip_failure(ip, now)
        if current["failures"] >= PAIR_CODE_MAX_FAILURES:
            _code = None
            return "voided", None
        return "wrong", None


# ---------- 裝置與 token ----------

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return value.isoformat() if value else None


def _epoch_iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def status(device: Device, now: datetime | None = None) -> str:
    if device.revoked_at is not None:
        return "revoked"
    seen = _aware(device.last_seen_at) or _aware(device.created_at)
    return "expired" if seen <= (now or utcnow()) - DEVICE_TTL else "active"


def serialise(device: Device) -> dict:
    seen = _aware(device.last_seen_at) or _aware(device.created_at)
    return {
        "id": device.id, "name": device.name, "level": device.level, "status": status(device),
        "created_at": _iso(device.created_at), "last_seen_at": _iso(device.last_seen_at), "last_ip": device.last_ip,
        "revoked_at": _iso(device.revoked_at), "expires_at": _iso(seen + DEVICE_TTL) if device.revoked_at is None else None,
    }


def clean_name(name: str) -> str:
    cleaned = "".join(ch for ch in (name or "") if ch.isprintable()).strip()[:60]
    return cleaned or "未命名裝置"


def record(db: Session, kind: str, ip: str = "", device_id: int | None = None, detail: str = "") -> int:
    """記一筆事件，回傳編號；重要事件、雜訊事件（NOISE_KINDS）各只留最近 EVENT_KEEP 筆。呼叫端負責 commit（用 commit_quietly）。"""
    event = DeviceEvent(kind=kind, ip=ip[:45], device_id=device_id, detail=detail[:200])
    db.add(event)
    db.flush()
    same_group = DeviceEvent.kind.in_(NOISE_KINDS) if kind in NOISE_KINDS else DeviceEvent.kind.not_in(NOISE_KINDS)
    cutoff = db.scalar(select(DeviceEvent.id).where(same_group).order_by(DeviceEvent.id.desc()).offset(EVENT_KEEP - 1).limit(1))
    if cutoff is not None:
        db.execute(delete(DeviceEvent).where(same_group, DeviceEvent.id < cutoff))
    return event.id


def record_noise(db: Session, kind: str, ip: str, detail: str = "", counted: bool = False) -> None:
    """雜訊事件：同一個位址同一種事件 10 分鐘只記一筆。counted＝同一筆的 detail 改成累計次數（配對失敗）。呼叫端負責 commit。"""
    now = time.time()
    key = (kind, ip)
    with _noise_lock:  # 整段（含寫入）同一把鎖：同一位址同時送來的請求不會各記一筆
        for stale in [item for item, (start, _, _) in _noted.items() if start <= now - NOISE_EVENT_INTERVAL]:
            del _noted[stale]
        entry = _noted.get(key)
        if entry is None:
            _noted[key] = [now, record(db, kind, ip, None, "1 次" if counted else detail), 1]
        elif counted:
            entry[2] += 1
            db.execute(update(DeviceEvent).where(DeviceEvent.id == entry[1]).values(detail=f"{entry[2]} 次"))


def commit_quietly(db: Session) -> None:
    """裝置資料不影響任何典藏快取（首頁、重複作品…），commit 時不讓快取失效。"""
    cache.quiet(db)
    db.commit()


def issue(db: Session, name: str, level: str, ip: str) -> tuple[Device, str]:
    """配對成功：建立裝置、回傳（裝置, token）。token 只在這裡出現一次，資料庫只存雜湊。"""
    token = secrets.token_urlsafe(32)
    device = Device(name=clean_name(name), token_hash=hash_token(token), level=level, last_seen_at=utcnow(), last_ip=ip[:45])
    db.add(device)
    db.flush()
    record(db, "paired", ip, device.id, device.name)
    return device, token


def authenticate(token: str, ip: str) -> tuple[dict | None, bool]:
    """以 cookie 的 token 找有效的裝置。回傳（裝置資料或 None, 這次有沒有更新最後連線時間〔要順延 cookie〕）。"""
    if not token or len(token) > 200:
        return None, False
    with SessionLocal() as db:
        device = db.scalar(select(Device).where(Device.token_hash == hash_token(token)))
        if device is None or status(device) != "active":
            return None, False
        now = utcnow()
        seen = _aware(device.last_seen_at)
        refreshed = seen is None or (now - seen).total_seconds() >= SEEN_INTERVAL or device.last_ip != ip[:45]
        if refreshed:
            db.execute(update(Device).where(Device.id == device.id).values(last_seen_at=now, last_ip=ip[:45]))
            commit_quietly(db)
        return {"id": device.id, "name": device.name, "level": device.level}, refreshed


def note_unauthorized(ip: str, path: str) -> None:
    """沒配對（或已失效）的裝置來要資料：同一個位址 10 分鐘最多記一筆（不寫資料庫的請求不開連線）。"""
    with _noise_lock:
        start = _noted.get(("unauthorized", ip), [0])[0]
    if start > time.time() - NOISE_EVENT_INTERVAL:
        return
    with SessionLocal() as db:
        record_noise(db, "unauthorized", ip, path)
        commit_quietly(db)


def events(db: Session) -> list[dict]:
    rows = db.execute(
        select(DeviceEvent, Device.name).outerjoin(Device, Device.id == DeviceEvent.device_id)
        .order_by(DeviceEvent.id.desc()).limit(EVENT_KEEP * 2)
    ).all()
    return [{"id": event.id, "time": _iso(event.time), "kind": event.kind, "ip": event.ip, "device_id": event.device_id,
             "device_name": name, "detail": event.detail} for event, name in rows]
