"""v0.19.5 程式檢視後的修正：網站圖示、非作品頁面、「原創」、重複作品的「不是重複」。"""
from app import intake, metadata
from app.database import CoverCache, Resource, SessionLocal, Tag


def add(client, code, **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": "其他網站", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_icon_urls():
    for url in ("https://ei.phncdn.com/www-static/favicon.ico?cache=1", "https://ei.phncdn.com/www-static/images/vmobile/icon_2x.png?cache=1",
                "https://forum.example/favicon.ico?v=2", "https://cdn-msp2.18comic.vip/media/logo/new_logo.png?t=1", "https://a.example/apple-touch-icon.png"):
        assert metadata.icon_url(url), url
    for url in ("https://t3.nhentai.net/galleries/1/cover.jpg", "https://fourhoi.com/abc-745/cover-n.jpg", "https://img.example/icons-of-love/cover.jpg"):
        assert not metadata.icon_url(url), url


def test_icon_cover_is_not_a_cover(client):
    """只有網站圖示當封面：新收錄標成 icon、算缺封面；舊資料啟動時補標。"""
    video = add(client, "https://www.example.com/watch/1", title="影片", image_url="https://www.example.com/favicon.ico", kind="影片")
    add(client, "https://www.example.com/thread/1", title="文章", image_url="https://www.example.com/favicon.ico")  # 其他類型：網站圖示就夠了
    assert video["image_kind"] == "icon"
    old = add(client, "https://www.example.com/watch/2", title="舊影片", image_url="https://www.example.com/x.jpg", kind="影片")
    with SessionLocal() as db:
        db.execute(Resource.__table__.update().where(Resource.id == old["id"]).values(image_url="https://www.example.com/images/icon_2x.png", image_kind=""))
        db.commit()
    assert intake.normalise_saved()["icons"] == 1
    missing = {entry["id"]: entry["missing"] for entry in client.get("/api/attention").json()["groups"]["retry"]}
    assert missing == {video["id"]: ["封面（只有網站圖示）"], old["id"]: ["封面（只有網站圖示）"]}


def test_non_works_not_recommended(client):
    site = add(client, "https://example.com/", title="某網站", status="閱讀中", kind="網站")
    work = add(client, "https://example.com/works/1", title="作品", status="閱讀中")
    home = client.get("/api/home").json()
    assert [entry["id"] for entry in home["hero"]] == [work["id"]]
    assert all(entry["id"] != site["id"] for row in home["rows"] for entry in row["items"])
    assert client.get("/api/resources", params={"works": True}).json()["total"] == 1


def test_original_is_not_a_parody_ranking(client):
    for index in range(12):
        add(client, f"https://example.com/o/{index}", title=f"原創作品{index}", tags=["原創"])
    with SessionLocal() as db:
        db.query(Tag).filter(Tag.name == "原創").update({"category": "parody"})
        db.commit()
    assert client.get("/api/insights/tags").json()["suspicious"] == []
    assert all(entry["name"] != "原創" for entry in client.get("/api/stats").json()["parodies"])


def test_dismissed_pairs_stay_dismissed_when_group_grows(client):
    """標成不是重複的作品彼此不再算重複；之後有新作品和它們相符，只列新的那對。"""
    a = add(client, "https://example.com/a", title="甲", image_url="https://img.example/same.jpg")
    b = add(client, "https://example.com/b", title="乙", image_url="https://img.example/same.jpg")
    assert len(client.get("/api/duplicates").json()["groups"]) == 1
    assert client.post("/api/duplicates/dismiss", json={"ids": [a["id"], b["id"]]}).status_code == 200
    assert client.get("/api/duplicates").json()["groups"] == []
    c = add(client, "https://example.com/c", title="丙", image_url="https://img.example/same.jpg")
    groups = client.get("/api/duplicates").json()["groups"]
    ids = {entry["id"] for entry in groups[0]["items"]}
    assert len(groups) == 1 and c["id"] in ids and len(ids) == 2  # 只列新的那對，標過的兩件不會一起回來


def test_use_site_icon(client, monkeypatch):
    """沒有封面的文章：讀得到頁面就用頁面上的網站圖示，讀不到就用 /favicon.ico；之後不再列在待整理。"""
    article = add(client, "https://forum.example/thread-1.html", title="論壇文章")
    blocked = add(client, "https://blocked.example/p/2", title="要登入的文章")
    assert {entry["id"] for entry in client.get("/api/attention").json()["groups"]["retry"]} >= {article["id"], blocked["id"]}

    def fake_get(url):
        if "blocked" in url:
            raise metadata.MetadataError("來源回應 HTTP 403", status=403)
        return '<link rel="apple-touch-icon" href="/touch.png">'
    monkeypatch.setattr(metadata, "_get", fake_get)
    first = client.post(f"/api/resources/{article['id']}/site-icon").json()
    second = client.post(f"/api/resources/{blocked['id']}/site-icon").json()
    assert (first["image_url"], first["image_kind"]) == ("https://forum.example/touch.png", "icon")
    assert (second["image_url"], second["image_kind"]) == ("https://blocked.example/favicon.ico", "icon")
    assert client.get("/api/attention/summary").json()["total"] == 0


def test_url_resolve_edge_cases(client):
    """Hanime1 的 v 參數不在第一個、MissAV 女優頁保留 actresses/名稱並當成創作者、hentaihaven 其他網域當成動畫。"""
    from app.database import Source
    with SessionLocal() as db:
        db.add_all([Source(name="Hanime1", template="https://hanime1.me/watch?v={}", kind="動畫"),
                    Source(name="MissAV", template="https://missav.ai/{}", kind="影片")])
        db.commit()
        assert metadata.resolve(db, "https://hanime1.me/watch?list=1&sort=oldest&v=90108") == ("Hanime1", "90108")
        assert metadata.resolve(db, "https://hanime1.me/watch?v=90107&fbclid=x") == ("Hanime1", "90107")
        assert metadata.resolve(db, "https://missav.ws/dm28/actresses/%E7%94%B1") == ("MissAV", "actresses/%E7%94%B1")
        assert metadata.resolve(db, "https://missav.ws/dm26/abc-745") == ("MissAV", "abc-745")
    assert metadata.page_kind("https://missav.ws/dm28/actresses/%E7%94%B1") == "創作者"
    assert metadata.page_kind("https://hentaihaven.red/hentai/some-title-episode-1/") == "動畫"
    assert metadata.page_kind("https://missav.ws/xyz-310") == ""
    created = client.post("/api/import/urls", json={"urls": ["https://missav.ws/dm28/actresses/%E7%94%B1", "https://hentaihaven.red/hentai/x-episode-1/"]}).json()["created"]
    kinds = [client.get(f"/api/resources/{row['id']}").json()["kind"] for row in created]
    assert kinds == ["創作者", "動畫"]
