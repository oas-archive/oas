from app import defaults
from app.archive import normalise_tags
from app.database import SessionLocal


def test_fresh_library_gets_sources_and_tag_pack(client):
    with SessionLocal() as db:
        assert defaults.is_empty(db)
        counts = defaults.seed(db)
        assert counts["sources"] == 6 and counts["aliases"] > 200 and counts["blocked"] > 30
        assert normalise_tags(db, ["Sole Male", "Sole Female", "porn", "自訂標籤"]) == ["單女主", "單男主", "自訂標籤"]  # 翻譯、封鎖、原樣保留
        assert not defaults.is_empty(db)
    sources = {row["name"]: row["template"] for row in client.get("/api/sources").json()}
    assert sources["nhentai"] == "https://nhentai.net/g/{}/"
    info = client.post("/api/metadata", json={"url": "https://nhentai.net/g/123456/", "fetch": False}).json()
    assert (info["source_name"], info["code"]) == ("nhentai", "123456")  # 網址規則立即可用


def test_existing_library_is_never_seeded(client, monkeypatch):
    monkeypatch.setattr(defaults, "enabled", lambda: True)
    client.post("/api/resources", json={"code": "x", "title": "我的作品"})
    with SessionLocal() as db:
        assert defaults.seed_if_empty(db) is None
    assert client.get("/api/sources").json() == [{"name": "其他網站", "template": "", "kind": ""}]
