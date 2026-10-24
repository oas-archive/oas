"""總覽與分析：首頁數字、漫遊探索、關聯圖、口味地圖、標籤健檢、重複作品、統計。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import explore as explore_module, graph as graph_module, insights
from ..archive import STATUSES, serialise
from ..database import DuplicateDismissal, Resource, Tag
from ..deps import db_session
from ..schemas import DismissInput

router = APIRouter(prefix="/api")


@router.get("/dashboard")
def dashboard(db: Session = Depends(db_session)):
    total = db.scalar(select(func.count(Resource.id))) or 0
    unread = db.scalar(select(func.count(Resource.id)).where(Resource.status == "未讀")) or 0
    six_months_ago = datetime.now(timezone.utc) - timedelta(days=180)
    deep_dive = db.scalars(
        select(Resource).options(selectinload(Resource.tags)).where(
            Resource.status == "未讀", Resource.created_at < six_months_ago, Resource.last_opened_at.is_(None),
        ).order_by(Resource.rating.desc(), func.random()).limit(1)
    ).first()
    status_counts = {status: 0 for status in STATUSES}
    status_counts.update(dict(db.execute(select(Resource.status, func.count(Resource.id)).group_by(Resource.status)).all()))
    top_tags = db.execute(
        select(Tag.name, func.count(Resource.id)).join(Tag.resources).group_by(Tag.name)
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


@router.get("/graph")
def relationship_graph(
    tag: str | None = None, focus: int | None = None,
    limit: int = Query(default=400, ge=10, le=2000), db: Session = Depends(db_session),
):
    return graph_module.graph(db, tag=tag or None, focus=focus, limit=limit)


@router.get("/insights/taste")
def taste_map(db: Session = Depends(db_session)):
    return insights.taste_map(db)


@router.get("/insights/tags")
def tag_health(db: Session = Depends(db_session)):
    return insights.tag_health(db)


@router.get("/duplicates")
def duplicate_groups(db: Session = Depends(db_session)):
    """標題相同、網址相同或封面相同的作品組。"""
    return insights.duplicates(db)


@router.post("/duplicates/dismiss")
def dismiss_duplicates(payload: DismissInput, db: Session = Depends(db_session)):
    key = insights.group_key(payload.ids)
    if db.get(DuplicateDismissal, key) is None:
        db.add(DuplicateDismissal(key=key))
        db.commit()
    return {"key": key}


@router.get("/stats")
def statistics(db: Session = Depends(db_session)):
    return insights.stats(db)
