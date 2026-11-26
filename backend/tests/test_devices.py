"""區網模式（多裝置登入：後端）。位址都是虛構的私人網段或文件用網段。"""
import hashlib
import logging
import sqlite3
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app import access, devices, settings
from app.database import Device, DeviceEvent, SessionLocal, engine, initialise_database, utcnow
from app.main import app

LAN_HOST = "192.168.50.10:8080"  # 這台電腦（虛構）的區網網址
PHONE = "192.168.50.21"
TABLET = "192.168.50.22"
JSON = {"Content-Type": "application/json"}


def make_client(ip, host=LAN_HOST):
    return TestClient(app, client=(ip, 50123), base_url=f"http://{host}", headers=JSON)


@pytest.fixture(autouse=True)
def fresh_memory():
    devices.reset_memory()
    yield
    devices.reset_memory()


@pytest.fixture()
def remote(client, monkeypatch, tmp_path):
    """區網模式開著；回傳電腦本機的 client（127.0.0.1）。"""
    monkeypatch.setattr(settings, "REMOTE", True)
    monkeypatch.setattr(settings, "REMOTE_FILE", tmp_path / "remote.json")
    return make_client("127.0.0.1", "localhost:8080")


def new_code(local, level="standard"):
    response = local.post("/api/devices/pairing", json={"level": level})
    assert response.status_code == 200, response.text
    return response.json()["code"]


def paired(local, ip=PHONE, level="standard", name="測試手機"):
    device = make_client(ip)
    response = device.post("/api/pair", json={"code": new_code(local, level), "name": name})
    assert response.status_code == 200, response.text
    return device, response


# ---------- 區網模式關閉：和沒有這個功能時完全相同 ----------

REQUESTS = [
    ("GET", "/api/resources", None), ("GET", "/api/backup", None), ("POST", "/api/resources", {}), ("POST", "/api/import/parse", {"text": "https://example.com/work/1"}),
    ("GET", "/", None), ("GET", "/api/no-such-api", None), ("GET", "/launch-probe", None), ("GET", "/api/health", None),
    ("POST", "/api/pair", {"code": "12345678", "name": "x"}),
    ("GET", "/api/devices", None), ("GET", "/api/devices/me", None), ("GET", "/api/devices/events", None), ("GET", "/api/devices/pairing", None),
    ("GET", "/api/devices/remote", None), ("POST", "/api/devices/pairing", {}), ("POST", "/api/devices/1/revoke", {}),
    ("POST", "/api/devices/revoke-all", {}), ("PATCH", "/api/devices/1", {"level": "readonly"}), ("GET", "/assets/app.js", None),
]


def _snapshot(test_client, extra_headers=None):
    rows = []
    for method, path, body in REQUESTS:
        response = test_client.request(method, path, json=body, headers=extra_headers or {})
        headers = {key: value for key, value in response.headers.items() if key not in ("date", "content-length")}
        content = response.content
        if path == "/api/backup":  # 匯出時間每次不同
            headers.pop("content-disposition")
            content = {key: value for key, value in response.json().items() if key != "exported_at"}
        rows.append((method, path, response.status_code, headers, content))
    return rows


def test_remote_off_behaves_the_same_for_every_source(client, monkeypatch):
    assert settings.REMOTE is False
    baseline = _snapshot(client)
    for ip in ("127.0.0.1", PHONE, "203.0.113.9"):
        other = make_client(ip, "testserver")
        other.cookies.set(settings.DEVICE_COOKIE, "made-up-token")
        for row, expected in zip(_snapshot(other, {"X-Forwarded-For": "198.51.100.4"}), baseline):
            assert row[:4] == expected[:4], row[:3]
            assert row[4] == expected[4], row[:3]
    assert all("set-cookie" not in row[3] for row in baseline)
    with SessionLocal() as db:
        assert db.scalars(select(DeviceEvent)).all() == []


def test_remote_off_keeps_the_old_host_rule(client):
    assert client.get("/api/resources", headers={"Host": LAN_HOST}).status_code == 400
    assert client.get("/api/resources", headers={"Host": "evil.example"}).status_code == 400


def test_remote_off_pairing_is_not_available(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "REMOTE_FILE", tmp_path / "remote.json")
    assert client.post("/api/pair", json={"code": "12345678"}).status_code == 404
    local = make_client("127.0.0.1", "localhost:8080")
    assert local.post("/api/devices/pairing", json={}).status_code == 409
    assert local.get("/api/devices").status_code == 200  # 清單與撤銷在關閉時也能用
    assert client.get("/api/devices").status_code == 403  # 不是本機（測試預設的來源不是 127.0.0.1）


# ---------- 手機 App 一律不開 ----------

def test_android_never_enables_remote(client, monkeypatch):
    assert settings.detect_remote("1", "android") is False
    assert settings.detect_remote("1", "desktop") is True
    assert settings.detect_remote(None, "desktop") is False and settings.detect_remote("true", "desktop") is False
    monkeypatch.setattr(settings, "REMOTE", True)
    monkeypatch.setattr(settings, "PLATFORM", "android")
    assert devices.remote_active() is False
    assert make_client(PHONE).get("/api/resources").status_code == 400  # 區網 IP 的 Host 照樣擋
    local = make_client("127.0.0.1", "localhost:8080")
    assert local.post("/api/pair", json={"code": "12345678"}).status_code == 404
    assert local.get("/api/devices").status_code == 404


def test_phone_token_still_comes_first(client, monkeypatch):
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "REMOTE", True)  # 就算被設了，手機版也只看通行碼
    local = make_client("127.0.0.1", "127.0.0.1:8080")
    assert local.get("/api/resources").status_code == 401
    local.cookies.set("oas_token", "secret-token")
    assert local.get("/api/resources").status_code == 200


# ---------- 來源位址與 Host ----------

@pytest.mark.parametrize("ip", ["203.0.113.9", "8.8.8.8", "100.64.1.2", "2001:db8::5", "testclient"])
def test_non_private_sources_are_refused(remote, ip):
    other = make_client(ip)
    for method, path in (("GET", "/api/health"), ("POST", "/api/pair"), ("GET", "/api/resources"), ("GET", "/")):
        response = other.request(method, path, json={"code": "12345678"} if method == "POST" else None)
        assert response.status_code == 403, (ip, path)


@pytest.mark.parametrize("ip", ["10.1.2.3", "172.20.0.4", PHONE, "169.254.10.20", "fe80::1234", "fd12:3456::7", "::ffff:192.168.50.30"])
def test_private_sources_reach_the_pairing(remote, ip):
    response = make_client(ip).post("/api/pair", json={"code": "00000000"})
    assert response.status_code == 403 and "配對碼" in response.json()["detail"]  # 走到配對檢查（碼錯），不是被來源擋下


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "192.168.50.10.nip.io", "8.8.8.8:8080", "100.64.1.2", "localhost.evil.example"])
def test_rebinding_hosts_are_still_refused(remote, host):
    assert make_client(PHONE, host).get("/api/health").status_code == 400


@pytest.mark.parametrize("host", [LAN_HOST, "10.0.0.5", "172.31.255.1:8080", "localhost:8080", "127.0.0.1:8080"])
def test_private_ip_hosts_are_allowed(remote, host):
    assert make_client(PHONE, host).get("/api/health").status_code == 200


def test_machine_name_host_is_allowed(remote, monkeypatch):
    monkeypatch.setattr(devices.socket, "gethostname", lambda: "DEMO-PC")
    assert make_client(PHONE, "demo-pc:8080").get("/api/health").status_code == 200
    assert make_client(PHONE, "demo-pc.local:8080").get("/api/health").status_code == 200
    assert make_client(PHONE, "demo-pc.example:8080").get("/api/health").status_code == 400


# ---------- 本機免登入與反向代理 ----------

@pytest.mark.parametrize("ip", ["127.0.0.1", "::1"])
def test_local_machine_needs_no_login(remote, ip):
    local = make_client(ip, "localhost:8080")
    assert local.get("/api/resources").status_code == 200
    assert local.get("/api/backup").status_code == 200
    assert local.get("/api/devices/me").json()["access"] == "local"


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "X-Forwarded-Host", "Via"])
def test_proxy_headers_lose_the_local_exemption(remote, header):
    proxied = make_client("127.0.0.1", "localhost:8080")
    headers = {header: "for=192.168.50.99" if header == "Forwarded" else "192.168.50.99"}
    assert proxied.get("/api/resources", headers=headers).status_code == 401
    assert proxied.get("/api/backup", headers=headers).status_code == 401
    assert proxied.get("/api/devices", headers=headers).status_code in (401, 403)
    assert proxied.post("/api/devices/pairing", json={}, headers=headers).status_code in (401, 403)


def test_device_admin_checks_local_machine_by_itself():
    # 管理 API 本身也檢查本機（不只靠 middleware）：區網模式沒開時 middleware 不檢查
    from fastapi import HTTPException
    from app.routers import devices as router

    class Fake:
        scope = {"client": ("127.0.0.1", 1)}
        headers = {"x-forwarded-for": "192.168.50.99"}

    with pytest.raises(HTTPException) as error:
        router.local_machine_only(Fake())
    assert error.value.status_code == 403


# ---------- 配對碼 ----------

def test_pairing_code_shape_and_status(remote):
    response = remote.post("/api/devices/pairing", json={})
    body = response.json()
    assert len(body["code"]) == 8 and body["code"].isdigit() and body["seconds"] == 300 and body["level"] == "standard"
    status = remote.get("/api/devices/pairing").json()
    assert status["active"] is True and "code" not in status
    assert remote.post("/api/devices/pairing", json={"level": "full"}).status_code == 422  # 不提供「完整」


def test_pairing_sets_a_device_cookie(remote):
    device, response = paired(remote)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{settings.DEVICE_COOKIE}=")
    lower = cookie.lower()
    assert "httponly" in lower and "samesite=strict" in lower and "path=/" in lower
    assert f"max-age={90 * 24 * 3600}" in lower
    assert "secure" not in lower and "domain=" not in lower
    assert response.json()["device"]["level"] == "standard" and response.json()["device"]["name"] == "測試手機"
    assert device.get("/api/resources").status_code == 200
    me = device.get("/api/devices/me").json()
    assert me == {"remote": True, "access": "standard", "device": {"id": me["device"]["id"], "name": "測試手機", "level": "standard"}}


def test_pairing_code_is_one_time(remote):
    code = new_code(remote)
    assert make_client(PHONE).post("/api/pair", json={"code": code}).status_code == 200
    assert make_client(TABLET).post("/api/pair", json={"code": code}).status_code == 403
    assert remote.get("/api/devices/pairing").json()["active"] is False


def test_pairing_code_accepts_spaces_and_dashes(remote):
    code = new_code(remote)
    assert make_client(PHONE).post("/api/pair", json={"code": f"{code[:4]}-{code[4:]} "}).status_code == 200


def test_pairing_code_expires(remote):
    code = new_code(remote)
    devices._code["expires"] = time.time() - 1
    assert make_client(PHONE).post("/api/pair", json={"code": code}).status_code == 403
    assert remote.get("/api/devices/pairing").json()["active"] is False


def test_new_code_replaces_old_and_can_be_cancelled(remote):
    old = new_code(remote)
    new = new_code(remote)
    if old != new:
        assert make_client(PHONE).post("/api/pair", json={"code": old}).status_code == 403
    assert remote.delete("/api/devices/pairing").json() == {"cancelled": True}
    assert make_client(TABLET).post("/api/pair", json={"code": new}).status_code == 403


def test_five_wrong_guesses_void_the_code(remote):
    code = new_code(remote)
    wrong = f"{(int(code) + 1) % 10 ** 8:08d}"
    for index in range(5):  # 從 5 個不同位址各猜一次（不觸發 per-IP 限速）
        assert make_client(f"192.168.50.{100 + index}").post("/api/pair", json={"code": wrong}).status_code == 403
    assert make_client(PHONE).post("/api/pair", json={"code": code}).status_code == 403  # 正確的碼也沒用了
    kinds = [row["kind"] for row in remote.get("/api/devices/events").json()["items"]]
    assert "code_voided" in kinds and kinds.count("pair_failed") == 6


def test_per_ip_rate_limit(remote):
    attacker = make_client("192.168.50.66")
    for _ in range(5):  # 沒有有效的碼時亂猜也算失敗
        assert attacker.post("/api/pair", json={"code": "11112222"}).status_code == 403
    code = new_code(remote)
    blocked = attacker.post("/api/pair", json={"code": code})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0
    assert make_client(PHONE).post("/api/pair", json={"code": code}).status_code == 200  # 其他位址不受影響
    # 被暫停時不消耗配對碼的猜錯次數
    assert devices._code is None


def test_rate_limit_expires(remote):
    attacker = make_client("192.168.50.67")
    for _ in range(5):
        attacker.post("/api/pair", json={"code": "11112222"})
    devices._ip_blocked["192.168.50.67"] = time.time() - 1
    code = new_code(remote)
    assert attacker.post("/api/pair", json={"code": code}).status_code == 200


def test_pair_requires_json_and_same_origin(remote):
    code = new_code(remote)
    phone = make_client(PHONE)
    assert phone.post("/api/pair", content=f'{{"code":"{code}"}}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert phone.post("/api/pair", json={"code": code}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert phone.post("/api/pair", json={"code": code}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert phone.post("/api/pair", json={"code": code}, headers={"Origin": f"http://{LAN_HOST}"}).status_code == 200


# ---------- token ----------

def test_token_is_stored_only_as_hash(remote):
    device, response = paired(remote)
    token = device.cookies.get(settings.DEVICE_COOKIE)
    assert token and len(token) >= 43
    with SessionLocal() as db:
        row = db.scalars(select(Device)).one()
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        values = [str(value) for value in db.execute(text("SELECT * FROM devices")).one()]
        events = " ".join(str(value) for row in db.execute(text("SELECT * FROM device_events")).all() for value in row)
    assert token not in " ".join(values) and token not in events


@pytest.mark.parametrize("token", ["", "made-up-token", "x" * 500])
def test_invalid_token_is_401(remote, token):
    phone = make_client(PHONE)
    if token:
        phone.cookies.set(settings.DEVICE_COOKIE, token)
    # 網頁路徑（/）未配對時回配對頁，見 test_frontend.py
    for path in ("/api/resources", "/api/no-such-api", "/launch-probe", "/api/devices/me", "/assets/index-x.js"):
        response = phone.get(path)
        assert response.status_code == 401, path
    assert phone.post("/api/resources", json={"code": "demo-x"}).status_code == 401
    assert response.json()["pair"] is True


def test_revoked_device_is_401_and_cookie_cleared(remote):
    device, _ = paired(remote)
    device_id = remote.get("/api/devices").json()["items"][0]["id"]
    assert remote.post(f"/api/devices/{device_id}/revoke").json()["status"] == "revoked"
    response = device.get("/api/resources")
    assert response.status_code == 401
    assert response.headers["set-cookie"].startswith(f'{settings.DEVICE_COOKIE}=""') or "max-age=0" in response.headers["set-cookie"].lower()


def test_revoke_all(remote):
    phone, _ = paired(remote, PHONE)
    tablet, _ = paired(remote, TABLET, name="測試平板")
    assert remote.post("/api/devices/revoke-all").json() == {"revoked": 2}
    assert phone.get("/api/resources").status_code == 401 and tablet.get("/api/resources").status_code == 401
    assert {row["status"] for row in remote.get("/api/devices").json()["items"]} == {"revoked"}


def test_unused_for_90_days_expires(remote):
    device, _ = paired(remote)
    with SessionLocal() as db:
        db.execute(text("UPDATE devices SET last_seen_at = :at"), {"at": utcnow() - timedelta(days=91)})
        db.commit()
    assert device.get("/api/resources").status_code == 401
    assert remote.get("/api/devices").json()["items"][0]["status"] == "expired"


def test_use_extends_the_90_days(remote):
    device, _ = paired(remote)
    with SessionLocal() as db:
        db.execute(text("UPDATE devices SET last_seen_at = :at"), {"at": utcnow() - timedelta(days=89)})
        db.commit()
    response = device.get("/api/resources")
    assert response.status_code == 200 and "max-age=7776000" in response.headers["set-cookie"].lower()  # cookie 順延
    with SessionLocal() as db:
        seen = db.scalars(select(Device)).one().last_seen_at
    assert devices._aware(seen) > utcnow() - timedelta(minutes=1)
    assert "set-cookie" not in device.get("/api/resources").headers  # 一分鐘內不重寫


# ---------- 級別 ----------

LOCAL_ONLY = [
    ("GET", "/api/backup", None), ("GET", "/api/backup/fingerprint", None), ("POST", "/api/restore", {}),
    ("POST", "/api/covers/clear", {}), ("PUT", "/api/sources", []), ("POST", "/api/tag-pack/mine", {}),
    ("GET", "/api/devices", None), ("GET", "/api/devices/events", None), ("PUT", "/api/devices/remote", {"enabled": False}),
    ("POST", "/api/devices/pairing", {}), ("POST", "/api/devices/revoke-all", {}), ("PATCH", "/api/devices/1", {"level": "standard"}),
]


@pytest.mark.parametrize("level", ["readonly", "standard"])
def test_local_only_routes_refuse_every_device(remote, level):
    device, _ = paired(remote, level=level)
    for method, path, body in LOCAL_ONLY:
        response = device.request(method, path, json=body)
        assert response.status_code == 403, (method, path)
    assert remote.get("/api/devices").json()["items"][0]["status"] == "active"  # 沒被自己改到


def test_readonly_device_can_only_read(remote):
    device, _ = paired(remote, level="readonly")
    assert device.get("/api/resources").status_code == 200
    assert device.get("/api/home").status_code == 200
    assert device.get("/api/devices/me").json()["access"] == "readonly"
    assert device.post("/api/resources", json={"code": "demo-ro"}).status_code == 403
    assert device.get("/api/covers/preview", params={"url": "https://img.example/a.png"}).status_code == 403  # 會連外
    assert device.post("/api/undo/1").status_code == 403


def test_standard_device_can_add_and_edit(remote):
    device, _ = paired(remote, level="standard")
    created = device.post("/api/resources", json={"code": "demo-std", "title": "虛構作品"})
    assert created.status_code == 201
    assert device.patch(f"/api/resources/{created.json()['id']}/quick", json={"rating": 4}).status_code == 200


def test_level_can_be_changed(remote):
    device, _ = paired(remote, level="standard")
    device_id = remote.get("/api/devices").json()["items"][0]["id"]
    assert remote.patch(f"/api/devices/{device_id}", json={"level": "readonly", "name": "  改名手機 "}).json()["level"] == "readonly"
    assert device.post("/api/resources", json={"code": "demo-lv"}).status_code == 403
    assert remote.get("/api/devices").json()["items"][0]["name"] == "改名手機"
    assert remote.patch(f"/api/devices/{device_id}", json={"level": "full"}).status_code == 422
    assert remote.patch("/api/devices/9999", json={"level": "readonly"}).status_code == 404


# ---------- 管理 API、開關、事件 ----------

def test_device_list_fields(remote):
    paired(remote, name="測試手機\x00\n")
    row = remote.get("/api/devices").json()["items"][0]
    assert set(row) == {"id", "name", "level", "status", "created_at", "last_seen_at", "last_ip", "revoked_at", "expires_at"}
    assert row["name"] == "測試手機" and row["last_ip"] == PHONE and row["status"] == "active"
    assert "token" not in str(row).lower()


def test_unnamed_device_gets_a_default_name(remote):
    _, response = paired(remote, name="   ")
    assert response.json()["device"]["name"] == "未命名裝置"


def test_remote_toggle_writes_next_start_setting(remote):
    state = remote.get("/api/devices/remote").json()
    assert state["enabled"] is True and state["enabled_next"] is False and state["restart_needed"] is True
    assert remote.put("/api/devices/remote", json={"enabled": True}).json()["restart_needed"] is False
    import json
    assert json.loads(settings.REMOTE_FILE.read_text(encoding="utf-8")) == {"enabled": True}
    assert remote.put("/api/devices/remote", json={"enabled": False}).json()["enabled_next"] is False
    assert make_client(PHONE).put("/api/devices/remote", json={"enabled": True}).status_code == 401


def test_remote_next_tolerates_a_broken_file(remote):
    settings.REMOTE_FILE.write_text("{not json", encoding="utf-8")
    assert devices.remote_next() is False
    settings.REMOTE_FILE.write_text('{"enabled": "yes"}', encoding="utf-8")
    assert devices.remote_next() is False


def test_events_keep_the_latest_200(remote):
    with SessionLocal() as db:
        for index in range(230):
            devices.record(db, "pair_failed", f"192.168.50.{index % 250}")
        db.commit()
    items = remote.get("/api/devices/events").json()["items"]
    assert len(items) == 200 and items[0]["id"] > items[-1]["id"]
    with SessionLocal() as db:
        assert db.scalar(text("SELECT COUNT(*) FROM device_events")) == 200


def test_unauthorized_attempts_are_recorded_but_throttled(remote):
    stranger = make_client("192.168.50.77")
    for _ in range(5):
        stranger.get("/api/resources")
    items = [row for row in remote.get("/api/devices/events").json()["items"] if row["kind"] == "unauthorized"]
    assert len(items) == 1 and items[0]["ip"] == "192.168.50.77" and items[0]["detail"] == "/api/resources"


def test_pairing_events(remote):
    paired(remote)
    kinds = [row["kind"] for row in remote.get("/api/devices/events").json()["items"]]
    assert kinds[:2] == ["paired", "code_created"]
    assert set(kinds) <= set(devices.EVENT_KINDS)


def test_device_writes_do_not_invalidate_caches(remote):
    from app import cache
    paired(remote)
    before = cache.version()
    phone, _ = paired(remote, TABLET)
    phone.get("/api/resources")
    assert cache.version() == before


# ---------- 記錄檔、備份、資料庫 ----------

def test_token_and_code_never_logged(remote, caplog):
    caplog.set_level(logging.DEBUG)
    code = new_code(remote)
    phone = make_client(PHONE)
    phone.post("/api/pair", json={"code": "99999999"})
    phone.post("/api/pair", json={"code": code, "name": "測試手機"})
    token = phone.cookies.get(settings.DEVICE_COOKIE)
    phone.get("/api/resources")
    assert token and code not in caplog.text and token not in caplog.text
    events = remote.get("/api/devices/events").text
    assert code not in events and token not in events


def test_devices_are_not_in_backups(remote):
    paired(remote)
    backup = remote.get("/api/backup")
    assert backup.status_code == 200
    body = backup.text
    assert "devices" not in backup.json() and "測試手機" not in body and "token" not in body


def test_restore_replace_keeps_devices(remote):
    device, _ = paired(remote)
    empty = {"format": "oas-backup", "version": 1, "sources": [], "resources": []}
    assert remote.post("/api/restore", params={"mode": "replace"}, json=empty).status_code == 200
    assert device.get("/api/resources").status_code == 200


def test_old_database_gets_the_new_tables(client):
    # 模擬舊版資料庫：沒有 devices、device_events，user_version 是 1，開著 WAL
    with SessionLocal() as db:
        db.execute(text("INSERT INTO resources (code, source_name, title, note, image_url, rating, status, created_at, updated_at, image_kind, link_dead, kind, format, progress) "
                        "VALUES ('demo-old', '其他網站', '舊資料庫的虛構作品', '', '', 0, '未讀', '2026-01-01', '2026-01-01', '', 0, '', '', '')"))
        db.commit()
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE devices"))
        connection.execute(text("DROP TABLE device_events"))
        connection.execute(text("PRAGMA user_version = 1"))
    initialise_database()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA user_version")).scalar() == 2
        tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"devices", "device_events"} <= tables
        assert connection.execute(text("SELECT title FROM resources WHERE code='demo-old'")).scalar() == "舊資料庫的虛構作品"
    initialise_database()  # 再跑一次不出錯


def test_ip_helpers():
    assert devices.allowed_source("192.168.1.2") and devices.allowed_source("::ffff:10.0.0.1") and devices.allowed_source("fe80::1%eth0")
    assert not devices.allowed_source("") and not devices.allowed_source("testclient") and not devices.allowed_source("100.100.1.1")
    assert devices.remote_host_allowed("192.168.1.2") and not devices.remote_host_allowed("127.0.0.2")
    assert not devices.remote_host_allowed("1.1.1.1")
    assert access.COOKIE_MAX_AGE == 90 * 24 * 3600



# ---------- 資安審查補充（2026-11-26） ----------

def _site(tmp_path):
    """和 main.py 一樣的防護，再掛上打包好的網頁（測試環境沒有 frontend/dist）。"""
    from fastapi import FastAPI
    from app import frontend, security
    from app.routers import devices as device_routes
    (tmp_path / "site" / "assets").mkdir(parents=True)
    (tmp_path / "site" / "index.html").write_text("<!doctype html><title>demo</title>", encoding="utf-8")
    (tmp_path / "site" / "assets" / "app.js").write_text("console.log('demo')", encoding="utf-8")
    site = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    security.install(site)
    site.include_router(device_routes.public)
    site.include_router(device_routes.router)
    assert frontend.install(site, tmp_path / "site")
    return site


def test_remote_off_pages_and_assets_are_identical(client, tmp_path):
    site = _site(tmp_path)
    paths = ["/", "/assets/app.js", "/library/demo", "/api/devices/me", "/api/devices"]

    def snapshot(ip, extra):
        test_client = TestClient(site, client=(ip, 50123), headers=JSON)
        rows = []
        for path in paths:
            response = test_client.get(path, headers=extra)
            rows.append((path, response.status_code, {k: v for k, v in response.headers.items() if k not in ("date", "etag", "last-modified")}, response.content))
        return rows

    baseline = snapshot("testclient", {})
    assert [row[1] for row in baseline] == [200, 200, 200, 200, 403]
    for ip in ("127.0.0.1", PHONE, "203.0.113.9"):
        assert snapshot(ip, {"X-Forwarded-For": "198.51.100.4", "Cookie": f"{settings.DEVICE_COOKIE}=made-up"}) == baseline, ip


def test_remote_on_assets_need_pairing(remote, tmp_path):
    site = _site(tmp_path)
    assert TestClient(site, client=(PHONE, 1), base_url=f"http://{LAN_HOST}").get("/assets/app.js").status_code == 401
    assert TestClient(site, client=("127.0.0.1", 1), base_url="http://localhost:8080").get("/assets/app.js").status_code == 200


def test_pair_flood_cannot_push_out_important_events(remote):
    paired(remote)  # code_created、paired
    device_id = remote.get("/api/devices").json()["items"][0]["id"]
    remote.post(f"/api/devices/{device_id}/revoke")
    attacker = make_client("192.168.50.66")
    for _ in range(260):  # 原本的攻擊：同一個位址連送錯碼，200 筆事件全變 pair_blocked
        assert attacker.post("/api/pair", json={"code": "11112222"}).status_code in (403, 429)
    items = remote.get("/api/devices/events").json()["items"]
    kinds = [row["kind"] for row in items]
    assert {"code_created", "paired", "revoked"} <= set(kinds)
    assert kinds.count("pair_blocked") == 1
    failed = [row for row in items if row["kind"] == "pair_failed"]
    assert len(failed) == 1 and failed[0]["detail"] == "5 次"


def test_noise_from_many_addresses_keeps_important_events(remote):
    paired(remote)
    for index in range(260):  # 換很多位址亂猜、未配對亂打
        ip = f"10.9.{index // 200}.{index % 200 + 1}"
        make_client(ip).post("/api/pair", json={"code": "11112222"})
        make_client(ip).get("/api/resources")
    items = remote.get("/api/devices/events").json()["items"]
    noise = [row for row in items if row["kind"] in devices.NOISE_KINDS]
    assert len(noise) == 200
    assert {"code_created", "paired"} <= {row["kind"] for row in items}
    with SessionLocal() as db:
        assert db.scalar(text("SELECT COUNT(*) FROM device_events")) == 202


def test_important_events_keep_their_own_200(remote):
    with SessionLocal() as db:
        for _ in range(230):
            devices.record(db, "level_changed", "127.0.0.1")
        devices.record(db, "unauthorized", PHONE, None, "/api/resources")
        db.commit()
    kinds = [row["kind"] for row in remote.get("/api/devices/events").json()["items"]]
    assert kinds.count("level_changed") == 200 and kinds.count("unauthorized") == 1


PATH_VARIANTS = [
    ("GET", "/api/%62ackup"), ("GET", "/api/backup/"), ("GET", "/API/BACKUP"), ("GET", "/api/Backup"), ("GET", "/api//backup"),
    ("HEAD", "/api/backup"), ("OPTIONS", "/api/backup"), ("GET", "/api/backup/fingerprint/"), ("GET", "/api/%64evices"),
    ("GET", "/api/devices/"), ("GET", "/api/devices/events/"), ("POST", "/api/%72estore"), ("POST", "/api/restore/"),
    ("POST", "/API/RESTORE"), ("HEAD", "/api/devices"), ("OPTIONS", "/api/restore"), ("PUT", "/api/devices/remote/"),
]


@pytest.mark.parametrize("method,path", PATH_VARIANTS)
def test_path_variants_cannot_reach_local_routes(remote, method, path):
    remote.post("/api/resources", json={"code": "demo-keep", "title": "虛構保留作品"})
    device, _ = paired(remote)
    body = {"format": "oas-backup", "version": 1, "sources": [], "resources": []} if "estore" in path.lower() else {"enabled": True}
    response = device.request(method, path, params={"mode": "replace"}, json=body if method in ("POST", "PUT") else None)
    if response.status_code == 200:
        # 有打包網頁（frontend/dist）時，大寫的 /API/BACKUP 不算 API 路徑，會落到網頁的 /{path:path}（route_access 是 PUBLIC，
        # frontend.py 自己判斷）：已配對的裝置拿到的是主程式 index.html，不是備份。確認內容就是那個檔案，不是任何 API 回應
        assert method == "GET" and response.headers["content-type"].startswith("text/html"), (method, path)
        assert response.content == (settings.FRONTEND_DIR / "index.html").read_bytes()
    else:
        assert response.status_code in (401, 403, 404, 405), response.status_code
    assert "oas-backup" not in response.text and "虛構保留作品" not in response.text and "fingerprint" not in response.text
    assert [row["code"] for row in remote.get("/api/resources").json()["items"]] == ["demo-keep"]  # 沒被清空
    assert not settings.REMOTE_FILE.exists()  # 開關沒被改


@pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "127.8.9.10", "127.0.0.1"])
def test_loopback_variants_count_as_local(remote, ip):
    assert make_client(ip, "localhost:8080").get("/api/backup").status_code == 200


@pytest.mark.parametrize("ip", ["0.0.0.0", "::", "255.255.255.255", "224.0.0.1"])
def test_unspecified_sources_are_refused(remote, ip):
    assert make_client(ip, "localhost:8080").get("/api/health").status_code == 403
    assert make_client(ip, "localhost:8080").get("/api/backup").status_code == 403
