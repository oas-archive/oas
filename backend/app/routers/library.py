"""來源網址規則、備份與還原、封面快取。"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import cache, covers, defaults, preferences, scheduler, settings
from ..archive import BACKUP_FORMAT, BACKUP_VERSION, export_backup, import_records, valid_template
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
    if not valid_template(payload.template):
        raise HTTPException(422, "網址範本要以 http:// 或 https:// 開頭")
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


# App 內的備份（手機兩個版本都放在私有空間，檔案選擇器看不到，由這裡列出來還原）；電腦公開版沒有 App，維持 403
USER_PACK_LIMIT = 50000
PRIVATE_BACKUP_NAME = re.compile(r"oas-[A-Za-z0-9_-]+\.json")


def _private_backups() -> list[os.DirEntry]:
    if not (settings.PRIVATE_BACKUPS or (preferences.is_public() and preferences.is_android())):
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
    if not (preferences.is_public() and preferences.is_android()):  # 電腦（含公開版）直接改資料夾裡的檔案
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


@router.get("/covers/stored")
def stored_covers(db: Session = Depends(db_session)):
    """已存在本機的封面張數與大小，也就是 POST /api/covers/clear 會刪掉的範圍（選「不存」時畫面問要不要刪）。只讀。"""
    return covers.stored_summary(db)


@router.post("/covers/clear")
def clear_covers(response: Response, db: Session = Depends(db_session)):
    """公開版選了不存之後，由使用者按下才刪除已存在本機的封面與縮圖。選不存但不刪時，舊封面留著，只是不再新增。"""
    if not preferences.is_public():
        raise HTTPException(403, "這個版本不能一次刪除封面")
    if preferences.saving_covers():
        raise HTTPException(409, f"要先設定不把封面存到{preferences.device_name()}")
    stored = covers.stored_summary(db)["covers"]
    removed = covers.clear_all(db)
    epoch = preferences.bump_cover_epoch(db)  # 封面網址換版本，瀏覽器暫存的舊封面不再被用到（Clear-Site-Data 實測清不掉）
    if not preferences.is_android():
        # 電腦的瀏覽器會暫存看過的封面（手機由 App 外殼清 WebView 暫存）；請瀏覽器清掉這個網站的暫存
        response.headers["Clear-Site-Data"] = '"cache"'
    return {"removed": removed, "covers": stored, "cover_epoch": epoch}


@router.post("/covers/cache", status_code=202)
def cache_covers(background: BackgroundTasks, retry_failed: bool = False, db: Session = Depends(db_session)):
    if not preferences.saving_covers():
        raise HTTPException(409, f"已設定不把封面存到{preferences.device_name()}")
    if covers.batch_state["running"]:
        raise HTTPException(409, "封面快取正在進行中")
    urls = covers.urls_to_cache(db, retry_failed=retry_failed)
    if urls:
        covers.batch_state.update(running=True, queued=len(urls), done=0, ok=0, failed=0)
        background.add_task(covers.run_batch, urls, retry_failed)
    return {"queued": len(urls)}


def _remote_response(url: str) -> Response:
    """把來源的圖片轉給畫面，不存檔，瀏覽器也不留（公開版沒選存封面、以及還沒收錄的預覽圖）。"""
    try:
        content, content_type = covers.remote_image(url)
    except covers.CoverError:
        raise HTTPException(404, "沒有封面") from None
    return Response(content, media_type=content_type, headers={"Cache-Control": "no-store", **covers.COVER_HEADERS})


@router.get("/covers/preview")
def cover_preview(url: str = Query(max_length=2000)):
    """還沒收錄的圖片（新增時的預覽、匯入與找替代來源的小圖）也不讓瀏覽器直接連外部，見 covers.remote_image。"""
    return _remote_response(url)


@router.get("/covers/{resource_id}")
def cover(resource_id: int, w: int | None = Query(default=None, ge=1, le=4000), db: Session = Depends(db_session)):
    resource = db.get(Resource, resource_id)
    if resource is None or not resource.image_url:
        raise HTTPException(404, "沒有封面")
    entry = covers.cached_entry(db, resource.image_url)
    if entry is None:
        # 還沒存到本機：一律由後端抓，不轉址給瀏覽器（外部網址可以再轉址回手機上的其他 App，瀏覽器會帶著通行碼過去）。
        # 公開版沒選存封面時只轉給畫面不存檔；其他情況抓下來存好，下次直接由本機提供。抓不到（含 24 小時內失敗過）回 404。
        if not preferences.saving_covers():
            return _remote_response(resource.image_url)
        entry = covers.stored_cover(db, resource.image_url)
        if entry is None:
            raise HTTPException(404, "沒有封面")
    path, media_type = covers.thumbnail_file(entry, w) if w else (covers.cover_file(entry), entry.content_type)
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=604800", **covers.COVER_HEADERS})
