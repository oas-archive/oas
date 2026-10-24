"""Explore page: curated shelves computed from the user's own catalogue.

Shelves
- preference : unread items scored by tag affinity learned from finished / in-progress / highly rated items;
               12 drawn from the top 60 with score-weighted sampling, so 「換一批」 rotates them
- deep       : unread, older than 180 days, never opened — random sample
- tag_walk   : a random mid-sized tag, a quiet corner of the collection
- recent     : most recently archived
A `seed` makes the random choices reproducible; the page's 「換一批」 increments it.
"""
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .archive import serialise
from .covers import with_ratios
from .database import Resource

SHELF_SIZE = 12
POOL_SIZE = 60
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


def explore(db: Session, seed: int = 0) -> dict:
    rng = random.Random(seed)
    resources = db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all()
    unread = [item for item in resources if item.status == "未讀"]
    shelves = []

    preferred, top_tags = preference_shelf(resources, unread, rng)
    if preferred:
        shelves.append({
            "key": "preference", "title": "依你的口味",
            "subtitle": f"從已完食、閱讀中與高分作品學到的偏好：{'、'.join('#' + tag for tag in top_tags)}",
            "items": preferred,
        })

    cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    deep = [item for item in unread if _aware(item.created_at) < cutoff and item.last_opened_at is None]
    if deep:
        shelves.append({
            "key": "deep", "title": "深潛區", "subtitle": f"收藏超過半年、從未開啟的 {len(deep)} 件作品中隨機挑選",
            "items": rng.sample(deep, min(SHELF_SIZE, len(deep))),
        })

    counts = Counter(tag.name for item in resources for tag in item.tags)
    mid_tags = sorted(name for name, count in counts.items() if 3 <= count <= 25)
    if mid_tags:
        tag = rng.choice(mid_tags)
        members = [item for item in resources if any(t.name == tag for t in item.tags)]
        rng.shuffle(members)
        shelves.append({
            "key": "tag_walk", "title": f"標籤漫遊：#{tag}", "subtitle": f"典藏中較少走進的角落，共 {counts[tag]} 件",
            "tag": tag, "items": members[:SHELF_SIZE],
        })

    recent = sorted(resources, key=lambda item: (_aware(item.created_at), item.id), reverse=True)[:SHELF_SIZE]
    if recent:
        shelves.append({"key": "recent", "title": "最近收錄", "subtitle": "依收錄時間由新到舊", "items": recent})

    for shelf in shelves:
        shelf["items"] = with_ratios(db, [serialise(item) for item in shelf["items"]])
    return {"seed": seed, "shelves": shelves}
