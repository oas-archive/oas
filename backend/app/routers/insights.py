"""總覽與分析：首頁數字、首頁書架、漫遊探索、關聯圖、口味地圖、標籤健檢、重複作品、統計。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import duplicates as duplicates_module, explore as explore_module, insights, metadata
from ..archive import STATUSES, serialise
from ..database import DuplicateDismissal, Resource, Tag
from ..deps import db_session
from ..schemas import DismissInput, TagKeepInput, TagPairsKeepInput, UndismissInput

router = APIRouter(prefix="/api")


def _deep_dive(db: Session) -> Resource | None:
    """收錄超過半年、還沒開過的未讀作品，隨機一件。"""
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
    return db.scalars(
        select(Resource).options(selectinload(Resource.tags)).where(
            Resource.status == "未讀", Resource.created_at < six_months_ago, Resource.last_opened_at.is_(None),
            Resource.kind.not_in(metadata.NON_WORK_KINDS),  # 網站頁面、創作者頁不是作品
        ).order_by(func.random()).limit(1)  # 純隨機：先排評分的話，只要有一件高分作品就會每次都抽到它
    ).first()


@router.get("/deep-dive")
def deep_dive_pick(db: Session = Depends(db_session)):
    """深潛區定時換一件時用，不必每次重算整份首頁數字。"""
    item = _deep_dive(db)
    return serialise(item) if item else None


@router.get("/dashboard")
def dashboard(db: Session = Depends(db_session)):
    total = db.scalar(select(func.count(Resource.id))) or 0
    unread = db.scalar(select(func.count(Resource.id)).where(Resource.status == "未讀")) or 0
    deep_dive = _deep_dive(db)
    status_counts = {status: 0 for status in STATUSES}
    status_counts.update(dict(db.execute(select(Resource.status, func.count(Resource.id)).group_by(Resource.status)).all()))
    top_tags = db.execute(
        select(Tag.name, func.count(Resource.id)).join(Tag.resources).where(Tag.name.not_in(metadata.ORIGINAL_NAMES)).group_by(Tag.name)
        .order_by(func.count(Resource.id).desc(), Tag.name).limit(8)
    ).all()
    dead_links = db.scalar(select(func.count(Resource.id)).where(Resource.link_dead.is_(True))) or 0
    return {
        "total": total, "unread": unread, "status_counts": status_counts, "dead_links": dead_links,
        "top_tags": [{"name": name, "count": count} for name, count in top_tags],
        "deep_dive": serialise(deep_dive) if deep_dive else None,
    }


@router.get("/explore")
def explore(seed: int = Query(default=0, ge=0), db: Session = Depends(db_session)):
    return explore_module.explore(db, seed=seed)


@router.get("/home")
def home(seed: int = Query(default=0, ge=0), personal: bool = False, db: Session = Depends(db_session)):
    """首頁：主視覺（最多 5 件）與橫向書架列；personal 時多放待評分、最近開啟（手機沒有側欄）。"""
    return explore_module.home(db, seed=seed, personal=personal)


@router.get("/insights/taste")
def taste_map(db: Session = Depends(db_session)):
    return insights.taste_map(db)


@router.get("/insights/tags")
def tag_health(db: Session = Depends(db_session)):
    return insights.tag_health(db)


@router.post("/insights/tags/keep-pairs")
def keep_tag_pairs(payload: TagPairsKeepInput, db: Session = Depends(db_session)):
    """標籤健檢：保留「幾乎總是一起出現」的組合，保留的不再列出。"""
    return {"kept": insights.keep_pairs(db, [pair.strip() for pair in payload.pairs])}


@router.post("/insights/tags/keep")
def keep_tag(payload: TagKeepInput, db: Session = Depends(db_session)):
    """標籤健檢：保留（或取消保留）只用過一次的標籤，保留的不再列出。"""
    return {"kept": insights.keep_tag(db, payload.name.strip(), payload.keep)}


@router.get("/duplicates")
def duplicate_groups(db: Session = Depends(db_session)):
    """標題相同或相近、網址相同、封面相同（含幾乎一樣的圖）的作品組。"""
    return duplicates_module.duplicates(db)


@router.get("/duplicates/of/{resource_id}")
def duplicates_of(resource_id: int, db: Session = Depends(db_session)):
    """這件作品可能重複的其他作品。"""
    return duplicates_module.duplicates_of(db, resource_id)


@router.post("/duplicates/dismiss")
def dismiss_duplicates(payload: DismissInput, db: Session = Depends(db_session)):
    key = duplicates_module.group_key(payload.ids)
    if db.get(DuplicateDismissal, key) is None:
        db.add(DuplicateDismissal(key=key))
        db.commit()
    return {"key": key}


@router.get("/duplicates/dismissed")
def dismissed_duplicates(db: Session = Depends(db_session)):
    """標成「不是重複」的組合。"""
    return {"groups": duplicates_module.dismissed_groups(db)}


@router.post("/duplicates/undismiss")
def undismiss_duplicates(payload: UndismissInput, db: Session = Depends(db_session)):
    """撤銷「不是重複」：這組會重新列入重複作品。"""
    entry = db.get(DuplicateDismissal, payload.key)
    if entry is not None:
        db.delete(entry)
        db.commit()
    return {"key": payload.key}


@router.get("/stats")
def statistics(tz: int = Query(default=0, ge=-840, le=840), db: Session = Depends(db_session)):
    """tz：使用者所在時區比 UTC 快幾分鐘，每月與今年的數字照這個時區算。"""
    return insights.stats(db, tz)
