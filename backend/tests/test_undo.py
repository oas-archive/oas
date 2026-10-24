def add(client, code, **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_undo_single_delete_restores_everything(client):
    item = add(client, "a", title="甲", tags=["純愛向", "作者甲"], rating=4, status="閱讀中", progress="第 3 話", note="筆記")
    client.patch("/api/tags/category", json={"name": "作者甲", "category": "artist"})
    shelf = client.post("/api/collections", json={"name": "最愛"}).json()
    client.post(f"/api/collections/{shelf['id']}/items", json={"ids": [item["id"]]})
    client.post(f"/api/resources/{item['id']}/open")
    before = client.get(f"/api/resources/{item['id']}").json()

    token = client.delete(f"/api/resources/{item['id']}").json()["undo"]
    assert client.get(f"/api/resources/{item['id']}").status_code == 404
    result = client.post(f"/api/undo/{token}").json()
    assert result["restored"] == 1 and "甲" in result["label"]
    after = client.get(f"/api/resources/{item['id']}").json()
    assert after == before  # 同一個 id、標籤、書單、進度、開啟時間都回來
    assert client.get("/api/tags/categories").json()["作者甲"] == "artist"
    assert client.post(f"/api/undo/{token}").status_code == 404  # 不能復原兩次


def test_undo_batch_status_tags_and_delete(client):
    a, b = add(client, "a", status="未讀", tags=["x"]), add(client, "b", status="已完食", tags=["y"])
    finished_b = client.get(f"/api/resources/{b['id']}").json()["finished_at"]
    token = client.post("/api/resources/batch", json={"ids": [a["id"], b["id"]], "action": "status", "status": "棄坑"}).json()["undo"]
    client.post(f"/api/undo/{token}")
    rows = {row["id"]: row for row in client.get("/api/resources").json()["items"]}
    assert rows[a["id"]]["status"] == "未讀" and rows[b["id"]]["status"] == "已完食" and rows[b["id"]]["finished_at"] == finished_b

    token = client.post("/api/resources/batch", json={"ids": [a["id"], b["id"]], "action": "add_tags", "tags": ["新標籤"]}).json()["undo"]
    client.post(f"/api/undo/{token}")
    rows = {row["id"]: row for row in client.get("/api/resources").json()["items"]}
    assert rows[a["id"]]["tags"] == ["x"] and rows[b["id"]]["tags"] == ["y"]

    token = client.post("/api/resources/batch", json={"ids": [a["id"], b["id"]], "action": "delete"}).json()["undo"]
    assert client.get("/api/resources").json()["total"] == 0
    assert client.post(f"/api/undo/{token}").json()["restored"] == 2
    assert client.get("/api/resources").json()["total"] == 2


def test_undo_merge_duplicates(client):
    keep = add(client, "1", title="同一本書第一卷", tags=["a"], rating=2)
    other = add(client, "2", title="同一本書第一卷", tags=["b"], rating=5, note="心得")
    token = client.post(f"/api/resources/{keep['id']}/merge", json={"ids": [other["id"]]}).json()["undo"]
    client.post(f"/api/undo/{token}")
    restored_keep = client.get(f"/api/resources/{keep['id']}").json()
    assert restored_keep["tags"] == ["a"] and restored_keep["rating"] == 2 and restored_keep["note"] == ""
    assert client.get(f"/api/resources/{other['id']}").json()["note"] == "心得"


def test_undo_tag_merge_and_remove(client):
    a, b = add(client, "a", tags=["Big Breasts"]), add(client, "b", tags=["巨乳", "Big Breasts"])
    c = add(client, "c", tags=["巨乳"])
    token = client.post("/api/tags/merge", json={"source": "Big Breasts", "target": "巨乳"}).json()["undo"]
    client.post(f"/api/undo/{token}")
    tags = {row["code"]: row["tags"] for row in client.get("/api/resources").json()["items"]}
    assert tags == {"a": ["Big Breasts"], "b": ["Big Breasts", "巨乳"], "c": ["巨乳"]}
    assert add(client, "d", tags=["big breasts"])["tags"] == ["big breasts"]  # 同義詞也撤回了

    token = client.post("/api/tags/remove", json={"name": "巨乳", "block": True}).json()["undo"]
    client.post(f"/api/undo/{token}")
    assert client.get(f"/api/resources/{c['id']}").json()["tags"] == ["巨乳"]
    assert add(client, "e", tags=["巨乳"])["tags"] == ["巨乳"]  # 封鎖也撤回了
    assert a["id"] and b["id"]


def test_category_edit_suspicious_and_profile(client):
    for index in range(12):
        add(client, f"x{index}", tags=["巨乳", "作者甲" if index < 3 else "其他"], rating=4 if index == 0 else 0, status="已完食" if index == 1 else "未讀")
    client.patch("/api/tags/category", json={"name": "巨乳", "category": "parody"})
    client.patch("/api/tags/category", json={"name": "作者甲", "category": "artist"})
    suspicious = client.get("/api/insights/tags").json()["suspicious"]
    assert [entry["name"] for entry in suspicious] == ["巨乳"]
    token = client.patch("/api/tags/category", json={"name": "巨乳", "category": "tag"}).json()["undo"]
    assert client.get("/api/insights/tags").json()["suspicious"] == []
    client.post(f"/api/undo/{token}")
    assert client.get("/api/tags/categories").json()["巨乳"] == "parody"
    assert client.patch("/api/tags/category", json={"name": "巨乳", "category": "nonsense"}).status_code == 422

    profile = client.get("/api/tags/profile", params={"name": "作者甲"}).json()
    assert profile["count"] == 3 and profile["avg_rating"] == 4 and profile["finished"] == 1 and profile["category"] == "artist"
    assert profile["people"][0]["name"] == "巨乳" and profile["people"][0]["count"] == 3
    assert client.get("/api/tags/profile", params={"name": "沒有"}).status_code == 404


def test_unrated_filter_and_latest_undo(client):
    a = add(client, "a", rating=0)
    add(client, "b", rating=3)
    client.post(f"/api/resources/{a['id']}/open")
    items = client.get("/api/resources", params={"opened": True, "rated": False}).json()["items"]
    assert [item["code"] for item in items] == ["a"]
    smart = client.post("/api/collections", json={"name": "待評分", "query": "opened=1&rated=0"}).json()
    assert smart["count"] == 1
    assert client.get("/api/undo/latest").json() is None
    client.delete(f"/api/resources/{a['id']}")
    assert "移除" in client.get("/api/undo/latest").json()["label"]
