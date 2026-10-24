"""「關聯」頁與統計頁的計算：口味地圖、標籤健檢、重複作品、統計。

都是從現有資料即時算出來的，不另外存結果；典藏數千件以內每次計算都在一秒內。
"""
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from urllib.parse import urlsplit

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session, selectinload

from .archive import serialise
from .covers import with_ratios
from .database import DuplicateDismissal, Resource, Tag, TagAlias, collection_items, resource_tags

# 口味地圖只用這些分類的標籤分群；語言、類別幾乎每件都有，分不出口味。
THEME_CATEGORIES = {"tag", "parody", "artist", "group"}
THEME_MAX_SHARE = 0.35  # 超過三成五作品都有的標籤太普遍，不當成一群
THEME_MERGE_JACCARD = 0.6  # 兩個標籤的作品重疊這麼多時，視為同一群（例如「純愛向」與「甜蜜」）
PRIOR_WEIGHT = 3  # 評分的貝氏平均：件數少的群組向整體平均靠攏，不會因為一件五星就排第一
NAMED_CATEGORIES = {"artist", "group", "parody", "character"}
SUSPICIOUS_SHARE = 0.12
STATUS_RANK = {"已完食": 4, "閱讀中": 3, "暫時擱置": 2, "棄坑": 1, "未讀": 0}


def _resources(db: Session) -> list[Resource]:
    return db.scalars(select(Resource).options(selectinload(Resource.tags)).order_by(Resource.id)).all()


def _card(db: Session, rows: list[Resource]) -> list[dict]:
    """卡片需要的欄位（含封面主色與比例）。"""
    return with_ratios(db, [serialise(row) for row in rows])


def _categories(db: Session) -> dict[str, str]:
    return dict(db.execute(select(Tag.name, Tag.category)).all())


# ---------- 口味地圖 ----------

def taste_map(db: Session, limit: int = 24) -> dict:
    """把作品依代表性標籤分成幾群，並算出每群的件數、未讀、平均評分與「口味分數」。"""
    resources = _resources(db)
    total = len(resources)
    rated_all = [row.rating for row in resources if row.rating > 0]
    prior = sum(rated_all) / len(rated_all) if rated_all else 3.0
    if total < 5:
        return {"themes": [], "prior": round(prior, 2), "total": total}
    categories = _categories(db)
    by_tag: dict[str, set[int]] = defaultdict(set)
    for row in resources:
        for tag in row.tags:
            if categories.get(tag.name, "tag") in THEME_CATEGORIES:
                by_tag[tag.name].add(row.id)
    min_count = max(3, round(total * 0.005))
    candidates = sorted(
        ((name, ids) for name, ids in by_tag.items() if min_count <= len(ids) <= total * THEME_MAX_SHARE),
        key=lambda pair: (-len(pair[1]), pair[0]),
    )
    themes: list[dict] = []
    for name, ids in candidates:
        for theme in themes:
            if len(ids & theme["ids"]) / len(ids | theme["ids"]) >= THEME_MERGE_JACCARD:
                if len(theme["names"]) < 3:
                    theme["names"].append(name)
                break
        else:
            themes.append({"names": [name], "ids": set(ids), "category": categories.get(name, "tag")})

    by_id = {row.id: row for row in resources}
    scored = []
    for theme in themes:
        rows = [by_id[item_id] for item_id in theme["ids"]]
        ratings = [row.rating for row in rows if row.rating > 0]
        statuses = Counter(row.status for row in rows)
        dropped = statuses["棄坑"] / len(rows)
        # 收藏得多＝口味所在（評分很少時的主要依據）；有評分時再依評分高低加減，棄坑多的扣分。
        bayes = (sum(ratings) + prior * PRIOR_WEIGHT) / (len(ratings) + PRIOR_WEIGHT)
        score = math.log(len(rows)) + (bayes - prior) * min(1.0, len(ratings) / PRIOR_WEIGHT) - dropped * 2
        scored.append((score, theme, rows, ratings, statuses))
    scored.sort(key=lambda entry: (-entry[0], -len(entry[2]), entry[1]["names"][0]))

    result = []
    for score, theme, rows, ratings, statuses in scored[:limit]:
        samples = sorted(rows, key=lambda row: (-row.rating, not row.image_url, -row.id))[:4]
        result.append({
            "names": theme["names"], "category": theme["category"], "count": len(rows),
            "unread": statuses["未讀"], "finished": statuses["已完食"], "dropped": statuses["棄坑"],
            "rated": len(ratings), "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "score": round(score, 3), "samples": _card(db, samples),
        })
    return {"themes": result, "prior": round(prior, 2), "total": total}


# ---------- 標籤健檢 ----------

def _name_key(name: str) -> str:
    """比對「名稱相近」用：忽略大小寫、空白與連字號，英文複數 s 也忽略。"""
    key = re.sub(r"[\s\-_·・.]+", "", name.lower())
    return key[:-1] if len(key) > 3 and key.endswith("s") and not key.endswith("ss") else key


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[㐀-鿿]", text))


def tag_health(db: Session) -> dict:
    resources = _resources(db)
    categories = _categories(db)
    counts: Counter = Counter()
    for row in resources:
        counts.update(tag.name for tag in row.tags)

    # 1. 名稱相近：只差大小寫、空白或單複數的標籤，建議合併到最常用的那個（同件數時優先中文）。
    groups: dict[str, list[str]] = defaultdict(list)
    for name in counts:
        groups[_name_key(name)].append(name)
    similar = []
    for names in groups.values():
        if len(names) < 2:
            continue
        names.sort(key=lambda name: (-counts[name], not _has_cjk(name), len(name), name))
        similar.append({"target": names[0], "sources": [{"name": name, "count": counts[name]} for name in names[1:]], "count": counts[names[0]]})
    similar.sort(key=lambda entry: -sum(source["count"] for source in entry["sources"]))
    similar_pairs = {frozenset((entry["target"], source["name"])) for entry in similar for source in entry["sources"]}

    # 2. 幾乎總是一起出現：兩個一般標籤九成以上同時出現，可能是同義詞（也可能只是常見組合，由你決定）。
    eligible = {name for name, count in counts.items() if count >= 3 and categories.get(name, "tag") == "tag"}
    together: Counter = Counter()
    for row in resources:
        names = sorted(tag.name for tag in row.tags if tag.name in eligible)
        together.update(combinations(names, 2))
    paired = []
    for (a, b), both in together.items():
        small, large = sorted((counts[a], counts[b]))
        if both >= 0.9 * small and small / large >= 0.5 and frozenset((a, b)) not in similar_pairs:
            target, source = (a, b) if (counts[a], _has_cjk(a)) >= (counts[b], _has_cjk(b)) else (b, a)
            paired.append({"target": target, "source": source, "together": both, "target_count": counts[target], "source_count": counts[source]})
    paired.sort(key=lambda entry: (-entry["together"], entry["target"]))

    # 3. 只用過一次的標籤（一般分類）；作者、角色本來就常只出現一次，不列入。
    by_tag_owner = {}
    for row in resources:
        for tag in row.tags:
            if counts[tag.name] == 1:
                by_tag_owner[tag.name] = row
    rare = sorted(
        ({"name": name, "id": row.id, "title": row.title or row.code} for name, row in by_tag_owner.items() if categories.get(name, "tag") == "tag"),
        key=lambda entry: entry["name"].lower(),
    )

    # 4. 孤兒作品：沒有標籤或只有一個標籤，跟其他作品連不起來；創作者與網站頁面本來就沒有標籤，不列入。
    orphans = [row for row in resources if len(row.tags) <= 1 and row.kind not in ("創作者", "其他")]
    unused = len(set(categories) - set(counts))

    # 5. 分類可疑：作者、社團、原作、角色通常只佔一小部分作品；被超過 SUSPICIOUS_SHARE 的作品帶著，多半是分類錯了
    #    （例如「巨乳」曾被誤標為原作）。
    total = len(resources) or 1
    suspicious = sorted(
        ({"name": name, "category": categories.get(name, "tag"), "count": count, "share": round(count / total, 3)}
         for name, count in counts.items()
         if categories.get(name, "tag") in NAMED_CATEGORIES and count >= 10 and count / total >= SUSPICIOUS_SHARE),
        key=lambda entry: -entry["count"],
    )
    return {
        "suspicious": suspicious,
        "similar": similar[:60], "paired": paired[:40],
        "rare": rare[:300], "rare_total": len(rare),
        "orphans": _card(db, orphans[:120]), "orphans_total": len(orphans),
        "unused": unused, "tag_total": len(counts),
    }


def merge_tag(db: Session, source: str, target: str) -> int:
    """把標籤 source 併入 target：作品改掛 target、刪除 source，並記成同義詞，之後抓到 source 也會自動換成 target。"""
    old = db.scalar(select(Tag).where(Tag.name == source))
    if old is None:
        raise LookupError(f"找不到標籤「{source}」")
    new = db.scalar(select(Tag).where(Tag.name == target))
    if new is None:
        new = Tag(name=target, category=old.category)
        db.add(new)
        db.flush()
    owners = set(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == old.id)).all())
    have = set(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == new.id)).all())
    missing = sorted(owners - have)
    if missing:  # 直接寫關聯表，不更新作品的 updated_at，典藏館排序不變
        db.execute(insert(resource_tags), [{"resource_id": rid, "tag_id": new.id} for rid in missing])
    db.execute(delete(resource_tags).where(resource_tags.c.tag_id == old.id))
    db.delete(old)
    # 同義詞：source → target；原本指向 source 的同義詞也一起改指 target
    for alias in db.scalars(select(TagAlias).where(TagAlias.canonical == source)).all():
        alias.canonical = target
    key = source.lower()
    if key != target.lower():
        existing = db.get(TagAlias, key)
        if existing is None:
            db.add(TagAlias(alias=key, canonical=target))
        else:
            existing.canonical = target
    return len(owners)


# ---------- 重複作品 ----------

def _title_key(title: str) -> str:
    """去掉【】[]() 裡的社團、語言等標記與標點後比對；太短的標題不比，以免誤判。"""
    text = re.sub(r"[\[\(【（［{].*?[\]\)】）］}]", " ", title or "").lower()
    text = re.sub(r"[\W_]+", "", text)
    return text if len(text) >= 6 else ""


def _url_key(code: str) -> str:
    if not code.startswith(("http://", "https://")):
        return ""
    parts = urlsplit(code.strip())
    host = re.sub(r"^www\d*\.", "", parts.netloc.lower())
    return f"{host}{parts.path.rstrip('/')}{'?' + parts.query if parts.query else ''}".lower()


def group_key(ids) -> str:
    return ",".join(str(item_id) for item_id in sorted(set(ids)))


def duplicates(db: Session) -> dict:
    resources = _resources(db)
    parent = {row.id: row.id for row in resources}

    def find(item_id: int) -> int:
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    reasons: dict[int, set[str]] = defaultdict(set)
    for reason, key_of in (
        ("title", lambda row: _title_key(row.title)),
        ("url", lambda row: _url_key(row.code)),
        # 網站圖示多件共用同一張，不能當成重複的依據
        ("cover", lambda row: row.image_url.strip() if row.image_url and row.image_kind != "icon" else ""),
    ):
        seen: dict[str, int] = {}
        for row in resources:
            key = key_of(row)
            if not key:
                continue
            if key in seen:
                parent[find(row.id)] = find(seen[key])
                reasons[row.id].add(reason)
                reasons[seen[key]].add(reason)
            else:
                seen[key] = row.id

    clusters: dict[int, list[Resource]] = defaultdict(list)
    for row in resources:
        clusters[find(row.id)].append(row)
    dismissed = set(db.scalars(select(DuplicateDismissal.key)).all())
    groups = []
    for rows in clusters.values():
        if len(rows) < 2 or group_key(row.id for row in rows) in dismissed:
            continue
        found = set().union(*(reasons[row.id] for row in rows))
        groups.append({"key": group_key(row.id for row in rows), "reasons": sorted(found), "items": _card(db, rows)})
    groups.sort(key=lambda group: (-len(group["items"]), group["items"][0]["id"]))
    return {"groups": groups, "dismissed": len(dismissed)}


def merge_resources(db: Session, keep_id: int, ids: list[int]) -> Resource:
    """保留 keep_id，把其他作品的標籤、書單、評分、筆記與閱讀紀錄併進來後刪除它們。"""
    keep = db.scalar(select(Resource).options(selectinload(Resource.tags)).where(Resource.id == keep_id))
    if keep is None:
        raise LookupError("找不到要保留的作品")
    others = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_([i for i in ids if i != keep_id]))).all()
    if not others:
        raise LookupError("沒有要合併的作品")
    tags = {tag.id: tag for tag in keep.tags}
    notes = [keep.note.strip()] if keep.note.strip() else []
    lists = set(db.scalars(select(collection_items.c.collection_id).where(collection_items.c.resource_id == keep.id)).all())
    for other in others:
        for tag in other.tags:
            tags.setdefault(tag.id, tag)
        keep.rating = max(keep.rating, other.rating)
        if STATUS_RANK.get(other.status, 0) > STATUS_RANK.get(keep.status, 0):
            keep.status, keep.finished_at = other.status, other.finished_at or keep.finished_at
        if other.last_opened_at and (keep.last_opened_at is None or other.last_opened_at > keep.last_opened_at):
            keep.last_opened_at = other.last_opened_at
        if other.created_at and other.created_at < keep.created_at:
            keep.created_at = other.created_at
        if not keep.image_url and other.image_url:
            keep.image_url, keep.image_kind = other.image_url, other.image_kind
        if not keep.title and other.title:
            keep.title = other.title
        if not keep.progress and other.progress:
            keep.progress = other.progress
        if other.note.strip():
            notes.append(other.note.strip())
        notes.append(f"合併自：{other.source_name} {other.code}（{datetime.now(timezone.utc):%Y-%m-%d}）")
        for collection_id in db.scalars(select(collection_items.c.collection_id).where(collection_items.c.resource_id == other.id)).all():
            if collection_id not in lists:
                db.execute(insert(collection_items).values(collection_id=collection_id, resource_id=keep.id, added_at=datetime.now(timezone.utc)))
                lists.add(collection_id)
        db.delete(other)
    keep.tags = list(tags.values())
    keep.note = "\n".join(notes)
    keep.updated_at = datetime.now(timezone.utc)
    return keep


# ---------- 統計 ----------

def _month(value: datetime | None) -> str:
    return value.strftime("%Y-%m") if value else ""


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _only_relink_note(note: str) -> bool:
    lines = [line for line in (note or "").splitlines() if line.strip()]
    return bool(lines) and all(line.startswith(("原連結：", "合併自：")) for line in lines)


def stats(db: Session) -> dict:
    resources = _resources(db)
    categories = _categories(db)
    now = datetime.now(timezone.utc)
    months = []
    year, month = now.year, now.month
    for _ in range(12):
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    months.reverse()
    added = Counter(_month(row.created_at) for row in resources)
    finished = Counter(_month(row.finished_at) for row in resources if row.finished_at)
    opened = Counter(_month(row.last_opened_at) for row in resources if row.last_opened_at)

    rated = [row.rating for row in resources if row.rating > 0]
    rating_buckets = Counter(min(5, max(1, int(-(-row.rating // 1)))) for row in resources if row.rating > 0)
    statuses = Counter(row.status for row in resources)
    kinds = Counter(row.kind or "未分類" for row in resources)
    sources = Counter(row.source_name for row in resources)

    per_tag: dict[str, list[Resource]] = defaultdict(list)
    for row in resources:
        for tag in row.tags:
            per_tag[tag.name].append(row)

    def ranking(category: str, size: int) -> list[dict]:
        names = [name for name in per_tag if categories.get(name, "tag") == category]
        names.sort(key=lambda name: (-len(per_tag[name]), name))
        return [{
            "name": name, "count": len(per_tag[name]),
            "avg_rating": _avg([row.rating for row in per_tag[name] if row.rating > 0]),
            "finished": sum(1 for row in per_tag[name] if row.status == "已完食"),
        } for name in names[:size]]

    tracked = [row.finished_at for row in resources if row.finished_at]
    return {
        "totals": {
            "total": len(resources), "rated": len(rated), "avg_rating": _avg(rated),
            "finished": statuses["已完食"], "finished_ratio": round(statuses["已完食"] / len(resources), 3) if resources else 0,
            "opened": sum(1 for row in resources if row.last_opened_at),
            "notes": sum(1 for row in resources if row.note.strip() and not _only_relink_note(row.note)),
            "this_year": sum(1 for row in resources if row.created_at and row.created_at.year == now.year),
            "tags": len(per_tag),
        },
        "months": months,
        "added": [added[key] for key in months],
        "finished": [finished[key] for key in months],
        "opened": [opened[key] for key in months],
        "finished_tracking_since": min(tracked).isoformat() if tracked else None,
        "ratings": {"unrated": len(resources) - len(rated), **{str(star): rating_buckets[star] for star in range(1, 6)}},
        "statuses": dict(statuses), "kinds": kinds.most_common(), "sources": sources.most_common(10),
        "tags": ranking("tag", 15), "artists": ranking("artist", 10), "parodies": ranking("parody", 8),
    }


# ---------- 作者／社團／原作資料頁 ----------

def tag_profile(db: Session, name: str) -> dict:
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        raise LookupError(f"找不到標籤「{name}」")
    rows = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.tags.any(Tag.id == tag.id))).all()
    categories = _categories(db)
    statuses = Counter(row.status for row in rows)
    ratings = [row.rating for row in rows if row.rating > 0]
    together: Counter = Counter()
    for row in rows:
        together.update(other.name for other in row.tags if other.name != name)
    related = [{"name": other, "count": count, "category": categories.get(other, "tag")} for other, count in together.most_common(40)]
    years = [row.created_at for row in rows if row.created_at]
    return {
        "name": tag.name, "category": tag.category, "count": len(rows),
        "statuses": dict(statuses), "rated": len(ratings), "avg_rating": _avg(ratings),
        "finished": statuses["已完食"], "unread": statuses["未讀"],
        "first_added": min(years).isoformat() if years else None, "last_added": max(years).isoformat() if years else None,
        # 一起出現的作者、社團、原作、角色另外列出，方便從作者跳到常合作的社團或常畫的原作
        "people": [entry for entry in related if entry["category"] in NAMED_CATEGORIES][:12],
        "tags": [entry for entry in related if entry["category"] == "tag"][:20],
    }
