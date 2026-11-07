import time

import pytest
from sqlalchemy import select

from app import metadata
from app.database import Resource, SessionLocal, Source

NHENTAI = '''<div id="cover" class="x"><a href="/g/1/1/"><img loading="eager" alt="(C86) [Circle (Artist)] Title [Chinese]" width="350" src="https://t3.nhentai.net/galleries/9/cover.jpg"></a></div>
<a class="tagchip" href="/parody/star-harbor/"><!--[!--><!--]--> <span class="name x"><!--[!--><!--]-->star harbor</span></a>
<a class="tagchip" href="/tag/anal/"> <span class="name x">anal</span></a><a href="/language/chinese/"><span class="name x">chinese</span></a>
<a href="/search/?q=x"><span class="name x">not a tag</span></a>'''
WNACG = '''<h2>[作者] 標題 ｜ 副標</h2><img alt="[作者] 標題 ｜ 副標" src="////t3.qy0.ru/data/t/1/2/cover.png" width="240">
<div class="addtags">標籤：<a class="tagshow" href="/a">女本位</a><a class="tagshow" href="/b">類型乙</a></div>'''


@pytest.fixture()
def sources(client):
    with SessionLocal() as db:
        db.add_all([
            Source(name="N網", template="https://nhentai.net/g/{}/"),
            Source(name="W1", template="https://www.wnacg.com/photos-index-aid-{}.html"),
            Source(name="hanime1", template="https://hanime1.me/watch?v={}"),
            Source(name="missav", template="https://missav.ai/{}"),
            Source(name="JM", template="https://18comic.vip/album/{}/"),
            Source(name="直接", template="{}"),
        ])
        db.commit()
    return client


@pytest.mark.parametrize("url, expected", [
    ("https://nhentai.net/g/100001/", ("N網", "100001")),
    ("http://www.nhentai.net/g/100001/3/", ("N網", "100001")),
    ("https://www.wnacg.com/photos-index-aid-200002.html", ("W1", "200002")),
    ("https://hanime1.me/watch?v=30003&fbclid=abc", ("hanime1", "30003")),
    ("https://missav.ai/dm18/fc2-ppv-4000004", ("missav", "fc2-ppv-4000004")),  # 分類那段不算代碼
    ("https://missav.ws/cn/abc-0001", ("missav", "abc-0001")),  # 鏡像網域
    ("https://18comic.org/album/500005/some-title", ("JM", "500005")),
    ("https://jmcomic1.me/album/500005/", ("JM", "500005")),
    ("https://missav.ai/abc-0005?ref=x", ("missav", "abc-0005")),
    ("https://example.com/x", ("其他網站", "https://example.com/x")),
])
def test_resolve(sources, url, expected):
    with SessionLocal() as db:
        assert metadata.resolve(db, url) == expected


def test_parsers():
    n = metadata.parse("https://nhentai.net/g/1/", NHENTAI)
    assert n == {"title": "(C86) [Circle (Artist)] Title [Chinese]", "image_url": "https://t3.nhentai.net/galleries/9/cover.jpg", "tags": ["star harbor", "anal", "chinese"],
                 "categories": {"star harbor": "parody", "anal": "tag", "chinese": "language"}}
    w = metadata.parse("https://www.wnacg.com/photos-index-aid-1.html", WNACG)
    assert w == {"title": "[作者] 標題 ｜ 副標", "image_url": "https://t3.qy0.ru/data/t/1/2/cover.png", "tags": ["女本位", "類型乙"]}
    g = metadata.parse("https://site.example/p", '<title>Hello » nhentai: hentai doujinshi and manga</title><meta property="og:image" content="/c.jpg"><meta name="keywords" content="a, b，c">')
    assert g == {"title": "Hello", "image_url": "https://site.example/c.jpg", "tags": ["a", "b", "c"]}


def test_endpoint_duplicate_and_blocked(sources, monkeypatch):
    sources.post("/api/resources", json={"code": "100001", "source_name": "N網", "title": "已收藏"})
    def blocked(url):
        raise metadata.MetadataError("blocked", blocked=True)
    monkeypatch.setattr(metadata, "_get", blocked)
    data = sources.post("/api/metadata", json={"url": "https://nhentai.net/g/100001/"}).json()
    assert data["duplicate"]["title"] == "已收藏" and data["blocked"] and not data["fetched"]
    monkeypatch.setattr(metadata, "_get", lambda url: NHENTAI)
    data = sources.post("/api/metadata", json={"url": "https://nhentai.net/g/1/"}).json()
    assert data["fetched"] and data["code"] == "1" and data["duplicate"] is None and "anal" in data["tags"]
    assert sources.post("/api/metadata", json={"url": "nhentai.net/g/1"}).status_code == 422


def test_aliases_blocklist_and_refresh_apply(client):
    from app import refresh
    from app.archive import normalise_tags
    from app.database import Resource, Tag, TagAlias, TagBlock
    with SessionLocal() as db:
        db.add_all([TagAlias(alias="big breasts", canonical="巨乳"), TagBlock(name="h圖")])
        db.commit()
        assert normalise_tags(db, ["Big Breasts", "H圖", "貓", "巨乳"]) == ["巨乳", "貓"]
    item = client.post("/api/resources", json={"code": "9", "source_name": "站A", "tags": ["big breasts", "H圖", "貓"]}).json()
    assert item["tags"] == ["巨乳", "貓"]  # 寫入時自動套用
    before = item["updated_at"]
    with SessionLocal() as db:
        resource = db.get(Resource, item["id"])
        data = {"title": "新標題", "image_url": "https://img.example/c.jpg", "tags": ["big breasts", "Artist A", "japanese"],
                "categories": {"big breasts": "tag", "Artist A": "artist", "japanese": "language", "貓": "character"}}
        changes = refresh.apply(db, resource, data)
        db.commit()
        assert changes["tags"] == 1 and changes["cover"] == 1  # 只新增作者；語言標記不新增
        assert db.scalar(select(Tag.category).where(Tag.name == "Artist A")) == "artist"
        assert db.scalar(select(Tag.category).where(Tag.name == "貓")) == "character"
    after = client.get(f"/api/resources/{item['id']}").json()
    assert after["title"] == "新標題" and after["image_url"] and after["updated_at"] == before
    assert sorted(after["tags"]) == ["Artist A", "巨乳", "貓"]
    assert client.get("/api/tags/categories").json() == {"Artist A": "artist", "貓": "character"}


def test_page_image_prefers_og_then_icon():
    assert metadata.page_image("https://a.example/model/x", '<meta property="og:image" content="/p.jpg">', "創作者") == ("https://a.example/p.jpg", "avatar")
    page = '<link rel="icon" href="/16.png" sizes="16x16"><link rel="apple-touch-icon" href="/touch.png">'
    assert metadata.page_image("https://forum.example/t/1", page) == ("https://forum.example/touch.png", "icon")
    assert metadata.page_image("https://bare.example/x", "<html></html>") == ("https://bare.example/favicon.ico", "icon")


def test_page_image_finds_creator_avatar():
    page = '<img class="js-menuSwap" data-image="https://x/other.jpg"><img id="getAvatar" src="https://cdn.example/me.jpg" alt="me">'
    assert metadata.page_image("https://site.example/model/me", page, "創作者") == ("https://cdn.example/me.jpg", "avatar")


def test_share_title_cleaned_and_known_covers(sources):
    data = sources.post("/api/metadata", json={"url": "https://18comic.org/album/500006/x", "fetch": False, "title": "[甲社團] 某本書 [中国翻訳] Comics - 禁漫天堂"}).json()
    assert (data["source_name"], data["code"], data["title"]) == ("JM", "500006", "[甲社團] 某本書 [中国翻訳]")
    assert data["image_url"] == "https://cdn-msp.18comic.vip/media/albums/500006.jpg"
    assert metadata.known_cover("https://missav.ai/dm26/abc-0002") == "https://fourhoi.com/abc-0002/cover-n.jpg"
    assert metadata.clean_title("某部影片 - MissAV | 免費高清AV在線看") == "某部影片"


def test_normalise_saved_relinks_and_merges(sources):
    from app import intake
    add = lambda **body: sources.post("/api/resources", json=body).json()
    jm = add(code="500007", source_name="JM")  # 一次收錄、只有編號
    shared = add(code="https://18comic.org/album/500007/x", source_name="其他網站", title="分享來的 Comics - 禁漫天堂", rating=4)
    video = add(code="dm26/abc-0003", source_name="missav", title="影片 - MissAV")
    stray = add(code="https://missav.ws/abc-0004", source_name="其他網站", title="另一部")
    assert intake.normalise_saved() == {"relinked": 2, "merged": 1, "titles": 2, "sites": 0, "icons": 0}
    kept = sources.get(f"/api/resources/{jm['id']}").json()
    assert kept["title"] == "分享來的" and kept["rating"] == 4 and sources.get(f"/api/resources/{shared['id']}").status_code == 404
    assert sources.get(f"/api/resources/{video['id']}").json()["code"] == "abc-0003"
    moved = sources.get(f"/api/resources/{stray['id']}").json()
    assert (moved["source_name"], moved["code"]) == ("missav", "abc-0004")
    with SessionLocal() as db:
        db.add(Source(name="ph", template="https://www.pornhub.com/{}"))
        db.commit()
    first = add(code="view_video.php?viewkey=aaa", source_name="ph", title="影片甲")
    second = add(code="view_video.php?viewkey=bbb", source_name="ph", title="影片乙")
    with SessionLocal() as db:
        assert metadata.resolve(db, "https://www.pornhub.com/view_video.php?viewkey=aaa") == ("ph", "view_video.php?viewkey=aaa")
    assert intake.normalise_saved() == {"relinked": 0, "merged": 0, "titles": 0, "sites": 0, "icons": 0}  # 再跑一次不會再改


    assert {item["id"] for item in sources.get("/api/resources?limit=50").json()["items"]} >= {first["id"], second["id"]}  # 其他網站不動


def test_jm_photo_page_is_the_album(sources):
    from app import intake
    with SessionLocal() as db:
        assert metadata.resolve(db, "https://18comic.vip/photo/900101") == ("JM", "900101")
    assert metadata.known_cover("https://18comic.org/photo/900101/") == "https://cdn-msp.18comic.vip/media/albums/900101.jpg"
    saved = sources.post("/api/resources", json={"code": "https://18comic.vip/photo/900101", "source_name": "直接", "title": "閱讀頁"}).json()
    assert intake.normalise_saved()["relinked"] == 1 and intake.backfill_known_covers() == 1
    moved = sources.get(f"/api/resources/{saved['id']}").json()
    assert (moved["source_name"], moved["code"]) == ("JM", "900101") and moved["image_url"].endswith("/900101.jpg")


@pytest.mark.parametrize("url, home", [
    ("https://missav.ws/", True), ("https://missav.ai/dm40", True), ("https://missav.ws/dm40/cn", True), ("https://example.com", True),
    ("https://missav.ai/abc-0003", False), ("https://nhentai.net/g/1/", False), ("https://example.com/?q=x", False), ("123", False),
    ("https://x.com/ab", False), ("https://missav.ws/zh-tw", True),
    ("https://www.pornhub.com/explore", True), ("http://video.fc2.com/a/search/video/?duration=&span=", True), ("https://forum.example/forum-1-2.html", True),
    ("https://av.jkforum.net/playlist/list/ABCD1234", True), ("https://www.jkforum.net/thread-900300-1-1.html", False), ("https://jable.tv/videos/abcd-191/", False),
    ("https://fantia.jp/fanclubs/900200/products", False), ("https://hanime1.me/watch?v=90106", False), ("https://www.pornhub.com/model/kasou-model", False),
])
def test_site_page(url, home):
    assert metadata.site_page(url) is home


def test_site_page_gets_site_kind(sources):
    from app import intake
    new = sources.post("/api/resources", json={"code": "dm40", "source_name": "missav", "title": "MissAV | 免費高清AV在線看"}).json()
    video = sources.post("/api/resources", json={"code": "abc-0005", "source_name": "missav", "title": "影片"}).json()
    assert new["kind"] == "網站" and video["kind"] != "網站"
    with SessionLocal() as db:  # 舊資料：啟動時整理改成網站
        db.execute(Resource.__table__.update().where(Resource.id == new["id"]).values(kind=""))
        db.commit()
    assert intake.normalise_saved()["sites"] == 1 and sources.get(f"/api/resources/{new['id']}").json()["kind"] == "網站"


JM_ALBUM = '''<link rel="canonical" href="https://18comic.vip/album/900102/"><meta property="og:image" content="https://cdn-msp2.18comic.vip/media/logo/new_logo.png">
<meta name="keywords" content="熟女,後宮,中文H漫,某作品H漫,同人誌,另一作品H漫"><h1 class="book-name mb-0" id="book-name">虛構的漫畫標題</h1>
<span itemprop="author" data-type="works"> <a href="/search/photos?search_query=x">從頭到尾都很不錯</a> </span><span itemprop="author" data-type="actor"> </span>
<span itemprop="genre" data-type="tags"> <a href="/search/photos?search_query=熟女" class="btn">熟女</a> <a class="btn">後宮</a> </span>
<span itemprop="author" data-type="author"> <a class="btn">Kasou Sakka</a> </span>
<div class="tag-hot tag-block"><a class="btn btn-sm tag-block">傲嬌</a></div>'''


def test_jm_album_page():
    """禁漫：標籤取 data-type 區塊（不取 keywords、側欄熱門標籤），封面依正式編號推得（og:image 是 logo），讀到首頁就當沒讀到。"""
    data = metadata.parse("https://18comic.org/album/900103/", JM_ALBUM)
    assert data["title"] == "虛構的漫畫標題" and data["tags"] == ["熟女", "後宮", "Kasou Sakka"] and data["categories"] == {"Kasou Sakka": "artist"}
    assert data["canonical"] == "https://18comic.vip/album/900102/" and data["image_url"] == "https://cdn-msp.18comic.vip/media/albums/900102.jpg"
    home = '<title>禁漫天堂</title><meta name="keywords" content="中文H漫,某作品H漫"><meta property="og:image" content="/logo.png">'
    assert metadata.parse("https://18comic.vip/album/900104/", home) == {"title": "", "image_url": "", "tags": []}


def test_describe_uses_jm_canonical_code(sources, monkeypatch):
    monkeypatch.setattr(metadata, "_get", lambda url: JM_ALBUM)
    data = sources.post("/api/metadata", json={"url": "https://18comic.vip/photo/900103", "fetch": True}).json()
    assert (data["source_name"], data["code"]) == ("JM", "900102") and data["image_url"].endswith("/900102.jpg")


def test_missing_pages_are_not_works(monkeypatch):
    """禁漫的「作品不存在」轉址頁當成找不到；紳士對訪客顯示「不存在」時說清楚可能要登入，不當成下架。"""
    class Response:
        headers = type("H", (), {"get_content_charset": lambda _: "utf-8"})()
        def __init__(self, final): self.final = final
        def read(self, n): return "您要訪問的漫畫不存在！ 你可以 點擊這裡登錄".encode()
        def geturl(self): return self.final
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(metadata, "check_url", lambda url: None)
    monkeypatch.setattr(metadata, "safe_urlopen", lambda request, timeout: Response("https://18comic.vip/error/album_missing"))
    with pytest.raises(metadata.MetadataError) as missing:
        metadata._get("https://18comic.vip/album/900105/")
    assert missing.value.gone
    with pytest.raises(metadata.MetadataError) as hidden:
        metadata.parse("https://www.wnacg.com/photos-index-aid-900105.html", "<div>您要訪問的漫畫不存在！</div>")
    assert not hidden.value.gone and "登入" in str(hidden.value)


HENTAIHAVEN = '''<meta property="og:title" content="Some Show Episode 1 - Hentai Haven | Watch free Hentai HD"/>
<meta property="og:image" content="https://img.hentaihaven.xxx/c.jpg"/><a href="/series/uncensored/">Uncensored</a>
<a data-slot="badge" data-variant="chip" class="x" href="/series/milf/">Milf</a><a data-slot="badge" data-variant="chip" class="x" href="/series/bdsm/">BDSM</a>
<a data-slot="badge" data-variant="chip" class="x" href="/series/milf/">Milf</a><a data-variant="chip" href="/release/2022/">2022</a>'''
PORNHUB = '''<meta property="og:title" content="A Video" /><meta property="og:image" content="https://ei.phncdn.com/t.jpg" />
<div class="usernameWrap"><a href="/model/other" class="bolded">Recommended Uploader</a></div>
<div class="video-detailed-info"><div class="usernameWrap clearfix" data-type="user"><span><a rel="" href="/model/maker-a" class="bolded">Maker A</a></span></div>
<div class="categoriesWrapper"><a class="item" data-label="category" href="/categories/adult">18-25</a> <a class="item" data-label="category" href="/hd">HD Porn</a></div>
<div class="tagsWrapper"><a data-label="tag" class="item isTag" href="/video/search?search=cosplay"><span>cosplay</span></a></div></div>'''
MISSAV = '''<meta property="og:title" content="ABC-123 標題 - 女優甲" /><a href="https://missav.ai/genres/VR">VR</a>
<div class="text-secondary"> <span>女優:</span> <a href="https://missav.ai/dm28/actresses/x" class="a">女優甲</a> </div>
<div class="text-secondary"> <span>類型:</span> <a href="https://missav.ai/dm1/genres/y" class="a">類型甲</a>, <a href="https://missav.ai/dm2/genres/z" class="a">中出</a> </div>
<div class="text-secondary"> <span>發行商:</span> <a href="https://missav.ai/makers/KASOU" class="a">KASOU</a> </div>'''


def test_video_site_parsers():
    h = metadata.parse("https://hentaihaven.xxx/watch/some-show/episode-1", HENTAIHAVEN)
    assert h == {"title": "Some Show Episode 1", "image_url": "https://img.hentaihaven.xxx/c.jpg", "tags": ["Milf", "BDSM"]}  # 選單與年份不算
    p = metadata.parse("https://www.pornhub.com/view_video.php?viewkey=1", PORNHUB)
    assert p["tags"] == ["18-25", "cosplay", "Maker A"] and p["categories"] == {"Maker A": "artist"}  # 去掉 HD Porn；推薦影片的上傳者不算
    m = metadata.parse("https://missav.ws/dm40/abc-123", MISSAV)
    assert m["tags"] == ["女優甲", "類型甲", "中出", "KASOU"] and m["categories"] == {"女優甲": "artist", "KASOU": "group"}


def test_new_parsers_are_refreshable():
    from app import refresh
    assert {"hentaihaven.xxx", "pornhub.com"} <= set(refresh.FETCHABLE_HOSTS) and not any("18comic" in h or "missav" in h for h in refresh.FETCHABLE_HOSTS)


def test_page_html_from_phone(sources, monkeypatch):
    """手機 App 送來隱藏瀏覽器讀到的頁面：不另外連線，照網站規則解析；分類一起回傳，儲存時標上。"""
    def never(url):
        raise AssertionError("有頁面內容時不該再連線")
    monkeypatch.setattr(metadata, "_get", never)
    data = sources.post("/api/metadata", json={"url": "https://missav.ws/abc-123", "html": MISSAV}).json()
    assert data["fetched"] and data["title"] == "ABC-123 標題 - 女優甲" and "類型甲" in data["tags"]
    assert data["categories"] == {"女優甲": "artist", "KASOU": "group"}
    item = sources.post("/api/resources", json={"code": data["code"], "source_name": data["source_name"], "tags": data["tags"],
                                                "tag_categories": data["categories"]}).json()
    assert sources.get("/api/tags/categories").json() == {"女優甲": "artist", "KASOU": "group"}

    bare = sources.post("/api/resources", json={"code": "zzz-999", "source_name": "missav"}).json()
    assert sources.post("/api/attention/fill-page", json={"id": bare["id"], "html": "<title>Just a moment...</title>"}).json() == {"filled": False, "tags": 0}
    filled = sources.post("/api/attention/fill-page", json={"id": bare["id"], "html": MISSAV}).json()
    assert filled["filled"] and filled["tags"] == 4
    after = sources.get(f"/api/resources/{bare['id']}").json()
    assert after["title"] and "中出" in after["tags"] and item["id"] != bare["id"]


def test_site_types_studios_and_mobile_pages():
    hanime = '''<meta property="og:title" content="作品 1"><a href="https://hanime1.me/search?genre=3DCG">3DCG</a>
<a id="video-artist-name" style="color: white;" href="https://hanime1.me/search?query=%E5%85%AC%E5%8F%B8&amp;genre=%E8%A3%8F%E7%95%AA"> 某公司 </a>
<div class="single-video-tag"><a href="/x">巨乳</a></div>'''
    h = metadata.parse("https://hanime1.me/watch?v=1", hanime)
    assert h["tags"] == ["巨乳", "裏番", "某公司"] and h["categories"] == {"裏番": "category", "某公司": "group"}  # 選單的 3DCG 不算
    studio = HENTAIHAVEN + '<span>Studio</span><a data-slot="badge" data-variant="chip" href="/studio/kasou-studio/">Kasou Studio</a>'
    assert metadata.parse("https://hentaihaven.xxx/watch/x", studio)["categories"] == {"Kasou Studio": "group"}
    mobile = '''<meta property="og:title" content="影片" /><span class="usernameWrapper"> <a href="/model/maker-b">Maker B</a></span>
<a data-label="tag" class="item isTag" href="/video/search?search=%E6%BB%B4" data-translated="translated-tags"><span>滴</span></a>
<a href="/video?c=138" data-label="category" class="item"><span>已认证素人</span></a><a href="/video?c=15" data-label="category" class="item"><span>内射中出</span></a>
<a data-label="tag" class="item isTag" href="/video/search?search=cum"><span>cum</span></a>'''
    p = metadata.parse("https://www.pornhub.com/view_video.php?viewkey=2", mobile)
    assert p["tags"] == ["内射中出", "cum", "Maker B"] and p["categories"] == {"Maker B": "artist"}  # 去掉認證標記與機器翻譯那份


@pytest.mark.parametrize("fragment", ["<meta ", '<meta name="og:title" ', '<meta name="x"', "<img ", '<img alt="a" ', "<link ", "<title>", "<a ", " ", '"', "<"])
@pytest.mark.parametrize("url", ["https://example.com/a", "https://nhentai.net/g/1/", "https://www.wnacg.com/photos-index-aid-1.html"])
def test_hostile_page_does_not_stall_parsing(url, fragment):
    page = fragment * (400_000 // len(fragment))
    started = time.monotonic()
    metadata.parse(url, page)
    metadata.page_image(url, page, "創作者")
    assert time.monotonic() - started < 5


def test_meta_tags_in_either_order_and_with_angle_brackets():
    page = """<meta property="og:title" content="A &gt; B > C"><meta content="https://x.example/a.jpg" name="og:image"><meta name="keywords" content="甲,乙">"""
    assert metadata.parse_generic(page) == {"title": "A > B > C", "image_url": "https://x.example/a.jpg", "tags": ["甲", "乙"]}
