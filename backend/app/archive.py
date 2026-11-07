"""Shared catalogue logic: tags, status normalisation, backup export and import."""
import json
from datetime import datetime, timezone
from typing import Iterable, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import Collection, DuplicateDismissal, Job, Resource, Setting, Source, Tag, TagAlias, TagBlock, UndoEntry, collection_items, resource_tags
from .titles import short_title

STATUSES = ["未讀", "閱讀中", "已完食", "暫時擱置", "棄坑"]
DEFAULT_SOURCE = "其他網站"
BACKUP_FORMAT = "oas-backup"
BACKUP_VERSION = 5  # 2：類型、標籤分類、同義詞與封鎖清單；3：自訂書單；4：閱讀進度、完食時間、智慧書單；5：巡檢時間、標籤健檢的保留清單、不是重複的組合

# 舊版曾使用的狀態字串，對應至目前的五種狀態。
STATUS_ALIASES = {"未讀/未看": "未讀", "未看": "未讀", "已讀": "已完食", "已看": "已完食", "已讀/已看": "已完食"}


def normalise_status(value: str | None) -> str:
    value = (value or "").strip()
    if value in STATUSES:
        return value
    return STATUS_ALIASES.get(value, "未讀")


def split_tags(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").replace("，", ",").split(",") if item.strip()]


def resolve_alias(aliases: dict[str, str], name: str) -> str:
    """沿著同義詞一路換到最後的名稱：A→B、B→C 時 A 會變成 C（手機同步、先後合併都可能留下這種鏈）；繞回來的鏈停在繞回之前。"""
    seen = {name.lower()}
    first = current = aliases.get(name.lower(), name)
    while current.lower() not in seen:
        seen.add(current.lower())
        current = aliases.get(current.lower(), current)
    return current if aliases.get(current.lower(), current) == current else first


def tag_rules(db: Session) -> tuple[dict[str, str], set[str]]:
    """整理標籤用的規則：（同義詞 {小寫原名: 名稱}，封鎖清單 {小寫名稱}）。"""
    return dict(db.execute(select(TagAlias.alias, TagAlias.canonical)).all()), set(db.scalars(select(TagBlock.name)).all())


def apply_tag_rules(aliases: dict[str, str], blocked: set[str], values: Iterable[str]) -> list[str]:
    """不碰資料庫的 normalise_tags（修復工具預演時也用這份規則）。"""
    names = []
    for value in values:
        name = (value or "").strip()[:100]
        if not name:
            continue
        name = resolve_alias(aliases, name)
        if name.lower() not in blocked and name not in names:
            names.append(name)
    return sorted(names)


def explain_tag_rules(aliases: dict[str, str], blocked: set[str], values: Iterable[str]) -> tuple[list[str], list[dict], list[str]]:
    """apply_tag_rules 加上說明（收錄預覽用）：哪些原文被規則換成別的名稱、哪些被封鎖略過。"""
    changed, dropped = [], []
    for value in values:
        name = (value or "").strip()[:100]
        if not name:
            continue
        final = resolve_alias(aliases, name)
        if final.lower() in blocked:
            dropped.append(name)
        elif final != name and not any(entry["from"] == name for entry in changed):
            changed.append({"from": name, "to": final})
    return apply_tag_rules(aliases, blocked, values), changed, list(dict.fromkeys(dropped))


def normalise_tags(db: Session, values: Iterable[str]) -> list[str]:
    """套用同義詞與封鎖清單：英文或簡體的同義標籤換成你整理後的名稱，宣傳字等直接略過。
    封鎖清單比對的是換完之後的名稱，所以封鎖一個翻譯後的標籤，會連同指向它的同義詞一起擋掉。"""
    return apply_tag_rules(*tag_rules(db), values)


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
        "title": resource.title, "short_title": short_title(resource.title), "note": resource.note, "image_url": resource.image_url,
        "rating": resource.rating, "status": resource.status, "kind": resource.kind or "", "image_kind": resource.image_kind or "", "link_dead": bool(resource.link_dead),
        "collections": sorted(collection.id for collection in resource.collections),
        "tags": sorted(tag.name for tag in resource.tags),
        "created_at": isoformat(resource.created_at), "updated_at": isoformat(resource.updated_at),
        "last_opened_at": isoformat(resource.last_opened_at),
        "progress": resource.progress or "", "finished_at": isoformat(resource.finished_at),
    }


def _dismissal_groups(db: Session) -> list[list[dict]]:
    """「不是重複」的組合改用來源＋代碼表示：鍵裡的資料庫編號換一台裝置就對不上。作品已刪到不足兩件的組合不匯出。"""
    members = {row.id: {"source_name": row.source_name, "code": row.code} for row in db.execute(select(Resource.id, Resource.source_name, Resource.code))}
    groups = []
    for key in sorted(db.scalars(select(DuplicateDismissal.key)).all()):
        group = [members[int(part)] for part in key.split(",") if part.isdigit() and int(part) in members]
        if len(group) >= 2:
            groups.append(group)
    return groups


def _apply_link_check(db: Session, row, item: dict) -> int:
    """備份的巡檢時間比本機新才更新時間與失效標記；回傳更新的件數。不動 updated_at（和巡檢本身一樣，典藏館排序不變）。"""
    checked = parse_datetime(item.get("link_checked_at"))
    local = parse_datetime(row.link_checked_at)
    if checked is None or (local is not None and checked <= local):
        return 0
    values = {"link_checked_at": checked.astimezone(timezone.utc), "updated_at": row.updated_at}
    if "link_dead" in item:
        values["link_dead"] = bool(item["link_dead"])
    db.execute(Resource.__table__.update().where(Resource.id == row.id).values(**values))
    return 1


def export_backup(db: Session) -> dict:
    from .insights import kept_pairs, kept_tags  # insights 匯入本模組，放在這裡避免循環

    resources = db.scalars(select(Resource).order_by(Resource.id)).all()
    sources = db.scalars(select(Source).order_by(Source.name)).all()
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": isoformat(datetime.now(timezone.utc)),
        "counts": {"resources": len(resources), "sources": len(sources)},
        "sources": [{"name": source.name, "template": source.template, "kind": source.kind} for source in sources],
        "resources": [{**{key: value for key, value in serialise(item).items() if key not in ("id", "short_title")},
                       "link_checked_at": isoformat(item.link_checked_at)} for item in resources],
        "tag_categories": dict(db.execute(select(Tag.name, Tag.category).where(Tag.category != "tag")).all()),
        "tag_aliases": dict(db.execute(select(TagAlias.alias, TagAlias.canonical)).all()),
        "tag_blocklist": sorted(db.scalars(select(TagBlock.name)).all()),
        # 書單以「來源＋代碼」記錄成員，還原到另一個資料庫時也對得上。
        "collections": [
            {"name": collection.name, "position": collection.position, "query": collection.query or "",
             "items": [{"source_name": item.source_name, "code": item.code} for item in collection.resources]}
            for collection in db.scalars(select(Collection).order_by(Collection.position, Collection.id)).all()
        ],
        "tag_health_keep": kept_tags(db),
        "tag_pairs_keep": kept_pairs(db),
        "duplicate_dismissals": _dismissal_groups(db),
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
    tag_health_keep: Iterable[str] | None = None,
    tag_pairs_keep: Iterable[str] | None = None,
    duplicate_dismissals: Iterable[Iterable[dict]] | None = None,
) -> dict:
    """Insert sources and resources, de-duplicating on (source_name, code).

    merge   — keep existing rows, add only missing ones（既有作品只在備份的巡檢時間較新時更新巡檢時間與失效標記）.
    replace — delete all resources, tags and sources first.
    標籤健檢的保留清單取聯集；「不是重複」的組合換算成本機編號後加入，組內有作品找不到就略過該組。
    舊版備份沒有這些欄位時不動本機資料。Caller commits.
    """
    from .duplicates import group_key  # 這兩個模組都間接匯入本模組，放在這裡避免循環
    from .insights import KEEP_KEY, PAIRS_KEY, kept_pairs, kept_tags

    if mode == "replace":
        db.execute(delete(resource_tags))
        db.execute(delete(Resource))
        db.execute(delete(Tag))
        db.execute(delete(Source))
        db.execute(delete(collection_items))
        db.execute(delete(Collection))
        db.execute(delete(TagAlias))
        db.execute(delete(TagBlock))
        # 這三種都記著資料庫編號，取代後編號都換了，留著會指到別的作品（呼叫端 commit 後還要重設背景工作的進度）
        db.execute(delete(DuplicateDismissal))
        db.execute(delete(UndoEntry))
        db.execute(delete(Job))
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

    report = {"created": 0, "skipped": 0, "sources_created": 0, "link_checks_updated": 0, "keep_added": 0, "dismissals_added": 0, "dismissals_skipped": 0}

    known_sources = set(db.scalars(select(Source.name)).all())
    for source in sources:
        name = (source.get("name") or "").strip()
        template = (source.get("template") or "").strip()
        if not name or not template or name in known_sources:
            continue
        db.add(Source(name=name[:120], template=template[:2000], kind=str(source.get("kind") or "")[:20]))
        known_sources.add(name)
        report["sources_created"] += 1

    existing = {(row.source_name, row.code): row for row in db.execute(select(Resource.id, Resource.source_name, Resource.code, Resource.link_checked_at, Resource.updated_at))}
    seen = set(existing)
    for item in resources:
        code = str(item.get("code") or "").strip()[:500]
        source_name = (str(item.get("source_name") or "").strip() or DEFAULT_SOURCE)[:120]
        if not code or (source_name, code) in seen:
            report["skipped"] += 1
            if (source_name, code) in existing:
                report["link_checks_updated"] += _apply_link_check(db, existing[(source_name, code)], item)
            continue
        seen.add((source_name, code))
        try:
            rating = min(max(float(item.get("rating") or 0), 0.0), 5.0)
        except (TypeError, ValueError):
            rating = 0.0
        created_at = parse_datetime(item.get("created_at")) or datetime.now(timezone.utc)
        checked = parse_datetime(item.get("link_checked_at"))
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
            image_kind=str(item.get("image_kind") or "")[:10],
            link_dead=bool(item.get("link_dead")),
            link_checked_at=checked.astimezone(timezone.utc) if checked else None,
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

    # 標籤健檢的保留清單：取聯集，只有真的多了名稱才寫入。
    current = set(kept_tags(db))
    wanted = {str(name).strip()[:100] for name in tag_health_keep or []} - {""}
    if wanted - current:
        report["keep_added"] = len(wanted - current)
        db.merge(Setting(key=KEEP_KEY, value=json.dumps(sorted(current | wanted), ensure_ascii=False)))

    # 「一起出現」的保留組合也取聯集。
    current_pairs = set(kept_pairs(db))
    wanted_pairs = {str(pair).strip()[:210] for pair in tag_pairs_keep or []} - {""}
    if wanted_pairs - current_pairs:
        report["pairs_added"] = len(wanted_pairs - current_pairs)
        db.merge(Setting(key=PAIRS_KEY, value=json.dumps(sorted(current_pairs | wanted_pairs), ensure_ascii=False)))

    # 「不是重複」的組合：兩邊的資料庫編號不同，以來源＋代碼換算成本機編號。
    dismissed = set(db.scalars(select(DuplicateDismissal.key)).all())
    for group in duplicate_dismissals or []:
        ids = [keys.get((str(item.get("source_name") or ""), str(item.get("code") or ""))) for item in group]
        if None in ids or len(set(ids)) < 2:
            report["dismissals_skipped"] += 1
            continue
        key = group_key(ids)
        if key not in dismissed:
            db.add(DuplicateDismissal(key=key))
            dismissed.add(key)
            report["dismissals_added"] += 1
    db.flush()
    return report
