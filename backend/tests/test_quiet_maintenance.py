"""巡檢時間與巡檢佇列的寫入不讓快取失效；標成失效、補資料的佇列照常讓快取重算。資料都是虛構的。"""
from app import cache, linkcheck, scheduler


def add(client, code):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_marking_alive_keeps_caches_but_dead_does_not(client):
    first, second = add(client, "1"), add(client, "2")
    before = cache.version()
    linkcheck._mark(first, dead=False)
    assert cache.version() == before  # 只記巡檢時間
    linkcheck._mark(second, dead=True)
    assert cache.version() == before + 1  # 標成失效：待整理、首頁都要重算


def test_linkcheck_queue_is_quiet_but_fill_queue_is_not(client, monkeypatch):
    item = add(client, "1")
    monkeypatch.setattr(scheduler._wake, "set", lambda: None)
    before = cache.version()
    scheduler.enqueue("linkcheck", [(item, "https://site.example/1")])
    scheduler.cancel("linkcheck")
    assert cache.version() == before
    scheduler.enqueue("fill", [(item, "https://site.example/1")])
    assert cache.version() == before + 1  # 待整理會顯示「排隊中」
    scheduler.cancel("fill")
