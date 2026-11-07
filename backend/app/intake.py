"""新收錄作品的背景補資料：只輸入網址就存的作品（單件或一次貼多個網址），由排程器慢慢讀取作品頁，補上標題、封面與標籤。

- 只補空白欄位、標籤只新增（與「補齊作品資訊」相同的規則，見 refresh.apply）。
- 工作存在 jobs 資料表，優先於「補齊作品資訊」與來源巡檢；請求頻率與被限流時的處理由 scheduler.py、fetcher.py 統一控制，
  關掉典藏館再開會接著補。
- 有防護、伺服器讀不到的網站（禁漫、MissAV）不排入，請用「收錄到 OAS」書籤。
"""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from . import covers, metadata, refresh, scheduler
from .database import Resource, SessionLocal, Source

state = {"running": False, "total": 0, "done": 0, "filled": 0, "failed": 0, "skipped": 0, "message": "", "started_at": None, "finished_at": None}


def needs_fill(title: str, image_url: str, tags: list) -> bool:
    """單件收錄時：標題、封面、標籤都空白，才代表「只輸入了網址」。"""
    return not title.strip() and not image_url.strip() and not tags


def _fetchable(url: str) -> bool:
    return url.startswith(("http://", "https://")) and not any(host in url for host in metadata.PROTECTED_HOSTS)


def fill_known_covers(db, rows, templates: dict) -> list[str]:
    """沒有封面、但網址有固定封面規則的作品（例如禁漫）直接補上封面網址，不必讀作品頁。回傳補上的封面網址；由呼叫者 commit。"""
    filled = []
    for row in rows:
        if not row.image_url and (image := metadata.known_cover(refresh.page_url(row, templates.get(row.source_name, "")))):
            refresh.apply(db, row, {"image_url": image})
            filled.append(image)
    return filled


def normalise_saved() -> dict:
    """啟動時整理既有作品：鏡像網址（missav.ws、18comic.org）或多一段分類的網址（MissAV 的 dm26/…）換成正確的來源與代碼，
    同一件已經在典藏中就合併進去（分享時被當成新作品的那種）；標題去掉網站名稱（「Comics - 禁漫天堂」）；
    網站首頁的類型改成「網站」（沿用來源類型的才改，你自己設過的不動）。保留 updated_at。"""
    from .insights import merge_resources  # 避免循環匯入

    counts = {"relinked": 0, "merged": 0, "titles": 0, "sites": 0, "icons": 0}
    with SessionLocal() as db:
        templates = dict(db.execute(select(Source.name, Source.template)).all())
        kinds = dict(db.execute(select(Source.name, Source.kind)).all())
        table = Resource.__table__
        for row in db.scalars(select(Resource)).all():
            title = metadata.clean_title(row.title) if row.title else ""
            if title and title != row.title:
                db.execute(table.update().where(table.c.id == row.id).values(title=title, updated_at=row.updated_at))
                counts["titles"] += 1
        db.commit()  # 先存好標題，合併時才會用到清理後的標題
        rows = db.scalars(select(Resource).order_by(Resource.id)).all()
        index = {(row.source_name, row.code): row.id for row in rows}
        for row in rows:
            url = refresh.page_url(row, templates.get(row.source_name, ""))
            if row.kind in ("", "其他", kinds.get(row.source_name)) and row.kind != metadata.SITE_KIND and metadata.site_page(url):
                db.execute(table.update().where(table.c.id == row.id).values(kind=metadata.SITE_KIND, updated_at=row.updated_at))
                counts["sites"] += 1
            if not url.startswith(("http://", "https://")) or not metadata.mirrored(url):
                continue
            key = metadata.resolve(db, url)
            if key == (row.source_name, row.code) or key[0] == metadata.DEFAULT_SOURCE:
                continue
            existing = index.get(key)
            if existing and existing != row.id:
                merge_resources(db, existing, [existing, row.id])
                db.flush()
                counts["merged"] += 1
            else:
                db.execute(table.update().where(table.c.id == row.id).values(source_name=key[0], code=key[1], kind=metadata.SITE_KIND if metadata.site_page(url) else kinds.get(key[0]) or row.kind, updated_at=row.updated_at))
                index[key] = row.id
                counts["relinked"] += 1
            index.pop((row.source_name, row.code), None)
        # 網站圖示當成的封面標成 icon（顯示成佔位圖、不比對重複；舊版存的沒有標）
        for row_id, image in db.execute(select(table.c.id, table.c.image_url).where(table.c.image_kind == "", table.c.image_url != "")).all():
            if metadata.icon_url(image):
                db.execute(table.update().where(table.c.id == row_id).values(image_kind="icon", updated_at=table.c.updated_at))
                counts["icons"] += 1
        # 網站頁面不算作品，不標來源失效（舊版巡檢可能標過）
        db.execute(table.update().where(table.c.kind == metadata.SITE_KIND, table.c.link_dead.is_(True)).values(link_dead=False, updated_at=table.c.updated_at))
        db.commit()
    return counts


def backfill_known_covers() -> int:
    """啟動時替既有作品補上可推得的封面（例如之前一次收錄、只有編號的禁漫作品）。封面圖片在顯示時才下載。"""
    with SessionLocal() as db:
        templates = dict(db.execute(select(Source.name, Source.template)).all())
        rows = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.image_url == "")).all()
        filled = fill_known_covers(db, rows, templates)
        db.commit()
    return len(filled)


def enqueue(ids: list[int]) -> int:
    """把作品排入補資料清單；讀不到的網址（非 http、有防護的網站）不排入，其中封面可依編號推得的先補上封面。回傳排入的件數。"""
    if not ids:
        return 0
    with SessionLocal() as db:
        templates = dict(db.execute(select(Source.name, Source.template)).all())
        rows = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(ids))).all()
        for image in fill_known_covers(db, rows, templates):
            covers.schedule(image)
        db.commit()
        items = [(row.id, url) for row in rows if _fetchable(url := refresh.page_url(row, templates.get(row.source_name, "")))]
    order = {item_id: index for index, item_id in enumerate(ids)}
    return scheduler.enqueue("fill", sorted(items, key=lambda item: order[item[0]]))


def process(resource_id: int) -> tuple[str, str]:
    """補一件；回傳（結果, 網址）。結果為 filled／failed／skipped，或 limited（被限流，稍後重試）。"""
    with SessionLocal() as db:
        resource = db.scalar(select(Resource).options(selectinload(Resource.tags)).where(Resource.id == resource_id))
        if resource is None:
            return "skipped", ""
        template = db.scalar(select(Source.template).where(Source.name == resource.source_name)) or ""
        url = refresh.page_url(resource, template)
        if not url.startswith(("http://", "https://")) or any(host in url for host in metadata.PROTECTED_HOSTS):
            return "skipped", url
        try:
            page = metadata._get(url)
        except metadata.MetadataError as error:
            if error.gone:  # 作品已下架：標成來源失效，不再列在「自動補資料沒成功」
                refresh.mark_gone(resource_id)
            return ("limited" if error.rate_limited else "failed"), url
        data = metadata.parse(url, page)
        if not data.get("image_url") and not any(host in url for host in refresh.FETCHABLE_HOSTS):
            image, image_kind = metadata.page_image(url, page, resource.kind)
            data.update(image_url=image, image_kind=image_kind)
        changes = refresh.apply(db, resource, data)
        db.commit()
        if changes["cover"]:
            covers.schedule(resource.image_url)
        return ("filled" if changes["changed"] else "skipped"), url


def handle(job: scheduler.JobInfo) -> str:
    return process(job.resource_id)[0]


scheduler.register("fill", "補新收錄", 0, handle, state, counters=("filled", "skipped"))
