from app import search


def make(client, code, title, tags=(), note=""):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": title, "tags": list(tags), "note": note}).json()


def codes(client, q, sort="relevance"):
    return [item["code"] for item in client.get("/api/resources", params={"q": q, "sort": sort}).json()["items"]]


def test_parse_terms_phrases_and_exclusions():
    query = search.parse('星港 "star harbor" -NTR -')
    assert query.terms == ["星港", "star harbor", "-"] and query.excluded == ["NTR"]


def test_fold_width_case_and_simplified():
    assert search.fold("ＳＴＡＲ　Harbor") == "star harbor"
    assert search.fold("星港传说") == search.fold("星港傳說")


def test_every_term_must_match_in_any_field(client):
    make(client, "1", "海邊旅人的一天", ["星港傳說"])
    make(client, "2", "海邊旅人", ["幻境物語"])
    make(client, "3", "別的作品", ["星港傳說"])
    assert codes(client, "旅人 星港") == ["1"]
    assert sorted(codes(client, "星港傳說 -旅人")) == ["3"]


def test_relevance_order(client):
    make(client, "n", "某作品", note="提到 巨乳")
    make(client, "t", "巨乳姊姊")
    make(client, "g", "另一件", ["巨乳"])
    make(client, "e", "巨乳")
    assert codes(client, "巨乳") == ["e", "g", "t", "n"]  # 標題相同 > 標籤相同 > 標題開頭 > 筆記


def test_simplified_and_alias_match(client):
    make(client, "1", "測試", ["星港傳說"])
    client.put("/api/tags/rules", json={"alias": "star harbor", "canonical": "星港傳說"})
    assert codes(client, "星港传说") == ["1"]
    assert codes(client, "Star Harbor") == ["1"]
    tags = client.get("/api/search/suggest", params={"q": "star harbor"}).json()["tags"]
    assert tags[0]["name"] == "星港傳說" and tags[0]["via"] == "star harbor"


def test_did_you_mean_on_zero_results(client):
    make(client, "1", "Summer Vacation", ["swimsuit"])
    result = client.get("/api/resources", params={"q": "swimsiut"}).json()
    assert result["total"] == 0 and result["did_you_mean"] == "swimsuit"
    assert client.get("/api/resources", params={"q": "zzzzzz"}).json()["did_you_mean"] is None
