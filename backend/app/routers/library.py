"""來源網址規則、備份與還原、封面快取。"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cache, covers, defaults, preferences, scheduler, settings
from ..archive import BACKUP_FORMAT, BACKUP_VERSION, export_backup, import_records
from ..database import Resource, Source
from ..deps import db_session
from ..schemas import BackupPayload, SourceInput, UserPack

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


def _fingerprint(db: Session) -> str:
    data = {key: value for key, value in export_backup(db).items() if key != "exported_at"}
    for item in data["resources"]:  # 巡檢時間不算資料變動，否則每次巡檢都會觸發手機的自動備份
        item.pop("link_checked_at", None)
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@router.get("/backup/fingerprint")
def backup_fingerprint(db: Session = Depends(db_session)):
    """備份內容的指紋（不含匯出時間）：手機 App 離開畫面時用來判斷資料有沒有變、要不要自動備份。"""
    return {"fingerprint": cache.cached("backup-fingerprint", lambda: _fingerprint(db))}


# App 內的備份（公開版放在私有空間，檔案選擇器看不到，由這裡列出來還原）
USER_PACK_LIMIT = 50000
PRIVATE_BACKUP_NAME = re.compile(r"oas-[A-Za-z0-9_-]+\.json")


def _private_backups() -> list[os.DirEntry]:
    if not preferences.is_public():
        raise HTTPException(403, "這個版本沒有 App 內的備份")
    try:
        return [entry for entry in os.scandir(settings.BACKUP_DIR) if entry.is_file() and PRIVATE_BACKUP_NAME.fullmatch(entry.name)]
    except OSError:
        return []


@router.get("/backup/files")
def private_backup_list():
    rows = [{"name": entry.name, "size": entry.stat().st_size, "at": datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc).isoformat()} for entry in _private_backups()]
    return sorted(rows, key=lambda row: row["at"], reverse=True)


@router.get("/backup/files/{name}")
def private_backup_file(name: str):
    # 只比對資料夾裡實際有的檔名，網址的內容不會碰到檔案系統
    entry = next((entry for entry in _private_backups() if entry.name == name), None)
    if entry is None:
        raise HTTPException(404, "找不到這份備份")
    return FileResponse(entry.path, media_type="application/json")


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
            collections=payload.collections, tag_health_keep=payload.tag_health_keep, tag_pairs_keep=payload.tag_pairs_keep, duplicate_dismissals=payload.duplicate_dismissals,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    if mode == "replace":
        scheduler.forget_all()
    return {"mode": mode, **report}


# ---------- 使用者自己的翻譯包 ----------

@router.post("/tag-pack/mine")
def import_my_pack(payload: UserPack, db: Session = Depends(db_session)):
    if not preferences.is_public():
        raise HTTPException(403, "這個版本的翻譯包請直接編輯資料夾裡的檔案")
    if len(payload.translate) + len(payload.suggest) + len(payload.block) > USER_PACK_LIMIT:
        raise HTTPException(422, "翻譯包太大")
    defaults.write_user_pack(payload.translate, payload.suggest, payload.block)
    defaults.sync_packs_safely(db)
    return {"translate": len(payload.translate), "suggest": len(payload.suggest), "block": len(payload.block)}


# ---------- 封面 ----------

@router.get("/covers/status")
def cover_status(db: Session = Depends(db_session)):
    return covers.stats(db)


@router.post("/covers/clear")
def clear_covers(db: Session = Depends(db_session)):
    if not preferences.is_public():
        raise HTTPException(403, "這個版本不能一次刪除封面")
    if preferences.saving_covers():
        raise HTTPException(409, "要先設定不把封面存到手機")
    return {"removed": covers.clear_all(db)}


@router.post("/covers/cache", status_code=202)
def cache_covers(background: BackgroundTasks, retry_failed: bool = False, db: Session = Depends(db_session)):
    if not preferences.saving_covers():
        raise HTTPException(409, "已設定不把封面存到手機")
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
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=604800", **covers.COVER_HEADERS})
    if not covers.redirectable(resource.image_url):
        raise HTTPException(404, "沒有封面")
    # 尚未快取：本次交由瀏覽器載入原始網址（與未快取前相同），同時在背景下載，下次即由本機提供。
    covers.schedule(resource.image_url)
    return RedirectResponse(resource.image_url, status_code=307, headers={"Cache-Control": "no-store"})
