"""各組 API 共用的工具：資料庫連線、典藏館的篩選條件。"""
from urllib.parse import parse_qs


from . import search
from .database import Collection, Resource, SessionLocal, Tag
from .metadata import NON_WORK_KINDS


def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def resource_filters(
    q: str = "", status: str | None = None, source: str | None = None, tags: list[str] | None = None, tag_mode: str = "all",
    kind: str | None = None, dead: bool | None = None, collection: int | None = None, opened: bool | None = None,
    rated: bool | None = None, not_status: str | None = None, works: bool = False, not_tags: list[str] | None = None,
) -> list:
    """典藏館的篩選條件；tags 可多選，tag_mode=all 需全部符合、any 符合任一即可；not_tags 是要排除的標籤。"""
    filters = []
    if not search.parse(q).empty:  # 拆詞、寬鬆比對與同義詞見 search.py
        with SessionLocal() as db:
            filters.append(Resource.id.in_(search.match_ids(db, q)))
    if status:
        filters.append(Resource.status == status)
    if source:
        filters.append(Resource.source_name == source)
    if kind:
        filters.append(Resource.kind == kind)
    if works:  # 只要作品：不含網站頁面、創作者頁
        filters.append(Resource.kind.not_in(NON_WORK_KINDS))
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
    excluded = [name for name in (not_tags or []) if name]
    if excluded:
        filters.append(~Resource.tags.any(Tag.name.in_(excluded)))
    return filters


def smart_filters(query: str) -> list:
    """智慧書單的條件（與典藏館網址相同的參數）轉成查詢條件。"""
    params = parse_qs(query)
    first = lambda key: (params.get(key) or [None])[0]
    return resource_filters(
        first("q") or "", first("status"), first("source"), params.get("tag", []), "any" if first("match") == "any" else "all",
        first("kind"), True if first("dead") == "1" else None,
        opened=True if first("opened") == "1" else None, rated=False if first("rated") == "0" else None, not_status=first("not_status"), not_tags=params.get("not_tag", []),
    )
