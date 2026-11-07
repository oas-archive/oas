"""健康檢查、復原、搜尋建議。"""
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import covers, preferences, search, settings, undo
from ..archive import serialise
from ..database import Resource, Tag, resource_tags
from ..deps import db_session
from ..schemas import PreferencesPatch, dump

router = APIRouter(prefix="/api")


@router.get("/health")
def health(nonce: str = Query(default="", max_length=128)):
    """服務就緒檢查。手機版帶 nonce 時回傳 proof = HMAC-SHA256(通行碼, nonce)：
    App 驗過 proof 才把畫面與通行碼 cookie 交給這個服務，手機上其他 App 搶先佔用同一個埠也假冒不了。"""
    result = {"status": "ok", "version": settings.VERSION}
    if settings.API_TOKEN and nonce:
        result["proof"] = hmac.new(settings.API_TOKEN.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return result


# ---------- 公開版的設定 ----------

@router.get("/preferences")
def read_preferences(db: Session = Depends(db_session)):
    return preferences.read(db)


@router.patch("/preferences")
def update_preferences(payload: PreferencesPatch, db: Session = Depends(db_session)):
    if not preferences.is_public():
        raise HTTPException(403, "這個版本沒有這些設定")
    return preferences.update(db, dump(payload))


# ---------- 復原 ----------

@router.post("/undo/{entry_id}")
def undo_action(entry_id: int, db: Session = Depends(db_session)):
    try:
        result = undo.apply(db, entry_id)
    except LookupError as error:
        raise HTTPException(404, str(error))
    db.commit()
    return result


@router.get("/undo/latest")
def undo_latest(db: Session = Depends(db_session)):
    return undo.latest(db)


# ---------- 搜尋建議 ----------

@router.get("/search/suggest")
def search_suggest(q: str = Query(min_length=1, max_length=100), db: Session = Depends(db_session)):
    """搜尋框的即時建議：符合的作品（和典藏館同一套比對與排序）與標籤（含分類與件數；同義詞命中的標註原文）。"""
    if search.parse(q).empty:
        return {"items": [], "tags": []}
    rows = search.load_ordered(db, [resource_id for resource_id, _ in search.rank(db, q)[:6]])
    # 標籤順序：完全相同 > 開頭 > 包含，再依件數
    match = search.tag_matcher(db, q)
    counts = db.execute(
        select(Tag.name, Tag.category, func.count(resource_tags.c.resource_id))
        .join(resource_tags, resource_tags.c.tag_id == Tag.id).group_by(Tag.id)
    ).all()
    tags = []
    for name, category, count in counts:
        level, via = match(name)
        if level:
            entry = {"name": name, "category": category, "count": count}
            if via:
                entry["via"] = via
            tags.append((level, count, entry))
    tags.sort(key=lambda row: (-row[0], -row[1]))
    return {
        "items": covers.with_ratios(db, [serialise(row) for row in rows]),
        "tags": [entry for _, _, entry in tags[:8]],
    }
