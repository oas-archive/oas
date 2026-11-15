"""作品的頁數（pages）與漫畫種類（format）：欄位升級、解析、手動值不被覆蓋、驗證、備份還原（含舊格式）、同步、篩選。資料皆為虛構。"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import metadata, refresh
from app.database import Base, Resource, SessionLocal, engine, initialise_database

BACKEND = Path(__file__).resolve().parents[1]
TOOLS = BACKEND.parent / "tools" / "dev"

NHENTAI = """<div id="cover"><a href="/g/1/1/"><img alt="[虛構社團] 虛構的書名" src="https://t.example/c.jpg"></a></div>
<section id="tags"><div class="tag-container field-name">Tags:<span class="tags"><a href="/tag/anthology/" class="tag"><span class="name">anthology</span></a></span></div>
<div class="tag-container field-name">Pages:<span class="tags"><a href="/search/?q=pages%3A32" class="tag"><span class="name">32</span></a></span></div></section>"""
NHENTAI_PLAIN = NHENTAI.replace("anthology", "romance")
WNACG = '<h2>虛構的書名</h2><div class="asTBcell uwconn"><label>分類：同人誌／漢化</label><label>頁數：24P</label></div>'


def add(client, code="P1", **fields):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", "title": "虛構作品", **fields})
    assert response.status_code == 201, response.text
    return response.json()


# ---------- 欄位升級 ----------

def test_old_database_gets_the_columns_and_restarts_are_harmless():
    Base.metadata.drop_all(engine)
    initialise_database()
    with engine.begin() as connection:  # 舊版建的資料庫：沒有 pages、format，已經有資料
        connection.execute(text("ALTER TABLE resources DROP COLUMN pages"))
        connection.execute(text("ALTER TABLE resources DROP COLUMN format"))
        connection.execute(text("INSERT INTO resources (id, code, source_name, title, note, image_url, rating, status, created_at, updated_at, image_kind, link_dead, kind, progress) "
                                "VALUES (1, 'UP-1', '虛構站', '舊作品', '', '', 0, '未讀', '2025-01-01', '2025-01-01', '', 0, '漫畫', '')"))
    initialise_database()
    initialise_database()
    with SessionLocal() as db:
        row = db.get(Resource, 1)
        assert row.title == "舊作品" and row.pages is None and row.format == ""


# ---------- 解析 ----------

def test_parsers_read_pages_only_when_the_page_has_them():
    assert metadata.parse("https://nhentai.net/g/1/", NHENTAI)["pages"] == 32
    assert metadata.parse("https://www.wnacg.com/photos-index-aid-1.html", WNACG)["pages"] == 24
    assert "pages" not in metadata.parse("https://www.wnacg.com/photos-index-aid-1.html", "<h2>虛構的書名</h2>")
    assert "pages" not in metadata.parse("https://site.example/p", "<title>x</title>")


def test_format_is_guessed_only_from_a_clear_tag():
    assert metadata.parse("https://nhentai.net/g/1/", NHENTAI)["format"] == "合集"
    assert "format" not in metadata.parse("https://nhentai.net/g/1/", NHENTAI_PLAIN)
    assert metadata.guess_format(["Anthology"]) == "合集" and metadata.guess_format(["anthologyish", "合集作者"]) == ""


def test_metadata_endpoint_carries_pages_and_format(client, monkeypatch):
    monkeypatch.setattr(metadata, "_get", lambda url: NHENTAI)
    data = client.post("/api/metadata", json={"url": "https://nhentai.net/g/1/"}).json()
    assert data["pages"] == 32 and data["format"] == "合集"
    monkeypatch.setattr(metadata, "_get", lambda url: NHENTAI_PLAIN)
    data = client.post("/api/metadata", json={"url": "https://nhentai.net/g/2/"}).json()
    assert data["pages"] == 32 and data["format"] == ""
    assert client.post("/api/metadata", json={"url": "https://nhentai.net/g/3/", "fetch": False}).json()["pages"] is None


def apply(item_id, data):
    with SessionLocal() as db:
        resource = db.get(Resource, item_id)
        changes = refresh.apply(db, resource, data)
        db.commit()
        return changes


def test_background_fill_sets_blanks_and_keeps_manual_values(client):
    blank = add(client, "B1", kind="漫畫")
    manual = add(client, "B2", kind="漫畫", pages=99, format="連載")
    video = add(client, "B3", kind="影片")
    data = {"pages": 32, "format": "合集"}
    assert apply(blank["id"], data)["changed"] is True
    assert apply(manual["id"], data)["changed"] is False
    assert apply(video["id"], data)["pages"] == 1  # 頁數不分類型
    got = {item["code"]: item for item in client.get("/api/resources").json()["items"]}
    assert (got["B1"]["pages"], got["B1"]["format"]) == (32, "合集")
    assert (got["B2"]["pages"], got["B2"]["format"]) == (99, "連載")
    assert (got["B3"]["pages"], got["B3"]["format"]) == (32, "")  # 種類只替漫畫填
    assert apply(blank["id"], {"pages": 50, "format": "單本"})["changed"] is False  # 已有值不再變


def test_fill_blanks_endpoint_fills_pages_and_format(client):
    item = add(client, "F1", kind="漫畫")
    filled = client.post(f"/api/resources/{item['id']}/fill-blanks", json={"pages": 40, "format": "單話"}).json()
    assert (filled["pages"], filled["format"]) == (40, "單話")
    again = client.post(f"/api/resources/{item['id']}/fill-blanks", json={"pages": 10, "format": "合集"}).json()
    assert (again["pages"], again["format"]) == (40, "單話")


# ---------- 輸入驗證與存檔 ----------

@pytest.mark.parametrize("fields", [{"pages": 0}, {"pages": -3}, {"pages": 100001}, {"pages": "很多"}, {"format": "長篇"}, {"format": "合集 "}])
def test_invalid_pages_or_format_are_rejected(client, fields):
    assert client.post("/api/resources", json={"code": "V1", "source_name": "站A", **fields}).status_code == 422
    item = add(client, "V2", kind="漫畫")
    assert client.patch(f"/api/resources/{item['id']}", json={"code": "V2", "source_name": "站A", **fields}).status_code == 422


def test_create_and_edit_new_item_keeps_pages_and_format(client):
    """收錄新作品（還沒有 id）：存檔後頁數與種類正確；之後編輯可改、可清空。"""
    item = add(client, "N1", kind="漫畫", pages=120, format="單本")
    assert (item["pages"], item["format"]) == (120, "單本")
    body = {"code": "N1", "source_name": "站A", "title": "虛構作品", "kind": "漫畫"}
    edited = client.patch(f"/api/resources/{item['id']}", json={**body, "pages": 130, "format": "合集"}).json()
    assert (edited["pages"], edited["format"]) == (130, "合集")
    cleared = client.patch(f"/api/resources/{item['id']}", json={**body, "pages": None, "format": ""}).json()
    assert (cleared["pages"], cleared["format"]) == (None, "")
    assert client.get(f"/api/resources/{item['id']}").json()["pages"] is None
    assert add(client, "N2")["pages"] is None and add(client, "N3")["format"] == ""  # 不填就是不知道


def test_format_is_cleared_for_non_manga(client):
    video = add(client, "M1", kind="影片", format="合集", pages=10)
    assert video["format"] == "" and video["pages"] == 10
    manga = add(client, "M2", kind="漫畫", format="合集")
    edited = client.patch(f"/api/resources/{manga['id']}", json={"code": "M2", "source_name": "站A", "kind": "動畫", "format": "合集"}).json()
    assert edited["format"] == ""


def test_format_filter_only_matches_manga(client):
    add(client, "X1", kind="漫畫", format="合集")
    add(client, "X2", kind="漫畫", format="單本")
    add(client, "X3", kind="漫畫")
    add(client, "X4", kind="漫畫", format="合集")
    with SessionLocal() as db:  # 類型被改掉（例如批次設定）時種類不會清掉，篩選仍只看漫畫
        db.execute(Resource.__table__.update().where(Resource.code == "X4").values(kind="影片"))
        db.commit()
    codes = lambda **params: sorted(item["code"] for item in client.get("/api/resources", params=params).json()["items"])
    assert codes(format="合集") == ["X1"] and codes(format="單本") == ["X2"] and codes(format="連載") == []
    assert codes() == ["X1", "X2", "X3", "X4"]
    assert client.get("/api/tags/stats", params={"format": "合集"}).status_code == 200


def test_smart_collection_query_accepts_format(client):
    add(client, "C1", kind="漫畫", format="合集")
    add(client, "C2", kind="漫畫", format="單本")
    made = client.post("/api/collections", json={"name": "合集書單", "query": "format=%E5%90%88%E9%9B%86"}).json()
    assert made["count"] == 1


# ---------- 合併、復原 ----------

def test_merge_keeps_pages_and_format_from_the_other_copy(client):
    keep = add(client, "G1", kind="漫畫")
    other = add(client, "G2", kind="漫畫", pages=77, format="連載")
    response = client.post(f"/api/resources/{keep['id']}/merge", json={"ids": [keep["id"], other["id"]]})
    assert response.status_code == 200, response.text
    merged = client.get(f"/api/resources/{keep['id']}").json()
    assert (merged["pages"], merged["format"]) == (77, "連載")


def test_undo_restores_deleted_item_with_pages_and_format(client):
    item = add(client, "U1", kind="漫畫", pages=55, format="單話")
    result = client.post("/api/resources/batch", json={"ids": [item["id"]], "action": "delete"}).json()
    assert client.post(f"/api/undo/{result['undo']}").status_code == 200
    back = client.get("/api/resources", params={"q": "U1"}).json()["items"][0]
    assert (back["pages"], back["format"]) == (55, "單話")


# ---------- 備份與還原 ----------

def test_backup_carries_the_new_fields_and_restores_them(client):
    add(client, "R1", kind="漫畫", pages=210, format="連載")
    add(client, "R2", kind="漫畫")
    backup = client.get("/api/backup").json()
    assert backup["version"] == 6
    by_code = {item["code"]: item for item in backup["resources"]}
    assert (by_code["R1"]["pages"], by_code["R1"]["format"]) == (210, "連載") and (by_code["R2"]["pages"], by_code["R2"]["format"]) == (None, "")
    assert client.post("/api/restore", params={"mode": "replace"}, json=backup).status_code == 200
    got = {item["code"]: item for item in client.get("/api/resources").json()["items"]}
    assert (got["R1"]["pages"], got["R1"]["format"]) == (210, "連載") and got["R2"]["pages"] is None


def test_old_backup_without_the_fields_restores_without_error(client):
    old = {"format": "oas-backup", "version": 5, "sources": [], "resources": [{"source_name": "站A", "code": "O1", "title": "舊備份作品", "status": "未讀", "rating": 0}]}
    assert client.post("/api/restore", json=old).status_code == 200
    item = client.get("/api/resources").json()["items"][0]
    assert item["pages"] is None and item["format"] == ""


def test_restore_ignores_bad_values_and_fills_only_blanks_on_existing_items(client):
    existing = add(client, "E1", kind="漫畫", pages=15)
    add(client, "E2", kind="漫畫")
    backup = {"format": "oas-backup", "version": 6, "sources": [], "resources": [
        {"source_name": "站A", "code": "E1", "pages": 999, "format": "合集"},  # 本機已有頁數：不蓋掉；種類是空白：補上
        {"source_name": "站A", "code": "E2", "pages": 0, "format": "亂填"},  # 無效：忽略
        {"source_name": "站A", "code": "E3", "kind": "漫畫", "pages": "12", "format": "單話"},
        {"source_name": "站A", "code": "E4", "pages": 10 ** 9, "format": 5},
    ]}
    report = client.post("/api/restore", json=backup).json()
    assert report["created"] == 2 and report["fields_filled"] == 1
    got = {item["code"]: item for item in client.get("/api/resources").json()["items"]}
    assert (got["E1"]["pages"], got["E1"]["format"]) == (15, "合集")
    assert (got["E2"]["pages"], got["E2"]["format"]) == (None, "")
    assert (got["E3"]["pages"], got["E3"]["format"]) == (12, "單話") and (got["E4"]["pages"], got["E4"]["format"]) == (None, "")
    assert existing["updated_at"] == client.get(f"/api/resources/{existing['id']}").json()["updated_at"]  # 補欄位不算改過作品


# ---------- 手機 ↔ 電腦同步 ----------

needs_tools = pytest.mark.skipif(not (TOOLS / "sync_from_phone.py").exists(), reason="tools/dev 不在這個版本")


def run_sync(tmp_path, backup, data_dir, port):
    path = tmp_path / "phone.json"
    path.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in ("OAS_DATABASE_URL", "OAS_COVER_DIR")}
    env.update(OAS_DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", OAS_GUARD_PORT=str(port))
    result = subprocess.run([sys.executable, str(TOOLS / "sync_from_phone.py"), str(path), "--apply"], cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout


@needs_tools
def test_sync_tool_takes_pages_and_format_from_the_phone_and_skips_old_backups(tmp_path):
    data_dir = tmp_path / "pc"
    data_dir.mkdir()
    db_path = data_dir / "oas.db"
    local = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(local)
    with Session(local) as db:
        db.add_all([Resource(code="S1", source_name="站A", kind="漫畫", pages=10), Resource(code="S2", source_name="站A", kind="漫畫", pages=20, format="單本")])
        db.commit()
    common = {"source_name": "站A", "title": "", "status": "未讀", "rating": 0, "kind": "漫畫"}
    rows = lambda: sqlite3.connect(db_path).execute("select code, pages, format from resources order by code").fetchall()
    with socket.socket() as reserved:  # 寫入前的「服務開著就拒絕」改探測一個沒人在聽的埠（綁住但不 listen）
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        run_sync(tmp_path, {"format": "oas-backup", "version": 6, "sources": [], "resources": [{**common, "code": "S1", "pages": 48, "format": "合集"}, {**common, "code": "S2", "pages": None, "format": ""}]}, data_dir, port)
        assert rows() == [("S1", 48, "合集"), ("S2", None, "")]  # 手機為準（含手機沒填）
        with Session(local) as db:  # 舊版手機（備份沒有這兩欄）：電腦的值不動
            db.query(Resource).filter(Resource.code == "S2").update({"pages": 20, "format": "單本"})
            db.commit()
        run_sync(tmp_path, {"format": "oas-backup", "version": 5, "sources": [], "resources": [{**common, "code": "S1"}, {**common, "code": "S2"}]}, data_dir, port)
        assert rows() == [("S1", 48, "合集"), ("S2", 20, "單本")]


def test_patch_without_pages_or_format_keeps_them(client):
    """舊版手機或外部呼叫的 PATCH 沒帶 pages／format：不能把兩欄清空；有帶（含 null、空字串）才改。"""
    item = add(client, "K1", kind="漫畫", pages=80, format="連載")
    body = {"code": "K1", "source_name": "站A", "title": "改過的標題", "kind": "漫畫"}
    kept = client.patch(f"/api/resources/{item['id']}", json=body).json()
    assert (kept["title"], kept["pages"], kept["format"]) == ("改過的標題", 80, "連載")
    only_pages = client.patch(f"/api/resources/{item['id']}", json={**body, "pages": 90}).json()
    assert (only_pages["pages"], only_pages["format"]) == (90, "連載")
    cleared = client.patch(f"/api/resources/{item['id']}", json={**body, "pages": None, "format": ""}).json()
    assert (cleared["pages"], cleared["format"]) == (None, "")


def test_restore_writes_format_only_for_comics(client):
    """備份還原：不是漫畫的作品，種類存成空字串；本機已有的作品也只在漫畫時才補種類。"""
    rows = [{"source_name": "站A", "code": code, "title": "虛構", "status": "未讀", "rating": 0, "kind": kind, "format": "單本"}
            for code, kind in [("R1", "漫畫"), ("R2", "動畫"), ("R3", "")]]
    assert client.post("/api/restore", json={"format": "oas-backup", "version": 6, "sources": [], "resources": rows}).status_code == 200
    formats = {item["code"]: item["format"] for item in client.get("/api/backup").json()["resources"]}
    assert formats == {"R1": "單本", "R2": "", "R3": ""}
    blank = add(client, "R4", kind="動畫")
    again = {"source_name": "站A", "code": "R4", "kind": "動畫", "format": "合集"}
    client.post("/api/restore", json={"format": "oas-backup", "version": 6, "sources": [], "resources": [again]})
    assert client.get(f"/api/resources/{blank['id']}").json()["format"] == ""
