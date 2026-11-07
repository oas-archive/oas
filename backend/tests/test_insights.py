import io
import random
import urllib.error

from PIL import Image

from app import covers, duplicates, linkcheck, scheduler
from app.database import CoverCache, SessionLocal


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
    add(client, "a", status="未讀", tags=["純愛向", "星空"])
    add(client, "b", status="未讀", tags=["純愛向"])
    add(client, "c", status="已完食", tags=["純愛向"])
    smart = client.post("/api/collections", json={"name": "純愛未讀", "query": "status=未讀&tag=純愛向"}).json()
    assert smart["count"] == 2 and smart["query"]
    manual = client.post("/api/collections", json={"name": "手動"}).json()
    lists = {row["name"]: row for row in client.get("/api/collections").json()}
    assert lists["純愛未讀"]["count"] == 2 and lists["手動"]["query"] == ""
    assert client.post(f"/api/collections/{smart['id']}/items", json={"ids": [1]}).status_code == 422
    client.patch(f"/api/collections/{smart['id']}", json={"query": "tag=純愛向&tag=星空"})
    assert {row["name"]: row["count"] for row in client.get("/api/collections").json()}["純愛未讀"] == 1
    client.patch(f"/api/collections/{smart['id']}", json={"name": "純愛星空"})  # 改名不會清掉條件
    assert {row["name"]: row["query"] for row in client.get("/api/collections").json()}["純愛星空"] == "tag=純愛向&tag=星空"
    assert client.patch(f"/api/collections/{manual['id']}", json={"query": "status=未讀"}).status_code == 422


def test_similar_unread_only(client):
    base = add(client, "base", tags=["巨乳", "星空", "純愛向"])
    near = add(client, "near", status="已完食", tags=["巨乳", "星空", "純愛向"])
    unread = add(client, "unread", tags=["星空", "純愛向"])
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
    other = client.post("/api/resources", json={"code": "222", "source_name": "站B", "title": "同一本書 第一卷 [英訳]", "tags": ["星空"], "rating": 5, "status": "已完食", "note": "好看"}).json()
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
    assert merged["tags"] == ["星空", "純愛向"] and merged["rating"] == 5 and merged["status"] == "已完食"
    assert "好看" in merged["note"] and "合併自：站B 222" in merged["note"]
    assert merged["collections"] == [collection["id"]]
    assert client.get(f"/api/resources/{other['id']}").status_code == 404

    client.post("/api/duplicates/dismiss", json={"ids": [url_b["id"], url_a["id"]]})
    assert client.get("/api/duplicates").json()["groups"] == []


def test_title_keys_split_translations_and_strip_markers():
    keys, tail = duplicates.title_keys("(C99) [甲社團 (乙作者)] Hoshi no Kaeru Michi | 星星回家的路 [Chinese] [丙漢化組")
    assert {"hoshinokaerumichi", "星星回家的路"} <= keys and tail == "星星回家的路"
    assert all("chinese" not in key and "漢化" not in key for key in keys)  # 沒收尾的括號也去掉
    assert duplicates.title_keys("Ｈｏｓｈｉ　ｎｏ　Ｋａｅｒｕ　Ｍｉｃｈｉ｜另一個譯名")[0] >= {"hoshinokaerumichi"}  # 全形
    assert duplicates._similar("ryouritohimitsunohoshizora", "ryoritohimitsunohoshizora")
    assert not duplicates._similar("ryouritohimitsunohoshizora", "ryouritohimitsunohoshizorazoku")  # 續作
    assert not duplicates._similar("ryouritohimitsunohoshizora2", "ryouritohimitsunohoshizora3")
    assert not duplicates._similar("星星回家的路與月亮上", "星星回家的路與月亮下")  # 上下集


def test_duplicates_near_titles(client):
    full = add(client, "301", title="[甲社團 (乙作者)] Hoshi no Kaeru Michi | 星星回家的路 [Chinese] [丙漢化組]")
    cut = client.post("/api/resources", json={"code": "302", "source_name": "站B", "title": "(C99) [甲社團]Hoshi no Kaeru Michi[Chine"}).json()
    other = add(client, "303", title="[丁社團] 星星回家的路 [中国翻訳] [DL版]")
    spelling = add(client, "304", title="[戊社團] Ryouri to Himitsu no Hoshizora [Chinese]")
    spelling2 = add(client, "305", title="Ryori to Himitsu no Hoshizora")
    add(client, "306", title="[戊社團] Ryouri to Himitsu no Hoshizora Zoku [Chinese]")  # 續作，不算重複
    add(client, "307", title="[戊社團] Ryouri to Himitsu no Hoshizora 2")
    # 同一來源共用的網站標語不算重複
    add(client, "401", title="第一支範例影片 - 範例影音 | 免費線上看範例站")
    add(client, "402", title="完全另一支影片 - 範例影音 | 免費線上看範例站")
    groups = {group["key"]: group["reasons"] for group in client.get("/api/duplicates").json()["groups"]}
    assert groups == {
        duplicates.group_key([full["id"], cut["id"], other["id"]]): ["title"],
        duplicates.group_key([spelling["id"], spelling2["id"]]): ["similar_title"],
    }


def _picture(seed: int, size=(60, 80)) -> bytes:
    rng = random.Random(seed)
    image = Image.new("RGB", (12, 16))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(12 * 16)])
    buffer = io.BytesIO()
    image.resize(size, Image.Resampling.BILINEAR).save(buffer, "JPEG" if size[0] > 100 else "PNG", quality=70)
    return buffer.getvalue()


def test_duplicates_same_cover_picture(client, monkeypatch):
    pictures = {"https://img.example/a.png": _picture(1), "https://cdn.example/a-large.jpg": _picture(1, (240, 320)),
                "https://img.example/b.png": _picture(2)}
    monkeypatch.setattr(covers, "download", lambda url: (pictures[url], "image/jpeg" if url.endswith(".jpg") else "image/png"))
    first = add(client, "501", title="甲作品的封面測試", image_url="https://img.example/a.png")
    second = add(client, "502", title="完全不同的乙標題", image_url="https://cdn.example/a-large.jpg")
    add(client, "503", title="另一部丙作品標題", image_url="https://img.example/b.png")
    with SessionLocal() as db:
        for url in pictures:
            assert len(covers.ensure_cover(db, url).fingerprint) == 16
    groups = client.get("/api/duplicates").json()["groups"]
    assert [(group["key"], group["reasons"]) for group in groups] == [(duplicates.group_key([first["id"], second["id"]]), ["cover_image"])]

    # 舊版快取的封面沒有雜湊：背景補色時一併補上
    with SessionLocal() as db:
        for entry in db.query(CoverCache).all():
            entry.fingerprint = None
        db.commit()
    assert client.get("/api/duplicates").json()["groups"] == []
    covers.backfill_colours()
    assert len(client.get("/api/duplicates").json()["groups"]) == 1


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
    assert backup["version"] == 5
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


def test_duplicates_keep_different_volumes_apart_and_lookup_one(client):
    a = add(client, "401", title="[甲社團] Hoshi Jikken Kiroku [Case. 7] (原創)")
    add(client, "402", title="[甲社團] Hoshi Jikken Kiroku [Case. 8] (原創)")
    same = add(client, "403", title="Hoshi Jikken Kiroku [Case. 7] [中國翻訳]")
    add(client, "404", title="(C99) [乙社團] Hoshizora Sanpo Michi (2)")
    add(client, "405", title="[乙社團] Hoshizora Sanpo Michi (3) [DL版]")
    groups = client.get("/api/duplicates").json()["groups"]
    assert [group["key"] for group in groups] == [f"{a['id']},{same['id']}"]  # (C99)、[DL版] 不是集數
    found = client.get(f"/api/duplicates/of/{same['id']}").json()
    assert [item["id"] for item in found["items"]] == [a["id"]] and found["reasons"] == ["title"]
    assert client.get("/api/duplicates/of/999999").json()["items"] == []

def test_dismissed_duplicates_can_be_restored(client):
    a = add(client, "501", title="[甲社團] Yoru no Toshokan de Aimashou")
    b = add(client, "502", title="Yoru no Toshokan de Aimashou [中國翻訳]")
    key = client.post("/api/duplicates/dismiss", json={"ids": [b["id"], a["id"]]}).json()["key"]
    assert client.get("/api/duplicates").json()["groups"] == []
    dismissed = client.get("/api/duplicates/dismissed").json()["groups"]
    assert [(group["key"], [item["id"] for item in group["items"]]) for group in dismissed] == [(key, [a["id"], b["id"]])]

    client.post("/api/duplicates/undismiss", json={"key": key})
    assert [group["key"] for group in client.get("/api/duplicates").json()["groups"]] == [key]
    assert client.get("/api/duplicates/dismissed").json()["groups"] == []
    client.delete(f"/api/resources/{b['id']}")
    client.post("/api/duplicates/dismiss", json={"ids": [a["id"], b["id"]]})
    assert client.get("/api/duplicates/dismissed").json()["groups"] == []  # 只剩一件的不列出


def test_rare_tag_can_be_kept(client):
    add(client, "601", title="只有一件有這個標籤", tags=["kasou"])
    rare = lambda: [entry["name"] for entry in client.get("/api/insights/tags").json()["rare"]]
    assert "kasou" in rare()
    assert client.post("/api/insights/tags/keep", json={"name": "kasou"}).json() == {"kept": ["kasou"]}
    assert "kasou" not in rare() and client.get("/api/insights/tags").json()["kept"] == ["kasou"]
    client.post("/api/insights/tags/keep", json={"name": "kasou", "keep": False})
    assert "kasou" in rare()



def test_duplicates_keep_codes_roman_numerals_and_parts_apart(client):
    # 番號不同：網站標語一樣也不算重複
    add(client, "j1", title="EFGH-320 某影片 女優甲 - Jable.TV | 免費高清AV在線看 | J片")
    add(client, "j2", title="ABCD-191 另一支影片 女優乙 - Jable.TV | 免費高清AV在線看 | J片 AV看到飽")
    # 結尾羅馬數字不同是不同集；同一集的兩個翻譯版本仍然算重複
    three = add(client, "k3", title="[Kasou Shobo (Kasou Sakka)]Tooi Hoshi no Uta. III[Chinese] [某某汉化]")
    three_b = add(client, "k3b", title="[Kasou Shobo (Kasou Sakka)] Tooi Hoshi no Uta. III | 遠方星星之歌。III [Chinese]")
    add(client, "k4", title="[Kasou Cocoa (Kasou Sakka)] Tooi Hoshi no Uta. IV | 遠方星星之歌。IV [Chinese]")
    add(client, "k2", title="[Kasou Shobo (Kasou Sakka)] Tooi Hoshi no Uta. Ⅱ [Chinese]")
    add(client, "k1", title="[Kasou Shobo (Kasou Sakka)] Tooi Hoshi no Uta. I [Chinese]")
    # 前編、後編
    add(client, "z1", title="[Kasou Club (Kasou)]Hoshizora Kansoku Nikki!(Zenpen)[Chinese]")
    add(client, "z2", title="[Kasou Club (Kasou)]Hoshizora Kansoku Nikki!!(Kouhen)[Chinese]")
    # 只有中文譯名寫了集數「2」
    add(client, "m1", title="[Kasou Letter (Kasou)] Neko no Asa Gohan. Asa×Yuki | 貓咪的早餐記事。早餐X雪 [Chinese]")
    add(client, "m2", title="[Kasou Letter (Kasou)] Neko no Asa Gohan. Asa×Yuki | 貓咪的早餐記事。早餐X雪2 [Chinese]")
    groups = [group["key"] for group in client.get("/api/duplicates").json()["groups"]]
    assert groups == [duplicates.group_key([three["id"], three_b["id"]])]


def test_volume_marks():
    assert duplicates._volumes("Tooi Hoshi no Uta. Ⅱ [Chinese]") == duplicates._volumes("Tooi Hoshi no Uta. II")
    assert duplicates._volumes("I Love You") == duplicates._volumes("Love You")  # 開頭的英文 I 不是集數
    assert duplicates._apart("ABCD-191 甲", "EFGH-320 甲") and not duplicates._apart("ABCD-191 甲", "abcd191 乙")


def test_same_title_but_clearly_different_covers_are_not_duplicates(client, monkeypatch):
    pictures = {"https://img.example/x.png": _picture(11), "https://img.example/y.png": _picture(12),
                "https://img.example/x-big.jpg": _picture(11, (240, 320))}
    monkeypatch.setattr(covers, "download", lambda url: (pictures[url], "image/jpeg" if url.endswith(".jpg") else "image/png"))
    first = add(client, "601", title="[甲社團] Yoru no Sora [Chinese] [乙漢化]", image_url="https://img.example/x.png")
    add(client, "602", title="[甲社團] Yoru no Sora | 夜空 [Chinese] [丙漢化]", image_url="https://img.example/y.png")  # 不同漢化、封面不同
    same = add(client, "603", title="[甲社團] Yoru no Sora [Chinese] [Digital]", image_url="https://img.example/x-big.jpg")  # 同一張封面
    with SessionLocal() as db:
        for url in pictures:
            covers.ensure_cover(db, url)
    groups = client.get("/api/duplicates").json()["groups"]
    assert [group["key"] for group in groups] == [duplicates.group_key([first["id"], same["id"]])]
