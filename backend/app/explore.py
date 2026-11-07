"""Explore page: curated shelves computed from the user's own catalogue.

Shelves
- preference : unread items scored by tag affinity learned from finished / in-progress / highly rated items;
               12 drawn from the top 60 with score-weighted sampling, so 「換一批」 rotates them
- deep       : unread, older than 180 days, never opened — random sample
- tag_walk   : two different random mid-sized tags (keys tag_walk, tag_walk_2), quiet corners of the collection
- recent     : most recently archived
A `seed` makes the random choices reproducible; the page's 「換一批」 increments it.

Home page (`home`) = a hero (up to 5 in-progress items, topped up with deep-dive picks) + rows:
繼續閱讀 / the explore shelves above / one row per collection.（「最近完食」2026-11-03 拿掉：OAS 沒有重讀次數，這列只是湊數）
"""
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import cache, metadata
from .archive import serialise
from .covers import with_ratios
from .database import Collection, Resource, collection_items
from .deps import smart_filters

SHELF_SIZE = 12
HERO_SIZE = 5
POOL_SIZE = 60
TAG_WALKS = 2
STATUS_WEIGHT = {"已完食": 2.0, "閱讀中": 1.5, "暫時擱置": 0.3, "棄坑": -1.0}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def tag_affinity(resources: list[Resource]) -> dict[str, float]:
    weights: dict[str, float] = defaultdict(float)
    for item in resources:
        signal = STATUS_WEIGHT.get(item.status, 0.0)
        if item.rating >= 4:
            signal += item.rating / 2
        elif 0 < item.rating < 2.5:
            signal -= 0.5
        if signal:
            for tag in item.tags:
                if tag.name not in metadata.ORIGINAL_NAMES:  # 「原創」不是口味
                    weights[tag.name] += signal
    return {name: weight for name, weight in weights.items() if weight > 0}


def preference_shelf(resources: list[Resource], unread: list[Resource], rng: random.Random) -> tuple[list[Resource], list[str]]:
    weights = tag_affinity(resources)
    if not weights:
        return [], []
    scored = []
    for item in unread:
        names = [tag.name for tag in item.tags]
        if not names:
            continue
        score = sum(weights.get(name, 0.0) for name in names) / math.sqrt(len(names))
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: (pair[0], -pair[1].id), reverse=True)
    # 從前 POOL_SIZE 名中依分數加權抽樣（Efraimidis–Spirakis）：高分作品較常出現，但「換一批」會輪替。
    pool = scored[:POOL_SIZE]
    keyed = [(rng.random() ** (1.0 / score), score, item) for score, item in pool]
    picked = sorted(keyed, key=lambda entry: entry[0], reverse=True)[:SHELF_SIZE]
    picked.sort(key=lambda entry: entry[1], reverse=True)
    top_tags = [name for name, _ in sorted(weights.items(), key=lambda pair: pair[1], reverse=True)[:3]]
    return [item for _, _, item in picked], top_tags


def _load(db: Session) -> list[Resource]:
    """首頁與探索用的作品：網站頁面、創作者頁不是作品，不推薦。"""
    return db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.kind.not_in(metadata.NON_WORK_KINDS)).order_by(Resource.id)).all()


def _deep(unread: list[Resource]) -> list[Resource]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    return [item for item in unread if _aware(item.created_at) < cutoff and item.last_opened_at is None]


def _finish(db: Session, shelves: list[dict]) -> list[dict]:
    for shelf in shelves:
        shelf["items"] = with_ratios(db, [serialise(item) for item in shelf["items"]])
    return shelves


def explore(db: Session, seed: int = 0) -> dict:
    return {"seed": seed, "shelves": _finish(db, explore_shelves(_load(db), random.Random(seed)))}


def explore_shelves(resources: list[Resource], rng: random.Random) -> list[dict]:
    unread = [item for item in resources if item.status == "未讀"]
    shelves = []

    preferred, top_tags = preference_shelf(resources, unread, rng)
    if preferred:
        shelves.append({
            "key": "preference", "title": "依你的口味",
            "subtitle": f"從已完食、閱讀中與高分作品學到的偏好：{'、'.join('#' + tag for tag in top_tags)}",
            "items": preferred,
        })

    deep = _deep(unread)
    if deep:
        shelves.append({
            "key": "deep", "title": "深潛區", "subtitle": f"收藏超過半年、從未開啟的 {len(deep)} 件作品中隨機挑選",
            "items": rng.sample(deep, min(SHELF_SIZE, len(deep))),
        })

    shelves += tag_walks(resources, rng)

    recent = sorted(resources, key=lambda item: (_aware(item.created_at), item.id), reverse=True)[:SHELF_SIZE]
    if recent:
        shelves.append({"key": "recent", "title": "最近收錄", "subtitle": "依收錄時間由新到舊", "items": recent})
    return shelves


WALK_SKIP = {"artist", "group"}


def tag_walks(resources: list[Resource], rng: random.Random) -> list[dict]:
    """兩列標籤漫遊，各用一個不同的中等數量標籤；第二列只放第一列沒出現過的作品，至少要有 3 件才成列。"""
    # 作者、社團是「誰做的」而不是題材，漫遊起來像在看某人作品集，不算走進典藏的角落
    counts = Counter(tag.name for item in resources for tag in item.tags if tag.category not in WALK_SKIP)
    mid_tags = sorted(name for name, count in counts.items() if 3 <= count <= 25)
    rows, shown = [], set()
    for tag in rng.sample(mid_tags, len(mid_tags)):
        members = [item for item in resources if item.id not in shown and any(t.name == tag for t in item.tags)]
        if len(members) < 3:
            continue
        rng.shuffle(members)
        members = members[:SHELF_SIZE]
        shown.update(item.id for item in members)
        rows.append({
            "key": "tag_walk" if not rows else f"tag_walk_{len(rows) + 1}", "title": f"標籤漫遊：#{tag}",
            "subtitle": f"典藏中較少走進的角落，共 {counts[tag]} 件", "tag": tag, "items": members,
        })
        if len(rows) == TAG_WALKS:
            break
    return rows


def _latest(items: list[Resource], key) -> list[Resource]:
    return sorted(items, key=lambda item: (_aware(key(item)), item.id), reverse=True)


def home(db: Session, seed: int = 0, personal: bool = False) -> dict:
    """首頁；同一個 seed、資料沒變時用上次的結果（回首頁不用再等）。personal：多放待評分、最近開啟（手機沒有側欄，放在這裡）。"""
    return cache.cached(("home", seed, personal), lambda: _home(db, seed, personal), copied=False)  # 只讀，直接回傳給前端


def _personal_rows(resources: list[Resource], reading: list[Resource]) -> list[dict]:
    """待評分（已完食、還沒評分）與最近開啟；前面的排已經出現的作品不重複。"""
    rows = []
    unrated = _latest([item for item in resources if item.status == "已完食" and not item.rating], lambda item: item.last_opened_at or item.updated_at)
    if unrated:
        rows.append({"key": "unrated", "title": "待評分", "subtitle": f"{len(unrated)} 件已完食還沒評分", "items": unrated[:SHELF_SIZE]})
    shown = {item.id for item in [*reading[:SHELF_SIZE], *unrated[:SHELF_SIZE]]}
    opened = _latest([item for item in resources if item.last_opened_at and item.id not in shown], lambda item: item.last_opened_at)
    if opened:
        rows.append({"key": "opened", "title": "最近開啟", "items": opened[:SHELF_SIZE]})
    return rows


def _home(db: Session, seed: int, personal: bool = False) -> dict:
    rng = random.Random(seed)
    resources = _load(db)
    reading = _latest([item for item in resources if item.status == "閱讀中"], lambda item: item.last_opened_at or item.updated_at)
    deep = _deep([item for item in resources if item.status == "未讀"])
    deep = rng.sample(deep, min(HERO_SIZE, len(deep)))  # 依 seed 隨機挑，「換一批」或重新開啟就會換
    hero = (reading + deep)[:HERO_SIZE] or _latest(resources, lambda item: item.created_at)[:1]

    rows = []
    if reading:
        rows.append({"key": "reading", "title": "繼續閱讀", "subtitle": f"{len(reading)} 件閱讀中，依最近開啟排列", "items": reading[:SHELF_SIZE]})
    if personal:
        rows += _personal_rows(resources, reading)
    rows += explore_shelves(resources, rng)
    rows += collection_rows(db)
    return {"seed": seed, "hero": _finish(db, [{"items": hero}])[0]["items"], "rows": _finish(db, rows)}


def collection_rows(db: Session) -> list[dict]:
    """One row per collection, in the sidebar's order; smart collections are evaluated live."""
    rows = []
    for collection in db.scalars(select(Collection).order_by(Collection.position, Collection.id)).all():
        if collection.query:
            items = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(*smart_filters(collection.query))
                               .order_by(Resource.updated_at.desc(), Resource.id.desc()).limit(SHELF_SIZE)).all()
        else:
            items = db.scalars(select(Resource).options(selectinload(Resource.tags))
                               .join(collection_items, collection_items.c.resource_id == Resource.id)
                               .where(collection_items.c.collection_id == collection.id)
                               .order_by(collection_items.c.added_at.desc(), Resource.id.desc()).limit(SHELF_SIZE)).all()
        if items:
            rows.append({"key": f"collection-{collection.id}", "title": f"書單：{collection.name}", "collection": collection.id, "items": items})
    return rows
