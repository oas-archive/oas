"""公開版的主畫面不直接連外部網站：外部網址可以轉址回手機上的其他埠，瀏覽器會帶著通行碼 cookie 跟過去（實機測到的）。"""
import pytest

from app import covers, settings
from app.database import Resource, SessionLocal

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
URL = "https://img.example.com/cover.png"


@pytest.fixture
def public(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EDITION", "public")
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path / "covers")
    (tmp_path / "covers").mkdir()
    covers._remote_failed.clear()
    return client


@pytest.fixture
def work(public):
    with SessionLocal() as db:
        resource = Resource(code="https://example.com/work/1", source_name="測試", title="虛構作品", image_url=URL)
        db.add(resource)
        db.commit()
        return resource.id


def choose(client, save):
    assert client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": save, "background_reader": False}).status_code == 200


def fake_fetch(monkeypatch, content=PNG, kind="image/png"):
    calls = []

    def fetch(url):
        calls.append(url)
        return content, kind

    monkeypatch.setattr(covers, "fetch_image", fetch)
    return calls


def test_not_saving_passes_the_image_through_without_a_redirect_or_a_file(public, work, monkeypatch, tmp_path):
    choose(public, False)
    calls = fake_fetch(monkeypatch)
    response = public.get(f"/api/covers/{work}", follow_redirects=False)
    assert response.status_code == 200 and response.content == PNG and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert calls == [URL] and list((tmp_path / "covers").iterdir()) == []


def test_saving_stores_the_cover_and_serves_it_without_a_redirect(public, work, monkeypatch, tmp_path):
    choose(public, True)
    fake_fetch(monkeypatch)
    monkeypatch.setattr(covers, "image_ratio", lambda path: 1.5)
    monkeypatch.setattr(covers, "image_colour", lambda path: "#112233")
    monkeypatch.setattr(covers, "image_fingerprint", lambda path: "0" * 16)
    response = public.get(f"/api/covers/{work}", follow_redirects=False)
    assert response.status_code == 200 and response.content == PNG
    assert [path.suffix for path in (tmp_path / "covers").iterdir()] == [".png"]


@pytest.mark.parametrize("save", [True, False])
def test_source_failure_is_a_plain_404_not_a_redirect(public, work, monkeypatch, save):
    choose(public, save)

    def fail(url):
        raise covers.CoverError("來源拒絕（HTTP 403）")

    monkeypatch.setattr(covers, "fetch_image", fail)
    assert public.get(f"/api/covers/{work}", follow_redirects=False).status_code == 404


def test_failed_source_is_not_retried_on_every_render(public, work, monkeypatch):
    choose(public, False)
    calls = []

    def fail(url):
        calls.append(url)
        raise covers.CoverError("連線失敗")

    monkeypatch.setattr(covers, "fetch_image", fail)
    for _ in range(3):
        assert public.get(f"/api/covers/{work}", follow_redirects=False).status_code == 404
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1:9777/x.png", "http://localhost:9777/x.png", "http://[::1]:9777/x.png", "http://192.168.0.1/x.png", "file:///etc/hosts"])
def test_internal_addresses_are_refused_before_connecting(public, url, monkeypatch):
    def no_connection(*args, **kwargs):
        raise AssertionError("不該連線")

    monkeypatch.setattr(covers, "safe_urlopen", no_connection)
    assert public.get("/api/covers/preview", params={"url": url}, follow_redirects=False).status_code == 404


def test_preview_returns_the_image_without_saving(public, monkeypatch, tmp_path):
    calls = fake_fetch(monkeypatch)
    response = public.get("/api/covers/preview", params={"url": URL}, follow_redirects=False)
    assert response.status_code == 200 and response.content == PNG and response.headers["cache-control"] == "no-store"
    assert calls == [URL] and list((tmp_path / "covers").iterdir()) == []


def test_redirect_to_an_internal_address_is_refused(public):
    """後端自己跟轉址，每一跳都檢查目標。"""
    with pytest.raises(covers.urllib.error.URLError):
        covers._CheckedRedirect().redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:9777/x")


@pytest.fixture
def personal(client, monkeypatch, tmp_path):
    """電腦版與手機個人版（都是 personal）：走同一條路，只是一律可以存封面。"""
    monkeypatch.setattr(settings, "EDITION", "personal")
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path / "covers")
    (tmp_path / "covers").mkdir()
    covers._remote_failed.clear()
    return client


def test_personal_preview_is_fetched_by_the_backend_without_saving(personal, monkeypatch, tmp_path):
    calls = fake_fetch(monkeypatch)
    response = personal.get("/api/covers/preview", params={"url": URL}, follow_redirects=False)
    assert response.status_code == 200 and response.content == PNG and response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert calls == [URL] and list((tmp_path / "covers").iterdir()) == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:9777/x.png", "http://localhost:9777/x.png", "http://[::1]:9777/x.png", "http://192.168.0.1/x.png", "file:///etc/hosts"])
def test_personal_preview_still_refuses_internal_addresses(personal, url, monkeypatch):
    def no_connection(*args, **kwargs):
        raise AssertionError("不該連線")

    monkeypatch.setattr(covers, "safe_urlopen", no_connection)
    response = personal.get("/api/covers/preview", params={"url": url}, follow_redirects=False)
    assert response.status_code == 404 and "location" not in response.headers


def add_work(code, image_url):
    with SessionLocal() as db:
        resource = Resource(code=code, source_name="測試", title="虛構作品", image_url=image_url)
        db.add(resource)
        db.commit()
        return resource.id


def test_personal_uncached_cover_is_not_redirected(personal, monkeypatch, tmp_path):
    resource_id = add_work("https://example.com/work/2", URL)
    calls = fake_fetch(monkeypatch)
    monkeypatch.setattr(covers, "image_ratio", lambda path: 1.5)
    monkeypatch.setattr(covers, "image_colour", lambda path: "#112233")
    monkeypatch.setattr(covers, "image_fingerprint", lambda path: "0" * 16)
    response = personal.get(f"/api/covers/{resource_id}", follow_redirects=False)
    assert response.status_code == 200 and response.content == PNG and "location" not in response.headers
    assert calls == [URL] and [path.suffix for path in (tmp_path / "covers").iterdir()] == [".png"]  # 抓下來順便存好
    assert personal.get(f"/api/covers/{resource_id}", follow_redirects=False).status_code == 200
    assert calls == [URL]  # 第二次直接由本機提供


def test_personal_cover_on_an_internal_address_is_404_without_connecting(personal, monkeypatch):
    resource_id = add_work("https://example.com/work/3", "http://127.0.0.1:9777/x.png")

    def no_connection(*args, **kwargs):
        raise AssertionError("不該連線")

    monkeypatch.setattr(covers, "safe_urlopen", no_connection)
    response = personal.get(f"/api/covers/{resource_id}", follow_redirects=False)
    assert response.status_code == 404 and "location" not in response.headers


@pytest.mark.parametrize("edition", ["public", "personal"])
def test_no_edition_loads_external_images(client, monkeypatch, edition):
    monkeypatch.setattr(settings, "EDITION", edition)
    policy = client.get("/api/preferences").headers["content-security-policy"]
    image_rule = next(part.strip() for part in policy.split(";") if part.strip().startswith("img-src"))
    assert image_rule == "img-src 'self' data: blob:"
