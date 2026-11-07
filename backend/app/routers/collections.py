"""書單：手動書單與智慧書單（依儲存的篩選條件自動更新）。"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import Collection, Resource, collection_items
from ..deps import db_session, smart_filters
from ..schemas import CollectionInput, CollectionItemsInput, CollectionPatch

router = APIRouter(prefix="/api/collections")


def _collection(db: Session, collection_id: int) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None:
        raise HTTPException(404, "找不到書單")
    return collection


@router.get("")
def list_collections(db: Session = Depends(db_session)):
    """自訂書單與件數，依自訂順序排列；智慧書單的件數依儲存的條件即時計算。"""
    rows = db.execute(
        select(Collection.id, Collection.name, Collection.query, func.count(collection_items.c.resource_id))
        .outerjoin(collection_items, collection_items.c.collection_id == Collection.id)
        .group_by(Collection.id).order_by(Collection.position, Collection.id)
    ).all()
    result = []
    for cid, name, query, count in rows:
        if query:
            count = db.scalar(select(func.count(Resource.id)).where(*smart_filters(query))) or 0
        result.append({"id": cid, "name": name, "count": count, "query": query or ""})
    return result


@router.post("", status_code=201)
def create_collection(payload: CollectionInput, db: Session = Depends(db_session)):
    name = payload.name.strip()
    if db.scalar(select(Collection.id).where(Collection.name == name)):
        raise HTTPException(409, "已經有同名的書單")
    collection = Collection(name=name, query=payload.query.strip(), position=(db.scalar(select(func.max(Collection.position))) or 0) + 1)
    db.add(collection)
    db.commit()
    count = db.scalar(select(func.count(Resource.id)).where(*smart_filters(collection.query))) if collection.query else 0
    return {"id": collection.id, "name": collection.name, "count": count, "query": collection.query}


@router.patch("/{collection_id}")
def rename_collection(collection_id: int, payload: CollectionPatch, db: Session = Depends(db_session)):
    """改名，或更新智慧書單的條件（手動書單不能改成智慧書單，以免裡面的作品消失）。"""
    collection = _collection(db, collection_id)
    if payload.name is not None:
        name = payload.name.strip()
        if db.scalar(select(Collection.id).where(Collection.name == name, Collection.id != collection_id)):
            raise HTTPException(409, "已經有同名的書單")
        collection.name = name
    if payload.query is not None:
        if not collection.query:
            raise HTTPException(422, "手動書單不能改成智慧書單")
        if not payload.query.strip():
            raise HTTPException(422, "智慧書單至少要有一個條件")
        collection.query = payload.query.strip()
    db.commit()
    return {"id": collection.id, "name": collection.name, "query": collection.query}


@router.delete("/{collection_id}", status_code=204)
def delete_collection(collection_id: int, db: Session = Depends(db_session)):
    """只刪除書單本身，書單裡的作品不受影響。"""
    db.delete(_collection(db, collection_id))
    db.commit()


@router.post("/{collection_id}/items")
def change_collection_items(collection_id: int, payload: CollectionItemsInput, db: Session = Depends(db_session)):
    """把作品加入或移出書單（可一次多件）；不更新作品的 updated_at。"""
    if _collection(db, collection_id).query:
        raise HTTPException(422, "智慧書單依條件自動更新，不能手動加入作品")
    current = set(db.scalars(select(collection_items.c.resource_id).where(collection_items.c.collection_id == collection_id)).all())
    existing = set(db.scalars(select(Resource.id).where(Resource.id.in_(payload.ids))).all())
    wanted = list(dict.fromkeys(payload.ids))
    if payload.action == "add":
        changed = [rid for rid in wanted if rid in existing and rid not in current]
        if changed:
            now = datetime.now(timezone.utc)
            db.execute(collection_items.insert(), [{"collection_id": collection_id, "resource_id": rid, "added_at": now} for rid in changed])
    else:
        changed = [rid for rid in wanted if rid in current]
        if changed:
            db.execute(collection_items.delete().where(collection_items.c.collection_id == collection_id, collection_items.c.resource_id.in_(changed)))
    db.commit()
    count = db.scalar(select(func.count()).select_from(collection_items).where(collection_items.c.collection_id == collection_id))
    return {"changed": len(changed), "count": count}
