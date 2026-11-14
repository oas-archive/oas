"""分享進來的作品已在典藏中，按「補上它的空白欄位」（/resources/{id}/fill-blanks）；以及失效作品搜尋欄的關鍵字。資料都是虛構的。"""
import pytest

from app import alternatives, covers
from app.database import Resource

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture()
def fake_download(monkeypatch):
    """網址含 broken 的抓不到；state["down"] 為 True 時全部抓不到（模擬來源暫時連不上）。"""
    state = {"down": False, "calls": []}

    def download(url):
        state["calls"].append(url)
        if state["down"] or "broken" in url:
            raise covers.CoverError("連線失敗：timed out")
        return PNG, "image/png"

    monkeypatch.setattr(covers, "download", download)
    monkeypatch.setattr(covers, "schedule", lambda url: None)  # 不在背景先存好：這裡要看作品頁讀封面那一次
    return state


def make(client, **values):
    return client.post("/api/resources", json={"code": "900501", "source_name": "站A", **values}).json()


def test_fills_only_blank_fields_and_adds_tags(client, fake_download):
    item = make(client, title="已整理的標題", tags=["舊標籤"])
    filled = client.post(f"/api/resources/{item['id']}/fill-blanks", json={
        "title": "分享帶來的標題", "image_url": "https://img.example/a.png", "tags": ["舊標籤", "新標籤", "虛構作者"],
        "tag_categories": {"虛構作者": "artist"},
    }).json()
    assert filled["title"] == "已整理的標題"  # 有內容的不覆蓋
    assert filled["image_url"] == "https://img.example/a.png"
    assert sorted(filled["tags"]) == ["新標籤", "舊標籤", "虛構作者"]
    assert filled["filled"] == {"title": 0, "cover": 1, "tags": 2, "categorised": 1}
    assert client.get(f"/api/covers/{item['id']}").status_code == 200


def test_nothing_to_fill(client, fake_download):
    item = make(client, title="有標題", image_url="https://img.example/b.png", tags=["甲"])
    filled = client.post(f"/api/resources/{item['id']}/fill-blanks", json={"title": "別的", "image_url": "https://img.example/c.png", "tags": ["甲"]}).json()
    assert filled["image_url"] == "https://img.example/b.png" and filled["filled"] == {"title": 0, "cover": 0, "tags": 0, "categorised": 0}
    assert client.post("/api/resources/99999/fill-blanks", json={}).status_code == 404


def test_cover_that_failed_before_shows_after_filling(client, fake_download):
    """回饋第 7 項：一次收錄時推得的封面網址當時抓不到（24 小時內不重抓），分享時表單預覽看得到同一張圖，
    補上空白欄位後跳到作品頁，封面要出現，不能因為舊的失敗紀錄回 404。"""
    item = make(client, image_url="https://img.example/guess-900501.jpg")
    fake_download["down"] = True
    assert client.get(f"/api/covers/{item['id']}").status_code == 404  # 當時抓不到，記成失敗
    fake_download["down"] = False
    assert client.get(f"/api/covers/{item['id']}").status_code == 404  # 失敗紀錄還在：不重抓（原本的問題）
    client.post(f"/api/resources/{item['id']}/fill-blanks", json={"title": "讀到的標題", "image_url": "https://img.example/guess-900501.jpg"})
    response = client.get(f"/api/covers/{item['id']}")
    assert response.status_code == 200 and response.content == PNG


def test_broken_cover_is_replaced_by_the_shared_one(client, fake_download):
    item = make(client, image_url="https://img.example/broken-old.jpg")
    assert client.get(f"/api/covers/{item['id']}").status_code == 404
    filled = client.post(f"/api/resources/{item['id']}/fill-blanks", json={"image_url": "https://img.example/new.jpg"}).json()
    assert filled["image_url"] == "https://img.example/new.jpg" and filled["filled"]["cover"] == 1
    assert client.get(f"/api/covers/{item['id']}").status_code == 200


def test_cover_not_tried_yet_is_kept_and_odd_urls_ignored(client, fake_download):
    item = make(client, image_url="https://img.example/untried.jpg")
    kept = client.post(f"/api/resources/{item['id']}/fill-blanks", json={"image_url": "https://img.example/other.jpg"}).json()
    assert kept["image_url"] == "https://img.example/untried.jpg"  # 沒失敗過的封面不換
    blank = make(client, code="900502")
    ignored = client.post(f"/api/resources/{blank['id']}/fill-blanks", json={"image_url": "javascript:alert(1)"}).json()
    assert ignored["image_url"] == "" and ignored["filled"]["cover"] == 0


def test_search_keyword_from_prefilled_field():
    """失效作品的搜尋欄先填好標題（沒有標題就填編號）：送回來的是標題時照樣整理，是純數字編號時只比對同編號。"""
    titled = Resource(code="900601", source_name="nhentai", title="[虛構漢化組] 倉庫與仙人掌 (C99)")
    assert alternatives.keyword(titled, "[虛構漢化組] 倉庫與仙人掌 (C99)") == "倉庫與仙人掌"
    assert alternatives.keyword(titled, "") == "倉庫與仙人掌"
    assert alternatives.keyword(titled, " 仙人掌 ") == "仙人掌"
    untitled = Resource(code="900602", source_name="nhentai", title="")
    assert alternatives.keyword(untitled, "900602") == ""
    assert alternatives.keyword(Resource(code="ABC-123", source_name="站A", title=""), "ABC-123") == "ABC-123"


def test_tag_limits_on_fill_blanks(client):
    """補空白欄位的標籤、分類有件數與長度上限（表單與讀作品頁各最多約 60 個，300 很寬）。"""
    item = make(client, title="虛構作品")
    url = f"/api/resources/{item['id']}/fill-blanks"
    many = [f"虛構標籤{n}" for n in range(300)]
    assert client.post(url, json={"tags": many, "tag_categories": {name: "artist" for name in many}}).status_code == 200
    assert client.post(url, json={"tags": many + ["多一個"]}).status_code == 422
    assert client.post(url, json={"tag_categories": {f"虛構作者{n}": "artist" for n in range(301)}}).status_code == 422
    assert client.post(url, json={"tags": ["長" * 501]}).status_code == 422
    assert client.post(url, json={"tag_categories": {"長" * 501: "artist"}}).status_code == 422
    assert client.post(url, json={"tag_categories": {"虛構作者": "分" * 41}}).status_code == 422
    assert client.post(url, json={}).status_code == 200  # 欄位都可以省略


def test_tag_limits_on_resource_input(client):
    """新增、編輯作品時整份標籤送回：合併、補資料累積的大量標籤（2000 以內）照常接受，超過才擋。"""
    many = [f"虛構標籤{n}" for n in range(2000)]
    item = client.post("/api/resources", json={"code": "900502", "source_name": "站A", "tags": many[:60]}).json()
    assert len(item["tags"]) == 60
    edited = client.patch(f"/api/resources/{item['id']}", json={"code": "900502", "source_name": "站A", "tags": many})
    assert edited.status_code == 200 and len(edited.json()["tags"]) == 2000
    assert client.patch(f"/api/resources/{item['id']}", json={"code": "900502", "source_name": "站A", "tags": many + ["多一個"]}).status_code == 422
    assert client.post("/api/resources", json={"code": "900503", "source_name": "站A", "tags": ["長" * 501]}).status_code == 422
    assert client.post("/api/resources", json={"code": "900504", "source_name": "站A", "tag_categories": {f"虛構{n}": "tag" for n in range(2001)}}).status_code == 422
    assert client.post("/api/resources", json={"code": "900505", "source_name": "站A"}).status_code == 201  # 標籤可以省略
