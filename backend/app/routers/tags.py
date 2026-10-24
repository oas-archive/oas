"""標籤：名稱與分類、件數統計、合併、刪除、修改分類、資料頁、清理沒用到的標籤。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import insights, undo
from ..database import Resource, Tag, TagAlias, TagBlock, resource_tags
from ..deps import db_session, resource_filters
from ..schemas import CategoryInput, TagMergeInput, TagRemoveInput

router = APIRouter(prefix="/api/tags")


@router.get("/categories")
def tag_categories(db: Session = Depends(db_session)):
    """非一般分類的標籤：{名稱: 分類}，前端用來把標籤分組顯示。"""
    return dict(db.execute(select(Tag.name, Tag.category).where(Tag.category != "tag")).all())


@router.get("")
def tag_names(db: Session = Depends(db_session)):
    return list(db.scalars(select(Tag.name).order_by(Tag.name)).all())


@router.get("/stats")
def tag_stats(
    q: str = "", status: str | None = None, source: str | None = None,
    tags: list[str] = Query(default=[]), tag_mode: Literal["all", "any"] = "all", kind: str | None = None, dead: bool | None = None,
    collection: int | None = None,
    limit: int = Query(default=300, ge=1, le=5000), db: Session = Depends(db_session),
):
    """標籤與件數。帶入目前的篩選條件時，只計算符合條件的作品（多選時可看出還能再縮小到哪些標籤）。"""
    matching = select(Resource.id).where(*resource_filters(q, status, source, tags, tag_mode, kind, dead, collection))
    rows = db.execute(
        select(Tag.name, func.count(resource_tags.c.resource_id))
        .join(resource_tags, resource_tags.c.tag_id == Tag.id)
        .where(resource_tags.c.resource_id.in_(matching))
        .group_by(Tag.name).order_by(func.count(resource_tags.c.resource_id).desc(), Tag.name).limit(limit)
    ).all()
    return [{"name": name, "count": count} for name, count in rows]


@router.post("/merge")
def merge_tag(payload: TagMergeInput, db: Session = Depends(db_session)):
    """把一個標籤併入另一個，並記成同義詞。"""
    source, target = payload.source.strip(), payload.target.strip()
    if source == target:
        raise HTTPException(422, "兩個標籤相同")
    old = db.scalar(select(Tag).where(Tag.name == source))
    if old is None:
        raise HTTPException(404, f"找不到標籤「{source}」")
    new = db.scalar(select(Tag).where(Tag.name == target))
    aliases = {alias.alias: alias.canonical for alias in db.scalars(select(TagAlias).where(TagAlias.canonical == source)).all()}
    aliases[source.lower()] = (db.get(TagAlias, source.lower()).canonical if db.get(TagAlias, source.lower()) else None)
    undo_id = undo.record(db, "tag_merge", f"把「{source}」併入「{target}」", {
        "source": source, "category": old.category, "target": target, "target_created": new is None,
        "owners": list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == old.id)).all()),
        "target_owners": list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == new.id)).all()) if new else [],
        "aliases": aliases,
    })
    moved = insights.merge_tag(db, source, target)
    db.commit()
    return {"source": source, "target": target, "count": moved, "undo": undo_id}


@router.post("/remove")
def remove_tag(payload: TagRemoveInput, db: Session = Depends(db_session)):
    """從所有作品移除一個標籤；block=true 時加入封鎖清單。"""
    name = payload.name.strip()
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        raise HTTPException(404, f"找不到標籤「{name}」")
    owners = list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == tag.id)).all())
    newly_blocked = payload.block and db.get(TagBlock, name.lower()) is None
    undo_id = undo.record(db, "tag_remove", f"刪除標籤「{name}」", {"name": name, "category": tag.category, "owners": owners, "blocked": newly_blocked})
    db.execute(resource_tags.delete().where(resource_tags.c.tag_id == tag.id))
    db.delete(tag)
    if newly_blocked:
        db.add(TagBlock(name=name.lower()))
    db.commit()
    return {"name": name, "count": len(owners), "undo": undo_id}


@router.patch("/category")
def set_tag_category(payload: CategoryInput, db: Session = Depends(db_session)):
    """手動修改標籤分類（作者、社團、原作、角色、一般標籤…）。"""
    tag = db.scalar(select(Tag).where(Tag.name == payload.name.strip()))
    if tag is None:
        raise HTTPException(404, f"找不到標籤「{payload.name}」")
    undo_id = undo.record(db, "category", f"修改「{tag.name}」的分類", {"name": tag.name, "category": tag.category})
    tag.category = payload.category
    db.commit()
    return {"name": tag.name, "category": tag.category, "undo": undo_id}


@router.get("/profile")
def tag_profile(name: str = Query(min_length=1, max_length=100), db: Session = Depends(db_session)):
    """作者、社團、原作等標籤的資料頁：件數、閱讀情況、評分、常一起出現的標籤。"""
    try:
        return insights.tag_profile(db, name)
    except LookupError as error:
        raise HTTPException(404, str(error))


@router.post("/cleanup")
def cleanup_tags(db: Session = Depends(db_session)):
    """刪除沒有任何作品使用的標籤。"""
    used = select(resource_tags.c.tag_id)
    unused = db.scalars(select(Tag).where(Tag.id.not_in(used))).all()
    for tag in unused:
        db.delete(tag)
    db.commit()
    return {"removed": len(unused)}
