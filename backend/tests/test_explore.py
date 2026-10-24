from datetime import datetime, timezone

from app.database import Resource, SessionLocal


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


def test_preference_shelf_rotates_with_seed(client):
    make(client, "done", ["奇幻"], status="已完食")
    for i in range(40):
        make(client, f"c{i}", ["奇幻", f"其他{i}"])
    picks = {tuple(item["code"] for item in shelves(client, seed=seed)["preference"]["items"]) for seed in range(5)}
    assert len(picks) > 1
    assert all(len(pick) == 12 for pick in picks)
    first = [item["code"] for item in shelves(client, seed=3)["preference"]["items"]]
    assert first == [item["code"] for item in shelves(client, seed=3)["preference"]["items"]]
