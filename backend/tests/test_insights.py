import urllib.error

from app import linkcheck, scheduler


def add(client, code, **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_progress_and_finished_at(client):
    item = add(client, "p1", status="閱讀中")
    saved = client.patch(f"/api/resources/{item['id']}/quick", json={"progress": "  第 3 話 "}).json()
    assert saved["progress"] == "第 3 話"
    assert saved["finished_at"] is None
    done = client.patch(f"/api/resources/{item['id']}/quick", json={"status": "已完食"}).json()
    assert done["finished_at"]
    again = client.patch(f"/api/resources/{item['id']}/quick", json={"rating": 4}).json()
    assert again["finished_at"] == done["finished_at"]  # 狀態沒變，時間不動
    back = client.post("/api/resources/batch", json={"ids": [item["id"]], "action": "status", "status": "閱讀中"})
    assert back.status_code == 200
    assert client.get(f"/api/resources/{item['id']}").json()["finished_at"] is None
    created = add(client, "p2", status="已完食")
    assert created["finished_at"]


def test_smart_collections(client):
    add(client, "a", status="未讀", tags=["純愛向", "學園"])
    add(client, "b", status="未讀", tags=["純愛向"])
    add(client, "c", status="已完食", tags=["純愛向"])
    smart = client.post("/api/collections", json={"name": "純愛未讀", "query": "status=未讀&tag=純愛向"}).json()
    assert smart["count"] == 2 and smart["query"]
    manual = client.post("/api/collections", json={"name": "手動"}).json()
    lists = {row["name"]: row for row in client.get("/api/collections").json()}
    assert lists["純愛未讀"]["count"] == 2 and lists["手動"]["query"] == ""
    assert client.post(f"/api/collections/{smart['id']}/items", json={"ids": [1]}).status_code == 422
    client.patch(f"/api/collections/{smart['id']}", json={"query": "tag=純愛向&tag=學園"})
    assert {row["name"]: row["count"] for row in client.get("/api/collections").json()}["純愛未讀"] == 1
    client.patch(f"/api/collections/{smart['id']}", json={"name": "純愛學園"})  # 改名不會清掉條件
    assert {row["name"]: row["query"] for row in client.get("/api/collections").json()}["純愛學園"] == "tag=純愛向&tag=學園"
    assert client.patch(f"/api/collections/{manual['id']}", json={"query": "status=未讀"}).status_code == 422


def test_similar_unread_only(client):
    base = add(client, "base", tags=["巨乳", "學園", "純愛向"])
    near = add(client, "near", status="已完食", tags=["巨乳", "學園", "純愛向"])
    unread = add(client, "unread", tags=["學園", "純愛向"])
    for index in range(20):
        add(client, f"filler{index}", tags=[f"其他{index}", "巨乳" if index < 2 else "無關"])
    rows = client.get(f"/api/resources/{base['id']}/related", params={"limit": 5}).json()
    assert rows[0]["id"] == near["id"] and rows[0]["similarity"] > rows[1]["similarity"]
    only = client.get(f"/api/resources/{base['id']}/related", params={"unread": True}).json()
    assert only and all(row["status"] == "未讀" for row in only) and only[0]["id"] == unread["id"]


def test_taste_map_groups_and_scores(client):
    for index in range(6):
        add(client, f"love{index}", tags=["純愛向", "甜蜜"], rating=5, status="已完食" if index < 3 else "未讀")
    for index in range(8):
        add(client, f"meh{index}", tags=["獵奇"], rating=2, status="棄坑" if index < 4 else "未讀")
    for index in range(10):
        add(client, f"x{index}", tags=[f"雜{index}"])
    themes = client.get("/api/insights/taste").json()["themes"]
    assert themes[0]["names"] == ["甜蜜", "純愛向"] or themes[0]["names"] == ["純愛向", "甜蜜"]  # 幾乎總是一起出現，併成一群
    assert themes[0]["unread"] == 3 and themes[0]["avg_rating"] == 5
    assert themes[-1]["names"] == ["獵奇"] and themes[-1]["score"] < themes[0]["score"]
    assert len(themes[0]["samples"]) == 4


def test_tag_health_merge_and_remove(client):
    for index in range(4):
        add(client, f"t{index}", tags=["Big Breasts", "巨乳"] if index < 3 else ["big breast"])
    add(client, "solo", tags=["只出現一次"])
    add(client, "bare")
    health = client.get("/api/insights/tags").json()
    similar = {entry["target"]: [source["name"] for source in entry["sources"]] for entry in health["similar"]}
    assert similar["Big Breasts"] == ["big breast"]
    assert any({entry["target"], entry["source"]} == {"Big Breasts", "巨乳"} for entry in health["paired"])
    assert {entry["name"] for entry in health["rare"]} >= {"只出現一次"}
    assert {item["code"] for item in health["orphans"]} >= {"bare", "solo"}

    merged = client.post("/api/tags/merge", json={"source": "Big Breasts", "target": "巨乳"}).json()
    assert merged["count"] == 3
    client.post("/api/tags/merge", json={"source": "big breast", "target": "巨乳"})
    assert client.get("/api/resources", params={"tags": "巨乳"}).json()["total"] == 4
    later = add(client, "later", tags=["BIG BREASTS"])  # 同義詞生效
    assert later["tags"] == ["巨乳"]

    assert client.post("/api/tags/remove", json={"name": "只出現一次", "block": True}).json()["count"] == 1
    assert add(client, "again", tags=["只出現一次"])["tags"] == []  # 封鎖後不會再加回來
    assert client.post("/api/tags/merge", json={"source": "不存在", "target": "巨乳"}).status_code == 404


def test_duplicates_merge_and_dismiss(client):
    keep = add(client, "111", title="[社團] 同一本書 第一卷 (中文)", tags=["純愛向"], rating=3)
    other = client.post("/api/resources", json={"code": "222", "source_name": "站B", "title": "同一本書 第一卷 [英訳]", "tags": ["學園"], "rating": 5, "status": "已完食", "note": "好看"}).json()
    url_a = add(client, "https://www.example.com/work/9/", title="甲")
    url_b = add(client, "http://example.com/work/9", title="乙")
    add(client, "short", title="短")
    add(client, "short2", title="短")  # 太短的標題不比對
    groups = client.get("/api/duplicates").json()["groups"]
    keys = {group["key"]: group["reasons"] for group in groups}
    assert keys == {f"{keep['id']},{other['id']}": ["title"], f"{url_a['id']},{url_b['id']}": ["url"]}

    collection = client.post("/api/collections", json={"name": "最愛"}).json()
    client.post(f"/api/collections/{collection['id']}/items", json={"ids": [other["id"]]})
    merged = client.post(f"/api/resources/{keep['id']}/merge", json={"ids": [other["id"]]}).json()
    assert merged["tags"] == ["學園", "純愛向"] and merged["rating"] == 5 and merged["status"] == "已完食"
    assert "好看" in merged["note"] and "合併自：站B 222" in merged["note"]
    assert merged["collections"] == [collection["id"]]
    assert client.get(f"/api/resources/{other['id']}").status_code == 404

    client.post("/api/duplicates/dismiss", json={"ids": [url_b["id"], url_a["id"]]})
    assert client.get("/api/duplicates").json()["groups"] == []


def test_stats_shape(client):
    add(client, "a", rating=4.5, status="已完食", tags=["純愛向"], note="心得")
    add(client, "b", rating=2, tags=["純愛向"])
    add(client, "c", note="原連結：站A 1（2026-01-01 改用 站B）")
    data = client.get("/api/stats").json()
    assert data["totals"]["total"] == 3 and data["totals"]["rated"] == 2 and data["totals"]["notes"] == 1
    assert len(data["months"]) == 12 and data["added"][-1] == 3 and data["finished"][-1] == 1
    assert data["ratings"]["5"] == 1 and data["ratings"]["2"] == 1 and data["ratings"]["unrated"] == 1
    assert data["tags"][0] == {"name": "純愛向", "count": 2, "avg_rating": 3.25, "finished": 1}


def test_backup_keeps_progress_and_smart_lists(client):
    add(client, "a", status="閱讀中", progress="p.45")
    client.post("/api/collections", json={"name": "智慧", "query": "status=閱讀中"})
    backup = client.get("/api/backup").json()
    assert backup["version"] == 4
    assert client.post("/api/restore?mode=replace", json=backup).status_code == 200
    assert client.get("/api/resources").json()["items"][0]["progress"] == "p.45"
    assert client.get("/api/collections").json()[0]["query"] == "status=閱讀中"


def test_linkcheck_marks_only_404(client, monkeypatch):
    client.put("/api/sources", json={"name": "站A", "template": "https://example.com/g/{}/"})
    alive, dead, blocked = add(client, "1"), add(client, "2"), add(client, "3")
    add(client, "https://18comic.vip/album/1", source_name="禁漫")  # 有防護的網站略過
    from app.database import SessionLocal
    with SessionLocal() as db:
        urls = dict(linkcheck.targets(db))
    assert set(urls) == {alive["id"], dead["id"], blocked["id"]}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return b""

    def fake_urlopen(request, timeout):
        code = {"/g/2/": 404, "/g/3/": 429}.get(request.full_url.replace("https://example.com", ""))
        if code:
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)
        return Response()

    monkeypatch.setattr(linkcheck, "safe_urlopen", fake_urlopen)
    monkeypatch.setattr(linkcheck, "check_url", lambda url: None)
    assert linkcheck.start() and not linkcheck.start()  # 已在清單中時不重複排入
    scheduler.run_until_idle()
    # 被限流（429）的重試 MAX_ATTEMPTS 次後放棄，不標記失效、也不記為已檢查
    assert linkcheck.state["dead"] == 1 and linkcheck.state["done"] == 3 and linkcheck.state["failed"] == 1 and not linkcheck.state["running"]
    rows = {item["id"]: item for item in client.get("/api/resources").json()["items"]}
    assert rows[dead["id"]]["link_dead"] is True
    assert rows[alive["id"]]["link_dead"] is False and rows[blocked["id"]]["link_dead"] is False
    with SessionLocal() as db:
        assert dict(linkcheck.targets(db)) == {blocked["id"]: urls[blocked["id"]]}  # 被擋的下次再檢查
