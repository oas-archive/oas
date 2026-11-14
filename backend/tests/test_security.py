import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app import covers
from app.main import app

EMPTY_BACKUP = '{"format": "oas-backup", "version": 1, "resources": []}'


def test_cross_site_writes_are_rejected(client):
    client.post("/api/resources", json={"code": "keep", "title": "不能被清掉"})
    # 其他網站的 Origin
    response = client.post("/api/restore", params={"mode": "replace"}, content=EMPTY_BACKUP, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    # 不帶 Content-Type（瀏覽器不會先預檢的寫法）
    bare = TestClient(app)
    assert bare.post("/api/restore", params={"mode": "replace"}, content=EMPTY_BACKUP).status_code == 415
    assert bare.post("/api/undo/1").status_code == 415
    assert bare.post("/api/resources", content='{"code": "x"}', headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.get("/api/resources").json()["total"] == 1


@pytest.mark.parametrize("host", [
    "localhost:8080",  # 桌面捷徑
    "127.0.0.1:8765",  # 手機 WebView（http://127.0.0.1:<埠>）
    "localhost:5173",  # npm run dev 經 Vite 代理，保留原 Host
])
def test_same_site_writes_still_work(client, host):
    response = client.post("/api/resources", json={"code": "ok"}, headers={"Host": host, "Origin": f"http://{host}"})
    assert response.status_code == 201
    assert TestClient(app).get("/api/health").status_code == 200  # 讀取不受影響


@pytest.mark.parametrize("origin", [
    "http://localhost:5173",  # 同一台電腦另一個開發伺服器
    "http://localhost:9999",
    "http://127.0.0.1:8080",  # 主機寫法不同也是不同來源
    "https://localhost:8080",  # scheme 不同
    "http://localhost",  # 沒寫連接埠是 80
    "null",
])
def test_other_port_or_scheme_writes_are_rejected(client, origin):
    client.post("/api/resources", json={"code": "keep"}, headers={"Host": "localhost:8080"})
    response = client.post("/api/restore", params={"mode": "replace"}, content=EMPTY_BACKUP,
                           headers={"Host": "localhost:8080", "Origin": origin})
    assert response.status_code == 403
    assert client.get("/api/resources").json()["total"] == 1


def test_configured_cors_origin_can_write(client, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "CORS_ORIGINS", ["http://127.0.0.1:4000"])
    headers = {"Host": "localhost:8080", "Origin": "http://127.0.0.1:4000"}
    assert client.post("/api/resources", json={"code": "ok"}, headers=headers).status_code == 201
    headers["Origin"] = "http://127.0.0.1:4001"
    assert client.post("/api/resources", json={"code": "no"}, headers=headers).status_code == 403


def test_cors_allows_no_origin_by_default():
    from app import settings
    assert settings._split("") == []
    assert settings.CORS_ORIGINS == []


def test_preflight_from_vite_port_is_refused(client):
    # 另一個專案的開發伺服器（5173）想跨來源呼叫 8080：預檢被拒，也拿不到允許的標頭
    response = client.options("/api/restore", headers={
        "Host": "localhost:8080", "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
    read = client.get("/api/backup", headers={"Host": "localhost:8080", "Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in read.headers  # 瀏覽器不會把備份內容交給那個網頁


def test_phone_token_required_when_set(client, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    assert client.get("/api/health").status_code == 200  # App 等服務就緒用，不需要通行碼
    import hashlib
    import hmac
    proof = client.get("/api/health", params={"nonce": "abc123"}).json()["proof"]
    assert proof == hmac.new(b"secret-token", b"abc123", hashlib.sha256).hexdigest()  # 只有知道通行碼的服務算得出來
    assert client.get("/api/backup").status_code == 401
    assert client.post("/api/restore", params={"mode": "replace"}, content=EMPTY_BACKUP).status_code == 401
    client.cookies.set("oas_token", "wrong")
    assert client.get("/api/resources").status_code == 401
    client.cookies.set("oas_token", "secret-token")
    assert client.get("/api/resources").status_code == 200


def test_unknown_host_is_rejected(client):
    # DNS rebinding：惡意網域解析到 127.0.0.1 時，Host 會是那個網域
    assert client.get("/api/resources", headers={"Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("target", ["http://127.0.0.1:8000/api/backup", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"])
def test_redirects_to_internal_addresses_are_refused(target):
    handler = covers._CheckedRedirect()
    request = urllib.request.Request("https://example.com/cover.jpg")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(request, None, 302, "Found", {}, target)


@pytest.mark.parametrize("url", ["http://100.64.0.1/a.png", "http://[::ffff:127.0.0.1]/a.png", "http://0.0.0.0/a.png", "http://224.0.0.1/a.png"])
def test_non_public_addresses_rejected(url):
    with pytest.raises(covers.CoverError):
        covers.check_url(url)


@pytest.fixture()
def local_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal")

        def log_message(self, *_):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_connection_to_internal_address_is_aborted(local_server):
    # 模擬 DNS rebinding：check_url 查到的是公開位址，真正連線時卻連到本機
    with pytest.raises(urllib.error.URLError):
        covers.safe_urlopen(urllib.request.Request(f"http://127.0.0.1:{local_server}/"), timeout=5)


def test_proxy_on_this_computer_still_works(local_server):
    # 使用者設定了本機代理伺服器（例如 127.0.0.1:7890）時，連到的是代理，不能因此全部擋掉
    request = urllib.request.Request("http://example.com/")
    request.set_proxy(f"127.0.0.1:{local_server}", "http")
    with covers.safe_urlopen(request, timeout=5) as response:
        assert response.read() == b"internal"


@pytest.mark.parametrize("host", ["127.0.0.1:8080/x?", "127.0.0.1:8080/x#", "127.0.0.1/x", "evil.example:80@127.0.0.1", "127.0.0.1:abc", ""])
def test_forged_host_cannot_skip_the_phone_token(client, monkeypatch, host):
    from app import settings
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    assert client.get("/api/backup", headers={"Host": host}).status_code == 400


def test_phone_token_covers_everything_except_health(client, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    for path in ("/docs", "/redoc", "/openapi.json", "/api/health/", "/api/backup", "/"):
        assert client.get(path, headers={"Host": "127.0.0.1:8080"}, follow_redirects=False).status_code == 401, path
    assert client.get("/api/health", headers={"Host": "127.0.0.1:8080"}).status_code == 200


def test_non_ascii_cookie_is_refused_not_a_crash(client, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    assert client.get("/api/backup", headers={"Cookie": "oas_token=\u00e9".encode("latin-1")}).status_code == 401


@pytest.mark.parametrize("path", ["/api/health", "/", "/api/no-such-api"])
def test_security_headers_on_every_response(client, path):
    response = client.get(path)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy and "unsafe-eval" not in policy
    assert "img-src 'self' data: blob:" in policy and "http:" not in policy and "https:" not in policy  # 圖片只准本機（後端代抓）


@pytest.mark.parametrize("site", ["cross-site", None])
def test_launch_probe_is_empty_and_only_it_allows_cross_origin(client, site):
    # 電腦版的啟動畫面是 file:// 頁面（tools/starting.html），no-cors 探測遇到 CORP same-origin 會被瀏覽器擋下而永遠不跳轉。
    # 只有這個空的探測路徑放寬，其他安全標頭照舊
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    response = client.get("/launch-probe", headers=headers)
    assert response.status_code == 204 and response.content == b""
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers  # 不開放讀取，只能知道成功或失敗
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    for path in ("/", "/api/health", "/launch-probe/x", "/launch-probe.html", "/api/launch-probe"):
        assert client.get(path).headers["cross-origin-resource-policy"] == "same-origin", path


def test_launch_probe_keeps_host_check_and_phone_token(client, monkeypatch):
    assert client.get("/launch-probe", headers={"Host": "evil.example"}).status_code == 400
    assert client.post("/launch-probe", json={}).status_code == 405  # 只接受 GET
    from app import settings
    monkeypatch.setattr(settings, "API_TOKEN", "secret-token")
    response = client.get("/launch-probe")  # 手機版不用啟動畫面，沒有通行碼就照樣擋
    assert response.status_code == 401 and response.headers["cross-origin-resource-policy"] == "same-origin"


def test_starting_page_probes_the_launch_probe():
    # 啟動畫面探測的路徑要和後端放寬的是同一個；直接探測首頁會被 CORP 擋下（v0.20.51～v0.20.67 捷徑啟動卡住的原因）
    from pathlib import Path
    from app import security
    html = (Path(__file__).resolve().parents[2] / "tools" / "starting.html").read_text(encoding="utf-8")
    assert "const target = 'http://localhost:8080/'" in html
    assert f"target + '{security.LAUNCH_PROBE_PATH.lstrip('/')}'" in html
    assert "fetch(probe," in html and "fetch(target" not in html


def test_security_headers_on_rejected_requests(client):
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_api_docs_are_not_served(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert "openapi" not in response.text.lower() and "swagger" not in response.text.lower()
    assert "script-src 'self'" in response.headers["content-security-policy"]


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
def test_api_triggered_by_other_sites_is_refused(client, monkeypatch, site):
    # 其他網頁用 <img> 叫典藏館抓圖：不能連外、佔住抓圖名額。same-site 包含同機其他 localhost 埠的網頁
    calls = []
    monkeypatch.setattr(covers, "fetch_image", lambda url: calls.append(url) or (b"\x89PNG fake", "image/png"))
    response = client.get("/api/covers/preview", params={"url": "https://img.example/a.png"}, headers={"Sec-Fetch-Site": site})
    assert response.status_code == 403 and calls == []
    assert response.headers["x-content-type-options"] == "nosniff"  # 被擋的回應也有安全標頭
    assert client.get("/api/backup", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert client.get("/api/health", headers={"Sec-Fetch-Site": site}).status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_own_page_and_tools_can_call_api(client, monkeypatch, site):
    # 自己的網頁是 same-origin、網址列直接開是 none；手機 App 外殼與工具腳本不帶這個標頭
    covers._remote_failed.clear()
    monkeypatch.setattr(covers, "fetch_image", lambda url: (b"\x89PNG fake", "image/png"))
    headers = {} if site is None else {"Sec-Fetch-Site": site}
    response = client.get("/api/covers/preview", params={"url": "https://img.example/b.png"}, headers=headers)
    assert response.status_code == 200 and response.content == b"\x89PNG fake"
    assert client.post("/api/resources", json={"code": "demo"}, headers=headers).status_code == 201


def test_page_navigation_from_other_sites_still_opens(client):
    # 「收錄到 OAS」書籤是從其他網站開 /?add=…：頁面導覽不在 /api 底下，不擋
    response = client.get("/", params={"add": "https://example.com/work/1"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code != 403
