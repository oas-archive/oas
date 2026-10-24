"""來源網址規則、備份與還原、封面快取。"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import covers
from ..archive import BACKUP_FORMAT, BACKUP_VERSION, export_backup, import_records
from ..database import Resource, Source
from ..deps import db_session
from ..schemas import BackupPayload, SourceInput

router = APIRouter(prefix="/api")


# ---------- 來源 ----------

@router.get("/sources")
def list_sources(db: Session = Depends(db_session)):
    known = {source.name: source for source in db.scalars(select(Source).order_by(Source.name)).all()}
    used = db.scalars(select(Resource.source_name).distinct()).all()
    names = sorted(set(known) | set(used))
    return [{"name": name, "template": known[name].template if name in known else "", "kind": known[name].kind if name in known else ""} for name in names]


@router.put("/sources")
def upsert_source(payload: SourceInput, db: Session = Depends(db_session)):
    source = db.scalar(select(Source).where(Source.name == payload.name.strip()))
    if source is None:
        source = Source(name=payload.name.strip(), template=payload.template.strip())
        db.add(source)
    else:
        source.template = payload.template.strip()
    db.commit()
    return {"name": source.name, "template": source.template}


# ---------- 備份與還原 ----------

@router.get("/backup")
def backup(db: Session = Depends(db_session)):
    data = export_backup(db)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="oas-backup-{stamp}.json"'})


@router.post("/restore")
def restore(payload: BackupPayload, mode: Literal["merge", "replace"] = "merge", db: Session = Depends(db_session)):
    if payload.format != BACKUP_FORMAT:
        raise HTTPException(422, "不是 OAS 備份檔")
    if payload.version > BACKUP_VERSION:
        raise HTTPException(422, f"備份版本 {payload.version} 較新，請先更新 OAS")
    try:
        report = import_records(
            db, payload.sources, payload.resources, mode=mode,
            tag_categories=payload.tag_categories, tag_aliases=payload.tag_aliases, tag_blocklist=payload.tag_blocklist,
            collections=payload.collections,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"mode": mode, **report}


# ---------- 封面 ----------

@router.get("/covers/status")
def cover_status(db: Session = Depends(db_session)):
    return covers.stats(db)


@router.post("/covers/cache", status_code=202)
def cache_covers(background: BackgroundTasks, retry_failed: bool = False, db: Session = Depends(db_session)):
    if covers.batch_state["running"]:
        raise HTTPException(409, "封面快取正在進行中")
    urls = covers.urls_to_cache(db, retry_failed=retry_failed)
    if urls:
        covers.batch_state.update(running=True, queued=len(urls), done=0, ok=0, failed=0)
        background.add_task(covers.run_batch, urls, retry_failed)
    return {"queued": len(urls)}


@router.get("/covers/{resource_id}")
def cover(resource_id: int, w: int | None = Query(default=None, ge=1, le=4000), db: Session = Depends(db_session)):
    resource = db.get(Resource, resource_id)
    if resource is None or not resource.image_url:
        raise HTTPException(404, "沒有封面")
    entry = covers.cached_entry(db, resource.image_url)
    if entry is not None:
        path, media_type = covers.thumbnail_file(entry, w) if w else (covers.cover_file(entry), entry.content_type)
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=604800"})
    # 尚未快取：本次交由瀏覽器載入原始網址（與未快取前相同），同時在背景下載，下次即由本機提供。
    covers.schedule(resource.image_url)
    return RedirectResponse(resource.image_url, status_code=307, headers={"Cache-Control": "no-store"})
