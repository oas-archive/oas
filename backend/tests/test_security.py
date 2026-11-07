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


def test_same_site_writes_still_work(client):
    response = client.post("/api/resources", json={"code": "ok"}, headers={"Origin": "http://localhost:8080"})
    assert response.status_code == 201
    assert TestClient(app).get("/api/health").status_code == 200  # 讀取不受影響


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


def test_security_headers_on_rejected_requests(client):
    response = client.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 400
    assert response.headers["x-content-type-options"] == "nosniff"


def test_api_docs_can_load_their_scripts(client):
    response = client.get("/docs")
    assert "content-security-policy" not in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
