"""待整理：需要你重試或動手補的作品，讓「哪些作品還缺東西」有地方看。

- retry：網站讀得到（nhentai、紳士漫畫、Hanime1，以及沒有防護的其他網站）但還沒有標題或封面、也不在背景排程裡，多半是自動補資料時被限流或連線失敗，可以重試；網站回應「找不到」的已在補資料時標成來源失效，歸到 dead。
- manual：網站有防護或沒有讀取規則（禁漫、MissAV、其他網站），還沒有標題或封面，要在作品頁用分享或「收錄到 OAS」書籤補。
  類型是「網站」的（網站首頁）不需要封面，只看標題。
- dead：巡檢或補資料時來源回應「找不到」，可以改用其他來源或刪除。

背景排程中的作品不列入（還在補）；失敗不另外記錄，直接由目前的資料推得，所以重試成功後自然消失。
"""
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import covers, fetcher, metadata, refresh, scheduler
from .database import Resource, Source

GROUPS = ("retry", "manual", "dead")


def _fetchable(url: str) -> bool:
    """背景補資料讀得到的網址：nhentai 等有專用規則；其他網站讀頁面的標題與 og:image，沒有就用網站圖示當封面。"""
    return url.startswith(("http://", "https://")) and not any(host in url for host in metadata.PROTECTED_HOSTS)


def missing(row, broken: set[str]) -> list[str]:
    """作品缺的東西：標題、封面（網站頁面不需要封面）、封面載不到（網址已經壞了，見 covers.BROKEN_ERRORS）、
    封面只有網站圖示（讀作品頁時沒找到代表圖；只有漫畫、動畫、影片算缺，其他類型用網站圖示就夠了）。"""
    result = [] if row.title.strip() else ["標題"]
    if row.kind != metadata.SITE_KIND:
        if not row.image_url.strip():
            result.append("封面")
        elif row.image_url in broken:
            result.append("封面（載不到）")
        elif row.image_kind == "icon" and row.kind in metadata.COVER_KINDS:  # 文章、論壇、創作者頁用網站圖示就夠了
            result.append("封面（只有網站圖示）")
    return result


def classify(db: Session) -> tuple[dict[str, list[tuple]], int]:
    """回傳（各組的 [(作品, 作品頁網址)]，背景排程中的件數）。"""
    from .insights import _resources  # 避免循環匯入

    templates = dict(db.execute(select(Source.name, Source.template)).all())
    broken = covers.broken_urls(db)
    queued = set(scheduler.pending_ids("fill")) | set(scheduler.pending_ids("refresh"))
    groups: dict[str, list[tuple]] = {key: [] for key in GROUPS}
    for row in _resources(db):
        url = refresh.page_url(row, templates.get(row.source_name, ""))
        if row.link_dead and row.kind != metadata.SITE_KIND:  # 網站的列表、搜尋頁不算作品，不列入來源失效
            groups["dead"].append((row, url))
        elif missing(row, broken) and row.id not in queued:
            groups["retry" if _fetchable(url) else "manual"].append((row, url))
    return groups, len(queued)


def summary(db: Session) -> dict:
    groups, queued = classify(db)
    counts = {key: len(rows) for key, rows in groups.items()}
    return {"counts": counts, "total": sum(counts.values()), "queued": queued}


def attention(db: Session) -> dict:
    from .insights import _card

    groups, queued = classify(db)
    broken = covers.broken_urls(db)
    result = {}
    for key, rows in groups.items():
        cards = _card(db, [row for row, _ in rows])
        result[key] = [{**card, "url": url, "missing": missing(row, broken)}
                       for card, (row, url) in zip(cards, rows)]
    return {"groups": result, "queued": queued}


def retry_ids(db: Session, ids: list[int]) -> list[int]:
    """要重試的作品：只取「可以重試」那一組；沒指定 ids 時就是整組。"""
    rows = classify(db)[0]["retry"]
    wanted = set(ids)
    picked = [row for row, _ in rows if not wanted or row.id in wanted]
    # 封面網址已經壞了（例如 Hanime1 的縮圖網址過期）：清掉，重試時才會換上作品頁現在的封面（補資料只補空白欄位）
    broken = covers.broken_urls(db)
    table = Resource.__table__
    for row in picked:
        if row.image_url in broken or (row.image_kind == "icon" and row.kind in metadata.COVER_KINDS):
            db.execute(table.update().where(table.c.id == row.id).values(image_url="", image_kind="", updated_at=row.updated_at))
    db.commit()
    return [row.id for row in picked]


def fetch_manual(db: Session) -> dict:
    """「要你手動補」的作品各試著讀一次作品頁（依 fetcher 的網站間隔，依序進行；被限流就停下那個網站）。
    讀到的標題、封面只補空白的，標籤只新增（refresh.apply）。回傳 {"tried": 件數, "filled": 補到的件數, "limited": 被限流停下的網站}。"""
    rows = classify(db)[0]["manual"]
    tried = filled = 0
    limited: set[str] = set()
    for row, url in rows:
        host = fetcher.host_of(url)
        if not url.startswith(("http://", "https://")) or host in limited:
            continue
        wait = fetcher.ready_at(host) - time.monotonic()
        if wait > fetcher.interval(host) * 2:  # 網站正在冷卻：這次跳過
            limited.add(host)
            continue
        time.sleep(max(0.0, wait))
        fetcher.reserve(host)
        tried += 1
        try:
            data = metadata.parse(url, metadata._get(url))
        except metadata.MetadataError as error:
            if error.rate_limited:
                fetcher.report_limited(host)
                limited.add(host)
            continue
        fetcher.report_ok(host)
        if not data.get("title"):
            continue
        changes = refresh.apply(db, row, data)
        db.commit()
        if changes["cover"] and row.image_url:
            covers.schedule(row.image_url)
        filled += 1
    return {"tried": tried, "filled": filled, "limited": sorted(limited)}


def fill_page(db: Session, resource_id: int, page: str) -> dict:
    """手機 App 在隱藏的瀏覽器讀到的作品頁：依網站規則解析，補上空白的標題、封面，標籤只新增（refresh.apply）。
    讀到的頁面沒有標題（例如還停在防護驗證頁、作品不存在）就不動。回傳 {"filled": 是否補到, "tags": 新增的標籤數}。"""
    row = db.get(Resource, resource_id)
    if row is None:
        return {"filled": False, "tags": 0}
    template = db.scalar(select(Source.template).where(Source.name == row.source_name)) or ""
    try:
        data = metadata.parse(refresh.page_url(row, template), page)
    except metadata.MetadataError:  # 例如紳士的「您要訪問的漫畫不存在」
        return {"filled": False, "tags": 0}
    if not data.get("title"):
        return {"filled": False, "tags": 0}
    changes = refresh.apply(db, row, data)
    db.commit()
    if changes["cover"] and row.image_url:
        covers.schedule(row.image_url)
    return {"filled": changes["changed"], "tags": changes["tags"]}
