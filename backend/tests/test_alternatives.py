import json

from app import alternatives, metadata
from app.database import SessionLocal, Source

NH = json.dumps({"result": [{"id": 900001, "english_title": "(C96) [example circle] Starlight Garden [Chinese]", "thumbnail": "galleries/9000010/thumb.jpg", "num_pages": 18}], "total": 1})
WN = '''<li class="li gallary_item"><div class="pic_box cate-38">
<a href="/photos-index-aid-900002.html" title="星海旅人"><img alt="星海旅人" src="//t3.qy0.ru/data/t/9000/30/1.jpg" /></a></div>
<div class="title"><a href="/photos-index-aid-900002.html" title="星海旅人">星海旅人</a></div>
<a href="/photos-index-aid-900003.html" title="[虛構社團 &amp; 作者] 星海旅人 1-110"><img alt="x" src="//t4.qy0.ru/data/t/9000/03/2.jpg" /></a>'''


def test_search_query_strips_noise():
    assert alternatives.search_query("星海旅人 Comics") == "星海旅人"
    assert alternatives.search_query("[某漢化組] 一個旅人的日常生活 Another Story/一个旅人的日常生活 外传") == "一個旅人的日常生活 Another Story"
    assert alternatives.search_query("(C105) [虛構社團 (作者)] 星空の約束。 (オリジナル)") == "星空の約束。"


def test_parsers():
    assert alternatives.parse_nhentai_search(NH) == [{"source_name": "nhentai", "code": "900001", "url": "https://nhentai.net/g/900001/",
                                                     "title": "(C96) [example circle] Starlight Garden [Chinese]", "thumb": "https://t3.nhentai.net/galleries/9000010/thumb.jpg", "pages": 18}]
    rows = alternatives.parse_wnacg_search(WN)
    assert [row["code"] for row in rows] == ["900002", "900003"] and rows[1]["title"] == "[虛構社團 & 作者] 星海旅人 1-110"
    assert rows[0]["thumb"] == "https://t3.qy0.ru/data/t/9000/30/1.jpg"


def test_dead_flag_alternatives_and_relink(client, monkeypatch):
    with SessionLocal() as db:
        db.add_all([Source(name="禁漫天堂", template="https://18comic.vip/album/{}/", kind="漫畫"),
                    Source(name="紳士漫畫", template="https://www.wnacg.com/photos-index-aid-{}.html", kind="漫畫")])
        db.commit()
    item = client.post("/api/resources", json={"code": "900004", "source_name": "禁漫天堂", "title": "星海旅人 Comics", "tags": ["韓漫"]}).json()
    client.post("/api/resources", json={"code": "900003", "source_name": "紳士漫畫", "title": "已有的"})
    marked = client.patch(f"/api/resources/{item['id']}/quick", json={"link_dead": True}).json()
    assert marked["link_dead"] is True and marked["updated_at"] == item["updated_at"]
    assert [row["id"] for row in client.get("/api/resources", params={"dead": True}).json()["items"]] == [item["id"]]
    assert client.get("/api/dashboard").json()["dead_links"] == 1

    def fake_get(url):
        if "nhentai" in url:
            return json.dumps({"result": []})
        if "search" in url:
            return WN
        return '<h2>星海旅人</h2><div class="addtags"><a class="tagshow" href="/a">完結</a></div>'
    monkeypatch.setattr(metadata, "_get", fake_get)
    found = client.get(f"/api/resources/{item['id']}/alternatives").json()
    # 編號是純數字：先用同一個編號讀紳士漫畫（自己的網站不讀），再列標題搜尋的結果
    assert found["query"] == "星海旅人" and [row["code"] for row in found["results"]] == ["900004", "900002", "900003"]
    assert found["results"][0]["same_code"] is True and found["results"][0]["title"] == "星海旅人"
    assert found["results"][2]["owned_id"] is not None  # 已在典藏中的會標示出來

    assert client.post(f"/api/resources/{item['id']}/relink", json={"source_name": "紳士漫畫", "code": "900003"}).status_code == 409
    moved = client.post(f"/api/resources/{item['id']}/relink", json={"source_name": "紳士漫畫", "code": "900002"}).json()
    assert moved["source_name"] == "紳士漫畫" and moved["code"] == "900002" and moved["link_dead"] is False
    assert "原連結：禁漫天堂 900004" in moved["note"] and "完結" in moved["tags"] and moved["added_tags"] == 1


def test_relink_replaces_guessed_cover(client, monkeypatch):
    with SessionLocal() as db:
        db.add_all([Source(name="禁漫天堂", template="https://18comic.vip/album/{}/", kind="漫畫"),
                    Source(name="紳士漫畫", template="https://www.wnacg.com/photos-index-aid-{}.html", kind="漫畫")])
        db.commit()
    guessed = client.post("/api/import/urls", json={"urls": ["https://18comic.vip/album/900010/"]}).json()["created"][0]["id"]
    assert client.get(f"/api/resources/{guessed}").json()["image_url"].endswith("/900010.jpg")
    monkeypatch.setattr(metadata, "_get", lambda url: '<h2>記錯網站的那本</h2><img src="//t1.qy0.ru/data/t/9000/10/cover.jpg">')
    moved = client.post(f"/api/resources/{guessed}/relink", json={"source_name": "紳士漫畫", "code": "900010"}).json()
    assert moved["title"] == "記錯網站的那本" and "qy0.ru" in moved["image_url"]  # 依舊編號推得的封面換成新來源的



def test_same_code_on_jm_by_cover(client, monkeypatch):
    """沒有標題的失效作品：不拿編號當關鍵字搜尋，只比對同編號；禁漫看封面圖片在不在。"""
    with SessionLocal() as db:
        db.add_all([Source(name="nhentai", template="https://nhentai.net/g/{}/", kind="漫畫"),
                    Source(name="禁漫天堂", template="https://18comic.vip/album/{}/", kind="漫畫"),
                    Source(name="紳士漫畫", template="https://www.wnacg.com/photos-index-aid-{}.html", kind="漫畫")])
        db.commit()
    jm = client.post("/api/resources", json={"code": "900106", "source_name": "nhentai"}).json()
    nowhere = client.post("/api/resources", json={"code": "447655", "source_name": "nhentai"}).json()
    for item in (jm, nowhere):
        client.patch(f"/api/resources/{item['id']}/quick", json={"link_dead": True})
    searched = []

    def fake_get(url):
        searched.append(url)
        raise metadata.MetadataError("找不到", status=404)
    monkeypatch.setattr(metadata, "_get", fake_get)
    monkeypatch.setattr(metadata, "cover_exists", lambda image: "900106" in image)
    monkeypatch.setattr(alternatives, "BULK_INTERVAL", 0)
    found = client.get(f"/api/resources/{jm['id']}/alternatives").json()
    assert found["query"] == "" and not any("search" in url for url in searched)
    assert [(row["source_name"], row["code"], row["thumb"]) for row in found["results"]] == [("禁漫天堂", "900106", "https://cdn-msp.18comic.vip/media/albums/900106.jpg")]
    assert client.get("/api/attention/same-code").json() == {"results": {str(jm["id"]): found["results"], str(nowhere["id"]): []}, "errors": {}}

    moved = client.post(f"/api/resources/{jm['id']}/relink", json={"source_name": "禁漫天堂", "code": "900106"}).json()
    assert moved["link_dead"] is False and moved["image_url"].endswith("/900106.jpg")  # 讀不到作品頁，封面依編號推得


def test_same_code_many_reports_failures_and_stops_limited_site(client, monkeypatch):
    """查失敗不能當成「沒有這個編號」；被限流的網站剩下的作品不再查。"""
    with SessionLocal() as db:
        db.add_all([Source(name="nhentai", template="https://nhentai.net/g/{}/", kind="漫畫"),
                    Source(name="紳士漫畫", template="https://www.wnacg.com/photos-index-aid-{}.html", kind="漫畫")])
        db.commit()
    ids = []
    for code in ("700001", "700002"):
        item = client.post("/api/resources", json={"code": code, "source_name": "nhentai"}).json()
        client.patch(f"/api/resources/{item['id']}/quick", json={"link_dead": True})
        ids.append(str(item["id"]))
    calls = []

    def limited(url):
        calls.append(url)
        raise metadata.MetadataError("來源回應 HTTP 429", status=429)
    monkeypatch.setattr(metadata, "_get", limited)
    monkeypatch.setattr(alternatives, "BULK_INTERVAL", 0)
    data = client.get("/api/attention/same-code").json()
    assert data["results"] == {ids[0]: [], ids[1]: []} and len(calls) == 1
    assert "429" in data["errors"][ids[0]][0] and "限流" in data["errors"][ids[1]][0]
