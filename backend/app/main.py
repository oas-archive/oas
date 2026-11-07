"""OAS API 進入點：建立 app、啟動時的準備工作、掛上防護與各組 API。

各組 API 在 app/routers/：
- resources：作品的列表、新增、修改、快速評分、批次、刪除、合併
- collections：書單與智慧書單
- tags：標籤、分類、合併、資料頁
- insights：首頁數字、探索、關聯圖、口味地圖、重複作品、統計
- library：來源網址規則、備份與還原、封面
- jobs：收錄、補齊作品資訊、來源巡檢、系統狀態
- misc：健康檢查、復原、搜尋建議

直接在電腦上執行時，打包好的網頁（frontend/dist）也由這裡提供（見 frontend.py）。
"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import cache, covers, defaults, duplicates, frontend, graph, intake, linkcheck, scheduler, search, security, settings
from .archive import STATUSES, assign_tags
from .database import SessionLocal, initialise_database
from .routers import collections, insights, jobs, library, misc, resources, tags

__all__ = ["app", "assign_tags", "STATUSES"]


def _warm_up() -> None:
    """啟動後在背景先算好搜尋、重複作品、相關作品的索引：第一次打開作品頁、搜尋不用等（手機剛啟動時要 1～2 秒）。失敗只記 log。"""
    search.warm_up()
    try:
        with SessionLocal() as db:
            duplicates.duplicates(db)
            cache.cached("similar-index", lambda: graph._similar_index(db), copied=False)
    except Exception:  # noqa: BLE001
        logging.getLogger("uvicorn.error").exception("重複作品、相關作品預熱失敗")


def _safely(step) -> None:
    """啟動時的資料整理只是輔助：出錯只記 log，不能讓典藏館開不起來。"""
    try:
        step()
    except Exception:  # noqa: BLE001
        logging.getLogger("uvicorn.error").exception("啟動時的整理失敗（已略過）：%s", step.__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialise_database()
    _safely(defaults.ensure_user_pack)
    with SessionLocal() as db:
        defaults.seed_if_empty(db)  # 全新的典藏館：放入常用網站規則與入門標籤翻譯包
        defaults.sync_packs_safely(db)  # 翻譯包之後新增的條目補進既有典藏（失敗只記 log）
    _safely(intake.normalise_saved)  # 鏡像網址、多一段分類的網址換成正確的來源與代碼（重複的合併）、標題去掉網站名稱
    _safely(intake.backfill_known_covers)  # 只有編號的禁漫、MissAV 作品：依編號補上封面網址
    _safely(covers.backfill_ratios)
    # 封面主色需要解碼圖片，於背景補齊，不延後 API 就緒。
    threading.Thread(target=covers.backfill_colours, name="oas-colour-backfill", daemon=True).start()
    if scheduler.enabled():
        threading.Thread(target=covers.backfill_thumbnails, name="oas-thumbnail-backfill", daemon=True).start()
    threading.Thread(target=_warm_up, name="oas-warm-up", daemon=True).start()
    scheduler.start()  # 接續上次沒做完的背景工作
    linkcheck.start_scheduled()
    yield


app = FastAPI(title="Omni-Archive System API", version=settings.VERSION, lifespan=lifespan)
security.install(app)
for module in (misc, resources, collections, tags, insights, library, jobs):
    app.include_router(module.router)
frontend.install(app, settings.FRONTEND_DIR)  # 放在最後：其他路徑才交給網頁
