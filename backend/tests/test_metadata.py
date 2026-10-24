import pytest
from sqlalchemy import select

from app import metadata
from app.database import SessionLocal, Source

NHENTAI = '''<div id="cover" class="x"><a href="/g/1/1/"><img loading="eager" alt="(C86) [Circle (Artist)] Title [Chinese]" width="350" src="https://t3.nhentai.net/galleries/9/cover.jpg"></a></div>
<a class="tagchip" href="/parody/touhou-project/"><!--[!--><!--]--> <span class="name x"><!--[!--><!--]-->touhou project</span></a>
<a class="tagchip" href="/tag/anal/"> <span class="name x">anal</span></a><a href="/language/chinese/"><span class="name x">chinese</span></a>
<a href="/search/?q=x"><span class="name x">not a tag</span></a>'''
WNACG = '''<h2>[作者] 標題 ｜ 副標</h2><img alt="[作者] 標題 ｜ 副標" src="////t3.qy0.ru/data/t/1/2/cover.png" width="240">
<div class="addtags">標籤：<a class="tagshow" href="/a">女本位</a><a class="tagshow" href="/b">師生</a></div>'''


@pytest.fixture()
def sources(client):
    with SessionLocal() as db:
        db.add_all([
            Source(name="N網", template="https://nhentai.net/g/{}/"),
            Source(name="W1", template="https://www.wnacg.com/photos-index-aid-{}.html"),
            Source(name="hanime1", template="https://hanime1.me/watch?v={}"),
            Source(name="missav", template="https://missav.ai/{}"),
            Source(name="直接", template="{}"),
        ])
        db.commit()
    return client


@pytest.mark.parametrize("url, expected", [
    ("https://nhentai.net/g/100001/", ("N網", "100001")),
    ("http://www.nhentai.net/g/100001/3/", ("N網", "100001")),
    ("https://www.wnacg.com/photos-index-aid-200002.html", ("W1", "200002")),
    ("https://hanime1.me/watch?v=30003&fbclid=abc", ("hanime1", "30003")),
    ("https://missav.ai/dm18/fc2-ppv-4000004", ("missav", "dm18/fc2-ppv-4000004")),
    ("https://example.com/x", ("其他網站", "https://example.com/x")),
])
def test_resolve(sources, url, expected):
    with SessionLocal() as db:
        assert metadata.resolve(db, url) == expected


def test_parsers():
    n = metadata.parse("https://nhentai.net/g/1/", NHENTAI)
    assert n == {"title": "(C86) [Circle (Artist)] Title [Chinese]", "image_url": "https://t3.nhentai.net/galleries/9/cover.jpg", "tags": ["touhou project", "anal", "chinese"],
                 "categories": {"touhou project": "parody", "anal": "tag", "chinese": "language"}}
    w = metadata.parse("https://www.wnacg.com/photos-index-aid-1.html", WNACG)
    assert w == {"title": "[作者] 標題 ｜ 副標", "image_url": "https://t3.qy0.ru/data/t/1/2/cover.png", "tags": ["女本位", "師生"]}
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
