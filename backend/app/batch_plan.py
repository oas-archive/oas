"""一次收錄的解析：單筆表單與批次用同一份規則（sites.py），先預覽、不連外，確認後才收錄。

規則只有三種：

    https://…            完整網址：任何網站都收；可以混在句子裡，一行可以有好幾個
    N123456 W123 JM456   編號簡寫：只有漫畫網站。逗號或空白可以分開多個；沒寫網站的純數字算 N

    N：                  分段：底下每行的數字都算這個網站，文字到這個網站搜尋（W：、JM：、漫畫：同理）
    123456

影片與其他網站沒有簡寫（番號、MISSAV：、動畫：這些），要貼完整網址；這類寫法會列出來並說明，不會默默略過。
不繞過任何網站的防護：讀不到的網站只給搜尋連結。
"""
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import Session

from . import alternatives, metadata
from .sites import COMIC_SEARCH, NO_SHORT_CODE_HEADERS, SEARCH_PAGES, SECTION_ALIASES, SHORT_CODE_URLS

COMICS = "漫畫"
MAX_ROWS = 300  # 和 /import/urls 的上限一致
SHORT_DIGITS = 3  # 3 位數以下的純數字常是頁數、序號，預設不勾
HEADER = re.compile(r"^\s*([^:：()（）]{1,20}?)\s*[：:]\s*$")
URL = re.compile(r"https?://[^\s\"'<>，、。；：！？「」『』【】（）《》]+", re.I)
SEPARATORS = re.compile(r"[\s,，、;；]+")
CODE = re.compile(r"^(?:(n|w|jm)\s*[#:：-]?\s*|#\s*)?(\d{1,7})$", re.I)
VIDEO_CODE = re.compile(r"^(?:fc2-ppv-\d{5,8}|[a-z]{2,6}-\d{2,5})$", re.I)
NO_VIDEO_CODES = "影片與其他網站沒有簡寫，請貼完整網址"
NEEDS_SITE = "「漫畫：」底下只能寫標題；編號要寫明是哪個網站（N、W、JM）"


def clean_url(url: str) -> str:
    """去掉尾端的英文標點與沒有成對的右括號（「看這本 https://…/123).」）。"""
    clean = re.sub(r"[.,;:!?'\"]+$", "", url)
    while clean.endswith(")") and clean.count("(") < clean.count(")"):
        clean = re.sub(r"[.,;:!?]+$", "", clean[:-1])
    return clean


def _section(label: str) -> str | None:
    """分段標題 → 網站簡寫（n、w、jm）或「漫畫」；不收簡寫的回傳空字串；不是分段標題回傳 None。"""
    text = label.strip().lower()
    if text == COMICS:
        return COMICS
    if text in SECTION_ALIASES:
        return SECTION_ALIASES[text]
    return "" if text in NO_SHORT_CODE_HEADERS else None


def _url_row(db: Session, line: str, url: str, short: bool = False) -> dict:
    try:
        info = metadata.describe(db, url, fetch=False)
    except metadata.MetadataError as error:
        return {"line": line, "kind": "invalid", "text": url, "reason": str(error)}
    return {"line": line, "kind": "url", "url": info["url"], "site": info["source_name"], "code": info["code"], "duplicate": info["duplicate"], "short": short}


def _search_row(line: str, text: str, section: str | None) -> dict:
    """標題文字：到這個分段的網站找；沒有分段就到全部漫畫網站。按了才搜尋。"""
    sites = COMIC_SEARCH if section in (None, COMICS) else [SHORT_CODE_URLS[section][0]]
    return {"line": line, "kind": "search", "text": text, "sites": sites}


def _line_rows(db: Session, line: str, section: str | None) -> list[dict]:
    if found := URL.findall(line):
        return [_url_row(db, line, clean_url(url)) for url in found]
    if section == "":
        return [{"line": line, "kind": "invalid", "text": line, "reason": NO_VIDEO_CODES}]
    tokens = [token for token in SEPARATORS.split(line) if token]
    # 「N 123456」：簡寫和編號中間有空白，先合起來；要整行都是編號才算，避免把文字裡的日期等數字誤當成作品
    joined = [match.group(0) for match in re.finditer(r"(?:(?:n|w|jm)\s*[#:：-]?\s*|#\s*)?\d{1,7}(?=$|[\s,，、;；])", line, re.I)]
    if joined and SEPARATORS.sub("", line).lower() == "".join(SEPARATORS.sub("", item).lower() for item in joined):
        tokens = joined
    if all(CODE.match(SEPARATORS.sub("", token)) for token in tokens):
        rows = []
        for token in tokens:
            prefix, digits = CODE.match(SEPARATORS.sub("", token)).groups()
            if not prefix and section == COMICS:
                rows.append({"line": line, "kind": "invalid", "text": token, "reason": NEEDS_SITE})
                continue
            key = (prefix or section or "n").lower()
            rows.append(_url_row(db, line, SHORT_CODE_URLS[key][1].format(digits), short=not prefix and not section and len(digits) <= SHORT_DIGITS))
        return rows
    if VIDEO_CODE.match(line):
        return [{"line": line, "kind": "invalid", "text": line, "reason": NO_VIDEO_CODES}]
    return [_search_row(line, line, section)]


def parse(db: Session, text: str) -> dict:
    """把貼上的文字拆成一項一項，不連外：rows 每項 kind 是 url、search 或 invalid；同一件只留第一次。"""
    rows, seen = [], set()
    repeats = dropped = 0
    section: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if len(rows) >= MAX_ROWS:
            dropped += 1
            continue
        header = HEADER.match(line)
        if header and (found := _section(header.group(1))) is not None:
            section = found
            if found == "":
                rows.append({"line": line, "kind": "invalid", "text": line, "reason": NO_VIDEO_CODES})
            continue
        for row in _line_rows(db, line, section):
            if row["kind"] == "url":
                key = (row["site"], metadata.work_key(row["site"], row["code"]))
                if key in seen:
                    repeats += 1
                    continue
                seen.add(key)
            if len(rows) >= MAX_ROWS:
                dropped += 1
                continue
            rows.append(row)
    return {"rows": rows, "repeats": repeats, "dropped": dropped, "limit": MAX_ROWS}


def search(rows: list[dict]) -> list[dict]:
    """對標題文字實際搜尋：有搜尋解析的網站列候選，其他給連結。每個網站各自排隊，不會一次打很多請求。"""
    jobs = []
    for row in rows:
        row["plans"] = []
        for name in row["sites"]:
            link = SEARCH_PAGES[name].format(urllib.parse.quote(row["text"]))
            searchable = name in (alternatives.NHENTAI_SOURCE, alternatives.WNACG_SOURCE)
            item = {"site": name, "mode": "search" if searchable else "link", "link": link, "results": []}
            row["plans"].append(item)
            if searchable:
                jobs.append((item, row["text"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for (item, _), (results, error) in zip(jobs, pool.map(lambda job: alternatives._search(job[0]["site"], job[1]), jobs)):
            item["results"], item["error"] = results, error
    return rows
