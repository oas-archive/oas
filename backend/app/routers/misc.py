"""健康檢查、復原、搜尋建議。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import covers, settings, undo
from ..archive import serialise
from ..database import Resource, Tag, resource_tags
from ..deps import db_session

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"status": "ok", "version": settings.VERSION}


# ---------- 復原 ----------

@router.post("/undo/{entry_id}")
def undo_action(entry_id: int, db: Session = Depends(db_session)):
    try:
        result = undo.apply(db, entry_id)
    except LookupError as error:
        raise HTTPException(404, str(error))
    db.commit()
    return result


@router.get("/undo/latest")
def undo_latest(db: Session = Depends(db_session)):
    return undo.latest(db)


# ---------- 搜尋建議 ----------

@router.get("/search/suggest")
def search_suggest(q: str = Query(min_length=1, max_length=100), db: Session = Depends(db_session)):
    """搜尋框的即時建議：符合的作品與標籤（含分類與件數）。"""
    text = q.strip()
    if not text:
        return {"items": [], "tags": []}
    # % 與 _ 照字面比對（例如搜尋「100%」），不當成萬用字元
    literal = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    term, starts = f"%{literal}%", f"{literal}%"
    rows = db.scalars(
        select(Resource).options(selectinload(Resource.tags))
        .where(or_(Resource.title.ilike(term, escape="\\"), Resource.code.ilike(term, escape="\\")))
        .order_by(Resource.title.ilike(starts, escape="\\").desc(), Resource.updated_at.desc()).limit(6)
    ).all()
    tags = db.execute(
        select(Tag.name, Tag.category, func.count(resource_tags.c.resource_id))
        .join(resource_tags, resource_tags.c.tag_id == Tag.id)
        .where(Tag.name.ilike(term, escape="\\")).group_by(Tag.id)
        .order_by(Tag.name.ilike(starts, escape="\\").desc(), func.count(resource_tags.c.resource_id).desc()).limit(8)
    ).all()
    return {
        "items": covers.with_ratios(db, [serialise(row) for row in rows]),
        "tags": [{"name": name, "category": category, "count": count} for name, category, count in tags],
    }
