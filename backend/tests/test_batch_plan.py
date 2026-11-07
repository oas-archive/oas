"""一次收錄的解析規則：實際會貼上的各種寫法。編號都是假的，不連外。"""
import pytest

from app import alternatives, batch_plan, metadata
from app.database import Resource, SessionLocal, Source
from app.defaults import SOURCES

N, W, JM = "https://nhentai.net/g/{}/", "https://www.wnacg.com/photos-index-aid-{}.html", "https://18comic.vip/album/{}/"


@pytest.fixture
def db(client):
    with SessionLocal() as session:
        session.query(Resource).delete()
        session.query(Source).delete()
        session.add_all(Source(name=name, template=template, kind=kind) for name, template, kind in SOURCES)
        session.commit()
        yield session


def rows(db, text):
    return batch_plan.parse(db, text)["rows"]


def urls(db, text):
    return [row["url"] for row in rows(db, text) if row["kind"] == "url"]


@pytest.mark.parametrize("text, expected", [
    ("N100001", N.format(100001)),
    ("n100001", N.format(100001)),
    ("N 100001", N.format(100001)),
    ("N#100001", N.format(100001)),
    ("#100001", N.format(100001)),
    ("100001", N.format(100001)),
    ("W100001", W.format(100001)),
    ("w 100001", W.format(100001)),
    ("JM100001", JM.format(100001)),
    ("jm100001", JM.format(100001)),
])
def test_short_codes_on_one_line(db, text, expected):
    assert urls(db, text) == [expected]


def test_sections(db):
    text = "N：\n100001\n100002\n\nW：\n100003\n\nJM：\n100004\n100005（x）\n"
    parsed = rows(db, text)
    assert [row.get("url") for row in parsed[:4]] == [N.format(100001), N.format(100002), W.format(100003), JM.format(100004)]
    assert parsed[4]["kind"] == "search" and parsed[4]["sites"] == ["禁漫天堂"]  # 不是數字：到這個網站搜尋


def test_copied_format_collects_nothing_until_filled(db):
    assert rows(db, "N：\n\nW：\n\nJM：\n") == []
    assert urls(db, "N：\n100001\n\nW：\n100002\n\nJM：\n100003\n") == [N.format(100001), W.format(100002), JM.format(100003)]


def test_header_with_half_width_colon_and_prefix_wins_over_section(db):
    assert urls(db, "W:\n100001\nN100002\n") == [W.format(100001), N.format(100002)]


def test_many_codes_on_one_line(db):
    assert urls(db, "100001, 100002，100003 100004、N100005 W100006") == [N.format(100001), N.format(100002), N.format(100003), N.format(100004), N.format(100005), W.format(100006)]


def test_urls_inside_text_and_several_on_a_line(db):
    assert urls(db, "看這本 https://nhentai.net/g/100001/ 很好看") == ["https://nhentai.net/g/100001/"]
    assert urls(db, "https://nhentai.net/g/100001/ https://nhentai.net/g/100002/") == ["https://nhentai.net/g/100001/", "https://nhentai.net/g/100002/"]


@pytest.mark.parametrize("line", ["https://nhentai.net/g/100001/。", "https://nhentai.net/g/100001/.", "（https://nhentai.net/g/100001/）", "https://nhentai.net/g/100001/)"])
def test_trailing_punctuation(db, line):
    assert urls(db, line) == ["https://nhentai.net/g/100001/"]


@pytest.mark.parametrize("text", ["XYZ-782", "xyz-782", "fc2-ppv-1234567"])
def test_video_codes_need_full_url(db, text):
    [row] = rows(db, text)
    assert row["kind"] == "invalid" and "完整網址" in row["reason"]


@pytest.mark.parametrize("header", ["MISSAV：", "H：", "動畫：", "影片：", "人：", "找：", "Pornhub：", "HentaiHaven："])
def test_other_sections_are_explained_not_guessed(db, header):
    parsed = rows(db, f"{header}\n100001\n某個文字\nhttps://example.org/a\n")
    assert [row["kind"] for row in parsed] == ["invalid", "invalid", "invalid", "url"]  # 完整網址在哪都收
    assert all("完整網址" in row["reason"] for row in parsed[:3])
    assert "100001" not in str(parsed[3])


def test_video_urls_are_still_recognised(db):
    [row] = rows(db, "https://missav.ai/xyz-782")
    assert row["kind"] == "url" and row["site"] == "MissAV" and row["code"] == "xyz-782"


def test_comic_section_needs_a_site_for_numbers(db):
    number, title = rows(db, "漫畫：\n100001\n某個標題\n")
    assert number["kind"] == "invalid" and "N、W、JM" in number["reason"]
    assert title["kind"] == "search" and title["sites"] == ["nhentai", "紳士漫畫", "禁漫天堂"]


def test_plain_text_is_a_search_not_a_number(db):
    [row] = rows(db, "NTR 2024 標題")
    assert row["kind"] == "search" and row["sites"] == ["nhentai", "紳士漫畫", "禁漫天堂"]


def test_short_bare_numbers_are_flagged_but_explicit_ones_are_not(db):
    short, explicit, section = rows(db, "123\nN124\nW：\n125\n")
    assert short["short"] is True and explicit["short"] is False and section["short"] is False


def test_repeats_are_counted_once(db):
    parsed = batch_plan.parse(db, "N100001\n100001\nhttps://nhentai.net/g/100001/\n")
    assert len(parsed["rows"]) == 1 and parsed["repeats"] == 2


def test_limit_is_reported_not_silent(db):
    parsed = batch_plan.parse(db, "\n".join(str(200000 + index) for index in range(305)))
    assert len(parsed["rows"]) == 300 and parsed["dropped"] == 5 and parsed["limit"] == 300


def test_existing_work_is_marked(db):
    db.add(Resource(code="100001", source_name="nhentai", title="已收錄"))
    db.commit()
    [row] = rows(db, "N100001")
    assert row["duplicate"]["title"] == "已收錄"


@pytest.mark.parametrize("url, site, code", [
    ("https://www.wnacg.com/photos-index-page-2-aid-100001.html", "紳士漫畫", "100001"),
    ("https://www.wnacg.com/photos-slide-aid-100001.html", "紳士漫畫", "100001"),
    ("https://www.wnacg.com/download-index-aid-100001.html", "紳士漫畫", "100001"),
    ("https://m.wnacg.com/photos-index-aid-100001.html", "紳士漫畫", "100001"),
    ("https://hanime1.me/download?v=100001", "Hanime1", "100001"),
    ("https://cn.pornhub.com/view_video.php?viewkey=abc123", "Pornhub", "view_video.php?viewkey=abc123"),
    ("https://hentaihaven.xxx/watch/some-title/", "HentaiHaven", "watch/some-title"),
])
def test_other_pages_of_the_same_work(db, url, site, code):
    assert metadata.resolve(db, url) == (site, code)


def test_other_domains_are_not_merged(db):
    assert metadata.resolve(db, "https://nhentai.to/g/100001/")[0] == "其他網站"


@pytest.mark.parametrize("saved, pasted", [
    ("xyz-782", "https://missav.ai/xyz-782-uncensored-leak"),
    ("xyz-782", "https://missav.ai/cn/xyz-782-chinese-subtitle"),
    ("xyz-782-chinese-subtitle", "https://missav.ai/xyz-782"),
    ("xyz-782-uncensored-leak", "https://missav.ai/xyz-782-chinese-subtitle"),
])
def test_versions_of_the_same_video_are_one_work(db, saved, pasted):
    db.add(Resource(code=saved, source_name="MissAV", title="已收錄"))
    db.commit()
    [row] = rows(db, pasted)
    assert row["duplicate"]["title"] == "已收錄"


def test_versions_in_one_paste_count_once(db):
    parsed = batch_plan.parse(db, "https://missav.ai/xyz-782\nhttps://missav.ai/xyz-782-chinese-subtitle\n")
    assert len(parsed["rows"]) == 1 and parsed["repeats"] == 1


def test_different_videos_stay_different(db):
    db.add(Resource(code="xyz-782", source_name="MissAV", title="已收錄"))
    db.commit()
    [row] = rows(db, "https://missav.ai/xyz-783")
    assert row["duplicate"] is None


def test_saved_version_keeps_its_own_code(client, db):
    response = client.post("/api/import/urls", json={"urls": ["https://missav.ai/xyz-782-chinese-subtitle", "https://missav.ai/xyz-782"]})
    result = response.json()
    assert len(result["created"]) == 1
    db.expire_all()
    assert db.query(Resource).filter_by(source_name="MissAV").one().code == "xyz-782-chinese-subtitle"


@pytest.mark.parametrize("host, kind", [("jable.tv", "影片"), ("javgg.net", "影片"), ("netflav1.com", "影片"), ("www.av01.tv", "影片"), ("spankbang.com", "影片"), ("www.xvideos.com", "影片"), ("www2.javhdporn.net", "影片"), ("myself-bbs.com", "動畫")])
def test_kind_from_known_hosts(host, kind):
    assert metadata.page_kind(f"https://{host}/a/some-work") == kind


def test_parse_api_does_not_search(client, db, monkeypatch):
    monkeypatch.setattr(alternatives, "_search", lambda *args: pytest.fail("預覽不該連外"))
    response = client.post("/api/import/parse", json={"text": "N100001\n某個標題\nXYZ-782\n"})
    assert response.status_code == 200
    assert [row["kind"] for row in response.json()["rows"]] == ["url", "search", "invalid"]


def test_search_api_only_searches_comic_sites(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(alternatives, "_search", lambda name, query: (calls.append((name, query)), ([{"code": "1", "url": "https://example.org/1", "title": "候選"}], ""))[1])
    response = client.post("/api/import/search", json={"text": "某個標題"})
    assert sorted(calls) == [("nhentai", "某個標題"), ("紳士漫畫", "某個標題")]
    plans = {item["site"]: item for item in response.json()["plans"]}
    assert plans["nhentai"]["results"][0]["title"] == "候選"
    assert plans["禁漫天堂"]["mode"] == "link" and "search_query=" in plans["禁漫天堂"]["link"]


def test_search_follows_the_section(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(alternatives, "_search", lambda name, query: (calls.append(name), ([], ""))[1])
    plans = client.post("/api/import/search", json={"text": "某個標題", "sites": ["紳士漫畫"]}).json()["plans"]
    assert calls == ["紳士漫畫"] and [item["site"] for item in plans] == ["紳士漫畫"]


def test_search_ignores_other_sites(client, db, monkeypatch):
    calls = []
    monkeypatch.setattr(alternatives, "_search", lambda name, query: (calls.append(name), ([], ""))[1])
    plans = client.post("/api/import/search", json={"text": "某個標題", "sites": ["MissAV"]}).json()["plans"]
    assert "MissAV" not in str(plans) and sorted(calls) == ["nhentai", "紳士漫畫"]


@pytest.mark.parametrize("text, expected", [("N100001", N.format(100001)), ("jm100001", JM.format(100001)), ("W 100001", W.format(100001)), ("100001", N.format(100001))])
def test_single_form_uses_the_same_short_codes(client, db, text, expected):
    result = client.post("/api/metadata", json={"url": text, "fetch": False}).json()
    assert result["url"] == expected


@pytest.mark.parametrize("text", ["XYZ-782", "H100001"])
def test_single_form_explains_the_rule(client, db, text):
    response = client.post("/api/metadata", json={"url": text, "fetch": False})
    assert response.status_code == 422 and "完整網址" in response.json()["detail"]
