"""作品：列表、單件、新增、修改、快速評分、開啟紀錄、改用其他來源、批次編輯、刪除、合併重複。"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import alternatives, covers, graph as graph_module, insights, intake, metadata, refresh, undo
from ..archive import assign_tags, normalise_tags, serialise
from ..database import Resource, Source
from ..deps import db_session, resource_filters
from ..schemas import BatchInput, MergeInput, QuickInput, RelinkInput, ResourceInput

router = APIRouter(prefix="/api/resources")


def finished_value(old_status: str, new_status: str, current):
    """狀態改成「已完食」時記下時間，改成其他狀態時清除；狀態沒變就保留原值。"""
    if new_status == old_status:
        return current
    return datetime.now(timezone.utc) if new_status == "已完食" else None


def find_duplicate(db: Session, code: str, source_name: str, exclude_id: int | None = None) -> Resource | None:
    stmt = select(Resource).where(Resource.code == code, Resource.source_name == source_name)
    if exclude_id is not None:
        stmt = stmt.where(Resource.id != exclude_id)
    return db.scalar(stmt)


def commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "同一來源已存在相同代碼")


@router.get("")
def list_resources(
    q: str = "", status: str | None = None, source: str | None = None,
    tag: str | None = None, tags: list[str] = Query(default=[]), tag_mode: Literal["all", "any"] = "all",
    kind: str | None = None, dead: bool | None = None, collection: int | None = None, opened: bool | None = None,
    rated: bool | None = None, not_status: str | None = None, sort: Literal["updated", "created", "rating", "title", "opened"] = "updated",
    limit: int = Query(default=60, ge=1, le=1000), offset: int = Query(default=0, ge=0),
    db: Session = Depends(db_session),
):
    order = {
        "updated": (Resource.updated_at.desc(), Resource.id.desc()),
        "created": (Resource.created_at.desc(), Resource.id.desc()),
        "rating": (Resource.rating.desc(), Resource.updated_at.desc()),
        "title": (Resource.title.asc(), Resource.id.asc()),
        "opened": (Resource.last_opened_at.is_(None), Resource.last_opened_at.desc(), Resource.updated_at.desc()),
    }[sort]
    filters = resource_filters(q, status, source, [*tags, *([tag] if tag else [])], tag_mode, kind, dead, collection, opened, rated, not_status)
    total = db.scalar(select(func.count(Resource.id)).where(*filters)) or 0
    stmt = select(Resource).options(selectinload(Resource.tags)).where(*filters).order_by(*order)
    rows = db.scalars(stmt.offset(offset).limit(limit)).all()
    return {"items": covers.with_ratios(db, [serialise(row) for row in rows]), "total": total, "offset": offset, "limit": limit}


@router.get("/{resource_id}")
def get_resource(resource_id: int, db: Session = Depends(db_session)):
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(404, "找不到作品")
    return covers.with_ratios(db, [serialise(resource)])[0]


@router.get("/{resource_id}/related")
def related_resources(resource_id: int, limit: int = Query(default=8, ge=1, le=50), unread: bool = False, db: Session = Depends(db_session)):
    """依 IDF 加權的共用標籤，找出最相似的典藏；unread=true 只列未讀。"""
    if db.get(Resource, resource_id) is None:
        raise HTTPException(404, "找不到作品")
    return graph_module.similar(db, resource_id, limit=limit, unread_only=unread)


@router.post("", status_code=201)
def create_resource(payload: ResourceInput, db: Session = Depends(db_session)):
    payload.code, payload.source_name = payload.code.strip(), payload.source_name.strip()
    if find_duplicate(db, payload.code, payload.source_name):
        raise HTTPException(409, "同一來源已存在相同代碼")
    resource = Resource(**payload.model_dump(exclude={"tags"}))
    resource.finished_at = datetime.now(timezone.utc) if payload.status == "已完食" else None
    if not resource.kind:  # 沒指定類型時沿用來源的類型
        resource.kind = db.scalar(select(Source.kind).where(Source.name == resource.source_name)) or ""
    db.add(resource)
    assign_tags(db, resource, payload.tags)
    commit_or_conflict(db)
    db.refresh(resource)
    if intake.needs_fill(payload.title, payload.image_url, payload.tags):
        intake.enqueue([resource.id])  # 只輸入了網址：背景補上標題、封面與標籤
    return covers.with_ratios(db, [serialise(resource)])[0]


@router.patch("/{resource_id}")
def update_resource(resource_id: int, payload: ResourceInput, db: Session = Depends(db_session)):
    resource = db.scalar(select(Resource).options(selectinload(Resource.tags)).where(Resource.id == resource_id))
    if resource is None:
        raise HTTPException(404, "找不到作品")
    payload.code, payload.source_name = payload.code.strip(), payload.source_name.strip()
    if find_duplicate(db, payload.code, payload.source_name, exclude_id=resource_id):
        raise HTTPException(409, "同一來源已存在相同代碼")
    if payload.image_url != resource.image_url:
        resource.image_kind = ""  # 手動換了圖片網址，就當作一般封面
    resource.finished_at = finished_value(resource.status, payload.status, resource.finished_at)
    for key, value in payload.model_dump(exclude={"tags"}).items():
        setattr(resource, key, value)
    if not resource.kind:  # 類型留空時沿用來源的類型
        resource.kind = db.scalar(select(Source.kind).where(Source.name == resource.source_name)) or ""
    assign_tags(db, resource, payload.tags)
    resource.updated_at = datetime.now(timezone.utc)
    commit_or_conflict(db)
    db.refresh(resource)
    return covers.with_ratios(db, [serialise(resource)])[0]


@router.patch("/{resource_id}/quick")
def quick_update(resource_id: int, payload: QuickInput, db: Session = Depends(db_session)):
    """卡片上的快速評分／狀態。不更新 updated_at，避免典藏館排序在操作當下變動、封面重新載入。"""
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(404, "找不到作品")
    values = payload.model_dump(exclude_none=True)
    if not values:
        raise HTTPException(422, "沒有要更新的欄位")
    if "rating" in values:
        values["rating"] = round(values["rating"] * 2) / 2  # 以 0.5 為單位
    if "status" in values:
        values["finished_at"] = finished_value(resource.status, values["status"], resource.finished_at)
    if "progress" in values:
        values["progress"] = values["progress"].strip()
    db.execute(Resource.__table__.update().where(Resource.id == resource_id).values(**values, updated_at=resource.updated_at))
    db.commit()
    db.refresh(resource)
    return covers.with_ratios(db, [serialise(resource)])[0]


@router.post("/{resource_id}/open")
def mark_opened(resource_id: int, db: Session = Depends(db_session)):
    """閱讀事件：記錄最後開啟時間，不影響 updated_at 排序。"""
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(404, "找不到作品")
    db.execute(
        Resource.__table__.update().where(Resource.id == resource_id)
        .values(last_opened_at=datetime.now(timezone.utc), updated_at=resource.updated_at)
    )
    db.commit()
    db.refresh(resource)
    return serialise(resource)


@router.get("/{resource_id}/alternatives")
def find_alternatives(resource_id: int, q: str = "", db: Session = Depends(db_session)):
    """來源失效時，到 nhentai 與紳士漫畫搜尋同名作品；q 留空時由標題自動整理關鍵字。"""
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(404, "找不到作品")
    return alternatives.find(db, resource, q)


@router.post("/{resource_id}/relink")
def relink(resource_id: int, payload: RelinkInput, db: Session = Depends(db_session)):
    """改用新的來源：舊連結記到筆記，清除失效標記，並從新來源補上標籤與封面（只新增不覆蓋）。"""
    resource = db.scalar(select(Resource).options(selectinload(Resource.tags)).where(Resource.id == resource_id))
    if resource is None:
        raise HTTPException(404, "找不到作品")
    source_name, code = payload.source_name.strip(), payload.code.strip()
    if find_duplicate(db, code, source_name, exclude_id=resource_id):
        raise HTTPException(409, "這個來源的作品已經在典藏中")
    old = f"原連結：{resource.source_name} {resource.code}（{datetime.now(timezone.utc):%Y-%m-%d} 改用 {source_name}）"
    resource.note = f"{resource.note}\n{old}".strip() if resource.note else old
    resource.source_name, resource.code, resource.link_dead = source_name, code, False
    resource.kind = db.scalar(select(Source.kind).where(Source.name == source_name)) or resource.kind
    resource.updated_at = datetime.now(timezone.utc)
    commit_or_conflict(db)
    template = db.scalar(select(Source.template).where(Source.name == source_name)) or ""
    url = refresh.page_url(resource, template)
    added = 0
    if url:
        try:
            changes = refresh.apply(db, resource, metadata.parse(url, metadata._get(url)))
            db.commit()
            added = changes["tags"]
        except metadata.MetadataError:
            db.rollback()
    db.refresh(resource)
    return {**covers.with_ratios(db, [serialise(resource)])[0], "added_tags": added}


@router.post("/batch")
def batch_update(payload: BatchInput, db: Session = Depends(db_session)):
    """批次編輯：一次修改多件的狀態、評分、類型，增刪標籤，或刪除。狀態與評分不更新 updated_at（同快速評分）。"""
    resources = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(payload.ids))).all()
    count = len(resources)
    if payload.action == "delete":
        undo_id = undo.record(db, "delete", f"刪除 {count} 件", {"resources": [undo.snapshot(db, resource) for resource in resources]})
        for resource in resources:
            db.delete(resource)
    elif payload.action in ("status", "rating", "kind"):
        value = {"status": payload.status, "rating": None if payload.rating is None else round(payload.rating * 2) / 2, "kind": payload.kind}[payload.action]
        if value is None:
            raise HTTPException(422, "缺少要設定的值")
        label = {"status": f"把 {count} 件設為「{value}」", "rating": f"設定 {count} 件的評分", "kind": f"把 {count} 件的類型設為「{value}」"}[payload.action]
        undo_id = undo.record(db, "fields", label, {"rows": [
            {"id": resource.id, "values": {payload.action: getattr(resource, payload.action), "finished_at": undo.isoformat(resource.finished_at)}} for resource in resources
        ]})
        for resource in resources:
            extra = {"finished_at": finished_value(resource.status, value, resource.finished_at)} if payload.action == "status" else {}
            db.execute(Resource.__table__.update().where(Resource.id == resource.id).values({payload.action: value, "updated_at": resource.updated_at, **extra}))
    else:
        names = normalise_tags(db, payload.tags)
        if not names:
            raise HTTPException(422, "沒有有效的標籤")
        undo_id = undo.record(db, "tags", f"{'加上' if payload.action == 'add_tags' else '移除'}標籤（{count} 件）", {"rows": [
            {"id": resource.id, "tags": [{"name": tag.name, "category": tag.category} for tag in resource.tags], "updated_at": undo.isoformat(resource.updated_at)} for resource in resources
        ]})
        for resource in resources:
            current = [tag.name for tag in resource.tags]
            wanted = sorted(set(current) | set(names)) if payload.action == "add_tags" else [name for name in current if name not in names]
            if sorted(current) != sorted(wanted):
                assign_tags(db, resource, wanted)
                resource.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"action": payload.action, "count": count, "undo": undo_id}


@router.delete("/{resource_id}")
def delete_resource(resource_id: int, db: Session = Depends(db_session)):
    resource = db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(404, "找不到作品")
    undo_id = undo.record(db, "delete", f"移除「{resource.title or resource.code}」", {"resources": [undo.snapshot(db, resource)]})
    db.delete(resource)
    db.commit()
    return {"undo": undo_id}


@router.post("/{resource_id}/merge")
def merge_resources(resource_id: int, payload: MergeInput, db: Session = Depends(db_session)):
    """保留這件，把其他作品的標籤、書單、評分與筆記併進來後刪除它們。"""
    keep = db.scalar(select(Resource).options(selectinload(Resource.tags)).where(Resource.id == resource_id))
    if keep is None:
        raise HTTPException(404, "找不到要保留的作品")
    others = [rid for rid in payload.ids if rid != resource_id]
    undo_id = undo.record(db, "merge", f"合併 {len(others) + 1} 件重複作品", {"keep": undo.snapshot(db, keep), "removed": undo.snapshots(db, others)})
    try:
        keep = insights.merge_resources(db, resource_id, payload.ids)
    except LookupError as error:
        db.rollback()
        raise HTTPException(404, str(error))
    db.commit()
    db.refresh(keep)
    return {**covers.with_ratios(db, [serialise(keep)])[0], "undo": undo_id}
