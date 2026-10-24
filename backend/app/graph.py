"""Relationship graph: items linked by shared tags.

Similarity is the cosine of IDF-weighted tag vectors, so a rare shared tag pulls two items together far more than
a ubiquitous one (翻譯、語言等標籤幾乎不提供資訊). Each item keeps only its strongest links (TOP_K), which keeps the
graph readable and the payload small. The frontend turns similarity into spring length: closer means more alike.
"""
import math
from collections import defaultdict
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .archive import serialise
from .covers import with_ratios
from .database import Resource, resource_tags

TOP_K = 6
MIN_SIMILARITY = 0.12
# 出現在超過此比例作品中的標籤不參與連線計算：IDF 已接近零，卻會產生大量配對。
MAX_TAG_SHARE = 0.3

_cache_lock = Lock()
_cache: dict = {"key": None, "edges": None, "shared": None}


def _cache_key(db: Session) -> tuple:
    return (
        db.scalar(select(func.count(Resource.id))),
        db.scalar(select(func.max(Resource.id))),
        db.scalar(select(func.count()).select_from(resource_tags)),
        db.scalar(select(func.max(Resource.updated_at))),
    )


def compute_edges(tag_sets: dict[int, set[str]]) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], list[str]]]:
    """Return ({(a, b): similarity}, {(a, b): shared tags ordered by rarity}) with a < b, pruned to each node's TOP_K."""
    total = len(tag_sets)
    if total < 2:
        return {}, {}
    members: dict[str, list[int]] = defaultdict(list)
    for item_id, names in tag_sets.items():
        for name in names:
            members[name].append(item_id)
    idf = {name: math.log(total / len(ids)) for name, ids in members.items()}
    norms = {item_id: math.sqrt(sum(idf[name] ** 2 for name in names)) for item_id, names in tag_sets.items()}

    dot: dict[tuple[int, int], float] = defaultdict(float)
    for name, ids in members.items():
        if len(ids) < 2 or len(ids) > total * MAX_TAG_SHARE:
            continue
        weight = idf[name] ** 2
        ids = sorted(ids)
        for index, a in enumerate(ids):
            for b in ids[index + 1:]:
                dot[(a, b)] += weight

    neighbours: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for (a, b), value in dot.items():
        similarity = value / (norms[a] * norms[b]) if norms[a] and norms[b] else 0.0
        if similarity >= MIN_SIMILARITY:
            neighbours[a].append((similarity, b))
            neighbours[b].append((similarity, a))

    edges: dict[tuple[int, int], float] = {}
    for a, linked in neighbours.items():
        linked.sort(key=lambda pair: (-pair[0], pair[1]))
        for similarity, b in linked[:TOP_K]:
            edges[(min(a, b), max(a, b))] = round(similarity, 4)

    shared = {
        pair: sorted(tag_sets[pair[0]] & tag_sets[pair[1]], key=lambda name: (-idf[name], name))[:3]
        for pair in edges
    }
    return edges, shared


def _edges(db: Session, resources: list[Resource]) -> tuple[dict, dict]:
    key = _cache_key(db)
    with _cache_lock:
        if _cache["key"] == key:
            return _cache["edges"], _cache["shared"]
    edges, shared = compute_edges({item.id: {tag.name for tag in item.tags} for item in resources})
    with _cache_lock:
        _cache.update(key=key, edges=edges, shared=shared)
    return edges, shared


def _node(item: dict, degree: int) -> dict:
    keys = ("id", "title", "code", "status", "rating", "tags", "image_url", "updated_at", "source_name", "cover_ratio", "cover_colour")
    return {**{key: item.get(key) for key in keys}, "degree": degree}


def graph(db: Session, tag: str | None = None, focus: int | None = None, limit: int = 400) -> dict:
    """Nodes and weighted edges.

    tag   — only items carrying this tag (and the links among them)
    focus — the item, its neighbours and their neighbours (two hops)
    limit — otherwise the most connected items, ranked by summed similarity
    """
    resources = db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all()
    edges, shared = _edges(db, resources)

    adjacency: dict[int, dict[int, float]] = defaultdict(dict)
    for (a, b), similarity in edges.items():
        adjacency[a][b] = similarity
        adjacency[b][a] = similarity
    strength = {item_id: sum(linked.values()) for item_id, linked in adjacency.items()}

    if focus is not None:
        if not any(item.id == focus for item in resources):
            return {"nodes": [], "edges": [], "total_linked": len(adjacency), "isolated": 0}
        first = set(adjacency.get(focus, {}))
        second = {other for item_id in first for other in adjacency[item_id]}
        chosen = {focus} | first | second
        if len(chosen) > limit:
            ranked = sorted(second - first - {focus}, key=lambda item_id: -max(adjacency[item_id].get(n, 0) for n in first))
            chosen = {focus} | first | set(ranked[:max(0, limit - 1 - len(first))])
    elif tag:
        chosen = {item.id for item in resources if any(t.name == tag for t in item.tags)}
    else:
        chosen = set(sorted(adjacency, key=lambda item_id: (-strength[item_id], item_id))[:limit])

    by_id = {item.id: item for item in resources}
    rows = with_ratios(db, [serialise(by_id[item_id]) for item_id in sorted(chosen)])
    selected_edges = [
        {"source": a, "target": b, "similarity": similarity, "shared": shared[(a, b)]}
        for (a, b), similarity in sorted(edges.items()) if a in chosen and b in chosen
    ]
    degree: dict[int, int] = defaultdict(int)
    for edge in selected_edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    return {
        "nodes": [_node(row, degree[row["id"]]) for row in rows],
        "edges": selected_edges,
        "total_linked": len(adjacency),
        "isolated": len(resources) - len(adjacency),
    }


def related(db: Session, resource_id: int, limit: int = 8) -> list[dict]:
    """The strongest links of one item, most similar first."""
    resources = db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all()
    edges, shared = _edges(db, resources)
    linked = []
    for (a, b), similarity in edges.items():
        if resource_id in (a, b):
            linked.append((similarity, b if a == resource_id else a, shared[(a, b)]))
    linked.sort(key=lambda entry: (-entry[0], entry[1]))
    by_id = {item.id: item for item in resources}
    rows = with_ratios(db, [serialise(by_id[item_id]) for _, item_id, _ in linked[:limit]])
    for row, (similarity, _, names) in zip(rows, linked[:limit]):
        row["similarity"], row["shared"] = similarity, names
    return rows


def similar(db: Session, resource_id: int, limit: int = 12, unread_only: bool = False) -> list[dict]:
    """與一件作品最相似的典藏：直接和所有作品比較（不受圖譜每件只留 TOP_K 條連線的限制），可只看未讀。"""
    resources = db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all()
    tag_sets = {item.id: {tag.name for tag in item.tags} for item in resources}
    target = tag_sets.get(resource_id)
    total = len(resources)
    if not target or total < 2:
        return []
    df: dict[str, int] = defaultdict(int)
    for names in tag_sets.values():
        for name in names:
            df[name] += 1
    idf = {name: math.log(total / count) for name, count in df.items()}
    norm = lambda names: math.sqrt(sum(idf[name] ** 2 for name in names))
    target_norm = norm(target)
    useful = {name for name in target if 1 < df[name] <= total * MAX_TAG_SHARE}
    by_id = {item.id: item for item in resources}
    scores = []
    for item_id, names in tag_sets.items():
        if item_id == resource_id or not (names & useful):
            continue
        if unread_only and by_id[item_id].status != "未讀":
            continue
        shared = names & target
        value = sum(idf[name] ** 2 for name in shared & useful)
        similarity = value / (target_norm * norm(names)) if target_norm and names else 0.0
        if similarity >= 0.08:
            scores.append((similarity, item_id, sorted(shared, key=lambda name: (-idf[name], name))[:3]))
    scores.sort(key=lambda entry: (-entry[0], entry[1]))
    rows = with_ratios(db, [serialise(by_id[item_id]) for _, item_id, _ in scores[:limit]])
    for row, (similarity, _, names) in zip(rows, scores[:limit]):
        row["similarity"], row["shared"] = round(similarity, 4), names
    return rows
