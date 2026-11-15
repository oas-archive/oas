"""tools/dev/backfill_pages.py：替既有漫畫補頁數。不連外：抓頁換成假函式、頁面是虛構的 HTML。"""
import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "dev"
if not (TOOLS / "backfill_pages.py").exists():  # 公開版沒有 tools/dev：整個檔略過（同 test_full_sync 的 needs_tools）
    pytest.skip("tools/dev 不在這個版本", allow_module_level=True)
sys.path.insert(0, str(TOOLS))
import backfill_pages as bf  # noqa: E402

from app.pagetext import MetadataError  # noqa: E402

NH, WN = "nhentai", "紳士漫畫"


def make_db(tmp_path, rows, name="t.db"):
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.execute("create table resources (id integer primary key, code text, source_name text, title text, kind text, pages integer, link_dead integer, updated_at text)")
    conn.executemany("insert into resources (id, code, source_name, title, kind, pages, link_dead, updated_at) values (?,?,?,?,?,?,?,'T0')", rows)
    conn.commit()
    conn.close()
    return path


def snapshot(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("select id, title, kind, pages, link_dead, updated_at from resources order by id").fetchall()
    finally:
        conn.close()


def pages_of(path):
    return {rid: pages for rid, _t, _k, pages, _d, _u in snapshot(path)}


def go(path, fetch, **kwargs):
    kwargs.setdefault("parse", lambda url, page: {"pages": int(page)} if page.isdigit() else {})
    return bf.run(path, fetch=fetch, sleep=lambda _s: None, out=lambda _t: None, **kwargs)


def comics(count, source=NH, start=1):
    return [(i, f"90000{i}", source, "t", "漫畫", None, 0) for i in range(start, start + count)]


def test_fills_only_null_and_leaves_other_fields_alone(tmp_path):
    path = make_db(tmp_path, [(1, "900001", NH, "虛構甲", "漫畫", None, 0), (2, "900002", NH, "虛構乙", "漫畫", 24, 0), (3, "900003", WN, "虛構丙", "", None, 0)])
    before = snapshot(path)
    result = go(path, lambda url: "48")
    assert result["done"] == 2 and result["stopped"] is None
    assert pages_of(path) == {1: 48, 2: 24, 3: 48}  # 已有的 24 不動
    for old, new in zip(before, snapshot(path)):  # 只有 pages 變，標題、種類、失效、updated_at 都沒動
        assert old[:3] == new[:3] and old[4:] == new[4:]


def test_skips_non_comics_dead_other_sources_and_filled(tmp_path):
    path = make_db(tmp_path, [(1, "900001", NH, "a", "影片", None, 0), (2, "900002", NH, "b", "漫畫", None, 1),
                              (3, "900003", "禁漫天堂", "c", "漫畫", None, 0), (4, "900004", NH, "d", "漫畫", 10, 0), (5, "900005", NH, "e", "漫畫", None, 0)])
    seen = []
    result = go(path, lambda url: seen.append(url) or "30")
    assert seen == ["https://nhentai.net/g/900005/"]
    assert result["total"] == 1 and pages_of(path) == {1: None, 2: None, 3: None, 4: 10, 5: 30}


def test_source_and_limit(tmp_path):
    path = make_db(tmp_path, comics(3, NH, 1) + comics(3, WN, 4))
    seen = []
    go(path, lambda url: seen.append(url) or "5", source="wnacg", limit=2)
    assert seen == ["https://www.wnacg.com/photos-index-aid-900004.html", "https://www.wnacg.com/photos-index-aid-900005.html"]
    assert [rid for rid, p in pages_of(path).items() if p] == [4, 5]


@pytest.mark.parametrize("status", [429, 403, 503])
def test_stops_on_block_statuses(tmp_path, status):
    path = make_db(tmp_path, comics(3))
    calls = []

    def fetch(url):
        calls.append(url)
        if len(calls) == 2:
            raise MetadataError("被擋", status=status)
        return "20"
    result = go(path, fetch)
    assert len(calls) == 2 and result["done"] == 1 and "被擋" in result["stopped"]
    assert pages_of(path) == {1: 20, 2: None, 3: None}


def test_stops_on_protection_flag_without_status(tmp_path):
    path = make_db(tmp_path, comics(3))

    def fetch(url):
        raise MetadataError("有防護", blocked=True)
    result = go(path, fetch)
    assert result["stopped"] and result["skipped"] == 0 and pages_of(path) == {1: None, 2: None, 3: None}


def test_stops_on_cloudflare_challenge_page(tmp_path):
    path = make_db(tmp_path, comics(2))
    result = go(path, lambda url: "<html><title>Just a moment...</title></html>")
    assert "挑戰頁" in result["stopped"] and result["done"] == 0 and pages_of(path) == {1: None, 2: None}


def test_normal_big_page_with_cloudflare_script_is_not_a_challenge(tmp_path):
    path = make_db(tmp_path, comics(1))
    page = '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>' + "x" * 300_000
    result = go(path, lambda url: page, parse=lambda url, p: {"pages": 31})
    assert result["stopped"] is None and pages_of(path) == {1: 31}


def test_isolated_errors_only_skip(tmp_path):
    path = make_db(tmp_path, comics(7))
    calls = []

    def flaky(url):  # 失敗、成功、失敗、成功…：不連續，不停
        calls.append(url)
        if len(calls) % 2:
            raise MetadataError("連線失敗：逾時")
        return "9"
    result = go(path, flaky)
    assert result["stopped"] is None and result["done"] == 3 and result["skipped"] == 4


def test_stops_after_three_errors_in_a_row(tmp_path):
    path = make_db(tmp_path, comics(6))
    seen = []

    def failing(url):
        seen.append(url)
        raise MetadataError("連線失敗：逾時")
    result = go(path, failing)
    assert len(seen) == 3 and "連續 3 次" in result["stopped"] and result["skipped"] == 3


def test_missing_page_or_gone_is_skipped_not_stopped(tmp_path):
    path = make_db(tmp_path, comics(3))

    def fetch(url):
        if url.endswith("900001/"):
            raise MetadataError("來源回應 HTTP 404", status=404)
        return "沒有頁數欄" if url.endswith("900002/") else "12"
    result = go(path, fetch)
    assert result["done"] == 1 and result["skipped"] == 2 and result["stopped"] is None
    assert pages_of(path) == {1: None, 2: None, 3: 12}
    assert snapshot(path)[0][4] == 0  # 沒有標失效


def test_resume_after_interrupt(tmp_path):
    path = make_db(tmp_path, comics(4))
    calls = []

    def interrupted(url):
        calls.append(url)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return "7"
    first = go(path, interrupted)
    assert "中斷" in first["stopped"] and first["done"] == 2
    seen = []
    second = go(path, lambda url: seen.append(url) or "8")
    assert second["done"] == 2 and len(seen) == 2 and pages_of(path) == {1: 7, 2: 7, 3: 8, 4: 8}


def test_does_not_overwrite_value_filled_meanwhile(tmp_path):
    path = make_db(tmp_path, comics(1))

    def fetch(url):  # 讀頁的當下，別處（例如服務）剛好補了手動值
        conn = sqlite3.connect(path)
        conn.execute("update resources set pages = 99 where id = 1")
        conn.commit()
        conn.close()
        return "20"
    result = go(path, fetch)
    assert result["done"] == 0 and pages_of(path) == {1: 99}


def test_real_parser_reads_nhentai_and_wnacg_pages(tmp_path):
    nh = ('<html><head><title>虛構作品 » nhentai</title></head><body><section id="tags"><div class="tag-container field-name">Pages: '
          '<span class="tags"><a href="/search/?q=pages%3A37" class="tag"><span class="name">37</span></a></span></div></section></body></html>')
    wn = "<html><head><title>虛構作品 - 紳士漫畫</title></head><body><label>頁數：24P</label></body></html>"
    path = make_db(tmp_path, [(1, "900001", NH, "a", "漫畫", None, 0), (2, "900002", WN, "b", "漫畫", None, 0)])
    result = bf.run(path, fetch=lambda url: nh if "nhentai" in url else wn, sleep=lambda _s: None, out=lambda _t: None)
    assert result["done"] == 2 and pages_of(path) == {1: 37, 2: 24}


def test_dry_run_does_not_touch_network_or_database(tmp_path, monkeypatch, capsys):
    path = make_db(tmp_path, comics(1))
    monkeypatch.setattr(bf, "default_fetch", lambda url: pytest.fail("預演不該連外"))
    monkeypatch.setattr(sys, "argv", ["backfill_pages.py", "--db", str(path)])
    before = snapshot(path)
    assert bf.main() == 0
    assert "1 件" in capsys.readouterr().out and snapshot(path) == before
