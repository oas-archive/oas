import io

from PIL import Image

from app import covers


def make(client, **extra):
    payload = {"code": "1", "source_name": "站A", "title": "作品", "tags": ["奇幻"]}
    payload.update(extra)
    return client.post("/api/resources", json=payload).json()


def test_quick_rating_and_status_keep_order(client):
    item = make(client)
    rated = client.patch(f"/api/resources/{item['id']}/quick", json={"rating": 4.3})
    assert rated.status_code == 200 and rated.json()["rating"] == 4.5 and rated.json()["status"] == "未讀"
    assert rated.json()["updated_at"] == item["updated_at"]
    done = client.patch(f"/api/resources/{item['id']}/quick", json={"status": "已完食"}).json()
    assert done["status"] == "已完食" and done["rating"] == 4.5
    cleared = client.patch(f"/api/resources/{item['id']}/quick", json={"rating": 0}).json()
    assert cleared["rating"] == 0


def test_quick_validation(client):
    item = make(client)
    assert client.patch(f"/api/resources/{item['id']}/quick", json={}).status_code == 422
    assert client.patch(f"/api/resources/{item['id']}/quick", json={"rating": 6}).status_code == 422
    assert client.patch(f"/api/resources/{item['id']}/quick", json={"status": "亂寫"}).status_code == 422
    assert client.patch("/api/resources/9999/quick", json={"rating": 3}).status_code == 404


def test_cover_ratio_reported(client, monkeypatch):
    buffer = io.BytesIO()
    Image.new("RGB", (600, 900)).save(buffer, "PNG")
    monkeypatch.setattr(covers, "download", lambda url: (buffer.getvalue(), "image/png"))
    make(client, image_url="https://img.example/tall.png")
    make(client, code="2", image_url="")
    assert all(item["cover_ratio"] is None for item in client.get("/api/resources").json()["items"])
    client.post("/api/covers/cache")
    ratios = {item["code"]: item["cover_ratio"] for item in client.get("/api/resources").json()["items"]}
    assert ratios == {"1": 1.5, "2": None}


def test_backfill_ratios(client, monkeypatch):
    from app.database import CoverCache, SessionLocal

    buffer = io.BytesIO()
    Image.new("RGB", (400, 300)).save(buffer, "PNG")
    monkeypatch.setattr(covers, "download", lambda url: (buffer.getvalue(), "image/png"))
    make(client, image_url="https://img.example/wide.png")
    client.post("/api/covers/cache")
    with SessionLocal() as db:
        db.query(CoverCache).update({CoverCache.ratio: None})
        db.commit()
    assert covers.backfill_ratios() == 1
    assert client.get("/api/resources").json()["items"][0]["cover_ratio"] == 0.75
