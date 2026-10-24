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
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import covers, defaults, frontend, linkcheck, scheduler, security, settings
from .archive import STATUSES, assign_tags
from .database import SessionLocal, initialise_database
from .routers import collections, insights, jobs, library, misc, resources, tags

__all__ = ["app", "assign_tags", "STATUSES"]


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialise_database()
    with SessionLocal() as db:
        defaults.seed_if_empty(db)  # 全新的典藏館：放入常用網站規則與入門標籤翻譯包
    covers.backfill_ratios()
    # 封面主色需要解碼圖片，於背景補齊，不延後 API 就緒。
    threading.Thread(target=covers.backfill_colours, name="oas-colour-backfill", daemon=True).start()
    scheduler.start()  # 接續上次沒做完的背景工作
    linkcheck.start_scheduled()
    yield


app = FastAPI(title="Omni-Archive System API", version=settings.VERSION, lifespan=lifespan)
security.install(app)
for module in (misc, resources, collections, tags, insights, library, jobs):
    app.include_router(module.router)
frontend.install(app, settings.FRONTEND_DIR)  # 放在最後：其他路徑才交給網頁
