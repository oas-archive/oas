"""收錄與背景作業：讀取網址資訊、一次收錄多個網址、補齊作品資訊、定期巡檢來源、側欄的系統狀態。"""
import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import alternatives, attention, batch_plan, cache, covers, intake, linkcheck, metadata, refresh, scheduler, settings, undo
from ..database import Resource, Source
from ..deps import db_session
from ..schemas import ImportInput, ImportPlanInput, ImportSearchInput, MetadataInput, PageInput, RetryInput

router = APIRouter(prefix="/api")


# ---------- 收錄 ----------

@router.post("/metadata")
def describe_url(payload: MetadataInput, db: Session = Depends(db_session)):
    """網址 → 來源、代碼、是否已收錄；fetch=true 時另讀取標題、封面與標籤（被網站防護擋下時回報 blocked）。"""
    try:
        return metadata.describe(db, payload.url, fetch=payload.fetch, title=payload.title, html_page=payload.html)
    except metadata.MetadataError as error:
        raise HTTPException(422, str(error))


@router.post("/import/parse")
def import_parse(payload: ImportPlanInput, db: Session = Depends(db_session)):
    """一次收錄的預覽：解析貼上的文字，標出已在典藏的與看不懂的。不連外，貼上時就可以呼叫。"""
    return batch_plan.parse(db, payload.text)


@router.post("/import/search")
def import_search(payload: ImportSearchInput):
    """對一段標題文字實際到漫畫網站搜尋（使用者按了才呼叫）；sites 是預覽時標的範圍，沒給就三個都找。"""
    sites = [name for name in payload.sites if name in batch_plan.COMIC_SEARCH] or batch_plan.COMIC_SEARCH
    return batch_plan.search([{"text": payload.text.strip(), "sites": sites}])[0]


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
        key = (info["source_name"], metadata.work_key(info["source_name"], info["code"]))
        if key in seen:
            continue
        seen.add(key)
        if info["duplicate"]:
            duplicates.append({"url": info["url"], **info["duplicate"]})
            continue
        resource = Resource(code=info["code"][:500], source_name=info["source_name"][:120])
        resource.kind = metadata.page_kind(info["url"]) or kinds.get(resource.source_name) or ""
        db.add(resource)
        db.flush()
        created.append({"id": resource.id, "url": info["url"], "source_name": resource.source_name})
    undo_id = undo.record(db, "created", f"一次收錄 {len(created)} 件", {"ids": [row["id"] for row in created]}) if created else None
    db.commit()
    intake.enqueue([row["id"] for row in created])
    return {"created": created, "duplicates": duplicates, "invalid": invalid, "undo": undo_id}


# ---------- 待整理 ----------

@router.get("/attention")
def attention_list(db: Session = Depends(db_session)):
    """待整理的作品：可以重試、要手動補、來源失效三組。"""
    return attention.attention(db)


@router.get("/attention/summary")
def attention_summary(db: Session = Depends(db_session)):
    """側欄用：各組件數。"""
    return cache.cached("attention-summary", lambda: attention.summary(db), copied=False)


@router.post("/attention/retry")
def attention_retry(payload: RetryInput, db: Session = Depends(db_session)):
    """把「可以重試」的作品重新排入背景補資料；沒指定作品時整組重試。"""
    return {"queued": intake.enqueue(attention.retry_ids(db, payload.ids))}


@router.get("/attention/same-code")
def attention_same_code(db: Session = Depends(db_session)):
    """「來源失效」那組一次比對同編號：每件作品在 N 網、紳士、禁漫有沒有同一個編號的作品，讓你選要改用哪個；查失敗的另外列出。"""
    rows = [row for row, _ in attention.classify(db)[0]["dead"]]
    return alternatives.same_code_many(db, rows)


@router.post("/attention/fetch-manual")
def attention_fetch_manual(db: Session = Depends(db_session)):
    """「要你手動補」那組試著讀一次作品頁：有防護的網站偶爾讀得到（例如手機上的禁漫），讀到就補上空白的標題、封面與標籤。"""
    return attention.fetch_manual(db)


@router.post("/attention/fill-page")
def attention_fill_page(payload: PageInput, db: Session = Depends(db_session)):
    """手機 App 在隱藏的瀏覽器讀到某件作品的頁面（禁漫、MissAV 等伺服器讀不到的網站）：依網站規則解析後補上空白欄位、新增標籤。"""
    return attention.fill_page(db, payload.id, payload.html)


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
    """側欄底部的系統狀態：最後備份時間、封面快取、背景作業。備份資料夾由 OAS_BACKUP_DIR 指定（手機版是「下載/OAS」）。"""
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
