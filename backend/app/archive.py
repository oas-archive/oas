"""Shared catalogue logic: tags, status normalisation, backup export and import."""
from datetime import datetime, timezone
from typing import Iterable, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import Collection, Resource, Source, Tag, TagAlias, TagBlock, collection_items, resource_tags

STATUSES = ["未讀", "閱讀中", "已完食", "暫時擱置", "棄坑"]
DEFAULT_SOURCE = "其他網站"
BACKUP_FORMAT = "oas-backup"
BACKUP_VERSION = 4  # 2：類型、標籤分類、同義詞與封鎖清單；3：自訂書單；4：閱讀進度、完食時間、智慧書單

# 舊版曾使用的狀態字串，對應至目前的五種狀態。
STATUS_ALIASES = {"未讀/未看": "未讀", "未看": "未讀", "已讀": "已完食", "已看": "已完食", "已讀/已看": "已完食"}


def normalise_status(value: str | None) -> str:
    value = (value or "").strip()
    if value in STATUSES:
        return value
    return STATUS_ALIASES.get(value, "未讀")


def split_tags(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").replace("，", ",").split(",") if item.strip()]


def normalise_tags(db: Session, values: Iterable[str]) -> list[str]:
    """套用同義詞與封鎖清單：英文或簡體的同義標籤換成你整理後的名稱，宣傳字等直接略過。"""
    aliases = dict(db.execute(select(TagAlias.alias, TagAlias.canonical)).all())
    blocked = set(db.scalars(select(TagBlock.name)).all())
    names = []
    for value in values:
        name = (value or "").strip()[:100]
        if not name:
            continue
        name = aliases.get(name.lower(), name)
        if name.lower() not in blocked and name not in names:
            names.append(name)
    return sorted(names)


def assign_tags(db: Session, resource: Resource, values: Iterable[str], categories: dict[str, str] | None = None) -> None:
    tags = []
    for name in normalise_tags(db, values):
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name, category=(categories or {}).get(name, "tag"))
            db.add(tag)
            db.flush()
        tags.append(tag)
    resource.tags = tags


def parse_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def serialise(resource: Resource) -> dict:
    return {
        "id": resource.id, "code": resource.code, "source_name": resource.source_name,
        "title": resource.title, "note": resource.note, "image_url": resource.image_url,
        "rating": resource.rating, "status": resource.status, "kind": resource.kind or "", "image_kind": resource.image_kind or "", "link_dead": bool(resource.link_dead),
        "collections": sorted(collection.id for collection in resource.collections),
        "tags": sorted(tag.name for tag in resource.tags),
        "created_at": isoformat(resource.created_at), "updated_at": isoformat(resource.updated_at),
        "last_opened_at": isoformat(resource.last_opened_at),
        "progress": resource.progress or "", "finished_at": isoformat(resource.finished_at),
    }


def export_backup(db: Session) -> dict:
    resources = db.scalars(select(Resource).order_by(Resource.id)).all()
    sources = db.scalars(select(Source).order_by(Source.name)).all()
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": isoformat(datetime.now(timezone.utc)),
        "counts": {"resources": len(resources), "sources": len(sources)},
        "sources": [{"name": source.name, "template": source.template, "kind": source.kind} for source in sources],
        "resources": [{key: value for key, value in serialise(item).items() if key != "id"} for item in resources],
        "tag_categories": dict(db.execute(select(Tag.name, Tag.category).where(Tag.category != "tag")).all()),
        "tag_aliases": dict(db.execute(select(TagAlias.alias, TagAlias.canonical)).all()),
        "tag_blocklist": sorted(db.scalars(select(TagBlock.name)).all()),
        # 書單以「來源＋代碼」記錄成員，還原到另一個資料庫時也對得上。
        "collections": [
            {"name": collection.name, "position": collection.position, "query": collection.query or "",
             "items": [{"source_name": item.source_name, "code": item.code} for item in collection.resources]}
            for collection in db.scalars(select(Collection).order_by(Collection.position, Collection.id)).all()
        ],
    }


def import_records(
    db: Session,
    sources: Iterable[dict],
    resources: Iterable[dict],
    mode: Literal["merge", "replace"] = "merge",
    tag_categories: dict | None = None,
    tag_aliases: dict | None = None,
    tag_blocklist: Iterable[str] | None = None,
    collections: Iterable[dict] | None = None,
) -> dict:
    """Insert sources and resources, de-duplicating on (source_name, code).

    merge   — keep existing rows, add only missing ones.
    replace — delete all resources, tags and sources first.
    Caller commits.
    """
    if mode == "replace":
        db.execute(delete(resource_tags))
        db.execute(delete(Resource))
        db.execute(delete(Tag))
        db.execute(delete(Source))
        db.execute(delete(collection_items))
        db.execute(delete(Collection))
        db.execute(delete(TagAlias))
        db.execute(delete(TagBlock))
        db.flush()

    # 先還原同義詞與封鎖清單，之後寫入的標籤才會套用同樣的整理規則。
    known_aliases = set(db.scalars(select(TagAlias.alias)).all())
    for alias, canonical in (tag_aliases or {}).items():
        alias, canonical = str(alias).strip().lower()[:100], str(canonical).strip()[:100]
        if alias and canonical and alias not in known_aliases:
            db.add(TagAlias(alias=alias, canonical=canonical))
            known_aliases.add(alias)
    known_blocked = set(db.scalars(select(TagBlock.name)).all())
    for name in tag_blocklist or []:
        name = str(name).strip().lower()[:100]
        if name and name not in known_blocked:
            db.add(TagBlock(name=name))
            known_blocked.add(name)
    db.flush()

    report = {"created": 0, "skipped": 0, "sources_created": 0}

    known_sources = set(db.scalars(select(Source.name)).all())
    for source in sources:
        name = (source.get("name") or "").strip()
        template = (source.get("template") or "").strip()
        if not name or not template or name in known_sources:
            continue
        db.add(Source(name=name[:120], template=template[:2000], kind=str(source.get("kind") or "")[:20]))
        known_sources.add(name)
        report["sources_created"] += 1

    seen = {(row.source_name, row.code) for row in db.execute(select(Resource.source_name, Resource.code))}
    for item in resources:
        code = str(item.get("code") or "").strip()[:500]
        source_name = (str(item.get("source_name") or "").strip() or DEFAULT_SOURCE)[:120]
        if not code or (source_name, code) in seen:
            report["skipped"] += 1
            continue
        seen.add((source_name, code))
        try:
            rating = min(max(float(item.get("rating") or 0), 0.0), 5.0)
        except (TypeError, ValueError):
            rating = 0.0
        created_at = parse_datetime(item.get("created_at")) or datetime.now(timezone.utc)
        resource = Resource(
            code=code, source_name=source_name,
            title=str(item.get("title") or "")[:500], note=str(item.get("note") or ""),
            image_url=str(item.get("image_url") or "")[:2000], rating=rating,
            status=normalise_status(item.get("status")),
            created_at=created_at,
            updated_at=parse_datetime(item.get("updated_at")) or created_at,
            last_opened_at=parse_datetime(item.get("last_opened_at")),
            kind=str(item.get("kind") or "")[:20],
            progress=str(item.get("progress") or "")[:40],
            finished_at=parse_datetime(item.get("finished_at")),
        )
        db.add(resource)
        tags = item.get("tags") or []
        assign_tags(db, resource, split_tags(tags) if isinstance(tags, str) else tags, categories=tag_categories)
        report["created"] += 1

    db.flush()
    # 書單：依名稱合併，成員以「來源＋代碼」對應到作品。
    keys = {(row.source_name, row.code): row.id for row in db.execute(select(Resource.source_name, Resource.code, Resource.id))}
    for entry in collections or []:
        name = str(entry.get("name") or "").strip()[:80]
        if not name:
            continue
        collection = db.scalar(select(Collection).where(Collection.name == name))
        if collection is None:
            collection = Collection(name=name, position=int(entry.get("position") or 0), query=str(entry.get("query") or "")[:1000])
            db.add(collection)
            db.flush()
        members = set(db.scalars(select(collection_items.c.resource_id).where(collection_items.c.collection_id == collection.id)).all())
        for item in entry.get("items") or []:
            rid = keys.get((str(item.get("source_name") or ""), str(item.get("code") or "")))
            if rid and rid not in members:
                db.execute(collection_items.insert().values(collection_id=collection.id, resource_id=rid, added_at=datetime.now(timezone.utc)))
                members.add(rid)
    db.flush()
    return report
