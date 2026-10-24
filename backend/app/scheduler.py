"""背景工作排程器：補新收錄、補齊作品資訊、巡檢來源，全部由這裡依序處理。

- 工作存在 jobs 資料表：關掉典藏館再開，會從清單接著做，不會遺失。
- 只有一個背景執行緒，依優先順序取工作（新收錄 → 補齊作品資訊 → 巡檢來源）；
  某個網站還在間隔或冷卻中時先做其他網站的工作，不必互相等待（見 fetcher.py）。
- 被限流的工作留在清單、稍後重試；重試超過該類工作的上限才算失敗。
- 各類工作向這裡登記（register），並各自保有一份 state 字典供 API 顯示進度：
  running、total、done、failed、message、started_at、finished_at，以及各自的計數（例如 filled、dead）。
- OAS_JOBS=off 時不啟動背景執行緒（測試環境改用 run_until_idle 同步執行）。
"""
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import delete, func, select

from . import fetcher
from .database import Job, SessionLocal


@dataclass(frozen=True)
class JobInfo:
    id: int
    kind: str
    resource_id: int
    url: str
    host: str
    mode: str
    attempts: int


@dataclass
class Kind:
    name: str
    label: str
    priority: int
    handler: Callable[[JobInfo], str]  # 回傳結果：limited（被限流，稍後重試）、failed，或其他（成功，若 state 有同名計數就加一）
    state: dict
    counters: tuple[str, ...]  # 新一批開始時歸零的計數
    max_attempts: int


KINDS: dict[str, Kind] = {}
_notices: dict[tuple[str, str], str] = {}  # (工作種類, 網站) → 限流提示；那個網站恢復或這類工作做完才清掉
_lock = threading.Lock()
_wake = threading.Event()
_thread: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _notice(kind: str, host: str, text: str | None) -> None:
    """更新某類工作在某個網站的提示（None 為清除），並把這類工作所有網站的提示合成 state["message"]。呼叫端需持有 _lock。"""
    if text is None:
        _notices.pop((kind, host), None)
    else:
        _notices[(kind, host)] = text
    KINDS[kind].state["message"] = "；".join(text for (name, _), text in _notices.items() if name == kind)


def register(name: str, label: str, priority: int, handler: Callable[[JobInfo], str], state: dict,
             counters: tuple[str, ...] = (), max_attempts: int = 5) -> None:
    KINDS[name] = Kind(name, label, priority, handler, state, ("done", "failed", *counters), max_attempts)
    state.setdefault("failed", 0)


def enabled() -> bool:
    return os.getenv("OAS_JOBS", "on").lower() != "off"


def pending(kind: str) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count(Job.id)).where(Job.kind == kind)) or 0


def pending_ids(kind: str) -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(Job.resource_id).where(Job.kind == kind).order_by(Job.priority, Job.id)).all())


def enqueue(kind: str, items: list[tuple[int, str]], mode: str = "") -> int:
    """排入工作（作品 id, 網址）；同一件作品已在清單中就略過。回傳實際新增的件數。"""
    spec = KINDS[kind]
    with SessionLocal() as db:
        existing = set(db.scalars(select(Job.resource_id).where(Job.kind == kind)).all())
        fresh, seen = [], set()
        for resource_id, url in items:
            if resource_id in existing or resource_id in seen:
                continue
            seen.add(resource_id)
            fresh.append(Job(kind=kind, resource_id=resource_id, url=url, host=fetcher.host_of(url), mode=mode, priority=spec.priority))
        db.add_all(fresh)
        db.commit()
    with _lock:
        if fresh and not existing:  # 新的一批：重新計算進度
            spec.state.update({key: 0 for key in spec.counters}, total=len(fresh), message="", started_at=_now(), finished_at=None)
        else:
            spec.state["total"] = spec.state.get("total", 0) + len(fresh)
        spec.state["running"] = bool(existing or fresh)
    _wake.set()
    return len(fresh)


def cancel(kind: str) -> int:
    """取消這一類還沒做的工作（「停止」按鈕）。"""
    with SessionLocal() as db:
        removed = db.execute(delete(Job).where(Job.kind == kind)).rowcount or 0
        db.commit()
    with _lock:
        for key in [key for key in _notices if key[0] == kind]:
            del _notices[key]
        KINDS[kind].state.update(running=False, message="已手動停止", finished_at=_now())
    return removed


def _next_job() -> JobInfo | float | None:
    """挑出現在可以做的下一件工作；都還不能做時回傳要等幾秒；清單是空的回傳 None。"""
    wall, mono = time.time(), time.monotonic()
    waits, candidates = [], []
    with SessionLocal() as db:
        for host in db.scalars(select(Job.host).distinct()).all():
            host_wait = fetcher.ready_at(host) - mono
            if host_wait > 0:
                waits.append(host_wait)
                continue
            job = db.scalar(select(Job).where(Job.host == host, Job.ready_at <= wall).order_by(Job.priority, Job.id).limit(1))
            if job is not None:
                candidates.append(job)
            else:
                waits.append((db.scalar(select(func.min(Job.ready_at)).where(Job.host == host)) or wall) - wall)
        if not candidates:
            return max(0.05, min(waits)) if waits else None
        job = min(candidates, key=lambda row: (row.priority, row.id))
        return JobInfo(job.id, job.kind, job.resource_id, job.url, job.host, job.mode, job.attempts)


def run_once() -> float | None:
    """做一件工作並回傳 0；沒有可做的工作時回傳要等幾秒（清單是空的回傳 None）。"""
    picked = _next_job()
    if not isinstance(picked, JobInfo):
        return picked
    spec = KINDS.get(picked.kind)
    if spec is None:  # 未知的工作種類（舊版留下的）：直接移除
        with SessionLocal() as db:
            db.execute(delete(Job).where(Job.id == picked.id))
            db.commit()
        return 0
    fetcher.reserve(picked.host)
    try:
        result = spec.handler(picked)
    except Exception:  # 單件出錯不影響其他件
        result = "failed"
    notice = None
    with SessionLocal() as db:
        job = db.get(Job, picked.id)  # 可能已被「停止」取消
        if result == "limited":
            paused = fetcher.report_limited(picked.host)
            wait = fetcher.PAUSE_SECONDS if paused else fetcher.BACKOFF_SECONDS
            if job is not None and picked.attempts + 1 < spec.max_attempts:
                job.attempts = picked.attempts + 1
                job.ready_at = time.time() + wait
                db.commit()
                with _lock:
                    _notice(picked.kind, picked.host, f"{picked.host} 連續被限流，先暫停 {round(wait / 60)} 分鐘，其他網站照常進行" if paused
                            else f"{picked.host} 暫時限流，{round(wait / 60)} 分鐘後繼續（第 {job.attempts} 次）")
                return 0
            result = "failed"
            notice = (f"{picked.host} 連續被限流，先暫停 {round(wait / 60)} 分鐘；剩下的會稍後或下次開啟時繼續" if paused
                      else _notices.get((picked.kind, picked.host)))
        else:
            fetcher.report_ok(picked.host)
        if job is not None:
            db.delete(job)
            db.commit()
        remaining = db.scalar(select(func.count(Job.id)).where(Job.kind == picked.kind)) or 0
    with _lock:
        state = spec.state
        state["done"] = state.get("done", 0) + 1
        if result in state and result not in ("running", "total", "done", "message"):
            state[result] += 1
        _notice(picked.kind, picked.host, notice)
        if remaining == 0:
            for key in [key for key in _notices if key[0] == picked.kind]:
                del _notices[key]
            state.update(running=False, finished_at=_now(), message=notice or "")
    return 0


def run_until_idle(limit: int = 10_000) -> None:
    """測試用：同步做完現在可以做的工作。"""
    for _ in range(limit):
        if run_once() != 0:
            return


def _loop() -> None:
    while True:
        try:
            wait = run_once()
        except Exception:
            wait = 5.0
        if wait == 0:
            continue
        _wake.wait(timeout=None if wait is None else min(wait, 60.0))
        _wake.clear()


def restore_states() -> None:
    """啟動時：清單裡還有上次沒做完的工作，就把各類的進度顯示為進行中。"""
    with SessionLocal() as db:
        counts = dict(db.execute(select(Job.kind, func.count(Job.id)).group_by(Job.kind)).all())
    with _lock:
        for name, spec in KINDS.items():
            if counts.get(name):
                spec.state.update({key: 0 for key in spec.counters}, total=counts[name], running=True, message="接續上次未完成的工作", started_at=_now(), finished_at=None)


def start() -> None:
    global _thread
    restore_states()
    if not enabled() or (_thread is not None and _thread.is_alive()):
        return
    _thread = threading.Thread(target=_loop, name="oas-jobs", daemon=True)
    _thread.start()
