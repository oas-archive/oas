import urllib.error
import urllib.request

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


def test_unknown_host_is_rejected(client):
    # DNS rebinding：惡意網域解析到 127.0.0.1 時，Host 會是那個網域
    assert client.get("/api/resources", headers={"Host": "evil.example"}).status_code == 400


@pytest.mark.parametrize("target", ["http://127.0.0.1:8000/api/backup", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"])
def test_redirects_to_internal_addresses_are_refused(target):
    handler = covers._CheckedRedirect()
    request = urllib.request.Request("https://example.com/cover.jpg")
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(request, None, 302, "Found", {}, target)
