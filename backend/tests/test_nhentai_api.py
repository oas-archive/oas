"""nhentai 2026-11 改版：作品頁不再含標題封面，改讀官方 API（v2）。資料都是虛構的。"""
import json
import urllib.error
from email.message import Message

import pytest

from app import linkcheck, metadata, refresh
from app.database import SessionLocal, Source

GALLERY = {
    "id": 900201, "media_id": "9002010",
    "title": {"english": "[Example Circle] Fictional Garden [Chinese]", "japanese": "[サンプル] 架空の庭", "pretty": "Fictional Garden"},
    "cover": {"path": "galleries/9002010/cover.webp", "width": 350, "height": 500},
    "thumbnail": {"path": "galleries/9002010/thumb.webp", "width": 250, "height": 350},
    "num_pages": 24,
    "tags": [
        {"id": 1, "type": "category", "name": "doujinshi"},
        {"id": 2, "type": "language", "name": "chinese"},
        {"id": 3, "type": "tag", "name": "fictional tag"},
        {"id": 4, "type": "artist", "name": "example artist"},
        {"id": 5, "type": "parody", "name": "original"},
        {"id": 6, "type": "tag", "name": "another tag"},
    ],
}


class Response:
    def __init__(self, body: bytes, url: str):
        self.body, self.url = body, url
        self.headers = Message()
        self.headers["Content-Type"] = "application/json; charset=utf-8"

    def read(self, n=-1): return self.body
    def geturl(self): return self.url
    def __enter__(self): return self
    def __exit__(self, *args): return False


def http_error(url: str, code: int, server: str = "cloudflare"):
    headers = Message()
    headers["Server"] = server
    return urllib.error.HTTPError(url, code, "error", headers, None)


def test_api_url_only_for_gallery_pages():
    assert metadata.api_url("https://nhentai.net/g/900201/") == "https://nhentai.net/api/v2/galleries/900201"
    assert metadata.api_url("https://www.nhentai.net/g/900201/1/") == "https://nhentai.net/api/v2/galleries/900201"
    assert metadata.api_url("https://nhentai.net/search/?q=x") == ""
    assert metadata.api_url("https://www.wnacg.com/photos-index-aid-900201.html") == ""


def test_parse_reads_api_json():
    data = metadata.parse("https://nhentai.net/g/900201/", json.dumps(GALLERY))
    assert data["title"] == "[Example Circle] Fictional Garden [Chinese]"
    assert data["image_url"] == "https://t3.nhentai.net/galleries/9002010/cover.webp"
    assert data["pages"] == 24
    # 依舊作品頁的順序：原作、標籤、作者、語言、分類；同一類保持 API 的順序
    assert data["tags"] == ["original", "fictional tag", "another tag", "example artist", "chinese", "doujinshi"]
    assert data["categories"]["example artist"] == "artist" and data["categories"]["doujinshi"] == "category"


def test_parse_api_falls_back_to_japanese_title_and_tolerates_bad_json():
    gallery = {**GALLERY, "title": {"english": "", "japanese": "架空の庭", "pretty": ""}}
    assert metadata.parse("https://nhentai.net/g/900201/", json.dumps(gallery))["title"] == "架空の庭"
    assert metadata.parse("https://nhentai.net/g/900201/", "{not json")["title"] == ""
    assert metadata.parse("https://nhentai.net/g/900201/", "[]")["image_url"] == ""


def test_get_reads_api_instead_of_page(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append((request.full_url, request.get_header("Accept")))
        return Response(json.dumps(GALLERY).encode(), request.full_url)

    monkeypatch.setattr(metadata, "check_url", lambda url: None)
    monkeypatch.setattr(metadata, "safe_urlopen", urlopen)
    body = metadata._get("https://nhentai.net/g/900201/")
    assert seen == [("https://nhentai.net/api/v2/galleries/900201", "application/json")]
    assert json.loads(body)["id"] == 900201


def test_api_not_found_is_gone_and_429_is_rate_limit_not_protection(monkeypatch):
    def not_found(request, timeout): raise http_error(request.full_url, 404)
    def too_many(request, timeout): raise http_error(request.full_url, 429)

    monkeypatch.setattr(metadata, "check_url", lambda url: None)
    monkeypatch.setattr(metadata, "safe_urlopen", not_found)
    with pytest.raises(metadata.MetadataError) as gone:
        metadata._get("https://nhentai.net/g/900202/")
    assert gone.value.gone
    monkeypatch.setattr(metadata, "safe_urlopen", too_many)
    with pytest.raises(metadata.MetadataError) as limited:
        metadata._get("https://nhentai.net/g/900203/")
    # 限流：稍後退避重試；不標失效，也不叫你改用書籤（API 不是防護頁）
    assert limited.value.rate_limited and not limited.value.gone and not limited.value.blocked and "書籤" not in str(limited.value)


def test_refresh_does_not_mark_gone_when_rate_limited(monkeypatch):
    def too_many(url): raise metadata.MetadataError("來源回應 HTTP 429", status=429)

    marked = []
    monkeypatch.setattr(refresh, "job_is_current", lambda resource_id, url: True)
    monkeypatch.setattr(refresh, "mark_gone", marked.append)
    monkeypatch.setattr(metadata, "_get", too_many)
    job = type("Job", (), {"resource_id": 1, "url": "https://nhentai.net/g/900204/", "mode": "all"})()
    assert refresh.handle(job) == "limited" and marked == []


def test_linkcheck_age_gate_page_counts_as_alive(monkeypatch):
    """改版後作品頁是年齡確認的空殼（HTTP 200）：巡檢仍算還在；只有 404 才算失效，403／429 不算。"""
    monkeypatch.setattr(linkcheck, "check_url", lambda url: None)
    monkeypatch.setattr(linkcheck, "safe_urlopen", lambda request, timeout: Response(b"<h1>Are you 18 or older?</h1>", request.full_url))
    assert linkcheck.check("https://nhentai.net/g/900205/") == "alive"
    for code, expected in ((403, "blocked"), (429, "blocked"), (404, "dead")):
        def fail(request, timeout, code=code): raise http_error(request.full_url, code)
        monkeypatch.setattr(linkcheck, "safe_urlopen", fail)
        assert linkcheck.check("https://nhentai.net/g/900205/") == expected


def test_describe_prefers_api_over_bookmarklet_page(client, monkeypatch):
    """書籤或手機送來的是年齡確認的空殼頁：有官方 API 的網站一律讀 API。"""
    with SessionLocal() as db:
        db.add(Source(name="N網", template="https://nhentai.net/g/{}/"))
        db.commit()
    calls = []
    monkeypatch.setattr(metadata, "_get", lambda url: calls.append(url) or json.dumps(GALLERY))
    data = client.post("/api/metadata", json={"url": "https://nhentai.net/g/900201/", "fetch": True,
                                              "html": "<html><h1>Are you 18 or older?</h1></html>"}).json()
    assert calls == ["https://nhentai.net/g/900201/"] and data["title"].startswith("[Example Circle]") and data["pages"] == 24
