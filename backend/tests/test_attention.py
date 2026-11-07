from app import scheduler


def add(client, code, **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": "其他網站", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_attention_groups_and_retry(client):
    retry = add(client, "https://nhentai.net/g/900001/", title="讀得到但沒封面")
    manual = add(client, "https://18comic.vip/album/900002/", title="有防護的網站")
    site = add(client, "https://example.com/article/1", title="某網站的文章")  # 沒有防護的其他網站：重試時抓網站圖示當封面
    dead = add(client, "https://example.com/gone", title="不見了", image_url="https://example.com/a.jpg")
    client.patch(f"/api/resources/{dead['id']}/quick", json={"link_dead": True})
    add(client, "https://example.com/done", title="完整的", image_url="https://example.com/b.jpg")
    queued = add(client, "https://nhentai.net/g/900003/")  # 只有網址：已在背景排程，不列入
    assert queued["id"] in scheduler.pending_ids("fill")

    data = client.get("/api/attention").json()
    assert {key: [item["id"] for item in items] for key, items in data["groups"].items()} == {
        "retry": [retry["id"], site["id"]], "manual": [manual["id"]], "dead": [dead["id"]]}
    assert data["groups"]["retry"][0]["missing"] == ["封面"] and data["groups"]["manual"][0]["url"] == "https://18comic.vip/album/900002/"
    assert client.get("/api/attention/summary").json() == {"counts": {"retry": 2, "manual": 1, "dead": 1}, "total": 4, "queued": 1}

    assert client.post("/api/attention/retry", json={"ids": [manual["id"]]}).json() == {"queued": 0}  # 要手動補的不能重試
    assert client.post("/api/attention/retry", json={"ids": []}).json() == {"queued": 2}
    assert client.get("/api/attention").json()["groups"]["retry"] == []


def test_site_page_needs_no_cover(client):
    add(client, "https://missav.ws/", title="MissAV 首頁")  # 網站首頁：有標題就好，不列入
    assert client.get("/api/attention/summary").json()["total"] == 0


def test_fetch_manual_fills_protected_pages(client, monkeypatch):
    from app import fetcher, metadata
    item = add(client, "https://18comic.vip/album/900005/")
    other = add(client, "https://18comic.vip/album/900006/")
    pages = {"900005": '<h1 id="book-name">讀得到的那本</h1><span data-type="tags"><a>純愛</a></span>'}
    monkeypatch.setattr(metadata, "_get", lambda url: pages.get(url.rstrip("/").rsplit("/", 1)[-1], "<title>禁漫天堂</title>"))
    monkeypatch.setattr(fetcher, "interval", lambda host: 0)
    result = client.post("/api/attention/fetch-manual").json()
    assert result == {"tried": 2, "filled": 1, "limited": []}
    filled = client.get(f"/api/resources/{item['id']}").json()
    assert filled["title"] == "讀得到的那本" and filled["tags"] == ["純愛"] and filled["image_url"].endswith("/900005.jpg")
    assert client.get(f"/api/resources/{other['id']}").json()["title"] == ""


def test_broken_cover_listed_and_replaced_on_retry(client):
    """封面網址已經壞了（404／410、回傳的不是圖片）也算缺封面；重試時清掉，讓補資料換上作品頁現在的封面。"""
    from app import covers
    from app.database import CoverCache, SessionLocal
    item = add(client, "https://hanime1.me/watch?v=900007", title="縮圖過期的影片", image_url="https://vdownload.example/thumb.jpg?se=1")
    blocked = add(client, "https://example.com/blocked", title="被擋的封面", image_url="https://img.example/403.jpg")
    with SessionLocal() as db:
        db.add_all([CoverCache(url_hash=covers.url_hash(item["image_url"]), url=item["image_url"], status="failed", error="來源拒絕（HTTP 410）"),
                    CoverCache(url_hash=covers.url_hash(blocked["image_url"]), url=blocked["image_url"], status="failed", error="來源拒絕（HTTP 403）")])
        db.commit()
    data = client.get("/api/attention").json()
    assert [(entry["id"], entry["missing"]) for entry in data["groups"]["retry"]] == [(item["id"], ["封面（載不到）"])]
    assert client.post("/api/attention/retry", json={"ids": [item["id"]]}).json() == {"queued": 1}
    assert client.get(f"/api/resources/{item['id']}").json()["image_url"] == ""


def test_site_pages_are_never_dead(client):
    page = add(client, "https://forum.example/forum-1-2.html", title="論壇分區")
    client.patch(f"/api/resources/{page['id']}/quick", json={"link_dead": True})
    assert client.get("/api/attention/summary").json()["counts"]["dead"] == 0
