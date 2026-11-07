from datetime import datetime, timezone

from sqlalchemy import select

from app.database import Resource, SessionLocal, Tag


def make(client, code, tags, status="未讀", rating=0):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags, "status": status, "rating": rating}).json()


def shelves(client, seed=0):
    return {shelf["key"]: shelf for shelf in client.get("/api/explore", params={"seed": seed}).json()["shelves"]}


def test_preference_shelf_learns_from_finished_and_rated(client):
    make(client, "done", ["奇幻", "冒險"], status="已完食")
    make(client, "loved", ["奇幻"], status="閱讀中", rating=5)
    make(client, "match", ["奇幻", "日常"])
    make(client, "nomatch", ["料理"])
    result = shelves(client)
    preference = result["preference"]
    codes = [item["code"] for item in preference["items"]]
    assert codes[0] == "match" and "nomatch" not in codes and "done" not in codes
    assert "#奇幻" in preference["subtitle"]


def test_no_preference_shelf_without_signals(client):
    make(client, "1", ["奇幻"])
    assert "preference" not in shelves(client)
    assert shelves(client)["recent"]["items"][0]["code"] == "1"


def test_deep_shelf_and_seed_reproducible(client):
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add_all([Resource(code=str(i), source_name="站A", created_at=old) for i in range(30)])
        db.commit()
    first = [item["code"] for item in shelves(client, seed=7)["deep"]["items"]]
    again = [item["code"] for item in shelves(client, seed=7)["deep"]["items"]]
    other = [item["code"] for item in shelves(client, seed=8)["deep"]["items"]]
    assert len(first) == 12 and first == again and first != other


def test_tag_walk_picks_mid_sized_tag(client):
    for i in range(4):
        make(client, f"t{i}", ["小眾"])
    walk = shelves(client)["tag_walk"]
    assert walk["tag"] == "小眾" and len(walk["items"]) == 4
    assert "tag_walk_2" not in shelves(client)  # 只有一個中等數量標籤時只有一列


def test_tag_walk_skips_artist_and_group(client):
    for i in range(4):
        make(client, f"t{i}", ["某作者", "某社團", "題材"])
    with SessionLocal() as db:
        db.scalar(select(Tag).where(Tag.name == "某作者")).category = "artist"
        db.scalar(select(Tag).where(Tag.name == "某社團")).category = "group"
        db.commit()
    for seed in range(5):
        result = shelves(client, seed)
        assert result["tag_walk"]["tag"] == "題材" and "tag_walk_2" not in result


def test_tag_walk_two_rows_with_different_tags(client):
    for i in range(4):
        make(client, f"a{i}", ["甲"])
        make(client, f"b{i}", ["乙"])
        make(client, f"c{i}", ["丙", "甲"])  # 和「甲」重疊：第二列只放第一列沒出現的作品
    for seed in range(6):
        result = shelves(client, seed)
        first, second = result["tag_walk"], result["tag_walk_2"]
        assert first["tag"] != second["tag"] and first["title"] == f"標籤漫遊：#{first['tag']}"
        assert not {item["code"] for item in first["items"]} & {item["code"] for item in second["items"]}
        again = shelves(client, seed)
        assert [again["tag_walk"]["tag"], again["tag_walk_2"]["tag"]] == [first["tag"], second["tag"]]
        assert [item["code"] for item in again["tag_walk_2"]["items"]] == [item["code"] for item in second["items"]]
    assert len({shelves(client, seed)["tag_walk"]["tag"] for seed in range(10)}) > 1


def test_preference_shelf_rotates_with_seed(client):
    make(client, "done", ["奇幻"], status="已完食")
    for i in range(40):
        make(client, f"c{i}", ["奇幻", f"其他{i}"])
    picks = {tuple(item["code"] for item in shelves(client, seed=seed)["preference"]["items"]) for seed in range(5)}
    assert len(picks) > 1
    assert all(len(pick) == 12 for pick in picks)
    first = [item["code"] for item in shelves(client, seed=3)["preference"]["items"]]
    assert first == [item["code"] for item in shelves(client, seed=3)["preference"]["items"]]


def home(client, seed=0):
    return client.get("/api/home", params={"seed": seed}).json()


def test_home_hero_prefers_recently_opened_reading(client):
    first = make(client, "r1", ["奇幻"], status="閱讀中")
    make(client, "r2", ["奇幻"], status="閱讀中")
    make(client, "u1", ["料理"])
    client.post(f"/api/resources/{first['id']}/open")
    result = home(client)
    assert [item["code"] for item in result["hero"]] == ["r1", "r2"]
    rows = {row["key"]: row for row in result["rows"]}
    assert [item["code"] for item in rows["reading"]["items"]] == ["r1", "r2"]
    assert "recent" in rows and "finished" not in rows


def test_home_hero_tops_up_with_deep_dive_and_falls_back(client):
    assert home(client)["hero"] == [] and home(client)["rows"] == []
    make(client, "new", ["奇幻"])
    assert [item["code"] for item in home(client)["hero"]] == ["new"]
    old = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add_all([Resource(code=f"d{i}", source_name="站A", created_at=old, rating=i) for i in range(8)])
        db.commit()
    make(client, "reading", ["奇幻"], status="閱讀中")
    picks = {seed: [item["code"] for item in home(client, seed)["hero"]] for seed in range(6)}
    assert all(codes[0] == "reading" and len(codes) == 5 and all(code.startswith("d") for code in codes[1:]) for codes in picks.values())
    assert picks[3] == [item["code"] for item in home(client, 3)["hero"]]  # 同一個 seed 結果相同
    assert len({tuple(codes) for codes in picks.values()}) > 1  # 深潛作品不會每次都是同一批


def test_home_has_no_finished_row_and_shows_collections(client):
    make(client, "done", ["奇幻"], status="已完食")
    other = make(client, "other", ["料理"])
    weekend = client.post("/api/collections", json={"name": "週末要看"}).json()
    client.post(f"/api/collections/{weekend['id']}/items", json={"ids": [other["id"]]})
    client.post("/api/collections", json={"name": "空的"})
    client.post("/api/collections", json={"name": "奇幻精選", "query": "tag=奇幻"})
    rows = {row["key"]: row for row in home(client)["rows"]}
    assert "finished" not in rows  # 「最近完食」已拿掉
    titles = [row["title"] for row in rows.values() if row.get("collection")]
    assert titles == ["書單：週末要看", "書單：奇幻精選"]
    assert [item["code"] for item in rows[f"collection-{weekend['id']}"]["items"]] == ["other"]


def test_home_personal_rows_only_when_asked(client):
    done = client.post("/api/resources", json={"code": "d", "source_name": "站", "title": "d", "status": "已完食"}).json()
    rated = client.post("/api/resources", json={"code": "r", "source_name": "站", "title": "r", "status": "已完食", "rating": 4}).json()
    client.post(f"/api/resources/{done['id']}/open")
    client.post(f"/api/resources/{rated['id']}/open")
    keys = lambda data: {row["key"]: [item["id"] for item in row["items"]] for row in data["rows"]}
    plain = keys(client.get("/api/home").json())
    assert "unrated" not in plain and "opened" not in plain
    personal = keys(client.get("/api/home", params={"personal": True}).json())
    assert personal["unrated"] == [done["id"]]
    assert personal["opened"] == [rated["id"]]  # 已經在待評分的不重複
