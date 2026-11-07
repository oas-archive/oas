"""定期巡檢來源：每件作品每 7 天檢查一次來源頁面，網站回應「找不到」（HTTP 404／410）就標記為來源失效。

- 典藏館只在你開著時執行，所以不是固定星期幾跑：每次啟動後等 START_DELAY 秒，接著檢查「超過 7 天沒檢查」的作品，
  中途關掉也沒關係，下次啟動會從還沒檢查的繼續。
- 工作存在 jobs 資料表，由 scheduler.py 處理：優先順序最低（新收錄、補齊作品資訊先做）；請求頻率依網站控制（fetcher.py），
  被限流時稍後重試，仍被擋就留到下次巡檢，不嘗試繞過。
- 只有明確的 404／410 才標記失效；連線失敗、被防護擋下都不算。有防護、伺服器讀不到的網站（禁漫、missav）直接略過。
- 不更新 updated_at，典藏館排序不變；標錯時可在展示頁按「取消失效標記」。
"""
import http.client
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from . import refresh, scheduler
from .covers import CoverError, check_url, safe_urlopen
from .database import Resource, SessionLocal, Source
from .metadata import PROTECTED_HOSTS, SITE_KIND, USER_AGENT

INTERVAL_DAYS = 7
START_DELAY = 90
TIMEOUT_SECONDS = 15
SKIP_HOSTS = PROTECTED_HOSTS
PENDING_TTL = 60  # 「待檢查件數」快取秒數：備份中心每幾秒查一次，不必每次都掃過全部作品
MAX_ATTEMPTS = 2  # 被限流時重試的次數；仍被擋就留到下次巡檢

state = {"running": False, "total": 0, "done": 0, "dead": 0, "failed": 0, "message": "", "started_at": None, "finished_at": None}


def targets(db) -> list[tuple[int, str]]:
    templates = {source.name: source.template for source in db.scalars(select(Source)).all()}
    cutoff = datetime.now(timezone.utc) - timedelta(days=INTERVAL_DAYS)
    rows = db.execute(  # 只取要用的欄位：備份中心的進度也會呼叫這裡，不必載入整件作品
        select(Resource.id, Resource.code, Resource.source_name).where(Resource.link_dead.is_(False), Resource.kind != SITE_KIND,  # 網站的列表、搜尋頁常擋程式，也不是作品，不巡檢
                                                                       or_(Resource.link_checked_at.is_(None), Resource.link_checked_at < cutoff))
        .order_by(Resource.link_checked_at.is_not(None), Resource.link_checked_at, Resource.id)
    ).all()
    result = []
    for resource in rows:
        url = refresh.page_url(resource, templates.get(resource.source_name, ""))
        if url.startswith(("http://", "https://")) and not any(host in url for host in SKIP_HOSTS):
            result.append((resource.id, url))
    return result


def check(url: str) -> str:
    """alive／dead／blocked／error。只讀前 1KB，夠判斷頁面存在即可。"""
    try:
        check_url(url)
    except CoverError:
        return "error"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with safe_urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read(1024)
        return "alive"
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return "dead"
        return "blocked" if error.code in (403, 429, 503) else "error"
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError):
        return "error"


def _mark(resource_id: int, dead: bool) -> None:
    with SessionLocal() as db:
        resource = db.get(Resource, resource_id)
        if resource is None:
            return
        values = {"link_checked_at": datetime.now(timezone.utc), "updated_at": resource.updated_at}
        if dead:
            values["link_dead"] = True
        db.execute(Resource.__table__.update().where(Resource.id == resource_id).values(**values))
        db.commit()


def handle(job: scheduler.JobInfo) -> str:
    """檢查一件：alive、dead（標記失效）、failed（連線失敗，下次再查）、limited（被限流，稍後重試），或 skipped（網址已換，不檢查）。"""
    if not refresh.job_is_current(job.resource_id, job.url):
        return "skipped"
    result = check(job.url)
    if result in ("alive", "dead"):
        _mark(job.resource_id, result == "dead")
        return result
    return "limited" if result == "blocked" else "failed"


def start() -> bool:
    """排入「超過 7 天沒檢查」的作品；巡檢已在清單中時回傳 False。"""
    if scheduler.pending("linkcheck"):
        return False
    with SessionLocal() as db:
        jobs = targets(db)
    scheduler.enqueue("linkcheck", jobs)
    _pending_cache["at"] = 0.0  # 待檢查件數下次重算
    return True


def start_scheduled() -> None:
    """API 啟動時呼叫：等 START_DELAY 秒再排入巡檢。OAS_LINKCHECK=off 時不自動執行（測試環境）。"""
    if os.getenv("OAS_LINKCHECK", "on").lower() != "off":
        timer = threading.Timer(START_DELAY, start)
        timer.daemon = True
        timer.start()


scheduler.register("linkcheck", "巡檢來源", 2, handle, state, counters=("dead",), max_attempts=MAX_ATTEMPTS)


_pending_cache = {"at": 0.0, "value": 0}


def pending(db) -> int:
    now = time.monotonic()
    if now - _pending_cache["at"] > PENDING_TTL:
        _pending_cache.update(at=now, value=len(targets(db)))
    return _pending_cache["value"]
