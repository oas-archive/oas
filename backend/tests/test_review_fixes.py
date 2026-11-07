"""2026-11 程式審查找到的問題：取代還原、復原、巡檢、封面下載、深潛區。"""
import http.client

import pytest

from app import covers, fetcher, linkcheck, main, metadata, refresh, scheduler


def add(client, code, **fields):
    return client.post("/api/resources", json={"code": code, "source_name": "站", "title": code, **fields}).json()


def test_replace_clears_undo_and_jobs_of_old_ids(client):
    a = add(client, "a")
    backup = client.get("/api/backup").json()
    token = client.post("/api/resources/batch", json={"ids": [a["id"]], "action": "status", "status": "棄坑"}).json()["undo"]
    scheduler.enqueue("linkcheck", [(a["id"], "https://example.com/a")])
    assert linkcheck.state["running"]
    client.post("/api/restore", params={"mode": "replace"}, json=backup)
    assert client.post(f"/api/undo/{token}").status_code == 404
    assert scheduler.pending("linkcheck") == 0 and not linkcheck.state["running"]


def test_undo_removed_tag_after_it_was_added_back(client):
    a = add(client, "a", tags=["甲"])
    token = client.post("/api/tags/remove-many", json={"names": ["甲"]}).json()["undo"]
    client.patch(f"/api/resources/{a['id']}", json={"code": "a", "source_name": "站", "tags": ["甲"]})
    assert client.post(f"/api/undo/{token}").status_code == 200
    assert client.get(f"/api/resources/{a['id']}").json()["tags"] == ["甲"]


def test_undo_batch_keeps_updated_at(client):
    a = add(client, "a")
    before = client.get(f"/api/resources/{a['id']}").json()["updated_at"]
    token = client.post("/api/resources/batch", json={"ids": [a["id"]], "action": "rating", "rating": 3}).json()["undo"]
    client.post(f"/api/undo/{token}")
    assert client.get(f"/api/resources/{a['id']}").json()["updated_at"] == before


def test_linkcheck_skips_job_whose_url_changed(client, monkeypatch):
    item = add(client, "https://example.com/old")
    scheduler.enqueue("linkcheck", [(item["id"], "https://example.com/old")])
    client.patch(f"/api/resources/{item['id']}", json={"code": "https://example.com/new", "source_name": "站"})
    monkeypatch.setattr(linkcheck, "check", lambda url: pytest.fail(f"不該檢查舊網址 {url}"))
    scheduler.run_until_idle()
    row = client.get(f"/api/resources/{item['id']}").json()
    assert not row["link_dead"] and scheduler.pending("linkcheck") == 0


def test_linkcheck_skips_job_of_deleted_work(client, monkeypatch):
    item = add(client, "https://example.com/gone")
    scheduler.enqueue("linkcheck", [(item["id"], "https://example.com/gone")])
    client.delete(f"/api/resources/{item['id']}")
    monkeypatch.setattr(linkcheck, "check", lambda url: pytest.fail("作品已刪除"))
    scheduler.run_until_idle()
    assert scheduler.pending("linkcheck") == 0


def test_changing_url_forgets_link_check(client):
    item = add(client, "https://example.com/a")
    linkcheck._mark(item["id"], False)
    from app.database import Resource, SessionLocal

    with SessionLocal() as db:
        assert db.get(Resource, item["id"]).link_checked_at is not None
    client.patch(f"/api/resources/{item['id']}", json={"code": "https://example.com/b", "source_name": "站"})
    with SessionLocal() as db:
        assert db.get(Resource, item["id"]).link_checked_at is None


def test_page_url_accepts_uppercase_scheme():
    class Row:
        code = "HTTP://example.com/a"

    assert refresh.page_url(Row, "https://site/{}") == "HTTP://example.com/a"


@pytest.mark.parametrize("url", ["http://example.com:abc/a.jpg", "http://[bad/a.jpg", "http://" + "a" * 70 + ".com/a.jpg"])
def test_malformed_cover_urls_are_cover_errors(url):
    with pytest.raises(covers.CoverError):
        covers.check_url(url)


@pytest.mark.parametrize("failure", [http.client.InvalidURL("URL can't contain spaces"), http.client.IncompleteRead(b""), ValueError("壞網址")])
def test_download_turns_http_errors_into_cover_errors(monkeypatch, failure):
    def boom(request, timeout):
        raise failure

    monkeypatch.setattr(covers, "check_url", lambda url: None)
    monkeypatch.setattr(covers, "safe_urlopen", boom)
    with pytest.raises(covers.CoverError):
        covers.download("https://example.com/a b.jpg")


def test_page_read_turns_http_errors_into_metadata_errors(monkeypatch):
    def boom(request, timeout):
        raise http.client.InvalidURL("URL can't contain spaces")

    monkeypatch.setattr(metadata, "check_url", lambda url: None)
    monkeypatch.setattr(metadata, "safe_urlopen", boom)
    with pytest.raises(metadata.MetadataError):
        metadata._get("https://example.com/a b")


def test_cover_batch_counts_everything_when_one_fails(client, monkeypatch):
    def ensure(db, url, force=False):
        raise RuntimeError("意外")

    monkeypatch.setattr(covers, "ensure_cover", ensure)
    covers.run_batch(["https://example.com/1.jpg", "https://example.com/2.jpg"])
    assert covers.batch_state["done"] == 2 and covers.batch_state["failed"] == 2 and not covers.batch_state["running"]


def test_deep_dive_endpoint(client):
    assert client.get("/api/deep-dive").json() is None
    client.post("/api/restore", json={"format": "oas-backup", "version": 5, "sources": [], "resources": [{"code": "old", "source_name": "站", "title": "舊", "status": "未讀", "created_at": "2020-01-01T00:00:00+00:00"}]})
    assert client.get("/api/deep-dive").json()["code"] == "old"


def test_library_can_list_more_than_a_thousand(client):
    assert client.get("/api/resources", params={"limit": 5000}).status_code == 200


def test_one_malformed_url_does_not_fail_the_whole_import(client):
    result = client.post("/api/import/urls", json={"urls": ["http://[bad", "https://example.com/ok"]}).json()
    assert result["invalid"] == ["http://[bad"] and len(result["created"]) == 1


def test_malformed_stored_url_does_not_break_pages(client):
    client.post("/api/restore", json={"format": "oas-backup", "version": 5, "sources": [], "resources": [{"code": "http://[bad", "source_name": "站", "title": "壞網址"}]})
    assert client.get("/api/duplicates").status_code == 200
    assert metadata.page_kind("http://[bad") == "" and not metadata.site_page("http://[bad") and not metadata.mirrored("http://[bad")
    assert fetcher.host_of("http://[bad") == ""


def test_broken_link_in_page_does_not_fail_parse():
    page = '<meta property="og:title" content="標題"><meta property="og:image" content="http://[bad">'
    assert metadata.parse("https://example.com/a", page)["image_url"] == ""


def test_rule_merges_tags_that_differ_only_in_case(client):
    a, b = add(client, "a", tags=["Star Harbor"]), add(client, "b", tags=["star harbor"])
    response = client.put("/api/tags/rules", json={"alias": "star harbor", "canonical": "星港傳說"})
    assert response.status_code == 200 and response.json()["merged"] == 2
    assert client.get(f"/api/resources/{a['id']}").json()["tags"] == ["星港傳說"]
    assert client.get(f"/api/resources/{b['id']}").json()["tags"] == ["星港傳說"]


def test_remove_many_blocks_names_that_differ_only_in_case(client):
    add(client, "a", tags=["Abc"])
    add(client, "b", tags=["abc"])
    assert client.post("/api/tags/remove-many", json={"names": ["Abc", "abc"], "block": True}).status_code == 200


def test_refresh_skips_job_whose_url_changed(client, monkeypatch):
    item = add(client, "https://example.com/old")
    scheduler.enqueue("refresh", [(item["id"], "https://example.com/old")])
    client.patch(f"/api/resources/{item['id']}", json={"code": "https://example.com/new", "source_name": "站"})
    monkeypatch.setattr(metadata, "_get", lambda url: pytest.fail(f"不該讀舊網址 {url}"))
    scheduler.run_until_idle()
    assert scheduler.pending("refresh") == 0


def test_refresh_uses_full_url_code_as_is():
    class Row:
        code = "https://example.com/a"

    assert refresh.page_url(Row, "https://site/{}") == "https://example.com/a"


def test_fill_page_handles_missing_work(client):
    item = add(client, "https://www.wnacg.com/photos-index-aid-1.html")
    result = client.post("/api/attention/fill-page", json={"id": item["id"], "html": "您要訪問的漫畫不存在！"})
    assert result.status_code == 200 and result.json()["filled"] is False


def test_startup_step_failure_is_only_logged():
    def boom():
        raise RuntimeError("壞資料")

    main._safely(boom)


@pytest.mark.parametrize("url,allowed", [
    ("https://example.com/a.jpg", True), ("http://127.0.0.1:9999/a.jpg", False), ("http://localhost:9999/a.jpg", False),
    ("http://192.168.1.1/a.jpg", False), ("javascript:alert(1)", False), ("http://[bad/a.jpg", False),
])
def test_cover_redirect_only_to_public_addresses(url, allowed):
    assert covers.redirectable(url) is allowed


def test_uncached_cover_never_redirects_to_this_device(client):
    item = add(client, "a", image_url="http://127.0.0.1:9999/a.jpg")
    assert client.get(f"/api/covers/{item['id']}", follow_redirects=False).status_code == 404


def test_side_panel_summaries_follow_data_changes(client):
    before = client.get("/api/attention/summary").json()["total"]
    add(client, "https://example.com/new")
    assert client.get("/api/attention/summary").json()["total"] == before + 1


def test_rule_merging_case_variants_undoes_all_at_once(client):
    a, b = add(client, "a", tags=["Foo"]), add(client, "b", tags=["FOO"])
    c = add(client, "c", tags=["目標"])
    saved = client.put("/api/tags/rules", json={"alias": "foo", "canonical": "目標"}).json()
    assert saved["merged"] == 2 and saved["final"] == "目標"
    assert client.get(f"/api/resources/{a['id']}").json()["tags"] == ["目標"]
    assert client.post(f"/api/undo/{saved['undo']}").json()["restored"] == 2
    assert client.get(f"/api/resources/{a['id']}").json()["tags"] == ["Foo"]
    assert client.get(f"/api/resources/{b['id']}").json()["tags"] == ["FOO"]
    assert client.get(f"/api/resources/{c['id']}").json()["tags"] == ["目標"]
    assert all(rule["alias"] != "foo" for rule in client.get("/api/tags/rules").json()["aliases"])
    assert client.post(f"/api/undo/{saved['undo']}").status_code == 404
    # 目標是這次才建立的：復原後不留下空標籤
    second = client.put("/api/tags/rules", json={"alias": "foo", "canonical": "新名稱"}).json()
    client.post(f"/api/undo/{second['undo']}")
    assert "新名稱" not in client.get("/api/tags").json()


def test_stats_counts_months_in_users_time_zone(client):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from app.database import Resource, SessionLocal

    a = add(client, "a")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # UTC 這個月第一天的前一小時＝上個月最後一天 23:00（UTC）；台灣時間已經是這個月 1 日早上 7 點
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        db.execute(update(Resource).where(Resource.id == a["id"]).values(created_at=first - timedelta(hours=1)))
        db.commit()
    utc, taipei = client.get("/api/stats").json(), client.get("/api/stats", params={"tz": 480}).json()
    this_month = taipei["months"].index((now + timedelta(hours=8)).strftime("%Y-%m"))
    if now.day == 1 and now.hour >= 16:  # 台灣已經是下個月：這一筆算上個月，這個測試比不出差別
        return
    assert taipei["added"][this_month] == 1
    assert utc["added"][utc["months"].index(now.strftime("%Y-%m"))] == 0
    assert client.get("/api/stats", params={"tz": 9999}).status_code == 422


def _cover_entry(tmp_path, monkeypatch, name="a.png", mode="RGB", size=(800, 1200)):
    from PIL import Image

    from app.database import CoverCache

    monkeypatch.setattr(covers, "COVER_DIR", tmp_path)
    Image.new(mode, size, (200, 30, 30, 120) if mode == "RGBA" else (200, 30, 30)).save(tmp_path / name)
    return CoverCache(url_hash=name.split(".")[0], url="http://example.com/" + name, status="ok", filename=name, content_type="image/png")


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_thumbnail_uses_jpeg_when_pillow_has_no_webp(tmp_path, monkeypatch, mode):
    """手機的 Pillow 沒有 WebP：要做出 JPEG 縮圖，不能退回整張原圖。"""
    from PIL import Image, features

    monkeypatch.setattr(features, "check", lambda name: False)
    entry = _cover_entry(tmp_path, monkeypatch, f"{mode}.png", mode)
    path, media_type = covers.thumbnail_file(entry, 240)
    assert media_type == "image/jpeg" and path.name == f"{mode}_w240.jpg"
    with Image.open(path) as image:
        assert image.format == "JPEG" and image.width == 240
    assert covers.thumbnail_file(entry, 240)[0] == path
    assert not list(tmp_path.glob("*.part"))


def test_thumbnail_failure_is_not_retried(tmp_path, monkeypatch):
    entry = _cover_entry(tmp_path, monkeypatch, "broken.png")
    (tmp_path / "broken.png").write_bytes(b"not an image")
    assert covers.thumbnail_file(entry, 240) == (tmp_path / "broken.png", "image/png")
    assert ("broken", 240) in covers._thumb_failed
    from PIL import Image

    monkeypatch.setattr(Image, "open", lambda *args: pytest.fail("做不出來的不該再開檔"))
    assert covers.thumbnail_file(entry, 240)[0].name == "broken.png"


def test_backfill_makes_small_thumbnails_once(client, tmp_path, monkeypatch):
    from app.database import SessionLocal

    entry = _cover_entry(tmp_path, monkeypatch, "fill.png")
    with SessionLocal() as db:
        db.add(entry)
        db.commit()
    assert covers.backfill_thumbnails(pause=0) == 1
    assert list(tmp_path.glob("fill_w240.*"))
    assert covers.backfill_thumbnails(pause=0) == 0
