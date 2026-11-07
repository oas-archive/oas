"""標籤規則一覽頁的 API：列出、新增、修改、刪除同義詞規則，解除封鎖，以及收錄預覽的「依規則換名稱」說明。"""
from app.archive import explain_tag_rules


def make(client, code, tags):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags}).json()


def rules(client):
    return client.get("/api/tags/rules").json()


def test_new_rule_applies_to_later_items_and_is_listed(client):
    saved = client.put("/api/tags/rules", json={"alias": "Swimsuit", "canonical": "泳裝"}).json()
    assert saved["alias"] == "swimsuit" and saved["final"] == "泳裝" and saved["merged"] == 0
    assert make(client, "1", ["SWIMSUIT", "海邊"])["tags"] == ["泳裝", "海邊"]
    row = next(entry for entry in rules(client)["aliases"] if entry["alias"] == "swimsuit")
    assert row == {"alias": "swimsuit", "canonical": "泳裝", "final": "泳裝", "count": 1}


def test_rule_merges_existing_tag_and_undo_restores(client):
    make(client, "1", ["bikini"])
    make(client, "2", ["泳裝"])
    saved = client.put("/api/tags/rules", json={"alias": "bikini", "canonical": "泳裝"}).json()
    assert saved["merged"] == 1
    assert client.get("/api/tags").json() == ["泳裝"]
    client.post(f"/api/undo/{saved['undo']}")
    assert sorted(client.get("/api/tags").json()) == ["bikini", "泳裝"]


def test_chain_shows_final_name_and_loop_is_rejected(client):
    client.put("/api/tags/rules", json={"alias": "bikini", "canonical": "比基尼"})
    client.put("/api/tags/rules", json={"alias": "比基尼", "canonical": "泳裝"})
    row = next(entry for entry in rules(client)["aliases"] if entry["alias"] == "bikini")
    assert row["canonical"] == "比基尼" and row["final"] == "泳裝"
    response = client.put("/api/tags/rules", json={"alias": "泳裝", "canonical": "bikini"})
    assert response.status_code == 422 and "繞圈" in response.json()["detail"]
    assert client.put("/api/tags/rules", json={"alias": "Same", "canonical": "same"}).status_code == 422


def test_edit_and_delete_rule_with_undo(client):
    client.put("/api/tags/rules", json={"alias": "neko", "canonical": "貓娘"})
    edited = client.put("/api/tags/rules", json={"alias": "neko", "canonical": "貓耳"}).json()
    client.post(f"/api/undo/{edited['undo']}")
    assert next(entry for entry in rules(client)["aliases"] if entry["alias"] == "neko")["canonical"] == "貓娘"
    deleted = client.delete("/api/tags/rules", params={"alias": "NEKO"}).json()
    assert all(entry["alias"] != "neko" for entry in rules(client)["aliases"])
    assert make(client, "1", ["neko"])["tags"] == ["neko"]
    client.post(f"/api/undo/{deleted['undo']}")
    assert make(client, "2", ["neko"])["tags"] == ["貓娘"]
    assert client.delete("/api/tags/rules", params={"alias": "none"}).status_code == 404


def test_unblock_and_undo(client):
    make(client, "1", ["宣傳字"])
    client.post("/api/tags/remove", json={"name": "宣傳字", "block": True})
    assert "宣傳字" in rules(client)["blocked"]
    assert make(client, "2", ["宣傳字"])["tags"] == []
    result = client.post("/api/tags/unblock", json={"name": "宣傳字"}).json()
    assert make(client, "3", ["宣傳字"])["tags"] == ["宣傳字"]
    client.post(f"/api/undo/{result['undo']}")
    assert "宣傳字" in rules(client)["blocked"]
    assert client.post("/api/tags/unblock", json={"name": "沒有"}).status_code == 404


def test_explain_tag_rules_for_intake_preview():
    names, changed, dropped = explain_tag_rules({"bikini": "比基尼", "比基尼": "泳裝"}, {"廣告"}, ["Bikini", "海邊", "廣告", "bikini"])
    assert names == ["泳裝", "海邊"]
    assert changed == [{"from": "Bikini", "to": "泳裝"}, {"from": "bikini", "to": "泳裝"}]
    assert dropped == ["廣告"]


def test_merge_promotes_category_and_undo_restores(client):
    make(client, "1", ["star harbor"])
    make(client, "2", ["星港傳說"])
    client.patch("/api/tags/category", json={"name": "star harbor", "category": "parody"})
    result = client.post("/api/tags/merge", json={"source": "star harbor", "target": "星港傳說"}).json()
    assert client.get("/api/tags/categories").json().get("星港傳說") == "parody"
    client.post(f"/api/undo/{result['undo']}")
    categories = client.get("/api/tags/categories").json()
    assert "星港傳說" not in categories and categories.get("star harbor") == "parody"
