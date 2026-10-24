import json

from app import settings


def add(client, code, **extra):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", **extra}).json()


def test_collections_crud_membership_and_filter(client):
    a, b, c = add(client, "a"), add(client, "b"), add(client, "c")
    weekend = client.post("/api/collections", json={"name": "週末要看"}).json()
    assert client.post("/api/collections", json={"name": "週末要看"}).status_code == 409
    favourite = client.post("/api/collections", json={"name": "最愛"}).json()

    result = client.post(f"/api/collections/{weekend['id']}/items", json={"ids": [a["id"], b["id"], a["id"], 99999]}).json()
    assert result == {"changed": 2, "count": 2}
    assert client.post(f"/api/collections/{weekend['id']}/items", json={"ids": [a["id"]]}).json()["changed"] == 0
    client.post(f"/api/collections/{favourite['id']}/items", json={"ids": [b["id"]]})

    lists = client.get("/api/collections").json()
    assert [(row["name"], row["count"]) for row in lists] == [("週末要看", 2), ("最愛", 1)]
    ids = sorted(item["id"] for item in client.get("/api/resources", params={"collection": weekend["id"]}).json()["items"])
    assert ids == sorted([a["id"], b["id"]])
    assert client.get(f"/api/resources/{b['id']}").json()["collections"] == sorted([weekend["id"], favourite["id"]])

    client.post(f"/api/collections/{weekend['id']}/items", json={"ids": [a["id"]], "action": "remove"})
    assert client.patch(f"/api/collections/{weekend['id']}", json={"name": "最愛"}).status_code == 409
    client.patch(f"/api/collections/{weekend['id']}", json={"name": "下班後"})
    assert [row["name"] for row in client.get("/api/collections").json()] == ["下班後", "最愛"]

    assert client.delete(f"/api/collections/{weekend['id']}").status_code == 204
    assert client.get(f"/api/resources/{b['id']}").json()["collections"] == [favourite["id"]]
    assert client.get("/api/resources").json()["total"] == 3  # 刪書單不會刪作品
    assert c["collections"] == []


def test_opened_filter_and_system_status(client, tmp_path, monkeypatch):
    a, b = add(client, "a", status="閱讀中"), add(client, "b")
    client.post(f"/api/resources/{a['id']}/open")
    opened = client.get("/api/resources", params={"opened": True, "sort": "opened"}).json()["items"]
    assert [item["id"] for item in opened] == [a["id"]]

    (tmp_path / "oas-auto-20261006-101010.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("x")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path))
    status = client.get("/api/system/status").json()
    assert status["backup"]["name"] == "oas-auto-20261006-101010.json" and status["backup_count"] == 1
    assert set(status["covers"]) == {"cached", "total", "running"} and status["refresh"]["running"] is False
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "missing"))
    assert client.get("/api/system/status").json()["backup"] is None


def test_backup_round_trip_keeps_collections(client):
    a, b = add(client, "a"), add(client, "b")
    shelf = client.post("/api/collections", json={"name": "最愛"}).json()
    client.post(f"/api/collections/{shelf['id']}/items", json={"ids": [b["id"]]})
    backup = client.get("/api/backup").json()
    assert backup["version"] == 4 and backup["collections"] == [{"name": "最愛", "position": 1, "query": "", "items": [{"source_name": "站A", "code": "b"}]}]

    assert client.post("/api/restore", params={"mode": "replace"}, content=json.dumps(backup)).status_code == 200
    lists = client.get("/api/collections").json()
    assert [(row["name"], row["count"]) for row in lists] == [("最愛", 1)]
    members = client.get("/api/resources", params={"collection": lists[0]["id"]}).json()["items"]
    assert [item["code"] for item in members] == ["b"]
