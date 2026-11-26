from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import frontend


def make_site(tmp_path):
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "index.html").write_text("<html>OAS</html>", encoding="utf-8")
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.png").write_bytes(b"png")
    (tmp_path.parent / "secret.txt").write_text("不能被讀到", encoding="utf-8")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # 與 main.py 相同

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    assert frontend.install(app, tmp_path)
    return TestClient(app)


def test_serves_built_frontend_with_spa_fallback(tmp_path):
    site = make_site(tmp_path / "dist")
    assert site.get("/").text == "<html>OAS</html>" and site.get("/").headers["cache-control"] == "no-cache"
    assert site.get("/?page=taste&item=3").text == "<html>OAS</html>"  # 單頁應用：網址參數交給前端
    assert site.get("/some/page").text == "<html>OAS</html>"
    asset = site.get("/assets/index-abc.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert site.get("/favicon.png").content == b"png"
    assert site.get("/api/health").json() == {"status": "ok"}  # API 不受影響
    assert site.get("/api/not-there").status_code == 404  # 打錯的 API 不會回網頁
    for closed in ("/docs", "/redoc", "/openapi.json"):  # API 文件已關閉，也不退回首頁
        assert site.get(closed).status_code == 404, closed
    assert "不能被讀到" not in site.get("/..%2Fsecret.txt").text


def test_skips_when_frontend_not_built(tmp_path):
    assert frontend.install(FastAPI(), tmp_path) is False


def test_never_touches_network_paths(tmp_path, monkeypatch):
    # Windows 會把 \主機\分享 當成網路位置並自動送出登入憑證；這類網址連檔案系統都不能碰
    site = make_site(tmp_path / "dist")
    touched = []
    original_is_file, original_resolve = frontend.Path.is_file, frontend.Path.resolve

    def spy_is_file(self, *args, **kwargs):
        touched.append(str(self))
        return original_is_file(self, *args, **kwargs)

    def spy_resolve(self, *args, **kwargs):
        touched.append(str(self))
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(frontend.Path, "is_file", spy_is_file)
    monkeypatch.setattr(frontend.Path, "resolve", spy_resolve)
    for url in ("/%5C%5Cattacker.example%5Cshare%5Cx.png", "/%2F%2Fattacker.example%2Fshare%2Fx.png", "/C:%5CWindows%5Cwin.ini",
                "/%2E%2E%2Fsecret.txt", "/favicon.png%00.html", "/a//b", "/./index.html"):
        touched.clear()
        response = site.get(url)
        assert response.text == "<html>OAS</html>", url  # 一律回首頁
        assert not any("attacker" in path or "Windows" in path or "secret" in path for path in touched), (url, touched)
    assert "不能被讀到" not in site.get("/assets%2F..%2F..%2Fsecret.txt").text  # /assets 由內建的靜態檔案處理，同樣跳不出資料夾
    assert site.get("/favicon.png").content == b"png"  # 正常的檔案照常提供


def test_assets_never_reach_network_paths(tmp_path, monkeypatch):
    site = make_site(tmp_path / "dist")
    looked_up = []
    monkeypatch.setattr(frontend._ImmutableFiles, "lookup_path", lambda self, path: looked_up.append(path) or ("", None))
    for path in ("/assets/%5C%5Cevil%5Cshare%5Ca.js", "/assets/%5Cevil.js", "/assets/C:%5Cwindows%5Cwin.ini", "/assets/a.js:stream"):
        assert site.get(path).status_code == 404, path
    assert looked_up == []


# ---------- 區網模式：還沒配對的裝置只拿得到配對頁 ----------

LAN_HOST = "192.168.50.10:8080"  # 虛構的區網網址
PHONE = "192.168.50.21"


def make_lan_site(tmp_path, monkeypatch):
    """和 main.py 相同的驗證層＋配對 API＋網頁（含配對頁），區網模式開著。回傳（建 client 的函式, 網頁資料夾）。"""
    from app import devices, security, settings
    from app.routers import devices as device_routes

    dist = tmp_path / "dist"
    make_site(dist)
    (dist / "pair.html").write_text("<html>PAIR</html>", encoding="utf-8")
    (dist / "pair").mkdir()
    (dist / "pair" / "pair-abc.js").write_text("console.log('pair')", encoding="utf-8")
    monkeypatch.setattr(settings, "REMOTE", True)
    monkeypatch.setattr(settings, "REMOTE_FILE", tmp_path / "remote.json")
    devices.reset_memory()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    security.install(app)
    app.include_router(device_routes.public)
    app.include_router(device_routes.router)
    assert frontend.install(app, dist)

    def make(ip, host=LAN_HOST):
        return TestClient(app, client=(ip, 50123), base_url=f"http://{host}", headers={"Content-Type": "application/json"})
    return make


def test_unpaired_device_only_gets_the_pair_page(client, tmp_path, monkeypatch):
    make = make_lan_site(tmp_path, monkeypatch)
    phone = make(PHONE)
    for path in ("/", "/?page=taste", "/some/page", "/index.html", "/pair.html"):
        response = phone.get(path)
        assert response.status_code == 200 and response.text == "<html>PAIR</html>", path
        assert response.headers["cache-control"] == "no-cache"
    assert phone.get("/pair/pair-abc.js").text == "console.log('pair')"  # 配對頁自己的程式
    assert phone.get("/favicon.png").content == b"png"
    asset = phone.get("/assets/index-abc.js")  # 主程式要配對後才給
    assert asset.status_code == 401 and asset.json()["pair"] is True
    for path in ("/api/no-such-api", "/api/resources"):  # 不存在的 API 也回 401，分不出哪些存在
        assert phone.get(path).status_code == 401, path
    assert phone.get("/docs").status_code == 401
    made_up = make(PHONE)
    made_up.cookies.set("oas_device", "made-up-token")
    assert made_up.get("/").text == "<html>PAIR</html>"


def test_paired_device_gets_the_app(client, tmp_path, monkeypatch):
    make = make_lan_site(tmp_path, monkeypatch)
    local = make("127.0.0.1", "localhost:8080")
    assert local.get("/").text == "<html>OAS</html>"  # 電腦本機不用配對
    code = local.post("/api/devices/pairing", json={"level": "readonly"}).json()["code"]
    phone = make(PHONE)
    assert phone.post("/api/pair", json={"code": code, "name": "測試手機"}).status_code == 200
    assert phone.get("/").text == "<html>OAS</html>"
    assert phone.get("/assets/index-abc.js").status_code == 200
    assert phone.get("/api/no-such-api").status_code == 404
    # 撤銷後回到配對頁
    device_id = local.get("/api/devices").json()["items"][0]["id"]
    local.post(f"/api/devices/{device_id}/revoke")
    assert phone.get("/").text == "<html>PAIR</html>"
    assert phone.get("/assets/index-abc.js").status_code == 401


def test_unpaired_without_pair_page_gets_nothing(client, tmp_path, monkeypatch):
    make = make_lan_site(tmp_path, monkeypatch)
    (tmp_path / "dist" / "pair.html").unlink()
    response = make(PHONE).get("/")
    assert response.status_code == 401 and "OAS" not in response.text


def test_remote_off_serves_the_app_as_before(tmp_path, monkeypatch):
    from app import settings
    monkeypatch.setattr(settings, "REMOTE", False)
    site = make_site(tmp_path / "dist")
    (tmp_path / "dist" / "pair.html").write_text("<html>PAIR</html>", encoding="utf-8")
    assert site.get("/").text == "<html>OAS</html>"
