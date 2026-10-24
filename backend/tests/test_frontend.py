from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import frontend


def make_site(tmp_path):
    (tmp_path / "assets").mkdir(parents=True)
    (tmp_path / "index.html").write_text("<html>OAS</html>", encoding="utf-8")
    (tmp_path / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "favicon.png").write_bytes(b"png")
    (tmp_path.parent / "secret.txt").write_text("不能被讀到", encoding="utf-8")
    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    assert frontend.install(app, tmp_path)
    return TestClient(app)


def test_serves_built_frontend_with_spa_fallback(tmp_path):
    site = make_site(tmp_path / "dist")
    assert site.get("/").text == "<html>OAS</html>" and site.get("/").headers["cache-control"] == "no-cache"
    assert site.get("/?page=graph&item=3").text == "<html>OAS</html>"  # 單頁應用：網址參數交給前端
    assert site.get("/some/page").text == "<html>OAS</html>"
    asset = site.get("/assets/index-abc.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    assert site.get("/favicon.png").content == b"png"
    assert site.get("/api/health").json() == {"status": "ok"}  # API 不受影響
    assert site.get("/api/not-there").status_code == 404  # 打錯的 API 不會回網頁
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
