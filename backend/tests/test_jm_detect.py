"""禁漫換編號偵測（jm_detect.py）：範例頁面全是虛構的標題與編號。"""
from datetime import datetime, timedelta, timezone

import pytest

from app import jm_detect, metadata
from app.database import Resource, SessionLocal, Source

ALIVE = '<!--oas-url:https%3A%2F%2F18comic.vip%2Falbum%2F880001%2F--><html><head><title>虛構的星空旅行 | 禁漫天堂</title></head><body><h1 id="book-name">虛構的星空旅行</h1><span data-type="tags"><a>純愛</a></span></body></html>'
MISSING = '<!--oas-url:https%3A%2F%2F18comic.vip%2Ferror%2Falbum_missing--><html><head><title>禁漫天堂</title></head><body><div>找不到這個本子</div></body></html>'
HOME = '<!--oas-url:https%3A%2F%2F18comic.vip%2F--><html><head><title>禁漫天堂</title></head><body><div>首頁</div></body></html>'
CHALLENGE = '<html><head><title>Just a moment...</title></head><body>Enable JavaScript and cookies to continue</body></html>'
SEARCH = '''<html><body><div class="row">
<div class="thumb-overlay"><a href="/album/880101/xing-kong"><img src="a.jpg"></a></div>
<span class="video-title title-truncate">[虛構社團] 虛構的星空旅行 (C000) [某某漢化]</span>
<div class="thumb-overlay"><a href="/album/880102/other"><img src="b.jpg"></a></div>
<span class="video-title title-truncate">完全不相干的海邊故事</span>
<div class="thumb-overlay"><a href="/album/880103/xing-kong-2"><img src="c.jpg"></a></div>
<span class="video-title">虛構的星空旅行 後篇</span>
<a href="/album/880101/xing-kong">again</a>
<a href="/album/880104/no-title"><img src="d.jpg"></a>
</div></body></html>'''
SEARCH_BLOCKED = '<html><head><title>請稍候…</title></head><body></body></html>'


@pytest.fixture()
def jm(client):
    with SessionLocal() as db:
        db.add_all([Source(name="禁漫天堂", template="https://18comic.vip/album/{}/", kind="漫畫"),
                    Source(name="紳士漫畫", template="https://www.wnacg.com/photos-index-aid-{}.html", kind="漫畫")])
        db.commit()
    return client


def add(client, code, source="禁漫天堂", **extra):
    response = client.post("/api/resources", json={"code": code, "source_name": source, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def get(client, item):
    return client.get(f"/api/resources/{item['id']}").json()


def checked_at(item_id):
    with SessionLocal() as db:
        return db.get(Resource, item_id).link_checked_at


def test_classify_page():
    assert jm_detect.classify_page(ALIVE) == "alive"
    assert jm_detect.classify_page(MISSING) == "dead"
    assert jm_detect.classify_page(HOME) == "unknown"  # 未開放的作品轉到首頁：認不出來，不標失效
    assert jm_detect.classify_page("<html><body>舊版 App 沒帶最後網址的錯誤頁</body></html>") == "unknown"
    assert jm_detect.classify_page(CHALLENGE) == "challenge"
    assert jm_detect.classify_page("<title>Attention Required! | Cloudflare</title>") == "challenge"
    assert jm_detect.classify_page("") == "challenge" and jm_detect.classify_page("  \n") == "challenge"
    # 挑戰頁就算網址帶著錯誤頁的字樣也不標失效
    assert jm_detect.classify_page("<!--oas-url:https%3A%2F%2F18comic.vip%2Ferror%2Falbum_missing-->" + CHALLENGE) == "challenge"


def test_record_marks_dead_only_on_missing_page(jm):
    dead, alive, home, blocked = (add(jm, code, title=title) for code, title in (("880011", "會失效的"), ("880012", "還在的"), ("880013", "轉到首頁的"), ("880014", "被擋的")))
    assert jm.post("/api/linkcheck/jm/result", json={"id": dead["id"], "html": MISSING}).json() == {"result": "dead"}
    assert jm.post("/api/linkcheck/jm/result", json={"id": alive["id"], "html": ALIVE}).json() == {"result": "alive"}
    assert jm.post("/api/linkcheck/jm/result", json={"id": home["id"], "html": HOME}).json() == {"result": "unknown"}
    assert jm.post("/api/linkcheck/jm/result", json={"id": blocked["id"], "html": CHALLENGE}).json() == {"result": "challenge"}
    assert get(jm, dead)["link_dead"] is True and checked_at(dead["id"]) is not None
    assert get(jm, dead)["updated_at"] == dead["updated_at"]  # 不動修改時間，書庫排序不變
    assert get(jm, alive)["link_dead"] is False and checked_at(alive["id"]) is not None
    assert get(jm, home)["link_dead"] is False and checked_at(home["id"]) is not None  # 記巡檢時間，下一輪才輪得到後面的作品
    assert get(jm, blocked)["link_dead"] is False and checked_at(blocked["id"]) is None  # 挑戰頁：什麼都不記，下次再試


def test_record_rejects_other_sources_and_missing(jm):
    other = add(jm, "880021", source="紳士漫畫", title="別的網站")
    assert jm.post("/api/linkcheck/jm/result", json={"id": other["id"], "html": MISSING}).status_code == 404
    assert jm.post("/api/linkcheck/jm/result", json={"id": 987654, "html": MISSING}).status_code == 404
    assert get(jm, other)["link_dead"] is False
    assert jm.post("/api/attention/jm-candidates", json={"id": other["id"], "html": SEARCH}).status_code == 404


def test_targets_round_limit_and_order(jm):
    items = [add(jm, str(880100 + index), title=f"虛構作品 {index}") for index in range(jm_detect.ROUND_LIMIT + 4)]
    recent, old, dead = items[0], items[1], items[2]
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.get(Resource, recent["id"]).link_checked_at = now - timedelta(days=1)  # 剛查過：這輪不用讀
        db.get(Resource, old["id"]).link_checked_at = now - timedelta(days=30)   # 很久沒查：排在沒查過的後面
        db.get(Resource, dead["id"]).link_dead = True                             # 已失效：不再讀
        db.commit()
    other = add(jm, "880090", source="紳士漫畫", title="別的網站")  # 不是禁漫
    data = jm.get("/api/linkcheck/jm/targets").json()
    ids = [row["id"] for row in data["targets"]]
    assert data["limit"] == jm_detect.ROUND_LIMIT == 10 and len(ids) == 10  # 一輪的上限
    assert recent["id"] not in ids and dead["id"] not in ids and other["id"] not in ids
    assert ids[0] == items[3]["id"] and old["id"] not in ids  # 沒巡檢過的先取，滿 10 件就不含最久沒查的那件
    assert data["targets"][0]["url"] == "https://18comic.vip/album/880103/"


def test_targets_includes_old_check_after_unchecked(jm):
    first, second = add(jm, "880201", title="沒查過"), add(jm, "880202", title="很久沒查")
    with SessionLocal() as db:
        db.get(Resource, second["id"]).link_checked_at = datetime.now(timezone.utc) - timedelta(days=10)
        db.commit()
    assert [row["id"] for row in jm.get("/api/linkcheck/jm/targets").json()["targets"]] == [first["id"], second["id"]]


def test_parse_search_page():
    rows = jm_detect.parse_search(SEARCH)
    assert rows == [{"code": "880101", "title": "[虛構社團] 虛構的星空旅行 (C000) [某某漢化]"}, {"code": "880102", "title": "完全不相干的海邊故事"},
                    {"code": "880103", "title": "虛構的星空旅行 後篇"}]  # 880104 沒有標題不列；880101 重複的連結只算一次
    assert jm_detect.parse_search("") == [] and jm_detect.parse_search("<html>沒有結果</html>") == []
    titled = '<a href="https://18comic.vip/album/880105/x" title="只有連結屬性的標題 &amp; 二"><img></a>'
    assert jm_detect.parse_search(titled) == [{"code": "880105", "title": "只有連結屬性的標題 & 二"}]


def test_similarity_ranks_same_work_first():
    old = "[虛構社團] 虛構的星空旅行 (C000)"
    same = jm_detect.similarity(old, "虛構的星空旅行 [某某漢化]")
    sequel = jm_detect.similarity(old, "虛構的星空旅行 後篇")
    other = jm_detect.similarity(old, "完全不相干的海邊故事")
    assert same == pytest.approx(1.0) and 0.6 < sequel < same and other < 0.3
    assert jm_detect.similarity("星空の約束", "星空の約束 / 中文名") > 0.8  # 多語標題取其中一段
    assert jm_detect.similarity("虚构的星空旅行", "虛構的星空旅行") > 0.95  # 簡繁
    assert jm_detect.similarity("", "任何標題") == 0.0


def test_candidates_ranked_and_filtered(jm):
    old = add(jm, "880301", title="[虛構社團] 虛構的星空旅行 (C000) [某某漢化]")
    owned = add(jm, "880103", title="已經在典藏的後篇")
    result = jm.post("/api/attention/jm-candidates", json={"id": old["id"], "html": SEARCH}).json()
    assert result["status"] == "ok"
    codes = [row["code"] for row in result["candidates"]]
    assert codes == ["880101", "880103"]  # 不相干的那件相似度太低，不列；依相似度排序
    first, second = result["candidates"]
    assert first["similarity"] == 1.0 and second["similarity"] < first["similarity"]
    assert first["url"] == "https://18comic.vip/album/880101/" and first["thumb"].endswith("/880101.jpg") and first["owned_id"] is None
    assert second["owned_id"] == owned["id"]  # 已在典藏的標示出來
    # 搜尋結果裡有舊編號自己：不列
    again = jm.post("/api/attention/jm-candidates", json={"id": old["id"], "html": SEARCH.replace("880102", "880301")}).json()
    assert "880301" not in [row["code"] for row in again["candidates"]]


def test_candidates_blocked_and_empty(jm):
    old = add(jm, "880302", title="虛構的星空旅行")
    assert jm.post("/api/attention/jm-candidates", json={"id": old["id"], "html": SEARCH_BLOCKED}).json() == {"status": "blocked", "candidates": []}
    assert jm.post("/api/attention/jm-candidates", json={"id": old["id"], "html": CHALLENGE}).json()["status"] == "blocked"
    assert jm.post("/api/attention/jm-candidates", json={"id": old["id"], "html": "<html><body>沒有結果</body></html>"}).json() == {"status": "empty", "candidates": []}


def test_confirm_relink_and_undo(jm, monkeypatch):
    def blocked(url):
        raise metadata.MetadataError("此網站有防護，無法由伺服器讀取。", blocked=True)
    monkeypatch.setattr(metadata, "_get", blocked)
    item = add(jm, "880401", title="虛構的星空旅行", tags=["純愛"], note="自己的筆記")
    jm.post("/api/linkcheck/jm/result", json={"id": item["id"], "html": MISSING})
    assert get(jm, item)["link_dead"] is True
    moved = jm.post(f"/api/resources/{item['id']}/relink", json={"source_name": "禁漫天堂", "code": "880101"}).json()
    assert moved["code"] == "880101" and moved["link_dead"] is False and moved["image_url"].endswith("/880101.jpg")
    assert "原連結：禁漫天堂 880401" in moved["note"] and moved["note"].startswith("自己的筆記") and moved["undo"]
    other = add(jm, "880402", title="另一件")  # 新編號已被別件作品用掉：擋下，不會換
    assert jm.post(f"/api/resources/{other['id']}/relink", json={"source_name": "禁漫天堂", "code": "880101"}).status_code == 409

    # 復原：整件作品（編號、封面、筆記、失效標記）回到換編號之前
    restored = jm.post(f"/api/undo/{moved['undo']}")
    assert restored.status_code == 200
    back = get(jm, item)
    assert back["code"] == "880401" and back["link_dead"] is True and back["note"] == "自己的筆記" and back["tags"] == ["純愛"]
    assert back["image_url"] == item["image_url"]
    assert jm.post(f"/api/undo/{moved['undo']}").status_code in (404, 410, 409)  # 只能復原一次
