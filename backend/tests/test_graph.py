import io

from PIL import Image

from app import covers


def make(client, code, tags, image_url=""):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags, "image_url": image_url}).json()


def test_rare_shared_tag_outweighs_common_one(client):
    # 「常見」出現在所有作品裡；「冷門」只有 a、b 共有；「普通」有 5 件
    a = make(client, "a", ["常見", "冷門", "普通"])
    b = make(client, "b", ["常見", "冷門"])
    for i in range(4):
        make(client, f"c{i}", ["常見", "普通"])
    for i in range(14):
        make(client, f"x{i}", ["常見"])
    related = client.get(f"/api/resources/{a['id']}/related").json()
    assert related[0]["id"] == b["id"] and related[0]["shared"][0] == "冷門"
    assert all(item["code"][0] != "x" for item in related)  # 只共用超常見標籤，不算相似


def test_related_and_cache_invalidation(client):
    a = make(client, "a", ["奇幻", "龍"])
    b = make(client, "b", ["奇幻", "龍"])
    for i in range(6):
        make(client, f"x{i}", [f"雜{i}"])
    related = client.get(f"/api/resources/{a['id']}/related").json()
    assert [item["id"] for item in related] == [b["id"]] and related[0]["shared"]
    # 修改標籤後重新計算
    client.patch(f"/api/resources/{b['id']}", json={"code": "b", "source_name": "站A", "tags": ["料理"]})
    assert client.get(f"/api/resources/{a['id']}/related").json() == []
    assert client.get("/api/resources/99999/related").status_code == 404


def test_cover_colour_extracted_and_exposed(client, monkeypatch):
    buffer = io.BytesIO()
    image = Image.new("RGB", (300, 400), (20, 20, 20))
    image.paste((210, 40, 60), (0, 0, 300, 300))  # 大面積紅色
    image.save(buffer, "PNG")
    monkeypatch.setattr(covers, "download", lambda url: (buffer.getvalue(), "image/png"))
    item = make(client, "red", ["奇幻"], image_url="https://img.example/red.png")
    assert client.post("/api/covers/cache").json() == {"queued": 1}
    colour = client.get(f"/api/resources/{item['id']}").json()["cover_colour"]
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    assert r > g + 60 and r > b + 40  # 偏紅，且明度已調亮
    listed = client.get("/api/resources").json()["items"][0]
    assert listed["cover_colour"] == colour


def test_grey_and_broken_images(tmp_path):
    grey = tmp_path / "grey.png"
    Image.new("RGB", (50, 50), (128, 128, 128)).save(grey)
    assert covers.image_colour(grey).startswith("#")
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not an image")
    assert covers.image_colour(broken) == ""


def tagged(client, code, tags, categories=None):
    created = client.post("/api/resources", json={"code": code, "source_name": "站", "title": code, "tags": tags}).json()
    for name, category in (categories or {}).items():
        client.patch("/api/tags/category", json={"name": name, "category": category})
    return created


def filler(client, count=30):
    for number in range(count):
        tagged(client, f"填{number}", [f"填充{number}"])


def test_same_parody_beats_short_item_sharing_a_general_tag(client):
    """同原作要排在「只有一個標籤、剛好有共同一般標籤」的作品前面（2026-11 使用者回報的情況）。"""
    filler(client)
    names = ["甲作", "一般一", "一般二", "一般三", "一般四", "一般五", "一般六", "一般七"]
    base = tagged(client, "本體", names, {"甲作": "parody"})
    same = tagged(client, "同原作", ["甲作", "別的一", "別的二", "別的三", "別的四", "別的五", "別的六", "別的七"])
    short = tagged(client, "短", ["一般一"])
    tagged(client, "短伴", ["一般一"])
    rows = client.get(f"/api/resources/{base['id']}/related").json()
    assert [row["id"] for row in rows].index(same["id"]) < [row["id"] for row in rows].index(short["id"])
    assert rows[0]["shared"] == ["甲作"]
    assert all(0 < row["similarity"] <= 1 for row in rows)


def test_order_parody_character_then_artist_general_then_group(client):
    filler(client)
    common = [f"自有{number}" for number in range(6)]
    base = tagged(client, "本體", ["原作名", "角色名", "作者名", "一般名", "社團名", *common], {"原作名": "parody", "角色名": "character", "作者名": "artist", "社團名": "group"})
    names = ("原作", "角色", "作者", "一般", "社團")
    others = {name: tagged(client, name, [name + "名", *[f"{name}{number}" for number in range(6)]]) for name in names}
    rows = {row["id"]: row["similarity"] for row in client.get(f"/api/resources/{base['id']}/related").json()}
    score = {name: rows.get(others[name]["id"], 0) for name in names}  # 社團份量最輕，可能低於顯示門檻而不列入
    assert score["原作"] > score["角色"] > score["作者"] > score["社團"]
    assert abs(score["作者"] - score["一般"]) < 1e-3  # 作者和一般標籤同權重


def test_original_is_not_weighted_as_a_parody(client):
    """「原創」不是一部作品：共同的是原創時，不能贏過共同的角色。"""
    filler(client)
    base = tagged(client, "本體", ["原創", "角色甲", "一般一", "一般二"], {"原創": "parody", "角色甲": "character"})
    both_original = tagged(client, "同原創", ["原創", "別一", "別二", "別三"])
    same_character = tagged(client, "同角色", ["角色甲", "他一", "他二", "他三"])
    rows = client.get(f"/api/resources/{base['id']}/related").json()
    assert [row["id"] for row in rows][:2] == [same_character["id"], both_original["id"]]
