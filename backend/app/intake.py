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


def enqueue(ids: list[int]) -> int:
    """把作品排入補資料清單；讀不到的網址（非 http、有防護的網站）不排入。回傳排入的件數。"""
    if not ids:
        return 0
    with SessionLocal() as db:
        templates = dict(db.execute(select(Source.name, Source.template)).all())
        rows = db.scalars(select(Resource).where(Resource.id.in_(ids))).all()
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
