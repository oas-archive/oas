"""作品相似度：共用標籤的 IDF 加權相似度，罕見標籤比常見標籤有份量（翻譯、語言等標籤幾乎不提供資訊）。

標籤種類也有份量（CATEGORY_WEIGHTS）：原作最重，其次角色；作者和一般標籤相同，社團最輕。比例和模擬依據見 docs/相關作品算法.md。
除數用「標籤總量」的 LENGTH_POWER 次方：餘弦相似度（1 次方）會把只有一兩個標籤的作品灌高，排到同作品前面。
"""
import math
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import cache, metadata
from .archive import serialise
from .covers import with_ratios
from .database import Resource, Tag, resource_tags

# 出現在超過此比例作品中的標籤不參與計算：IDF 已接近零，卻會產生大量配對。
MAX_TAG_SHARE = 0.3
CATEGORY_WEIGHTS = {"parody": 8.0, "character": 2.0, "artist": 1.0, "group": 0.5}
LENGTH_POWER = 0.4
MIN_SIMILARITY = 0.04  # 加權後主要標籤佔掉大部分份量，門檻要比餘弦時低，清單才不會太短
# 「原創」不是一部作品，只代表沒有原作，不能吃原作的加權
NOT_A_WORK = metadata.ORIGINAL_NAMES | {"原創作品"}


def _weight(name: str, category: str) -> float:
    if category == "parody" and name.strip().lower() in NOT_A_WORK:
        return 1.0
    return CATEGORY_WEIGHTS.get(category, 1.0)


def _similar_index(db: Session) -> tuple[dict, dict, dict, dict, dict, dict]:
    """相關作品要用的標籤索引（只讀）：各件的標籤集合、狀態、IDF、出現件數、向量長度、各標籤的份量。資料沒變時重用（見 cache.py）。"""
    tag_sets: dict[int, frozenset] = {}
    for resource_id, name in db.execute(select(resource_tags.c.resource_id, Tag.name).join(Tag, Tag.id == resource_tags.c.tag_id)):
        tag_sets.setdefault(resource_id, set()).add(name)
    statuses = dict(db.execute(select(Resource.id, Resource.status)).all())
    tag_sets = {item_id: frozenset(tag_sets.get(item_id, ())) for item_id in sorted(statuses)}
    total = len(tag_sets)
    df: dict[str, int] = defaultdict(int)
    for names in tag_sets.values():
        for name in names:
            df[name] += 1
    idf = {name: math.log(total / count) for name, count in df.items()} if total else {}
    # 份量＝種類權重 × IDF²：共同標籤加總時用它，向量長度也用它，兩邊才一致
    categories = dict(db.execute(select(Tag.name, Tag.category)).all())
    weights = {name: _weight(name, categories.get(name, "tag")) * value ** 2 for name, value in idf.items()}
    norms = {item_id: math.sqrt(sum(weights[name] for name in names)) for item_id, names in tag_sets.items()}
    return tag_sets, statuses, idf, dict(df), norms, weights


def similar(db: Session, resource_id: int, limit: int = 12, unread_only: bool = False) -> list[dict]:
    """與一件作品最相似的典藏：直接和所有作品比較，可只看未讀。"""
    tag_sets, statuses, idf, df, norms, weights = cache.cached("similar-index", lambda: _similar_index(db), copied=False)
    target = tag_sets.get(resource_id)
    total = len(tag_sets)
    if not target or total < 2:
        return []
    target_norm = norms[resource_id]
    self_score = target_norm ** (2 - 2 * LENGTH_POWER)
    useful = {name for name in target if 1 < df[name] <= total * MAX_TAG_SHARE}
    scores = []
    for item_id, names in tag_sets.items():
        if item_id == resource_id or not (names & useful):
            continue
        if unread_only and statuses[item_id] != "未讀":
            continue
        shared = names & target
        value = sum(weights[name] for name in shared & useful)
        rank = value / (target_norm * norms[item_id]) ** LENGTH_POWER if target_norm and names else 0.0
        # 顯示的百分比：除以作品和自己比的分數，標籤完全相同時是 100%
        similarity = min(1.0, rank / self_score) if self_score else 0.0
        if similarity >= MIN_SIMILARITY:
            scores.append((rank, similarity, item_id, sorted(shared, key=lambda name: (-weights[name], name))[:3]))
    scores.sort(key=lambda entry: (-entry[0], entry[2]))
    wanted = [item_id for _, _, item_id, _ in scores[:limit]]
    by_id = {item.id: item for item in db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(wanted)))}
    rows = with_ratios(db, [serialise(by_id[item_id]) for item_id in wanted])
    for row, (_, similarity, _, names) in zip(rows, scores[:limit]):
        row["similarity"], row["shared"] = round(similarity, 4), names
    return rows
