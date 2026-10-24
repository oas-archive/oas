"""復原：會改動或刪除資料的動作，先把「改動前的狀態」存進 undo_log，按「復原」時寫回。

- 每筆紀錄保留 KEEP_MINUTES 分鐘（前端只在提示訊息顯示 10 秒，但重新整理後仍可從指令面板復原最近一筆）。
- 寫回時不經過同義詞與封鎖清單，標籤名稱照原樣還原。
- 被刪除的作品會盡量用原本的 id 還原，書單與標籤也一併接回；同一來源與代碼若已被重新收錄，則略過那一件。
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, selectinload

from .archive import isoformat, parse_datetime
from .database import Collection, Resource, Tag, TagAlias, TagBlock, UndoEntry, collection_items, resource_tags

KEEP_MINUTES = 60
FIELDS = ("code", "source_name", "title", "note", "image_url", "rating", "status", "image_kind", "link_dead", "kind", "progress")
TIMES = ("created_at", "updated_at", "last_opened_at", "finished_at", "link_checked_at")


def snapshot(db: Session, resource: Resource) -> dict:
    """一件作品的完整狀態（含標籤分類與所在書單）。"""
    return {
        "id": resource.id,
        **{key: getattr(resource, key) for key in FIELDS},
        **{key: isoformat(getattr(resource, key)) for key in TIMES},
        "tags": [{"name": tag.name, "category": tag.category} for tag in resource.tags],
        "collections": list(db.scalars(select(collection_items.c.collection_id).where(collection_items.c.resource_id == resource.id)).all()),
    }


def record(db: Session, kind: str, label: str, payload: dict) -> int:
    """存一筆復原紀錄並回傳 id；順便清掉過期的紀錄。呼叫端負責 commit。"""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=KEEP_MINUTES)
    db.execute(delete(UndoEntry).where(UndoEntry.created_at < cutoff))
    entry = UndoEntry(kind=kind, label=label[:200], payload=json.dumps(payload, ensure_ascii=False, default=str))
    db.add(entry)
    db.flush()
    return entry.id


def _tag(db: Session, name: str, category: str = "tag") -> Tag:
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        tag = Tag(name=name, category=category or "tag")
        db.add(tag)
        db.flush()
    return tag


def _set_tags(db: Session, resource_id: int, tags: list[dict]) -> None:
    """直接寫關聯表，不經過同義詞、也不更新 updated_at。"""
    db.execute(delete(resource_tags).where(resource_tags.c.resource_id == resource_id))
    ids = {_tag(db, entry["name"], entry.get("category", "tag")).id for entry in tags}
    if ids:
        db.execute(insert(resource_tags), [{"resource_id": resource_id, "tag_id": tag_id} for tag_id in ids])


def _set_collections(db: Session, resource_id: int, collection_ids: list[int]) -> None:
    existing = set(db.scalars(select(Collection.id).where(Collection.id.in_(collection_ids))).all()) if collection_ids else set()
    db.execute(delete(collection_items).where(collection_items.c.resource_id == resource_id))
    if existing:
        now = datetime.now(timezone.utc)
        db.execute(insert(collection_items), [{"collection_id": cid, "resource_id": resource_id, "added_at": now} for cid in existing])


def _values(snap: dict) -> dict:
    return {**{key: snap[key] for key in FIELDS}, **{key: parse_datetime(snap[key]) for key in TIMES}}


def _restore_deleted(db: Session, snap: dict) -> bool:
    if db.scalar(select(Resource.id).where(Resource.source_name == snap["source_name"], Resource.code == snap["code"])):
        return False  # 已經重新收錄過
    values = _values(snap)
    if db.get(Resource, snap["id"]) is None:
        values["id"] = snap["id"]
    result = db.execute(insert(Resource).values(**values))
    resource_id = values.get("id") or result.inserted_primary_key[0]
    _set_tags(db, resource_id, snap["tags"])
    _set_collections(db, resource_id, snap["collections"])
    return True


def _overwrite(db: Session, snap: dict) -> None:
    db.execute(Resource.__table__.update().where(Resource.id == snap["id"]).values(**_values(snap)))
    _set_tags(db, snap["id"], snap["tags"])
    _set_collections(db, snap["id"], snap["collections"])


def _touched_since_created(resource: Resource) -> bool:
    """收錄後是否被你動過（評分、狀態、進度、筆記、開啟、加入書單或編輯）；背景補上的標題、封面、標籤不算。"""
    edited = resource.updated_at and resource.created_at and (resource.updated_at - resource.created_at).total_seconds() > 2
    return bool(
        edited or resource.rating > 0 or resource.status != "未讀" or resource.progress or resource.note.strip()
        or resource.last_opened_at or resource.collections
    )


def apply(db: Session, entry_id: int) -> dict:
    """執行復原並刪除該筆紀錄；回傳 {label, restored, kept}。找不到（已過期或已復原）時丟 LookupError。"""
    entry = db.get(UndoEntry, entry_id)
    if entry is None:
        raise LookupError("這個動作已經無法復原（超過一小時或已復原過）")
    payload = json.loads(entry.payload)
    restored = kept = 0  # kept：復原「一次收錄」時因為已被編輯而保留的件數
    if entry.kind == "delete":
        restored = sum(_restore_deleted(db, snap) for snap in payload["resources"])
    elif entry.kind == "merge":
        if db.get(Resource, payload["keep"]["id"]) is not None:
            _overwrite(db, payload["keep"])
        restored = 1 + sum(_restore_deleted(db, snap) for snap in payload["removed"])
    elif entry.kind == "fields":
        for row in payload["rows"]:
            values = {key: parse_datetime(value) if key in TIMES else value for key, value in row["values"].items()}
            db.execute(Resource.__table__.update().where(Resource.id == row["id"]).values(**values))
            restored += 1
    elif entry.kind == "tags":
        for row in payload["rows"]:
            if db.get(Resource, row["id"]) is not None:
                _set_tags(db, row["id"], row["tags"])
                db.execute(Resource.__table__.update().where(Resource.id == row["id"]).values(updated_at=parse_datetime(row["updated_at"])))
                restored += 1
    elif entry.kind == "tag_merge":
        source = _tag(db, payload["source"], payload["category"])
        target = db.scalar(select(Tag).where(Tag.name == payload["target"]))
        owners = [rid for rid in payload["owners"] if db.get(Resource, rid) is not None]
        have = set(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == source.id)).all())
        if owners:
            missing = [rid for rid in owners if rid not in have]
            if missing:
                db.execute(insert(resource_tags), [{"resource_id": rid, "tag_id": source.id} for rid in missing])
        if target is not None:
            keep = set(payload["target_owners"])
            drop = [rid for rid in owners if rid not in keep]
            if drop:
                db.execute(delete(resource_tags).where(resource_tags.c.tag_id == target.id, resource_tags.c.resource_id.in_(drop)))
            if payload["target_created"] and not db.scalar(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == target.id).limit(1)):
                db.delete(target)
        for alias, canonical in payload["aliases"].items():
            row = db.get(TagAlias, alias)
            if canonical is None and row is not None:
                db.delete(row)
            elif canonical is not None:
                if row is None:
                    db.add(TagAlias(alias=alias, canonical=canonical))
                else:
                    row.canonical = canonical
        restored = len(owners)
    elif entry.kind == "tag_remove":
        tag = _tag(db, payload["name"], payload["category"])
        owners = [rid for rid in payload["owners"] if db.get(Resource, rid) is not None]
        if owners:
            db.execute(insert(resource_tags), [{"resource_id": rid, "tag_id": tag.id} for rid in owners])
        if payload["blocked"]:
            block = db.get(TagBlock, payload["name"].lower())
            if block is not None:
                db.delete(block)
        restored = len(owners)
    elif entry.kind == "created":  # 復原「一次收錄」：刪掉那次新增、之後沒動過的作品
        for resource in db.scalars(select(Resource).where(Resource.id.in_(payload["ids"]))).all():
            if _touched_since_created(resource):
                kept += 1
                continue
            db.delete(resource)
            restored += 1
    elif entry.kind == "category":
        tag = db.scalar(select(Tag).where(Tag.name == payload["name"]))
        if tag is not None:
            tag.category = payload["category"]
            restored = 1
    label = entry.label
    db.delete(entry)
    return {"label": label, "restored": restored, "kept": kept}


def latest(db: Session) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=KEEP_MINUTES)
    entry = db.scalar(select(UndoEntry).where(UndoEntry.created_at >= cutoff).order_by(UndoEntry.id.desc()).limit(1))
    return {"id": entry.id, "label": entry.label, "created_at": isoformat(entry.created_at)} if entry else None


def snapshots(db: Session, ids: list[int]) -> list[dict]:
    rows = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(ids))).all()
    return [snapshot(db, row) for row in rows]
