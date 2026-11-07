"""新收錄作品的翻譯標籤不見（2026-11）：同義詞鏈、冷門標籤清單、修復工具。資料皆為虛構。"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from app.archive import resolve_alias
from app.database import SessionLocal, TagAlias, engine

TOOL = Path(__file__).resolve().parents[2] / "tools" / "dev" / "repair_tags.py"


def add(client, code, tags):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", "tags": tags})
    assert response.status_code == 201, response.text
    return response.json()


def aliases(**pairs):
    with SessionLocal() as db:
        for alias, canonical in pairs.items():
            db.merge(TagAlias(alias=alias.replace("_", " "), canonical=canonical))
        db.commit()


def rare(client):
    return {entry["name"] for entry in client.get("/api/insights/tags").json()["rare"]}


def test_resolve_alias_follows_chains_and_stops_on_loops():
    table = {"fluffy ears": "毛耳", "毛耳": "獸耳", "ping": "pong", "pong": "ping", "kitty": "Kitty"}
    assert resolve_alias(table, "Fluffy Ears") == "獸耳"
    assert resolve_alias(table, "ping") == "pong"  # 繞回來的鏈：停在第一步，與以前相同
    assert resolve_alias(table, "kitty") == "Kitty"  # 只差大小寫的對應照樣換
    assert resolve_alias(table, "其他") == "其他"


def test_new_work_gets_final_name_through_alias_chain(client):
    """手機上再合併過（毛耳→獸耳），電腦還留著舊的 fluffy ears→毛耳：新作品要拿到獸耳，不是已經沒人用的毛耳。"""
    add(client, "old", ["獸耳"])
    aliases(fluffy_ears="毛耳", **{"毛耳": "獸耳"})
    assert add(client, "new", ["Fluffy Ears", "純白"])["tags"] == ["獸耳", "純白"]


def test_translated_tag_is_not_listed_as_rare_and_survives_delete_all(client):
    """重現：原文標籤合併成翻譯後的名稱，剛收錄時只有一件，被列進「只用過一次」，按「全部刪除」就被刪掉。"""
    add(client, "a", ["tiny wings", "共通", "拼錯的字"])
    add(client, "b", ["共通"])
    assert client.post("/api/tags/merge", json={"source": "tiny wings", "target": "小翅膀"}).status_code == 200
    names = rare(client)
    assert "小翅膀" not in names and "拼錯的字" in names
    assert client.post("/api/tags/remove-many", json={"names": sorted(names), "block": False}).status_code == 200
    tags = {row["code"]: row["tags"] for row in client.get("/api/resources").json()["items"]}
    assert tags == {"a": ["共通", "小翅膀"], "b": ["共通"]}
    # 之後收錄、帶同一個原文標籤的作品照樣換成翻譯後的名稱
    assert add(client, "c", ["Tiny Wings"])["tags"] == ["小翅膀"]


def test_deleting_keeps_synonyms_but_blocking_skips_them(client):
    add(client, "a", ["tiny wings"])
    client.post("/api/tags/merge", json={"source": "tiny wings", "target": "小翅膀"})
    client.post("/api/tags/remove", json={"name": "小翅膀", "block": False})
    assert add(client, "b", ["tiny wings"])["tags"] == ["小翅膀"]  # 刪除：之後的新作品仍會帶上
    client.post("/api/tags/remove", json={"name": "小翅膀", "block": True})
    assert add(client, "c", ["tiny wings", "純白"])["tags"] == ["純白"]  # 封鎖：連同翻譯前的原文一起略過


def _tool():
    spec = importlib.util.spec_from_file_location("repair_tags", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not TOOL.exists(), reason="tools/dev 不在這個版本")
def test_repair_tool_restores_translated_tags_from_backup(client, tmp_path):
    add(client, "a", ["tiny wings", "共通", "拼錯的字"])
    add(client, "b", ["共通", "fluffy ears"])
    client.post("/api/tags/merge", json={"source": "tiny wings", "target": "小翅膀"})
    client.post("/api/tags/merge", json={"source": "fluffy ears", "target": "毛耳"})
    backup = tmp_path / "oas-auto-20990101-000000.json"
    backup.write_text(json.dumps(client.get("/api/backup").json(), ensure_ascii=False), encoding="utf-8")
    # 舊版「全部刪除」把翻譯後的標籤與真正的冷門標籤一起刪掉，又留下沒攤平的同義詞鏈
    client.post("/api/tags/remove-many", json={"names": ["小翅膀", "毛耳", "拼錯的字"], "block": False})
    aliases(**{"毛耳": "獸耳"})

    tool = _tool()
    snapshots = [tool.read_snapshot(path) for path in tool.snapshot_files(tmp_path)]
    db_file = engine.url.database
    conn = sqlite3.connect(f"file:{Path(db_file).as_posix()}?mode=ro", uri=True)
    result = tool.plan(conn, snapshots)
    conn.close()
    assert result["flatten"] == {"fluffy ears": "獸耳"}
    assert result["restore"] == {("站A", "a"): ["小翅膀"], ("站A", "b"): ["獸耳"]}  # 拼錯的字沒翻譯過，預設不補
    conn = sqlite3.connect(db_file)
    with conn:
        tool.apply(conn, result)
    conn.close()
    tags = {row["code"]: row["tags"] for row in client.get("/api/resources").json()["items"]}
    assert tags == {"a": ["共通", "小翅膀"], "b": ["共通", "獸耳"]}
    assert add(client, "c", ["fluffy ears"])["tags"] == ["獸耳"]
    with SessionLocal() as db:
        assert db.get(TagAlias, "fluffy ears").canonical == "獸耳"
