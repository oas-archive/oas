"""依網站控制請求頻率：每個網站各自記錄「下一次可以送請求的時間」，排程器據此決定先做哪一件。

- nhentai 等會限流的網站（SLOW_HOSTS）每 SLOW_INTERVAL 秒一件，其他網站每 OTHER_INTERVAL 秒一件。
- 被限流（429、503、Cloudflare）時只冷卻那個網站 BACKOFF_SECONDS 秒，其他網站照常進行。
- 同一網站連續被限流 MAX_STREAK 次，就暫停那個網站 PAUSE_SECONDS 秒（尊重來源的頻率限制，不嘗試繞過）。
- 狀態只存在記憶體：典藏館重新開啟後從頭計算。
"""
import threading
import time
from urllib.parse import urlsplit

SLOW_HOSTS = ("nhentai.net", "wnacg.com", "hanime1.me")
SLOW_INTERVAL = 6.0  # 2 秒時約 20 件後就被來源限流；6 秒較能持續
OTHER_INTERVAL = 1.5
BACKOFF_SECONDS = 180
MAX_STREAK = 5
PAUSE_SECONDS = 30 * 60

_hosts: dict[str, dict] = {}
_lock = threading.Lock()


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def interval(host: str) -> float:
    return SLOW_INTERVAL if any(host == slow or host.endswith("." + slow) for slow in SLOW_HOSTS) else OTHER_INTERVAL


def _entry(host: str) -> dict:
    return _hosts.setdefault(host, {"next": 0.0, "streak": 0})


def ready_at(host: str) -> float:
    """這個網站最早可以再送請求的時間（time.monotonic）。"""
    with _lock:
        return _entry(host)["next"]


def reserve(host: str) -> None:
    """即將對這個網站送出請求：下一次至少要間隔 interval 秒。"""
    with _lock:
        entry = _entry(host)
        entry["next"] = max(entry["next"], time.monotonic() + interval(host))


def report_ok(host: str) -> None:
    with _lock:
        _entry(host)["streak"] = 0


def report_limited(host: str) -> bool:
    """被限流：冷卻這個網站。回傳 True 表示連續被擋太多次，已暫停較久。"""
    with _lock:
        entry = _entry(host)
        entry["streak"] += 1
        paused = entry["streak"] >= MAX_STREAK
        entry["next"] = time.monotonic() + (PAUSE_SECONDS if paused else BACKOFF_SECONDS)
        if paused:
            entry["streak"] = 0
        return paused


def reset() -> None:
    """測試用：清除所有網站的狀態。"""
    with _lock:
        _hosts.clear()
