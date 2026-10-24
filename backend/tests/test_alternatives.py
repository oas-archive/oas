import json

from app import alternatives, metadata
from app.database import SessionLocal, Source

NH = json.dumps({"result": [{"id": 900001, "english_title": "(C96) [example circle] Starlight Garden [Chinese]", "thumbnail": "galleries/9000010/thumb.jpg", "num_pages": 18}], "total": 1})
WN = '''<li class="li gallary_item"><div class="pic_box cate-38">
<a href="/photos-index-aid-900002.html" title="星海旅人"><img alt="星海旅人" src="//t3.qy0.ru/data/t/1101/30/1.jpg" /></a></div>
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
    assert rows[0]["thumb"] == "https://t3.qy0.ru/data/t/1101/30/1.jpg"


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
    assert found["query"] == "星海旅人" and [row["code"] for row in found["results"]] == ["900002", "900003"]
    assert found["results"][1]["owned_id"] is not None  # 已在典藏中的會標示出來

    assert client.post(f"/api/resources/{item['id']}/relink", json={"source_name": "紳士漫畫", "code": "900003"}).status_code == 409
    moved = client.post(f"/api/resources/{item['id']}/relink", json={"source_name": "紳士漫畫", "code": "900002"}).json()
    assert moved["source_name"] == "紳士漫畫" and moved["code"] == "900002" and moved["link_dead"] is False
    assert "原連結：禁漫天堂 900004" in moved["note"] and "完結" in moved["tags"] and moved["added_tags"] == 1
