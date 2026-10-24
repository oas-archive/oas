import io

from PIL import Image

from app import covers, graph


def make(client, code, tags, image_url=""):
    return client.post("/api/resources", json={"code": code, "source_name": "站A", "title": f"作品{code}", "tags": tags, "image_url": image_url}).json()


def test_rare_shared_tag_outweighs_common_one():
    # 「常見」出現在所有作品裡；「冷門」只有 1、2 共有；「普通」有 5 件
    tag_sets = {i: {"常見"} for i in range(1, 21)}
    tag_sets[1] |= {"冷門", "普通"}
    tag_sets[2] |= {"冷門"}
    for i in (3, 4, 5, 6):
        tag_sets[i] |= {"普通"}
    edges, shared = graph.compute_edges(tag_sets)
    assert edges[(1, 2)] > edges.get((1, 3), 0)
    assert shared[(1, 2)][0] == "冷門"
    assert (7, 8) not in edges  # 只共用超常見標籤，不連線


def test_each_node_keeps_top_links():
    tag_sets = {i: {"群", f"獨{i}"} for i in range(20)}
    tag_sets.update({100 + i: {f"填{i}"} for i in range(60)})  # 讓「群」不超過 MAX_TAG_SHARE
    edges, _ = graph.compute_edges(tag_sets)
    degree = {}
    for a, b in edges:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
    # 每個節點自己最多保留 TOP_K 條；被他人選中可能略多，但總邊數受限
    assert len(edges) <= 20 * graph.TOP_K


def test_graph_endpoint_filters(client):
    a = make(client, "a", ["奇幻", "龍"])
    b = make(client, "b", ["奇幻", "龍"])
    c = make(client, "c", ["奇幻", "海"])
    make(client, "d", ["料理"])
    make(client, "e", [])
    for i in range(6):
        make(client, f"x{i}", [f"雜{i}"])
    data = client.get("/api/graph").json()
    ids = {node["id"] for node in data["nodes"]}
    assert {a["id"], b["id"], c["id"]} <= ids and data["isolated"] == 8
    edge = next(e for e in data["edges"] if {e["source"], e["target"]} == {a["id"], b["id"]})
    assert edge["similarity"] > 0.9 and set(edge["shared"]) == {"奇幻", "龍"}
    assert all(e["source"] in ids and e["target"] in ids for e in data["edges"])

    by_tag = client.get("/api/graph", params={"tag": "海"}).json()
    assert [node["id"] for node in by_tag["nodes"]] == [c["id"]] and by_tag["edges"] == []

    focus = client.get("/api/graph", params={"focus": a["id"]}).json()
    assert {node["id"] for node in focus["nodes"]} == {a["id"], b["id"], c["id"]}
    assert client.get("/api/graph", params={"focus": 99999}).json()["nodes"] == []


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
