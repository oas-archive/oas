"""標籤：名稱與分類、件數統計、合併、刪除、修改分類、資料頁、清理沒用到的標籤、規則（同義詞與封鎖清單）、建議併入。"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import cache, insights, search, tag_suggest, undo
from ..archive import resolve_alias, tag_rules
from ..database import Resource, Tag, TagAlias, TagBlock, resource_tags
from ..deps import db_session, resource_filters
from ..schemas import CategoryInput, TagMergeInput, TagRemoveInput, TagRemoveManyInput, TagRuleInput, TagSuggestSkipInput, TagUnblockInput

router = APIRouter(prefix="/api/tags")


@router.get("/categories")
def tag_categories(db: Session = Depends(db_session)):
    """非一般分類的標籤：{名稱: 分類}，前端用來把標籤分組顯示。"""
    return dict(db.execute(select(Tag.name, Tag.category).where(Tag.category != "tag")).all())


@router.get("")
def tag_names(db: Session = Depends(db_session)):
    return list(db.scalars(select(Tag.name).order_by(Tag.name)).all())


@router.get("/stats")
def tag_stats(
    q: str = "", status: str | None = None, source: str | None = None,
    tags: list[str] = Query(default=[]), tag_mode: Literal["all", "any"] = "all", kind: str | None = None, dead: bool | None = None,
    collection: int | None = None, rated: bool | None = None, not_tags: list[str] = Query(default=[]), term: str = "",
    limit: int = Query(default=300, ge=1, le=5000), db: Session = Depends(db_session),
):
    """標籤與件數。帶入目前的篩選條件時，只計算符合條件的作品（多選時可看出還能再縮小到哪些標籤）。
    term 搜尋標籤名稱（和搜尋框同一套比對：全半形、簡繁、同義詞），最接近的排前面。"""
    matching = select(Resource.id).where(*resource_filters(q, status, source, tags, tag_mode, kind, dead, collection, rated=rated, not_tags=not_tags))
    rows = db.execute(
        select(Tag.name, Tag.category, func.count(resource_tags.c.resource_id))
        .join(resource_tags, resource_tags.c.tag_id == Tag.id)
        .where(resource_tags.c.resource_id.in_(matching))
        .group_by(Tag.id).order_by(func.count(resource_tags.c.resource_id).desc(), Tag.name)
    ).all()
    entries = [{"name": name, "category": category, "count": count} for name, category, count in rows]
    if term.strip():
        match = search.tag_matcher(db, term)
        hits = []
        for entry in entries:
            level, via = match(entry["name"])
            if level:
                hits.append((level, {**entry, "via": via} if via else entry))
        hits.sort(key=lambda hit: -hit[0])  # sort 保留原順序：同一級仍依件數
        entries = [entry for _, entry in hits]
    return entries[:limit]


@router.post("/merge")
def merge_tag(payload: TagMergeInput, db: Session = Depends(db_session)):
    """把一個標籤併入另一個，並記成同義詞。"""
    source, target = payload.source.strip(), payload.target.strip()
    if source == target:
        raise HTTPException(422, "兩個標籤相同")
    old = db.scalar(select(Tag).where(Tag.name == source))
    if old is None:
        raise HTTPException(404, f"找不到標籤「{source}」")
    moved, undo_id = _merge(db, old, target)
    db.commit()
    return {"source": source, "target": target, "count": moved, "undo": undo_id}


def _merge(db: Session, old: Tag, target: str) -> tuple[int, int]:
    """合併標籤（記一筆復原）；「合併」與「新增規則時典藏裡已有這個原文標籤」共用。"""
    source = old.name
    new = db.scalar(select(Tag).where(Tag.name == target))
    aliases = {alias.alias: alias.canonical for alias in db.scalars(select(TagAlias).where(TagAlias.canonical == source)).all()}
    aliases[source.lower()] = (db.get(TagAlias, source.lower()).canonical if db.get(TagAlias, source.lower()) else None)
    undo_id = undo.record(db, "tag_merge", f"把「{source}」併入「{target}」", {
        "source": source, "category": old.category, "target": target, "target_created": new is None,
        "target_category": new.category if new else None,
        "owners": list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == old.id)).all()),
        "target_owners": list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == new.id)).all()) if new else [],
        "aliases": aliases,
    })
    return insights.merge_tag(db, source, target), undo_id


# ---------- 規則：同義詞（原文 → 統一後的名稱）與封鎖清單 ----------

@router.get("/rules")
def tag_rule_list(db: Session = Depends(db_session)):
    """所有規則：每條同義詞附上一路換到底的最終名稱與使用件數；封鎖清單。"""
    aliases, blocked = tag_rules(db)
    counts = dict(db.execute(
        select(Tag.name, func.count(resource_tags.c.resource_id)).join(resource_tags, resource_tags.c.tag_id == Tag.id).group_by(Tag.name)
    ).all())
    rows = []
    for alias, canonical in sorted(aliases.items()):
        final = resolve_alias(aliases, alias)
        rows.append({"alias": alias, "canonical": canonical, "final": final, "count": counts.get(final, 0)})
    return {"aliases": rows, "blocked": sorted(blocked)}


@router.put("/rules")
def save_tag_rule(payload: TagRuleInput, db: Session = Depends(db_session)):
    """新增或修改一條規則：之後收錄、補資料、編輯時，原文會換成統一後的名稱。
    典藏裡已經有這個原文標籤時，順便把它併入統一後的名稱（和「合併」相同，可以復原）。"""
    key, canonical = payload.alias.strip().lower(), payload.canonical.strip()
    if not key or not canonical or key == canonical.lower():
        raise HTTPException(422, "原文和統一後的名稱相同")
    aliases, _ = tag_rules(db)
    seen, current = {key}, canonical.lower()
    while current in aliases and current not in seen:  # 沿著統一後名稱自己的規則走，走回原文就是循環
        seen.add(current)
        current = aliases[current].lower()
    if current == key:
        raise HTTPException(422, f"「{canonical}」最後會換回「{payload.alias.strip()}」，這樣會繞圈，請改指別的名稱")
    existing = db.scalars(select(Tag).where(func.lower(Tag.name) == key, Tag.name != canonical)).all()
    if existing:
        moved, undo_ids = 0, []
        for old in existing:
            count, undo_id = _merge(db, old, canonical)
            moved += count
            undo_ids.append(undo_id)
        if len(undo_ids) > 1:  # 大小寫不同的同名標籤：按一次復原全部
            undo_id = undo.group(db, undo_ids, f"規則「{key} → {canonical}」（併入 {len(undo_ids)} 個標籤）")
    else:
        row = db.get(TagAlias, key)
        undo_id = undo.record(db, "tag_rule", f"規則「{key} → {canonical}」", {"alias": key, "before": row.canonical if row else None})
        if row is None:
            db.add(TagAlias(alias=key, canonical=canonical))
        else:
            row.canonical = canonical
        moved = 0
    db.commit()
    aliases, _ = tag_rules(db)
    return {"alias": key, "canonical": canonical, "final": resolve_alias(aliases, key), "merged": moved, "undo": undo_id}


@router.delete("/rules")
def delete_tag_rule(alias: str = Query(min_length=1, max_length=100), db: Session = Depends(db_session)):
    """刪除一條規則：之後收錄時原文不再換名稱。已經換好的作品標籤不變。"""
    row = db.get(TagAlias, alias.strip().lower())
    if row is None:
        raise HTTPException(404, f"找不到「{alias}」的規則")
    undo_id = undo.record(db, "tag_rule", f"刪除規則「{row.alias} → {row.canonical}」", {"alias": row.alias, "before": row.canonical})
    db.delete(row)
    db.commit()
    return {"alias": row.alias, "undo": undo_id}


@router.get("/suggestions")
def tag_suggestions(db: Session = Depends(db_session)):
    """建議併入：還不在規則裡、但看起來就是某個現有標籤的標籤（每次即時計算，見 tag_suggest.py）。
    採用時呼叫 PUT /rules（建立規則並合併，可以復原）。"""
    return cache.cached("tag-suggestions", lambda: tag_suggest.suggestions(db), copied=False)  # 資料沒變時不重算：側欄也會查


@router.post("/suggestions/skip")
def skip_tag_suggestion(payload: TagSuggestSkipInput, db: Session = Depends(db_session)):
    """略過一筆建議（之後不再列出）；skip=false 取消略過，source、target 都留空時恢復全部。"""
    if payload.skip and not (payload.source.strip() and payload.target.strip()):
        raise HTTPException(422, "要略過的建議缺少標籤名稱")
    return {"skipped": tag_suggest.set_skip(db, payload.source, payload.target, payload.skip)}


@router.post("/unblock")
def unblock_tag(payload: TagUnblockInput, db: Session = Depends(db_session)):
    """解除封鎖：之後收錄或補資料時會重新帶上這個標籤（已經略過的作品不會自動補回）。"""
    row = db.get(TagBlock, payload.name.strip().lower())
    if row is None:
        raise HTTPException(404, f"「{payload.name}」不在封鎖清單")
    undo_id = undo.record(db, "tag_block", f"解除封鎖「{row.name}」", {"name": row.name})
    db.delete(row)
    db.commit()
    return {"name": row.name, "undo": undo_id}


def _remove(db: Session, name: str, block: bool) -> dict | None:
    """從所有作品移除一個標籤（可加入封鎖清單）；回傳復原用的內容，標籤不存在時回傳 None。單一與批次刪除共用。

    刪除（block=false）只影響現有作品：同義詞留著，之後收錄的作品帶到同一個原文標籤時仍會換成這個名稱、重新建立標籤。
    封鎖（block=true）是永久的：之後收錄或補資料時這個名稱一律略過，指向它的同義詞（翻譯前的原文）也會一起被擋掉。"""
    tag = db.scalar(select(Tag).where(Tag.name == name))
    if tag is None:
        return None
    owners = list(db.scalars(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == tag.id)).all())
    newly_blocked = block and db.get(TagBlock, name.lower()) is None
    db.execute(resource_tags.delete().where(resource_tags.c.tag_id == tag.id))
    db.delete(tag)
    if newly_blocked:
        db.add(TagBlock(name=name.lower()))
        db.flush()  # 一次刪除多個時，大小寫不同的名稱才不會重複加入
    return {"name": name, "category": tag.category, "owners": owners, "blocked": newly_blocked}


@router.post("/remove")
def remove_tag(payload: TagRemoveInput, db: Session = Depends(db_session)):
    """從所有作品移除一個標籤；block=true 時另外加入封鎖清單（永久略過，含指向它的同義詞），見 _remove。"""
    name = payload.name.strip()
    removed = _remove(db, name, payload.block)
    if removed is None:
        raise HTTPException(404, f"找不到標籤「{name}」")
    undo_id = undo.record(db, "tag_remove", f"刪除標籤「{name}」", removed)
    db.commit()
    return {"name": name, "count": len(removed["owners"]), "undo": undo_id}


@router.post("/remove-many")
def remove_tags(payload: TagRemoveManyInput, db: Session = Depends(db_session)):
    """一次移除多個標籤（標籤健檢的批次刪除）；block=true 時全部加入封鎖清單（見 _remove）。只記一筆復原，可一次復原。"""
    removed = [entry for name in dict.fromkeys(name.strip() for name in payload.names if name.strip())
               if (entry := _remove(db, name, payload.block)) is not None]
    works = sum(len(entry["owners"]) for entry in removed)
    if not removed:
        raise HTTPException(404, "找不到要刪除的標籤")
    undo_id = undo.record(db, "tags_remove", f"刪除 {len(removed)} 個標籤", {"tags": removed})
    db.commit()
    return {"count": len(removed), "works": works, "undo": undo_id}


@router.patch("/category")
def set_tag_category(payload: CategoryInput, db: Session = Depends(db_session)):
    """手動修改標籤分類（作者、社團、原作、角色、一般標籤…）。"""
    tag = db.scalar(select(Tag).where(Tag.name == payload.name.strip()))
    if tag is None:
        raise HTTPException(404, f"找不到標籤「{payload.name}」")
    undo_id = undo.record(db, "category", f"修改「{tag.name}」的分類", {"name": tag.name, "category": tag.category})
    tag.category = payload.category
    db.commit()
    return {"name": tag.name, "category": tag.category, "undo": undo_id}


@router.get("/profile")
def tag_profile(name: str = Query(min_length=1, max_length=100), db: Session = Depends(db_session)):
    """作者、社團、原作等標籤的資料頁：件數、閱讀情況、評分、常一起出現的標籤。"""
    try:
        return insights.tag_profile(db, name)
    except LookupError as error:
        raise HTTPException(404, str(error))


@router.post("/cleanup")
def cleanup_tags(db: Session = Depends(db_session)):
    """刪除沒有任何作品使用的標籤。"""
    used = select(resource_tags.c.tag_id)
    unused = db.scalars(select(Tag).where(Tag.id.not_in(used))).all()
    for tag in unused:
        db.delete(tag)
    db.commit()
    return {"removed": len(unused)}
