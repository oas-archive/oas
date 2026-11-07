def add(client, code, tags, **extra):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags, **extra}).json()


def codes(client, **params):
    return sorted(item["code"] for item in client.get("/api/resources", params=params).json()["items"])


def test_exclude_tags(client):
    add(client, "a", ["奇幻", "龍"])
    add(client, "b", ["奇幻"])
    add(client, "c", ["料理"])
    assert codes(client, not_tags=["龍"]) == ["b", "c"]
    assert codes(client, tags=["奇幻"], not_tags=["龍"]) == ["b"]
    assert codes(client, not_tags=["龍", "料理"]) == ["b"]


def test_tag_stats_follow_excluded_and_unrated(client):
    add(client, "a", ["奇幻", "龍"], rating=3)
    add(client, "b", ["奇幻"])
    counts = lambda **params: {row["name"]: row["count"] for row in client.get("/api/tags/stats", params=params).json()}
    assert counts(not_tags=["龍"]) == {"奇幻": 1}
    assert counts(rated=False) == {"奇幻": 1}
    assert counts() == {"奇幻": 2, "龍": 1}


def test_tag_stats_reports_category(client):
    add(client, "a", ["某作者"])
    client.patch("/api/tags/category", json={"name": "某作者", "category": "artist"})
    assert client.get("/api/tags/stats").json()[0]["category"] == "artist"


def test_tag_stats_search_folds_and_follows_aliases(client):
    add(client, "a", ["純愛向", "純愛向外傳"])
    add(client, "b", ["純愛向"])
    client.put("/api/tags/rules", json={"alias": "pure love", "canonical": "純愛向"})
    term = lambda text: client.get("/api/tags/stats", params={"term": text}).json()
    assert [row["name"] for row in term("純愛向")] == ["純愛向", "純愛向外傳"]  # 完全相同排前面
    assert [row["name"] for row in term("纯爱向")][0] == "純愛向"  # 簡體
    hit = term("Pure Love")[0]
    assert hit["name"] == "純愛向" and hit["via"] == "Pure Love"  # 同義詞命中，標註原文
    assert term("沒有這個") == []


def test_smart_collection_keeps_excluded_tags(client):
    add(client, "a", ["奇幻", "龍"])
    add(client, "b", ["奇幻"])
    query = "tag=%E5%A5%87%E5%B9%BB&not_tag=%E9%BE%8D"
    smart = client.post("/api/collections", json={"name": "不要龍", "query": query}).json()
    assert smart["count"] == 1
    backup = client.get("/api/backup").json()
    assert backup["collections"][0]["query"] == query
