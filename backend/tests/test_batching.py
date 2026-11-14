"""超過 SQLite 參數上限（32,766）的長清單：分批或把編號寫進 SQL 後不會出錯，結果與排序和原本相同。
不真的建幾萬件作品：直接對函式餵編號清單（大部分是不存在的編號）。"""
from types import SimpleNamespace

from sqlalchemy import insert, select

from app import batching, covers, deps, duplicates, intake, search
from app.database import CoverCache, Resource, SessionLocal

OVER = 33_000  # 大於 SQLite 的參數上限 32,766


def make(client, code, title, tags=(), image_url=""):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": title, "tags": list(tags), "image_url": image_url}).json()


def test_chunks_keep_order_and_size():
    assert list(batching.chunks(range(7), 3)) == [[0, 1, 2], [3, 4, 5], [6]]
    assert list(batching.chunks([], 3)) == []
    assert all(len(batch) <= batching.BATCH for batch in batching.chunks(range(OVER)))


def test_id_in_accepts_more_ids_than_the_parameter_limit(client):
    first, second = make(client, "B1", "虛構甲")["id"], make(client, "B2", "虛構乙")["id"]
    with SessionLocal() as db:
        ids = [*range(10_000_000, 10_000_000 + OVER), second]
        assert db.scalars(select(Resource.id).where(batching.id_in(Resource.id, ids))).all() == [second]
        assert db.scalars(select(Resource.id).where(batching.id_in(Resource.id, []))).all() == []
        assert sorted(db.scalars(select(Resource.id).where(~batching.id_in(Resource.id, []))).all()) == [first, second]


def test_search_filter_with_huge_match_list_keeps_results_and_order(client, monkeypatch):
    make(client, "S1", "星港旅人", ["虛構標籤"])
    make(client, "S2", "星港", ["虛構標籤"])
    make(client, "S3", "別的作品")
    client.post("/api/collections", json={"name": "虛構智慧書單", "query": "q=星港"})
    params = {"q": "星港", "sort": "relevance"}
    before = client.get("/api/resources", params=params).json()
    tags_before = client.get("/api/tags", params={"q": "星港"}).json()
    lists_before = client.get("/api/collections").json()

    real = search.match_ids
    monkeypatch.setattr(search, "match_ids", lambda db, q: [*real(db, q), *range(10_000_000, 10_000_000 + OVER)])
    after = client.get("/api/resources", params=params)
    assert after.status_code == 200
    assert [item["code"] for item in after.json()["items"]] == [item["code"] for item in before["items"]] == ["S2", "S1"]
    assert after.json()["total"] == before["total"] == 2
    assert client.get("/api/tags", params={"q": "星港"}).json() == tags_before
    assert client.get("/api/collections").json() == lists_before
    assert deps.resource_filters(q="星港")  # 篩選條件本身也能建立


def test_cover_hashes_with_more_resources_than_the_parameter_limit(client):
    rows = [SimpleNamespace(id=i, image_url=f"https://img.example.com/fake/{i}.jpg", image_kind="", kind="") for i in range(OVER)]
    fingerprint = "0f0f0f0f0f0f0f0f"  # 32 個 1，算得出差別
    with SessionLocal() as db:
        db.execute(insert(CoverCache), [{"url_hash": covers.url_hash(row.image_url), "url": row.image_url, "status": "ok", "fingerprint": fingerprint}
                                        for row in rows[::1000]])
        db.commit()
        hashes = duplicates._cover_hashes(db, rows)
    assert hashes == {row.id: int(fingerprint, 16) for row in rows[::1000]}


def test_cover_metadata_with_more_urls_than_the_parameter_limit(client):
    urls = [f"https://img.example.com/meta/{i}.jpg" for i in range(OVER)]
    with SessionLocal() as db:
        db.add(CoverCache(url_hash=covers.url_hash(urls[-1]), url=urls[-1], status="ok", ratio=1.4, colour="#123456"))
        db.commit()
        assert covers.metadata_for(db, urls) == {urls[-1]: (1.4, "#123456")}


def test_enqueue_with_more_ids_than_the_parameter_limit(client, monkeypatch):
    item = make(client, "E1", "", image_url="")  # 只有代碼、沒有網址範本：讀不到，不排入
    queued = []
    monkeypatch.setattr(intake.scheduler, "enqueue", lambda kind, items, **_: queued.extend(items) or len(items))
    assert intake.enqueue([*range(10_000_000, 10_000_000 + OVER), item["id"]]) == 0
    assert queued == []
