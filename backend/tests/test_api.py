
def make(client, **overrides):
    payload = {"code": "A-1", "source_name": "測試來源", "title": "第一件", "rating": 4.5, "tags": ["奇幻", "冒險"]}
    payload.update(overrides)
    return client.post("/api/resources", json=payload)


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_create_and_duplicate_rule(client):
    created = make(client)
    assert created.status_code == 201
    body = created.json()
    assert body["tags"] == ["冒險", "奇幻"] and body["rating"] == 4.5 and body["status"] == "未讀"
    assert make(client).status_code == 409
    assert make(client, source_name="另一來源").status_code == 201


def test_update_conflict_and_tags(client):
    first = make(client).json()
    second = make(client, code="B-2", tags=["日常"]).json()
    assert client.patch(f"/api/resources/{second['id']}", json={**second, "code": "A-1"}).status_code == 409
    updated = client.patch(f"/api/resources/{first['id']}", json={**first, "status": "已完食", "tags": ["奇幻"]})
    assert updated.status_code == 200 and updated.json()["status"] == "已完食" and updated.json()["tags"] == ["奇幻"]
    assert client.patch(f"/api/resources/{first['id']}", json={**first, "status": "不存在"}).status_code == 422


def test_search_filters_and_total(client):
    make(client)
    make(client, code="B-2", title="另一件", tags=["日常"])
    assert client.get("/api/resources", params={"q": "日常"}).json()["total"] == 1
    assert client.get("/api/resources", params={"tag": "奇幻"}).json()["items"][0]["code"] == "A-1"
    assert client.get("/api/resources", params={"q": "B-2"}).json()["total"] == 1
    page = client.get("/api/resources", params={"limit": 1, "sort": "title"}).json()
    assert page["total"] == 2 and len(page["items"]) == 1


def test_delete_and_open_event(client):
    item = make(client).json()
    opened = client.post(f"/api/resources/{item['id']}/open").json()
    assert opened["last_opened_at"] and opened["updated_at"] == item["updated_at"]
    assert client.delete(f"/api/resources/{item['id']}").status_code == 200
    assert client.get(f"/api/resources/{item['id']}").status_code == 404
    assert client.get("/api/dashboard").json()["total"] == 0


def test_backup_restore_roundtrip(client):
    client.put("/api/sources", json={"name": "測試來源", "template": "https://example.com/{}"})
    make(client)
    make(client, code="B-2", note="私人筆記")
    backup = client.get("/api/backup")
    assert backup.status_code == 200 and "attachment" in backup.headers["content-disposition"]
    data = backup.json()
    assert data["counts"] == {"resources": 2, "sources": 1}

    merged = client.post("/api/restore", json=data).json()
    assert merged["created"] == 0 and merged["skipped"] == 2

    make(client, code="C-3")
    replaced = client.post("/api/restore", params={"mode": "replace"}, json=data).json()
    assert replaced["created"] == 2
    items = client.get("/api/resources").json()["items"]
    assert sorted(item["code"] for item in items) == ["A-1", "B-2"]
    restored = next(item for item in items if item["code"] == "B-2")
    original = next(item for item in data["resources"] if item["code"] == "B-2")
    assert restored["note"] == "私人筆記" and restored["created_at"] == original["created_at"]
    assert client.get("/api/sources").json()[0]["template"] == "https://example.com/{}"


def test_restore_rejects_foreign_file(client):
    assert client.post("/api/restore", json={"format": "other", "version": 1}).status_code == 422


def test_multi_tag_filter_and_stats(client):
    def add(code, tags):
        client.post("/api/resources", json={"code": code, "source_name": "站A", "tags": tags})
    add("1", ["貓", "日常"])
    add("2", ["貓", "冒險"])
    add("3", ["狗", "日常"])
    codes = lambda **params: sorted(item["code"] for item in client.get("/api/resources", params=params).json()["items"])
    assert codes(tags=["貓", "日常"]) == ["1"]
    assert codes(tags=["貓", "日常"], tag_mode="any") == ["1", "2", "3"]
    assert codes(tag="貓") == ["1", "2"]  # 舊的單一標籤參數仍可用
    stats = {row["name"]: row["count"] for row in client.get("/api/tags/stats").json()}
    assert stats == {"貓": 2, "日常": 2, "冒險": 1, "狗": 1}
    within = {row["name"]: row["count"] for row in client.get("/api/tags/stats", params={"tags": ["貓"]}).json()}
    assert within == {"貓": 2, "日常": 1, "冒險": 1}


def test_batch_edit_sort_and_kind(client):
    ids = [client.post("/api/resources", json={"code": str(i), "source_name": "站A", "tags": ["舊"]}).json()["id"] for i in range(3)]
    assert client.post("/api/resources/batch", json={"ids": ids[:2], "action": "status", "status": "已完食"}).json()["count"] == 2
    assert client.post("/api/resources/batch", json={"ids": ids, "action": "add_tags", "tags": ["新"]}).status_code == 200
    assert client.post("/api/resources/batch", json={"ids": [ids[0]], "action": "remove_tags", "tags": ["舊"]}).status_code == 200
    assert client.post("/api/resources/batch", json={"ids": [ids[1]], "action": "kind", "kind": "漫畫"}).status_code == 200
    items = {item["id"]: item for item in client.get("/api/resources").json()["items"]}
    assert items[ids[0]]["status"] == "已完食" and items[ids[0]]["tags"] == ["新"]
    assert items[ids[2]]["status"] == "未讀" and items[ids[2]]["tags"] == ["新", "舊"]
    assert [item["id"] for item in client.get("/api/resources", params={"kind": "漫畫"}).json()["items"]] == [ids[1]]
    client.post(f"/api/resources/{ids[2]}/open")
    assert client.get("/api/resources", params={"sort": "opened"}).json()["items"][0]["id"] == ids[2]
    assert client.post("/api/resources/batch", json={"ids": ids[:1], "action": "status"}).status_code == 422
    client.post("/api/resources/batch", json={"ids": ids, "action": "delete"})
    assert client.get("/api/resources").json()["total"] == 0


def test_backup_fingerprint_follows_backup_content(client):
    fingerprint = lambda: client.get("/api/backup/fingerprint").json()["fingerprint"]
    empty = fingerprint()
    assert fingerprint() == empty  # 資料沒變，指紋不變（不受匯出時間影響）
    item = client.post("/api/resources", json={"code": "1", "source_name": "站A", "title": "作品"}).json()
    added = fingerprint()
    assert added != empty
    client.post(f"/api/resources/{item['id']}/open")
    opened = fingerprint()
    assert opened != added
    client.patch(f"/api/resources/{item['id']}/quick", json={"rating": 4})
    assert fingerprint() != opened
