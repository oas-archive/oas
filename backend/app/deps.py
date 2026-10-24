"""各組 API 共用的工具：資料庫連線、典藏館的篩選條件。"""
from urllib.parse import parse_qs

from sqlalchemy import or_

from .database import Collection, Resource, SessionLocal, Tag


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def resource_filters(
    q: str = "", status: str | None = None, source: str | None = None, tags: list[str] | None = None, tag_mode: str = "all",
    kind: str | None = None, dead: bool | None = None, collection: int | None = None, opened: bool | None = None,
    rated: bool | None = None, not_status: str | None = None,
) -> list:
    """典藏館的篩選條件；tags 可多選，tag_mode=all 需全部符合、any 符合任一即可。"""
    filters = []
    if q.strip():
        term = f"%{q.strip()}%"
        filters.append(or_(
            Resource.title.ilike(term), Resource.note.ilike(term), Resource.code.ilike(term),
            Resource.tags.any(Tag.name.ilike(term)),
        ))
    if status:
        filters.append(Resource.status == status)
    if source:
        filters.append(Resource.source_name == source)
    if kind:
        filters.append(Resource.kind == kind)
    if dead is not None:
        filters.append(Resource.link_dead.is_(dead))
    if collection is not None:
        filters.append(Resource.collections.any(Collection.id == collection))
    if opened is not None:
        filters.append(Resource.last_opened_at.is_not(None) if opened else Resource.last_opened_at.is_(None))
    if rated is not None:
        filters.append(Resource.rating > 0 if rated else Resource.rating <= 0)
    if not_status:
        filters.append(Resource.status != not_status)
    names = [name for name in (tags or []) if name]
    if names and tag_mode == "any":
        filters.append(Resource.tags.any(Tag.name.in_(names)))
    else:
        filters.extend(Resource.tags.any(Tag.name == name) for name in names)
    return filters


def smart_filters(query: str) -> list:
    """智慧書單的條件（與典藏館網址相同的參數）轉成查詢條件。"""
    params = parse_qs(query)
    first = lambda key: (params.get(key) or [None])[0]
    return resource_filters(
        first("q") or "", first("status"), first("source"), params.get("tag", []), "any" if first("match") == "any" else "all",
        first("kind"), True if first("dead") == "1" else None,
        opened=True if first("opened") == "1" else None, rated=False if first("rated") == "0" else None, not_status=first("not_status"),
    )
