from app import cache


def test_cached_until_data_changes(client):
    calls = []
    compute = lambda: calls.append(1) or {"n": len(calls)}
    assert cache.cached("t", compute) == {"n": 1}
    copy = cache.cached("t", compute)
    copy["n"] = 99  # 呼叫端改了也不影響快取
    assert cache.cached("t", compute) == {"n": 1} and len(calls) == 1
    client.post("/api/resources", json={"code": "c1", "source_name": "站A", "title": "測試"})
    assert cache.cached("t", compute) == {"n": 2}


def test_related_and_home_follow_edits(client):
    a = client.post("/api/resources", json={"code": "a", "source_name": "站A", "title": "甲", "tags": ["共同", "x1"]}).json()
    client.post("/api/resources", json={"code": "b", "source_name": "站A", "title": "乙", "tags": ["共同", "x2"]})
    for index in range(6):  # 「共同」要是少見的標籤才拿來算相似度
        client.post("/api/resources", json={"code": f"z{index}", "source_name": "站A", "title": f"其他{index}", "tags": [f"別的{index}"]})
    assert [row["code"] for row in client.get(f"/api/resources/{a['id']}/related").json()] == ["b"]
    c = client.post("/api/resources", json={"code": "c", "source_name": "站A", "title": "丁", "tags": ["共同", "x1"]}).json()
    assert [row["code"] for row in client.get(f"/api/resources/{a['id']}/related").json()][0] == "c"
    client.patch(f"/api/resources/{c['id']}/quick", json={"status": "閱讀中"})
    home = client.get("/api/home?seed=1").json()
    assert any(row["key"] == "reading" and row["items"][0]["code"] == "c" for row in home["rows"])


def test_concurrent_callers_compute_once():
    import threading
    import time
    calls = []
    def slow():
        calls.append(1)
        time.sleep(0.2)
        return {"ok": True}
    threads = [threading.Thread(target=lambda: cache.cached("slow-key", slow)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
