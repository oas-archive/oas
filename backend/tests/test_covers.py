import time

import pytest

from app import covers

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def fake_download(monkeypatch):
    calls = []

    def download(url):
        calls.append(url)
        if "broken" in url:
            raise covers.CoverError("來源拒絕（HTTP 403）")
        return PNG, "image/png"

    monkeypatch.setattr(covers, "download", download)
    return calls


def make(client, code, image_url):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "image_url": image_url}).json()


def wait_for(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_cover_miss_redirects_then_serves_local(client, fake_download):
    item = make(client, "1", "https://img.example/1.png")
    first = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
    assert first.status_code == 307 and first.headers["location"] == "https://img.example/1.png"
    assert wait_for(lambda: client.get("/api/covers/status").json()["cached"] == 1)
    second = client.get(f"/api/covers/{item['id']}", follow_redirects=False)
    assert second.status_code == 200 and second.headers["content-type"] == "image/png" and second.content == PNG


def test_batch_cache_and_failures(client, fake_download):
    make(client, "1", "https://img.example/1.png")
    make(client, "2", "https://img.example/broken.png")
    make(client, "3", "https://img.example/1.png")  # 同一網址只下載一次
    make(client, "4", "")
    assert client.post("/api/covers/cache").json() == {"queued": 2}
    status = client.get("/api/covers/status").json()
    assert status["with_image"] == 2 and status["cached"] == 1 and status["failed"] == 1 and status["pending"] == 0
    assert status["batch"]["done"] == 2 and status["batch"]["running"] is False
    # 失敗紀錄 24 小時內不重試，除非明確要求
    assert client.post("/api/covers/cache").json() == {"queued": 0}
    assert client.post("/api/covers/cache", params={"retry_failed": True}).json() == {"queued": 1}
    assert fake_download.count("https://img.example/1.png") == 1


def test_cover_without_image(client, fake_download):
    item = make(client, "1", "")
    assert client.get(f"/api/covers/{item['id']}").status_code == 404
    assert client.get("/api/covers/99999").status_code == 404


def test_internal_addresses_rejected():
    for url in ["http://127.0.0.1/a.png", "http://localhost/a.png", "file:///etc/passwd", "http://10.0.0.5/a.png"]:
        with pytest.raises(covers.CoverError):
            covers.check_url(url)


def test_thumbnail_downscales_and_falls_back(client, monkeypatch):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1200, 1600), (200, 40, 60)).save(buffer, "PNG")
    big = buffer.getvalue()
    monkeypatch.setattr(covers, "download", lambda url: (big if "big" in url else PNG, "image/png"))
    large = make(client, "big", "https://img.example/big.png")
    broken = make(client, "bad", "https://img.example/not-really.png")
    assert client.post("/api/covers/cache").json() == {"queued": 2}

    thumb = client.get(f"/api/covers/{large['id']}", params={"w": 360})
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.width == 360 and image.height == 480
    assert len(thumb.content) < len(big)
    assert client.get(f"/api/covers/{large['id']}").content == big  # 不帶 w 仍回傳原圖

    fallback = client.get(f"/api/covers/{broken['id']}", params={"w": 360})
    assert fallback.status_code == 200 and fallback.headers["content-type"] == "image/png" and fallback.content == PNG
