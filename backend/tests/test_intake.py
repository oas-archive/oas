from app import fetcher, intake, linkcheck, metadata, refresh, scheduler
from app.database import SessionLocal

PAGE = '<html><head><meta property="og:title" content="{title}"><meta property="og:image" content="https://example.com/{image}.jpg"><meta name="keywords" content="純愛向, 學園"></head></html>'


def add(client, code, **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_import_urls_report_queue_and_undo(client):
    existing = client.post("/api/resources", json={"code": "https://example.com/old", "source_name": "其他網站", "title": "舊的"}).json()
    report = client.post("/api/import/urls", json={"urls": [
        "https://example.com/a", "https://example.com/a", "  ", "not a url", "https://example.com/old", "https://example.com/b",
    ]}).json()
    assert [row["url"] for row in report["created"]] == ["https://example.com/a", "https://example.com/b"]
    assert report["duplicates"] == [{"url": "https://example.com/old", "id": existing["id"], "title": "舊的"}]
    assert report["invalid"] == ["not a url"]
    ids = [row["id"] for row in report["created"]]
    assert scheduler.pending_ids("fill") == ids and intake.state["total"] == 2
    assert client.get("/api/resources").json()["total"] == 3

    client.post(f"/api/undo/{report['undo']}")
    assert client.get("/api/resources").json()["total"] == 1


def test_single_add_only_queues_when_just_a_url(client):
    bare = add(client, "https://example.com/bare", source_name="其他網站")
    add(client, "https://example.com/named", source_name="其他網站", title="有標題")
    assert scheduler.pending_ids("fill") == [bare["id"]]


def test_process_fills_blank_fields_only(client, monkeypatch):
    bare = add(client, "https://example.com/x", source_name="其他網站")
    named = add(client, "https://example.com/y", source_name="其他網站", title="我取的名字")
    monkeypatch.setattr(metadata, "_get", lambda url: PAGE.format(title="網站上的標題", image=url[-1]))
    monkeypatch.setattr(intake.covers, "schedule", lambda url: None)
    assert intake.process(bare["id"])[0] == "filled"
    assert intake.process(named["id"])[0] == "filled"
    filled = client.get(f"/api/resources/{bare['id']}").json()
    assert filled["title"] == "網站上的標題" and filled["image_url"] == "https://example.com/x.jpg" and filled["tags"] == ["學園", "純愛向"]
    assert client.get(f"/api/resources/{named['id']}").json()["title"] == "我取的名字"  # 已有的不覆蓋
    assert intake.process(999999)[0] == "skipped"


def test_rate_limited_items_are_retried_not_dropped(client, monkeypatch):
    item = add(client, "https://example.com/limited", source_name="其他網站")
    calls = []

    def flaky(url):
        calls.append(url)
        if len(calls) <= 2:
            raise metadata.MetadataError("來源回應 HTTP 429", status=429)
        return PAGE.format(title="第三次才成功", image="q")

    monkeypatch.setattr(metadata, "_get", flaky)
    monkeypatch.setattr(intake.covers, "schedule", lambda url: None)
    assert scheduler.pending_ids("fill") == [item["id"]]  # 只填網址的收錄已自動排入清單
    scheduler.run_until_idle()
    assert len(calls) == 3 and intake.state["filled"] == 1 and intake.state["failed"] == 0 and not intake.state["running"]
    assert client.get(f"/api/resources/{item['id']}").json()["title"] == "第三次才成功"

    other = add(client, "https://example.com/always", source_name="其他網站")
    monkeypatch.setattr(metadata, "_get", lambda url: (_ for _ in ()).throw(metadata.MetadataError("x", status=503)))
    scheduler.run_until_idle()
    assert intake.state["failed"] == 1 and intake.state["done"] == 1 and scheduler.pending_ids("fill") == []  # 重試到上限才算失敗


def test_not_status_filter_for_unrated_list(client):
    a = add(client, "a", status="閱讀中")
    b = add(client, "b")
    for item in (a, b):
        client.post(f"/api/resources/{item['id']}/open")
    rows = client.get("/api/resources", params={"opened": True, "rated": False, "not_status": "閱讀中"}).json()
    assert rows["total"] == 1 and rows["items"][0]["code"] == "b"
    smart = client.post("/api/collections", json={"name": "待評分", "query": "opened=1&rated=0&not_status=%E9%96%B1%E8%AE%80%E4%B8%AD"}).json()
    assert smart["count"] == 1


def test_metadata_reports_similar_titles(client, monkeypatch):
    original = add(client, "1", title="[社團] 同一本書 第一卷 (中文)")
    monkeypatch.setattr(metadata, "_get", lambda url: PAGE.format(title="同一本書 第一卷 [英訳]", image="z"))
    info = client.post("/api/metadata", json={"url": "https://example.com/other"}).json()
    assert info["similar"] == [{"id": original["id"], "title": original["title"], "source_name": "站A"}]


def test_search_suggest(client):
    add(client, "a1", title="Komomo 的日常", tags=["奇幻", "komomo series"])
    add(client, "b2", title="別的作品", tags=["奇幻"])
    data = client.get("/api/search/suggest", params={"q": "komo"}).json()
    assert [item["code"] for item in data["items"]] == ["a1"]
    assert [tag["name"] for tag in data["tags"]] == ["komomo series"]
    tags = client.get("/api/search/suggest", params={"q": "奇"}).json()["tags"]
    assert tags == [{"name": "奇幻", "category": "tag", "count": 2}]


def test_persistent_rate_limit_pauses_only_that_site(client, monkeypatch):
    first = add(client, "https://example.com/p1", source_name="其他網站")
    second = add(client, "https://example.com/p2", source_name="其他網站")
    elsewhere = add(client, "https://example.org/p3", source_name="其他網站")

    def blocked_site(url):
        if "example.com" in url:
            raise metadata.MetadataError("x", status=429)
        return PAGE.format(title="別的網站照常補", image="o")

    monkeypatch.setattr(metadata, "_get", blocked_site)
    monkeypatch.setattr(intake.covers, "schedule", lambda url: None)
    scheduler.run_until_idle()
    # 第一件重試到上限後算失敗、那個網站暫停；同網站的第二件留在清單，其他網站不受影響
    assert intake.state["failed"] == 1 and intake.state["filled"] == 1 and "暫停" in intake.state["message"]
    assert scheduler.pending_ids("fill") == [second["id"]] and intake.state["running"]
    assert client.get(f"/api/resources/{elsewhere['id']}").json()["title"] == "別的網站照常補"
    assert first["id"] not in scheduler.pending_ids("fill")
    third = add(client, "https://example.com/p4", source_name="其他網站")
    assert scheduler.pending_ids("fill") == [second["id"], third["id"]] and intake.state["total"] == 4  # 接在同一批後面


def test_new_imports_go_before_refresh_and_linkcheck(client, monkeypatch):
    client.put("/api/sources", json={"name": "站A", "template": "https://nhentai.net/g/{}/"})  # 補齊作品資訊只處理有解析規則的網站
    old = add(client, "1", title="舊作品", image_url="https://example.com/c.jpg", tags=["x"])
    order = []
    for kind in ("fill", "refresh", "linkcheck"):
        monkeypatch.setattr(scheduler.KINDS[kind], "handler", lambda job, kind=kind: order.append(kind) or "ok")
    linkcheck.start()
    refresh.start("all")
    fresh = add(client, "https://example.com/new", source_name="其他網站")
    assert scheduler.pending_ids("fill") == [fresh["id"]] and scheduler.pending_ids("refresh") == [old["id"]]
    scheduler.run_until_idle()
    assert order[0] == "fill" and order.index("refresh") < order.index("linkcheck")


def test_unfinished_jobs_survive_a_restart(client):
    item = add(client, "https://example.com/later", source_name="其他網站")
    intake.state.update(running=False, total=0, done=0)  # 模擬重新開啟：記憶體裡的進度歸零，清單還在資料庫
    scheduler.restore_states()
    assert intake.state["running"] and intake.state["total"] == 1 and scheduler.pending_ids("fill") == [item["id"]]


def test_stop_cancels_pending_jobs(client):
    client.put("/api/sources", json={"name": "站A", "template": "https://nhentai.net/g/{}/"})
    for code in ("1", "2"):
        add(client, code, title="t", image_url="https://example.com/c.jpg", tags=["x"])
    assert client.post("/api/refresh", params={"mode": "all"}).json()["queued"] == 2
    assert client.post("/api/refresh").status_code == 409  # 已在進行中
    client.post("/api/refresh/stop")
    assert scheduler.pending("refresh") == 0 and not refresh.state["running"] and refresh.state["message"] == "已手動停止"


def test_hosts_are_paced_independently(monkeypatch):
    monkeypatch.setattr(fetcher, "SLOW_INTERVAL", 6.0)
    monkeypatch.setattr(fetcher, "OTHER_INTERVAL", 1.5)
    fetcher.reserve("nhentai.net")
    assert fetcher.ready_at("nhentai.net") > fetcher.ready_at("example.org")  # 慢的網站在間隔中，不影響其他網站
    assert fetcher.interval("nhentai.net") == 6.0 and fetcher.interval("i.nhentai.net") == 6.0 and fetcher.interval("example.org") == 1.5


def test_similar_titles_sees_titles_filled_in_background(client, monkeypatch):
    original = add(client, "https://example.com/bg", source_name="其他網站")
    with SessionLocal() as db:
        assert metadata.similar_titles(db, "背景補上的長標題") == []  # 先建立索引（此時還沒有標題）
    monkeypatch.setattr(metadata, "_get", lambda url: PAGE.format(title="背景補上的長標題", image="b"))
    monkeypatch.setattr(intake.covers, "schedule", lambda url: None)
    assert intake.process(original["id"])[0] == "filled"
    with SessionLocal() as db:
        assert [row["id"] for row in metadata.similar_titles(db, "背景補上的長標題")] == [original["id"]]


def test_title_only_fill_counts_as_filled(client, monkeypatch):
    item = add(client, "https://example.com/t", source_name="其他網站", image_url="https://example.com/has.jpg", tags=["學園", "純愛向"])
    monkeypatch.setattr(metadata, "_get", lambda url: PAGE.format(title="只補標題", image="t"))
    monkeypatch.setattr(intake.covers, "schedule", lambda url: None)
    assert intake.process(item["id"])[0] == "filled"
    assert intake.process(item["id"])[0] == "skipped"  # 都已有資料：沒有變更就不算補上


def test_undo_import_keeps_items_edited_afterwards(client):
    report = client.post("/api/import/urls", json={"urls": ["https://example.com/u1", "https://example.com/u2", "https://example.com/u3"]}).json()
    rated, opened, untouched = [row["id"] for row in report["created"]]
    client.patch(f"/api/resources/{rated}/quick", json={"rating": 4})
    client.post(f"/api/resources/{opened}/open")
    result = client.post(f"/api/undo/{report['undo']}").json()
    assert result["restored"] == 1 and result["kept"] == 2
    assert client.get(f"/api/resources/{untouched}").status_code == 404
    assert client.get(f"/api/resources/{rated}").json()["rating"] == 4


def test_search_suggest_blank_and_wildcards(client):
    add(client, "w1", title="百分之百 100% 純愛")
    add(client, "w2", title="別的作品")
    assert client.get("/api/search/suggest", params={"q": "  "}).json() == {"items": [], "tags": []}
    assert client.get("/api/search/suggest", params={"q": "_"}).json()["items"] == []
    assert [item["code"] for item in client.get("/api/search/suggest", params={"q": "100%"}).json()["items"]] == ["w1"]


def test_bare_code_means_nhentai(client):
    client.put("/api/sources", json={"name": "nhentai", "template": "https://nhentai.net/g/{}/"})
    for text in ("123456", " #123456 "):
        info = client.post("/api/metadata", json={"url": text, "fetch": False}).json()
        assert (info["url"], info["source_name"], info["code"]) == ("https://nhentai.net/g/123456/", "nhentai", "123456")
    assert client.post("/api/metadata", json={"url": "12345678", "fetch": False}).status_code == 422  # 太長，不當成作品編號
    report = client.post("/api/import/urls", json={"urls": ["654321", "https://nhentai.net/g/654321/"]}).json()
    assert [row["url"] for row in report["created"]] == ["https://nhentai.net/g/654321/"]  # 同一本只收一次


def test_short_codes_work_too(client):
    client.put("/api/sources", json={"name": "nhentai", "template": "https://nhentai.net/g/{}/"})
    for text, code in (("12345", "12345"), ("#987", "987")):
        info = client.post("/api/metadata", json={"url": text, "fetch": False}).json()
        assert (info["source_name"], info["code"]) == ("nhentai", code)
