"""來源失效時找替代來源：用作品標題到 nhentai（官方 v2 搜尋 API）與紳士漫畫搜尋同名作品；
代碼是純數字時，另外用同一個編號直接讀這兩個網站的作品頁，禁漫則看封面圖片在不在（作品頁有防護讀不到）。
禁漫的封面只能當線索：已下架、不存在的作品與章節編號也有封面圖（實測 19 件中 12 件不存在、5 件是章節編號），
所以標成 unconfirmed，要你點開確認。編號常被記到錯的網站，例如紳士或禁漫的編號記成 N 網。
沒有標題的作品不做關鍵字搜尋（拿編號當關鍵字只會搜到不相干的作品），只比對同編號。

只做一般的搜尋請求（誠實的 User-Agent、不繞過任何防護）；被擋或逾時的網站略過，不影響其他網站的結果。
"""
import html
import json
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import fetcher, metadata
from .database import Resource, Source
from .sites import JM_SOURCE, NHENTAI_SOURCE, WNACG_SOURCE

LIMIT_PER_SITE = 8
BULK_INTERVAL = 1.0  # 一次比對全部時，同一個網站每件間隔的秒數（各網站同時進行）
SAME_CODE_SOURCES = (NHENTAI_SOURCE, WNACG_SOURCE, JM_SOURCE)
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


def _same_code(name: str, template: str, code: str) -> tuple[list[dict], str, bool]:
    """用同一個編號讀另一個網站的作品頁；網站說找不到就不列。回傳（候選, 錯誤訊息, 是否被限流）。"""
    url = template.replace("{}", code)
    try:
        data = metadata.parse(url, metadata._get(url))
    except metadata.MetadataError as error:
        return [], "" if error.gone else f"{name}：{error}", error.rate_limited
    if not data.get("title"):
        return [], "", False
    return [{"source_name": name, "code": code, "title": data["title"], "url": url, "thumb": data.get("image_url", ""), "same_code": True}], "", False


def _jm_cover(template: str, code: str) -> tuple[list[dict], str, bool]:
    """禁漫：作品頁有防護，改看封面圖片在不在。"""
    url, image = template.replace("{}", code), metadata.known_cover(template.replace("{}", code))
    if not image:
        return [], "", False
    try:
        found = metadata.cover_exists(image)
    except metadata.MetadataError as error:
        return [], f"{JM_SOURCE}：{error}", error.rate_limited
    if not found:
        return [], "", False
    # 只確認到封面：禁漫的封面伺服器連已下架、不存在的作品都留著圖，章節編號也有圖，所以不能當成作品存在
    return [{"source_name": JM_SOURCE, "code": code, "title": f"禁漫 {code}：只確認到封面，作品可能已下架或是章節編號，請先點開確認",
             "url": url, "thumb": image, "same_code": True, "unconfirmed": True}], "", False


def same_code_tasks(db: Session, resource: Resource) -> list[tuple[str, str, object]]:
    """同一個編號在其他網站（N 網、紳士、禁漫）的查詢工作：[(網站, 網址, 工作)]；代碼不是純數字時沒有。"""
    code = resource.code.strip()
    if not code.isdigit():
        return []
    templates = dict(db.execute(select(Source.name, Source.template).where(Source.name.in_(SAME_CODE_SOURCES))).all())
    tasks = []
    for name, template in templates.items():
        if name == resource.source_name or "{}" not in template:
            continue
        probe = _jm_cover if name == JM_SOURCE else lambda template, code, name=name: _same_code(name, template, code)
        url = metadata.known_cover(template.replace("{}", code)) if name == JM_SOURCE else template.replace("{}", code)
        tasks.append((name, url, lambda probe=probe, template=template: probe(template, code)))
    return tasks


def _owned(db: Session, results: list[dict]) -> None:
    owned = {(row.source_name, row.code): row.id for row in db.execute(select(Resource.source_name, Resource.code, Resource.id))}
    for row in results:
        row["owned_id"] = owned.get((row["source_name"], row["code"]))


def find(db: Session, resource: Resource, query: str | None = None) -> dict:
    query = (query or "").strip() or search_query(resource.title)
    tasks = [task for _, _, task in same_code_tasks(db, resource)]
    if not query and not tasks:
        return {"query": "", "results": [], "errors": ["沒有標題可搜尋，請輸入關鍵字（作品名、作者或社團）"]}
    if query:
        tasks += [lambda name=name: (*_search(name, query), False) for name in (NHENTAI_SOURCE, WNACG_SOURCE)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda task: task(), tasks))
    results = [row for rows, _, _ in outcomes for row in rows]
    _owned(db, results)
    return {"query": query, "results": results, "errors": [message for _, message, _ in outcomes if message]}


def same_code_many(db: Session, resources: list[Resource]) -> dict:
    """待整理「來源失效」一次比對：每件作品的同編號候選（不做關鍵字搜尋）。

    同一個網站依序查、每件間隔 BULK_INTERVAL 秒（各網站同時進行）；網站正在冷卻（背景補資料被限流）或這次被限流時，
    那個網站剩下的作品不再查，記成錯誤，並通知 fetcher 讓背景補資料也一起放慢。查不到與查失敗分開回報。
    回傳 {"results": {作品 id: [候選]}, "errors": {作品 id: [訊息]}}。
    """
    by_site: dict[str, list[tuple[int, str, object]]] = {}
    for resource in resources:
        for name, url, task in same_code_tasks(db, resource):
            by_site.setdefault(name, []).append((resource.id, url, task))
    results: dict[int, list[dict]] = {resource.id: [] for resource in resources}
    errors: dict[int, list[str]] = {resource.id: [] for resource in resources}

    def run_site(name: str, jobs: list) -> None:
        stopped = ""
        for index, (resource_id, url, task) in enumerate(jobs):
            host = fetcher.host_of(url)
            if not stopped and fetcher.ready_at(host) > time.monotonic() + BULK_INTERVAL:
                stopped = f"{name}：網站正在冷卻（剛被限流），稍後再比對"
            if stopped:
                errors[resource_id].append(stopped)
                continue
            if index:
                time.sleep(BULK_INTERVAL)
            rows, message, limited = task()
            results[resource_id].extend(rows)
            if message:
                errors[resource_id].append(message)
            if limited:
                fetcher.report_limited(host)
                stopped = f"{name}：被網站限流，稍後再比對"
            else:
                fetcher.report_ok(host)

    with ThreadPoolExecutor(max_workers=max(1, len(by_site))) as pool:
        list(pool.map(lambda item: run_site(*item), by_site.items()))
    _owned(db, [row for rows in results.values() for row in rows])
    return {"results": {str(key): value for key, value in results.items()}, "errors": {str(key): value for key, value in errors.items() if value}}
