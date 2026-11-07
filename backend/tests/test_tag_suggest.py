"""標籤建議併入：規則變體、名稱幾乎一樣、簡繁轉換、提示字典、共現；略過；API 與收錄預覽。資料都是虛構的。"""
import json

import pytest

from app import tag_suggest, zhconv
from app.database import SessionLocal


def make(client, code, tags, categories=None):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags,
                                               "tag_categories": categories or {}}).json()


@pytest.fixture()
def pack(tmp_path, monkeypatch):
    """測試用的翻譯包（不讀真正的翻譯包，結果才固定）。"""
    path = tmp_path / "pack.json"
    path.write_text(json.dumps({"translate": {"sole heroine": "單女主角"}, "suggest": {"強姦": "強暴"}, "block": []}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("app.defaults.TAG_PACKS", [path])
    return path


def items(client):
    return {entry["source"]: entry for entry in client.get("/api/tags/suggestions").json()["items"]}


def test_name_key_ignores_case_spacing_plural_and_variants():
    assert tag_suggest.name_key("Big-Breasts") == tag_suggest.name_key("big breast")
    assert tag_suggest.name_key("Ｆｕｌｌ　Color") == tag_suggest.name_key("full color")
    assert tag_suggest.name_key("姐妹丼") == tag_suggest.name_key("姊妹丼")
    assert tag_suggest.name_key("glasses") == tag_suggest.name_key("glass")


def test_simplified_conversion_only_when_certain():
    assert zhconv.to_traditional("恩将仇报") == "恩將仇報"
    assert zhconv.to_traditional("短发") is None  # 发 可能是 髮 或 發
    assert zhconv.candidates("短发")[:2] == ["短髮", "短發"]
    assert not zhconv.has_simplified("貓娘")


def test_variant_of_existing_rule(client, pack):
    client.put("/api/tags/rules", json={"alias": "big breasts", "canonical": "巨乳"})
    make(client, "1", ["Big-Breast"])
    entry = items(client)["Big-Breast"]
    assert entry["target"] == "巨乳" and entry["method"] == "variant" and entry["confidence"] == "high"
    assert "big breasts" in entry["reason"]


def test_variant_of_pack_translation(client, pack):
    make(client, "1", ["Sole Heroines"])
    entry = items(client)["Sole Heroines"]
    assert entry["target"] == "單女主角" and entry["target_exists"] is False and "翻譯包" in entry["reason"]


def test_same_name_prefers_traditional_and_more_used(client, pack):
    make(client, "1", ["姊妹丼"])
    make(client, "2", ["姊妹丼"])
    make(client, "3", ["姐妹丼"])
    found = items(client)
    assert found["姐妹丼"]["target"] == "姊妹丼" and found["姐妹丼"]["method"] == "same"
    assert "姊妹丼" not in found  # 不會反過來建議


def test_simplified_matches_existing_or_suggests_rename(client, pack):
    make(client, "1", ["貓娘"])
    make(client, "2", ["猫娘", "恩将仇报", "短发"])
    found = items(client)
    assert found["猫娘"]["target"] == "貓娘" and found["猫娘"]["confidence"] == "high"
    assert found["恩将仇报"]["target"] == "恩將仇報" and found["恩将仇报"]["confidence"] == "medium" and not found["恩将仇报"]["target_exists"]
    assert "短发" not in found  # 一對多的字又沒有現有標籤可以對：不猜


def test_simplified_with_ambiguous_char_uses_existing_tag(client, pack):
    make(client, "1", ["短髮"])
    make(client, "2", ["短发"])
    assert items(client)["短发"]["target"] == "短髮"


def test_hint_needs_existing_target(client, pack):
    make(client, "1", ["強姦"])
    assert "強姦" not in items(client)  # 「強暴」還不存在：不建議
    make(client, "2", ["強暴"])
    entry = items(client)["強姦"]
    assert entry["target"] == "強暴" and entry["method"] == "hint" and entry["confidence"] == "medium"


def test_names_keep_original_and_canonicals_are_left_alone(client, pack):
    make(client, "1", ["貓娘"])
    make(client, "2", ["猫娘"], {"猫娘": "artist"})  # 作者名不轉繁體
    client.put("/api/tags/rules", json={"alias": "neko", "canonical": "貓娘"})
    found = items(client)
    assert "猫娘" not in found and "貓娘" not in found


def test_rule_not_yet_applied_is_reported(client, pack):
    make(client, "1", ["bikini"])
    with SessionLocal() as db:  # 例如從備份還原：規則有了、標籤還沒換
        from app.database import TagAlias
        db.add(TagAlias(alias="bikini", canonical="泳裝"))
        db.commit()
    entry = items(client)["bikini"]
    assert entry["method"] == "rule" and entry["target"] == "泳裝"


def test_together_for_english_tag_only_when_unique(client, pack):
    for code in "123":
        make(client, code, ["hot spring", "溫泉", "單女主"])
    for code in "456789":
        make(client, code, ["單女主"])
    entry = items(client)["hot spring"]
    assert entry["target"] == "溫泉" and entry["method"] == "together" and entry["confidence"] == "low"
    make(client, "10", ["溫泉"])
    make(client, "11", ["溫泉"])
    make(client, "12", ["溫泉"])
    make(client, "13", ["溫泉"])
    assert "hot spring" not in items(client)  # 溫泉 大多數作品沒有 hot spring：可能只是常見組合


def test_blocked_target_is_not_suggested(client, pack):
    make(client, "1", ["貓娘"])
    make(client, "2", ["猫娘"])
    client.post("/api/tags/remove", json={"name": "貓娘", "block": True})
    assert "猫娘" not in items(client)


def test_apply_via_rule_api_and_undo(client, pack):
    make(client, "1", ["貓娘"])
    make(client, "2", ["猫娘"])
    saved = client.put("/api/tags/rules", json={"alias": "猫娘", "canonical": "貓娘"}).json()
    assert saved["merged"] == 1 and "猫娘" not in items(client)
    assert make(client, "3", ["猫娘"])["tags"] == ["貓娘"]
    client.post(f"/api/undo/{saved['undo']}")
    assert "猫娘" in items(client)


def test_skip_and_restore(client, pack):
    make(client, "1", ["貓娘"])
    make(client, "2", ["猫娘"])
    assert client.post("/api/tags/suggestions/skip", json={"source": "猫娘", "target": "貓娘"}).json() == {"skipped": 1}
    body = client.get("/api/tags/suggestions").json()
    assert body["total"] == 0 and body["skipped"] == 1
    assert client.post("/api/tags/suggestions/skip", json={"skip": True}).status_code == 422
    client.post("/api/tags/suggestions/skip", json={"skip": False})
    assert "猫娘" in items(client)


def test_capture_preview_lists_new_tags(client, pack, monkeypatch):
    make(client, "1", ["貓娘", "強暴"])
    page = "<title>虛構作品</title>"
    monkeypatch.setattr("app.metadata._get", lambda url: page)
    monkeypatch.setattr("app.metadata.parse", lambda url, html: {"title": "虛構作品", "image_url": "", "tags": ["猫娘", "強姦", "新標籤"], "categories": {}})
    result = client.post("/api/metadata", json={"url": "https://example.com/work/1", "fetch": True}).json()
    assert {(entry["source"], entry["target"]) for entry in result["tag_suggestions"]} == {("猫娘", "貓娘"), ("強姦", "強暴")}


def test_chained_suggestions_go_to_the_end(client, pack, monkeypatch):
    path = pack
    path.write_text(json.dumps({"translate": {}, "suggest": {"賣淫": "賣春"}, "block": []}, ensure_ascii=False), encoding="utf-8")
    make(client, "1", ["賣春"])
    make(client, "2", ["賣淫"])
    make(client, "3", ["卖淫"])
    found = items(client)
    assert found["卖淫"]["target"] == "賣春" and found["卖淫"]["confidence"] == "medium" and "也建議併入" in found["卖淫"]["reason"]
    assert found["賣淫"]["target"] == "賣春"
    with SessionLocal() as db:
        from app.tag_suggest import suggest_for
        assert [(entry["source"], entry["target"]) for entry in suggest_for(db, ["卖春"])] == [("卖春", "賣春")]
