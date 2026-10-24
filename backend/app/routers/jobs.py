"""收錄與背景作業：讀取網址資訊、一次收錄多個網址、補齊作品資訊、定期巡檢來源、側欄的系統狀態。"""
import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import covers, intake, linkcheck, metadata, refresh, scheduler, settings, undo
from ..database import Resource, Source
from ..deps import db_session
from ..schemas import ImportInput, MetadataInput

router = APIRouter(prefix="/api")


# ---------- 收錄 ----------

@router.post("/metadata")
def describe_url(payload: MetadataInput, db: Session = Depends(db_session)):
    """網址 → 來源、代碼、是否已收錄；fetch=true 時另讀取標題、封面與標籤（被網站防護擋下時回報 blocked）。"""
    try:
        return metadata.describe(db, payload.url, fetch=payload.fetch)
    except metadata.MetadataError as error:
        raise HTTPException(422, str(error))


@router.post("/import/urls")
def import_urls(payload: ImportInput, db: Session = Depends(db_session)):
    """一次收錄多個網址：先建立只有網址的作品，再於背景補上標題、封面與標籤。已在典藏中的會列出來、不重複收錄。"""
    created, duplicates, invalid, seen = [], [], [], set()
    kinds = dict(db.execute(select(Source.name, Source.kind)).all())
    for raw in payload.urls:
        url = raw.strip()
        if not url:
            continue
        try:
            info = metadata.describe(db, url, fetch=False)
        except metadata.MetadataError:
            invalid.append(url)
            continue
        key = (info["source_name"], info["code"])
        if key in seen:
            continue
        seen.add(key)
        if info["duplicate"]:
            duplicates.append({"url": info["url"], **info["duplicate"]})
            continue
        resource = Resource(code=info["code"][:500], source_name=info["source_name"][:120])
        resource.kind = kinds.get(resource.source_name) or ""
        db.add(resource)
        db.flush()
        created.append({"id": resource.id, "url": info["url"], "source_name": resource.source_name})
    undo_id = undo.record(db, "created", f"一次收錄 {len(created)} 件", {"ids": [row["id"] for row in created]}) if created else None
    db.commit()
    intake.enqueue([row["id"] for row in created])
    return {"created": created, "duplicates": duplicates, "invalid": invalid, "undo": undo_id}


@router.get("/intake/status")
def intake_status():
    return {**intake.state, "queued": scheduler.pending("fill")}


# ---------- 補齊作品資訊 ----------

@router.post("/refresh", status_code=202)
def start_refresh(mode: Literal["all", "missing", "images"] = "all"):
    """背景補齊作品資訊（封面、標題、標籤與標籤分類）。
    mode=missing 只處理缺封面或沒有標籤的作品；images 為所有沒有圖片的作品（含創作者、論壇等頁面）找代表圖或網站圖示。"""
    count = refresh.start(mode)
    if count < 0:
        raise HTTPException(409, "補齊作業正在進行中")
    return {"queued": count, "seconds": round(count * refresh.REQUEST_INTERVAL)}


@router.get("/refresh/status")
def refresh_status():
    return dict(refresh.state)


@router.post("/refresh/stop")
def stop_refresh():
    scheduler.cancel("refresh")
    return dict(refresh.state)


# ---------- 定期巡檢來源 ----------

@router.get("/linkcheck/status")
def linkcheck_status(db: Session = Depends(db_session)):
    return {**linkcheck.state, "pending": linkcheck.pending(db), "interval_days": linkcheck.INTERVAL_DAYS}


@router.post("/linkcheck/start", status_code=202)
def linkcheck_start():
    if not linkcheck.start():
        raise HTTPException(409, "巡檢正在進行中")
    return dict(linkcheck.state)


@router.post("/linkcheck/stop")
def linkcheck_stop():
    scheduler.cancel("linkcheck")
    return dict(linkcheck.state)


# ---------- 系統狀態 ----------

@router.get("/system/status")
def system_status(db: Session = Depends(db_session)):
    """側欄底部的系統狀態：最後備份時間、封面快取、背景作業。備份資料夾以唯讀方式掛載到 OAS_BACKUP_DIR。"""
    latest, count = None, 0
    try:
        files = [entry for entry in os.scandir(settings.BACKUP_DIR) if entry.is_file() and entry.name.startswith("oas-") and entry.name.endswith(".json")]
        count = len(files)
        if files:
            newest = max(files, key=lambda entry: entry.stat().st_mtime)
            latest = {"name": newest.name, "at": datetime.fromtimestamp(newest.stat().st_mtime, timezone.utc).isoformat()}
    except OSError:
        pass
    cover = covers.stats(db)
    job = dict(refresh.state)
    return {
        "version": settings.VERSION, "backup": latest, "backup_count": count,
        "covers": {"cached": cover["cached"], "total": cover["with_image"], "running": cover["batch"]["running"]},
        "refresh": {"running": job["running"], "done": job["done"], "total": job["total"]},
        "linkcheck": {key: linkcheck.state[key] for key in ("running", "done", "total", "dead", "message", "finished_at")},
        "intake": {key: intake.state[key] for key in ("running", "done", "total", "filled", "failed", "message")},
    }
