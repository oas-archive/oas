"""資料沒變時重用算好的結果：重複作品、相關作品的標籤權重、首頁。

每次有寫入的 commit 就把資料版本加一；cached(key, compute) 在版本沒變時直接回傳上次的結果（深複製，呼叫端改了也不影響快取）。
手機上這幾個計算各要 0.5～0.8 秒，打開作品頁、回首頁時一直重算會讓畫面卡住。
只偵測經過 Session 的寫入（ORM 物件與 update()/delete()/insert()）；目前只有啟動時的資料庫升級直接用連線寫，那時快取還是空的。
"""
import copy
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

MAX_ENTRIES = 64  # 首頁每個 seed 一份；太多時整個清掉重來
_lock = threading.Lock()
_version = 0
_store: dict = {}
_computing: dict = {}  # 每個 key 一把鎖：同一份結果正在算時，後來的請求等它算完，不另外再算一份（啟動時背景預熱與使用者同時要）


@event.listens_for(Session, "after_flush")
def _flushed(session, _context) -> None:
    session.info["oas_changed"] = True


@event.listens_for(Session, "do_orm_execute")
def _executed(state) -> None:
    if state.is_insert or state.is_update or state.is_delete:
        state.session.info["oas_changed"] = True


@event.listens_for(Session, "after_commit")
def _committed(session) -> None:
    global _version
    if session.info.pop("oas_changed", False):
        with _lock:
            _version += 1


@event.listens_for(Session, "after_rollback")
def _rolled_back(session) -> None:
    session.info.pop("oas_changed", None)


def version() -> int:
    return _version


def cached(key, compute, copied: bool = True):
    """copied=False：呼叫端保證只讀不改（例如相關作品的標籤索引），省下每次複製的時間。"""
    with _lock:
        current, hit = _version, _store.get(key)
        key_lock = _computing.setdefault(key, threading.Lock())
    if hit is not None and hit[0] == current:
        return copy.deepcopy(hit[1]) if copied else hit[1]
    with key_lock:
        with _lock:
            current, hit = _version, _store.get(key)
        if hit is None or hit[0] != current:  # 等鎖的時候別人可能已經算好了
            value = compute()
            with _lock:
                if len(_store) >= MAX_ENTRIES:
                    _store.clear()
                _store[key] = (current, value)
            hit = (current, value)
    return copy.deepcopy(hit[1]) if copied else hit[1]
