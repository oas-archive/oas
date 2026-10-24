"""來源失效時找替代來源：用作品標題到 nhentai（官方 v2 搜尋 API）與紳士漫畫搜尋同名作品。

只做一般的搜尋請求（誠實的 User-Agent、不繞過任何防護）；被擋或逾時的網站略過，不影響其他網站的結果。
"""
import html
import json
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import metadata
from .database import Resource

LIMIT_PER_SITE = 8
NHENTAI_SOURCE, WNACG_SOURCE = "nhentai", "紳士漫畫"
NOISE = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）|\bComics\b|\bComic\b|\bDigital\b", re.I)


def search_query(title: str) -> str:
    """從收藏標題整理出搜尋關鍵字：去掉漢化組、場次、Comics 等，多語標題只取第一段。"""
    text = NOISE.sub(" ", metadata.clean_title(title or ""))
    first = re.split(r"\s*[/｜|]\s*", text.strip())[0]
    return re.sub(r"\s+", " ", first).strip()[:80]


def parse_nhentai_search(body: str) -> list[dict]:
    rows = []
    for item in json.loads(body).get("result", [])[:LIMIT_PER_SITE]:
        thumb = item.get("thumbnail") or ""
        rows.append({
            "source_name": NHENTAI_SOURCE, "code": str(item["id"]), "url": f"https://nhentai.net/g/{item['id']}/",
            "title": item.get("english_title") or item.get("japanese_title") or "",
            "thumb": f"https://t3.nhentai.net/{thumb}" if thumb and not thumb.startswith("http") else thumb,
            "pages": item.get("num_pages"),
        })
    return rows


def parse_wnacg_search(body: str) -> list[dict]:
    rows, seen = [], set()
    pattern = r'<a href="/photos-index-aid-(\d+)\.html" title="([^"]*)"><img[^>]+src="([^"]+)"'
    for aid, title, thumb in re.findall(pattern, body):
        if aid in seen:
            continue
        seen.add(aid)
        rows.append({
            "source_name": WNACG_SOURCE, "code": aid, "url": f"https://www.wnacg.com/photos-index-aid-{aid}.html",
            "title": html.unescape(title), "thumb": re.sub(r"^/+", "https://", thumb), "pages": None,
        })
        if len(rows) >= LIMIT_PER_SITE:
            break
    return rows


def _search(name: str, query: str) -> tuple[list[dict], str]:
    quoted = urllib.parse.quote(query)
    try:
        if name == NHENTAI_SOURCE:
            return parse_nhentai_search(metadata._get(f"https://nhentai.net/api/v2/search?query={quoted}")), ""
        return parse_wnacg_search(metadata._get(f"https://www.wnacg.com/search/?q={quoted}&f=_all&s=create_time_DESC&syn=yes")), ""
    except (metadata.MetadataError, ValueError, KeyError) as error:
        return [], f"{name}：{error}"


def find(db: Session, resource: Resource, query: str | None = None) -> dict:
    query = (query or "").strip() or search_query(resource.title or resource.code)
    if not query:
        return {"query": "", "results": [], "errors": ["沒有可搜尋的標題，請輸入關鍵字"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda name: _search(name, query), (NHENTAI_SOURCE, WNACG_SOURCE)))
    results = [row for rows, _ in outcomes for row in rows]
    owned = {(row.source_name, row.code): row.id for row in db.execute(select(Resource.source_name, Resource.code, Resource.id))}
    for row in results:
        row["owned_id"] = owned.get((row["source_name"], row["code"]))
    return {"query": query, "results": results, "errors": [message for _, message in outcomes if message]}
