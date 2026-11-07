"""背景補齊作品資訊：重新讀取可直接讀取的來源頁面，補上缺少的封面、標題與標籤，並記錄標籤分類。

- 只新增、不刪除：既有的標題、封面與標籤不會被覆蓋或移除。
- 新標籤一律經過同義詞與封鎖清單；語言標記只用來分類既有標籤，不會新增（網站自己的類別，例如同人誌、裏番，會新增）。
- 工作存在 jobs 資料表，由 scheduler.py 處理：優先順序在「補新收錄」之後；請求頻率與被限流時的處理由 fetcher.py 依網站控制。
- 不更新 updated_at，典藏館的排序不會因此變動。
"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from . import covers, fetcher, metadata, preferences, scheduler
from .archive import normalise_tags
from .database import Resource, SessionLocal, Source, Tag

REQUEST_INTERVAL = fetcher.SLOW_INTERVAL  # 用來估計所需時間
# 有專用解析規則、可直接讀取的網站（禁漫、MissAV 有防護，伺服器讀不到，不排進來）
FETCHABLE_HOSTS = tuple(host for host in metadata.PARSERS if not any(protected in host for protected in metadata.PROTECTED_HOSTS))
CATEGORY_ONLY = {"language"}  # 語言只用來標記既有標籤，不新增；網站自己的類別（同人誌、裏番、3DCG…）會新增，可以拿來篩選

state = {"running": False, "total": 0, "done": 0, "updated": 0, "failed": 0, "added_tags": 0,
         "added_covers": 0, "categorised": 0, "message": "", "started_at": None, "finished_at": None}


def page_url(resource: Resource, template: str) -> str:
    if resource.code.lower().startswith(("http://", "https://")):
        return resource.code
    return template.replace("{}", resource.code) if "{}" in template and template != "{}" else ""


def targets(db, mode: str) -> list[tuple[int, str]]:
    templates = {source.name: source.template for source in db.scalars(select(Source)).all()}
    result = []
    for resource in db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all():
        template = templates.get(resource.source_name, "")
        if mode == "images":  # 所有沒有圖片的作品，不限來源
            url = page_url(resource, template)
            if not resource.image_url and url:
                result.append((resource.id, url))
            continue
        if "{}" not in template or not any(host in template for host in FETCHABLE_HOSTS) or not resource.code.strip():
            continue
        if mode == "missing" and resource.image_url and resource.tags:
            continue
        result.append((resource.id, page_url(resource, template)))  # 代碼本身就是完整網址時不能再套範本
    return result


def job_is_current(resource_id: int, url: str) -> bool:
    """排入工作後作品可能已改用別的來源、被刪除，或編號給了別件作品：網址對不上就不該再處理。"""
    with SessionLocal() as db:
        resource = db.get(Resource, resource_id)
        if resource is None:
            return False
        template = db.scalar(select(Source.template).where(Source.name == resource.source_name)) or ""
        return page_url(resource, template) == url


def apply(db, resource: Resource, data: dict) -> dict:
    """把讀到的資訊合併進作品，回傳變更統計。"""
    changes = {"tags": 0, "cover": 0, "title": 0, "categorised": 0, "changed": False}
    values = {}
    if not resource.image_url and data.get("image_url"):
        values["image_url"] = data["image_url"][:2000]
        values["image_kind"] = data.get("image_kind", "") or ("icon" if metadata.icon_url(data["image_url"]) else "")
        changes["cover"] = 1
    if not resource.title and data.get("title"):
        values["title"] = data["title"][:500]
        changes["title"] = 1
    if values:  # 以直接更新保留原本的 updated_at（ORM 的 onupdate 會把它改成現在）
        db.execute(Resource.__table__.update().where(Resource.id == resource.id).values(**values, updated_at=resource.updated_at))
        for key, value in values.items():
            set_committed_value(resource, key, value)
    raw_categories = data.get("categories") or {}
    addable = [name for name in data.get("tags", []) if raw_categories.get(name) not in CATEGORY_ONLY]
    existing = {tag.name for tag in resource.tags}
    for name in normalise_tags(db, addable):
        if name in existing:
            continue
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        resource.tags.append(tag)
        existing.add(name)
        changes["tags"] += 1
    changes["categorised"] = categorise(db, raw_categories)
    changes["changed"] = bool(changes["tags"] or changes["cover"] or changes["title"] or changes["categorised"])
    return changes


CATEGORIES = {"artist", "group", "parody", "character", "category", "language"}


def categorise(db, raw_categories: dict) -> int:
    """分類：原文標籤名稱經同義詞轉換後，若對應到的標籤還是一般分類，就標上來源網站的分類（你自己改過的分類不動）。回傳標記了幾個。"""
    count = 0
    for raw, namespace in (raw_categories or {}).items():
        if namespace not in CATEGORIES:
            continue
        names = normalise_tags(db, [raw])
        if not names:
            continue
        tag = db.scalar(select(Tag).where(Tag.name == names[0]))
        if tag is not None and tag.category == "tag":
            tag.category = namespace
            count += 1
    return count


def mark_gone(resource_id: int) -> None:
    """讀作品頁時網站回應「找不到」：標記來源失效，讓它從「待整理 → 自動補資料沒成功」移到「來源失效」，不再一直重試。"""
    with SessionLocal() as db:
        resource = db.get(Resource, resource_id)
        if resource is not None:
            db.execute(Resource.__table__.update().where(Resource.id == resource_id).values(
                link_dead=True, link_checked_at=datetime.now(timezone.utc), updated_at=resource.updated_at))
            db.commit()


def handle(job: scheduler.JobInfo) -> str:
    """補一件：updated（有變更）、unchanged、failed，或 limited（被限流，稍後重試）。"""
    if not job_is_current(job.resource_id, job.url):
        return "unchanged"
    try:
        page = metadata._get(job.url)
    except metadata.MetadataError as error:
        if error.gone:
            mark_gone(job.resource_id)
        # 找圖模式各網站不同，被擋就跳過；其他模式被限流則稍後重試同一件（尊重來源的頻率限制，不嘗試繞過）
        return "limited" if error.rate_limited and job.mode != "images" else "failed"
    data = metadata.parse(job.url, page)
    with SessionLocal() as db:
        resource = db.get(Resource, job.resource_id)
        if resource is None:
            return "unchanged"
        if job.mode == "images" and not any(host in job.url for host in FETCHABLE_HOSTS):
            image, image_kind = metadata.page_image(job.url, page, resource.kind)
            # 先實際下載一次：被擋或不是圖片就不存，改由前端顯示佔位圖，避免出現空白框。
            if not image or (preferences.saving_covers() and covers.ensure_cover(db, image, force=True).status != "ok"):
                image = ""
            data = {"image_url": image, "image_kind": image_kind, "tags": []}
        changes = apply(db, resource, data)
        db.commit()
        if changes["cover"]:
            covers.schedule(resource.image_url)
    state["added_tags"] += changes["tags"]
    state["added_covers"] += changes["cover"]
    state["categorised"] += changes["categorised"]
    return "updated" if changes["changed"] else "unchanged"


def start(mode: str = "all") -> int:
    """排入補齊作業；已有補齊作業在清單中時回傳 -1。"""
    if scheduler.pending("refresh"):
        return -1
    with SessionLocal() as db:
        jobs = targets(db, mode)
    return scheduler.enqueue("refresh", jobs, mode=mode)


scheduler.register("refresh", "補齊作品資訊", 1, handle, state, counters=("updated", "added_tags", "added_covers", "categorised"))
