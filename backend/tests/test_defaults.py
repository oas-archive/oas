import json

from app import defaults
from app.archive import normalise_tags
from app.database import SessionLocal


def test_fresh_library_gets_sources_and_tag_pack(client):
    with SessionLocal() as db:
        assert defaults.is_empty(db)
        counts = defaults.seed(db)
        assert counts["sources"] == 8 and counts["aliases"] > 200 and counts["blocked"] > 30
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


def test_user_pack_is_created_once_and_never_overwritten(tmp_path, monkeypatch):
    from app import defaults, settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OAS_SEED", "on")
    path = tmp_path / defaults.USER_PACK_NAME
    defaults.ensure_user_pack()
    pack = json.loads(path.read_text(encoding="utf-8"))
    assert pack["translate"] == {} and pack["suggest"] == {} and pack["block"] == []
    path.write_text('{"translate": {"my word": "我的譯名"}}', encoding="utf-8")
    defaults.ensure_user_pack()
    assert "我的譯名" in path.read_text(encoding="utf-8")


def test_user_pack_overrides_builtin_and_broken_file_is_ignored(tmp_path, monkeypatch):
    from app import defaults, settings
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    builtin = tmp_path / "builtin.json"
    builtin.write_text('{"translate": {"a": "內建", "b": "內建"}, "block": ["c"]}', encoding="utf-8")
    monkeypatch.setattr(defaults, "TAG_PACKS", [builtin])
    user = tmp_path / defaults.USER_PACK_NAME
    user.write_text('\ufeff{"translate": {"a": "我的", "壞": 1}, "block": ["d", 2]}', encoding="utf-8")  # 記事本存檔會帶 BOM
    assert defaults.load_packs() == ({"a": "我的", "b": "內建"}, {"c", "d"})
    for broken in ("{不是 JSON", "[1, 2]", '{"translate": [1], "block": "x"}'):
        user.write_text(broken, encoding="utf-8")
        assert defaults.load_packs() == ({"a": "內建", "b": "內建"}, {"c"})
