"""備份 v5（巡檢時間、標籤健檢保留清單、不是重複的組合）與手機同步工具。資料皆為虛構。"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import linkcheck
from app.archive import BACKUP_VERSION
from app.covers import url_hash
from app.database import Base, CoverCache, DuplicateDismissal, Job, Resource, SessionLocal

BACKEND = Path(__file__).resolve().parents[1]
TOOLS = BACKEND.parent / "tools" / "dev"
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def add(client, code, source="站A", **fields):
    response = client.post("/api/resources", json={"code": code, "source_name": source, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def backup(client):
    return client.get("/api/backup").json()


def row(code, source="站A", **fields):
    return {"code": code, "source_name": source, "status": "未讀", **fields}


def payload(resources, **fields):
    return {"format": "oas-backup", "version": BACKUP_VERSION, "resources": resources, **fields}


def set_link(resource_id, checked_at, dead=False):
    with SessionLocal() as db:
        db.execute(Resource.__table__.update().where(Resource.id == resource_id).values(link_checked_at=checked_at, link_dead=dead, updated_at=NOW - timedelta(days=30)))
        db.commit()


def fetch(resource_id):
    with SessionLocal() as db:
        return db.get(Resource, resource_id)


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def test_export_v5_contents(client):
    one, two = add(client, "1"), add(client, "2", source="站B")
    set_link(one["id"], NOW)
    client.post("/api/insights/tags/keep", json={"name": "冷門標籤"})
    client.post("/api/duplicates/dismiss", json={"ids": [one["id"], two["id"]]})
    data = backup(client)
    assert data["version"] == 5
    item = next(entry for entry in data["resources"] if entry["code"] == "1")
    assert item["link_checked_at"] == stamp(NOW.replace(tzinfo=None)) and "image_kind" in item and "link_dead" in item
    assert data["tag_health_keep"] == ["冷門標籤"]
    assert data["duplicate_dismissals"] == [[{"source_name": "站A", "code": "1"}, {"source_name": "站B", "code": "2"}]]
    assert "link_checked_at" not in client.get(f"/api/resources/{one['id']}").json()  # 一般 API 不變


def test_export_skips_dismissal_with_deleted_members(client):
    one, two, three = add(client, "1"), add(client, "2"), add(client, "3")
    client.post("/api/duplicates/dismiss", json={"ids": [one["id"], two["id"], three["id"]]})
    client.delete(f"/api/resources/{three['id']}")
    assert len(backup(client)["duplicate_dismissals"][0]) == 2  # 還有兩件，關係仍然成立
    client.delete(f"/api/resources/{two['id']}")
    assert backup(client)["duplicate_dismissals"] == []


def test_merge_takes_link_check_only_when_newer(client):
    older, newer, never = add(client, "old"), add(client, "new"), add(client, "never")
    set_link(older["id"], NOW - timedelta(days=9))
    set_link(newer["id"], NOW - timedelta(days=1), dead=True)
    before = fetch(older["id"]).updated_at
    report = client.post("/api/restore", json=payload([
        row("old", link_checked_at=stamp(NOW - timedelta(days=2)), link_dead=True, title="不該蓋掉"),
        row("new", link_checked_at=stamp(NOW - timedelta(days=5)), link_dead=False),
        row("never", link_checked_at=None, link_dead=True),
    ])).json()
    assert report["skipped"] == 3 and report["created"] == 0 and report["link_checks_updated"] == 1
    updated = fetch(older["id"])
    assert utc(updated.link_checked_at) == NOW - timedelta(days=2) and updated.link_dead is True
    assert updated.updated_at == before and updated.title == ""  # 不動修改時間，其他欄位仍然只補不改
    kept = fetch(newer["id"])
    assert utc(kept.link_checked_at) == NOW - timedelta(days=1) and kept.link_dead is True
    untouched = fetch(never["id"])
    assert untouched.link_checked_at is None and untouched.link_dead is False


def test_new_rows_carry_kind_and_link_state(client):
    client.post("/api/restore", json=payload([row("n", image_kind="icon", link_dead=True, link_checked_at=stamp(NOW))]))
    item = client.get("/api/resources").json()["items"][0]
    assert item["image_kind"] == "icon" and item["link_dead"] is True
    assert utc(fetch(item["id"]).link_checked_at) == NOW


def test_keep_list_is_a_union(client):
    client.post("/api/insights/tags/keep", json={"name": "本機的"})
    report = client.post("/api/restore", json=payload([], tag_health_keep=["備份的", "本機的"])).json()
    assert report["keep_added"] == 1
    assert backup(client)["tag_health_keep"] == ["備份的", "本機的"]


def test_dismissals_translate_between_different_ids(client):
    """備份來源的編號和這裡不同：鍵要換算成這裡的編號，不能直接複製。"""
    add(client, "佔位")  # 讓這裡的編號往後移
    first, second = add(client, "a"), add(client, "b", source="站B")
    third = add(client, "c")
    groups = [
        [{"source_name": "站A", "code": "a"}, {"source_name": "站B", "code": "b"}],
        [{"source_name": "站A", "code": "c"}, {"source_name": "站A", "code": "沒有這件"}],  # 找不到：整組略過
        [{"source_name": "站A", "code": "c"}],  # 不足兩件
    ]
    report = client.post("/api/restore", json=payload([], duplicate_dismissals=groups)).json()
    assert report["dismissals_added"] == 1 and report["dismissals_skipped"] == 2
    with SessionLocal() as db:
        assert [entry.key for entry in db.query(DuplicateDismissal).all()] == [f"{first['id']},{second['id']}"]
    again = client.post("/api/restore", json=payload([], duplicate_dismissals=groups)).json()
    assert again["dismissals_added"] == 0
    assert third["id"] not in (first["id"], second["id"])


def test_dismissals_round_trip_with_new_rows(client):
    """合併還原時一起新增的作品也換算得到。"""
    groups = [[{"source_name": "站A", "code": "x"}, {"source_name": "站A", "code": "y"}]]
    client.post("/api/restore", json=payload([row("x"), row("y")], duplicate_dismissals=groups))
    assert backup(client)["duplicate_dismissals"] == groups


def test_replace_drops_old_ids_and_restores_dismissals(client):
    one, two = add(client, "1"), add(client, "2")
    client.post("/api/duplicates/dismiss", json={"ids": [one["id"], two["id"]]})
    groups = [[{"source_name": "站A", "code": "8"}, {"source_name": "站A", "code": "9"}]]
    client.post("/api/restore", params={"mode": "replace"}, json=payload([row("7"), row("8"), row("9")], duplicate_dismissals=groups))
    assert backup(client)["duplicate_dismissals"] == groups
    with SessionLocal() as db:
        assert db.query(DuplicateDismissal).count() == 1


def test_old_backup_still_restores_and_changes_nothing_new(client):
    item = add(client, "1")
    other = add(client, "2")
    set_link(item["id"], NOW)
    client.post("/api/insights/tags/keep", json={"name": "本機的"})
    client.post("/api/duplicates/dismiss", json={"ids": [item["id"], other["id"]]})
    old = {"format": "oas-backup", "version": 4, "resources": [row("1", link_dead=True), row("舊版新件")]}
    report = client.post("/api/restore", json=old)
    assert report.status_code == 200 and report.json()["created"] == 1 and report.json()["link_checks_updated"] == 0
    data = backup(client)
    assert data["tag_health_keep"] == ["本機的"] and len(data["duplicate_dismissals"]) == 1
    assert fetch(item["id"]).link_dead is False and fetch(item["id"]).link_checked_at is not None
    assert next(entry for entry in data["resources"] if entry["code"] == "舊版新件")["link_checked_at"] is None


def test_fingerprint_ignores_link_check_time(client):
    fingerprint = lambda: client.get("/api/backup/fingerprint").json()["fingerprint"]
    one, two = add(client, "1"), add(client, "2")
    start = fingerprint()
    linkcheck._mark(one["id"], False)  # 巡檢通過：只有巡檢時間變了，不必自動備份
    assert fingerprint() == start
    linkcheck._mark(one["id"], True)  # 巡檢發現失效：這是資料變動
    dead = fingerprint()
    assert dead != start
    client.post("/api/insights/tags/keep", json={"name": "保留"})
    kept = fingerprint()
    assert kept != dead
    client.post("/api/duplicates/dismiss", json={"ids": [one["id"], two["id"]]})
    assert fingerprint() != kept
    assert backup(client)["resources"][0]["link_checked_at"]  # 指紋排除，備份本身仍然帶著


# ---------- 工具（tools/dev 不在公開版，沒有就略過） ----------

needs_tools = pytest.mark.skipif(not (TOOLS / "sync_from_phone.py").exists(), reason="tools/dev 不在這個版本")


def run_tool(name, *args, data_dir):
    env = {key: value for key, value in os.environ.items() if key not in ("OAS_DATABASE_URL", "OAS_COVER_DIR")}
    env.update(OAS_DATA_DIR=str(data_dir), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, str(TOOLS / name), *map(str, args)], cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    return result.stdout


def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def phone_backup(tmp_path, resources, **fields):
    path = tmp_path / "phone.json"
    path.write_text(json.dumps({"format": "oas-backup", "version": 5, "sources": [], **{"resources": resources}, **fields}, ensure_ascii=False), encoding="utf-8")
    return path


def pc_rows(engine):
    with Session(engine) as db:
        return {item.code: item for item in db.query(Resource).all()}


@needs_tools
def test_sync_tool_link_state_keep_dismissals_and_jobs(tmp_path):
    data_dir = tmp_path / "pc"
    engine = make_db(data_dir / "oas.db")
    original = NOW - timedelta(days=40)
    with Session(engine) as db:
        db.add_all([Resource(id=10 + number, code=code, source_name="站A", updated_at=original, created_at=original, **fields) for number, (code, fields) in enumerate({
            "phone-newer": {"link_checked_at": NOW - timedelta(days=20), "image_kind": "icon"},
            "pc-newer": {"link_checked_at": NOW - timedelta(days=1), "link_dead": True},
            "both-old": {"link_checked_at": NOW - timedelta(days=30)},
        }.items())])
        db.add_all([Job(kind="linkcheck", resource_id=10, url="https://example.com/1"), Job(kind="linkcheck", resource_id=12, url="https://example.com/3"),
                    Job(kind="fill", resource_id=10, url="https://example.com/1")])
        db.commit()
    common = {"title": "", "status": "未讀", "rating": 0, "updated_at": stamp(original.replace(tzinfo=None))}
    groups = [[{"source_name": "站A", "code": "phone-newer"}, {"source_name": "站A", "code": "pc-newer"}],
              [{"source_name": "站A", "code": "both-old"}, {"source_name": "站A", "code": "電腦沒有"}]]
    path = phone_backup(tmp_path, [
        {**common, "source_name": "站A", "code": "phone-newer", "image_kind": "", "link_dead": True, "link_checked_at": stamp(NOW - timedelta(days=2))},
        {**common, "source_name": "站A", "code": "pc-newer", "image_kind": "", "link_dead": False, "link_checked_at": stamp(NOW - timedelta(days=3))},
        {**common, "source_name": "站A", "code": "both-old", "image_kind": "avatar", "link_dead": False, "link_checked_at": stamp(NOW - timedelta(days=30))},
    ], tag_health_keep=["保留的"], duplicate_dismissals=groups)

    preview = run_tool("sync_from_phone.py", path, data_dir=data_dir)
    assert "封面種類更新 2 件" in preview and "巡檢時間更新 1 件" in preview and "失效標記更新 1 件" in preview
    assert "電腦較新保留 1 件（其中失效標記和手機不同 1 件）" in preview and "刪除排隊中的巡檢工作 1 個" in preview
    assert "新增 1 組" in preview and "略過 1 組" in preview
    assert "更新作品 0" in preview
    assert pc_rows(engine)["phone-newer"].link_dead is False  # 預演沒有寫入

    run_tool("sync_from_phone.py", path, "--apply", data_dir=data_dir)
    items = pc_rows(engine)
    assert items["phone-newer"].link_dead is True and utc(items["phone-newer"].link_checked_at) == NOW - timedelta(days=2) and items["phone-newer"].image_kind == ""
    assert items["pc-newer"].link_dead is True and utc(items["pc-newer"].link_checked_at) == NOW - timedelta(days=1)
    assert items["both-old"].image_kind == "avatar"
    assert all(utc(item.updated_at) == original for item in items.values())  # 這些不算使用者改過作品
    with Session(engine) as db:
        assert {(job.kind, job.resource_id) for job in db.query(Job).all()} == {("linkcheck", 12), ("fill", 10)}  # 只刪週期內查過的巡檢工作
        assert [entry.key for entry in db.query(DuplicateDismissal).all()] == ["10,11"]
    connection = sqlite3.connect(data_dir / "oas.db")
    assert json.loads(connection.execute("select value from settings where key='tag_health_keep'").fetchone()[0]) == ["保留的"]
    connection.close()


@needs_tools
def test_sync_tool_skips_fields_missing_from_old_backup(tmp_path):
    data_dir = tmp_path / "pc"
    engine = make_db(data_dir / "oas.db")
    checked = NOW - timedelta(days=1)
    with Session(engine) as db:
        db.add(Resource(code="1", source_name="站A", link_checked_at=checked, link_dead=True, image_kind="icon"))
        db.add(DuplicateDismissal(key="1,2"))
        db.add(Job(kind="linkcheck", resource_id=1, url="https://example.com/1"))
        db.commit()
    path = phone_backup(tmp_path, [{"source_name": "站A", "code": "1", "title": "", "status": "未讀", "rating": 0}])
    output = run_tool("sync_from_phone.py", path, "--apply", data_dir=data_dir)
    assert "沒有巡檢時間" in output
    item = pc_rows(engine)["1"]
    assert item.link_dead is True and item.image_kind == "icon" and utc(item.link_checked_at) == checked
    with Session(engine) as db:
        assert db.query(DuplicateDismissal).count() == 1 and db.query(Job).count() == 1


COVER_FIELDS = dict(status="ok", content_type="image/jpeg")


@needs_tools
def test_pull_phone_covers(tmp_path):
    data_dir, phone_dir = tmp_path / "pc", tmp_path / "phone"
    pc = make_db(data_dir / "oas.db")
    phone_engine = make_db(phone_dir / "oas.db")
    urls = {name: f"https://example.com/{name}.jpg" for name in ("missing", "failed", "have", "nofile", "short", "nowhere")}
    hashes = {name: url_hash(url) for name, url in urls.items()}
    with Session(pc) as db:
        db.add_all([Resource(code=name, source_name="站A", image_url=url) for name, url in urls.items()])
        db.add(CoverCache(url_hash=hashes["failed"], url=urls["failed"], status="failed", error="HTTP 403"))
        db.add(CoverCache(url_hash=hashes["have"], url=urls["have"], filename="have.jpg", size=3, colour="", ratio=None, fingerprint="aaaa", **COVER_FIELDS))
        db.commit()
    (data_dir / "covers").mkdir()
    (data_dir / "covers" / "have.jpg").write_bytes(b"old")
    with Session(phone_engine) as db:
        for name, size in (("missing", 3), ("failed", 3), ("have", 3), ("nofile", 3), ("short", 9)):
            db.add(CoverCache(url_hash=hashes[name], url=urls[name], filename=f"{name}.jpg", size=size, ratio=1.5, colour="#112233", fingerprint="bbbb", **COVER_FIELDS))
        db.commit()
    folder = tmp_path / "phone_covers"
    folder.mkdir()
    for name in ("missing", "failed", "have", "short"):
        (folder / f"{name}.jpg").write_bytes(b"new")

    preview = run_tool("pull_phone_covers.py", phone_dir / "oas.db", folder, data_dir=data_dir)
    assert "電腦已有 1；可從手機補 2" in preview and "沒有檔案 1" in preview and "不符 1" in preview and "手機也沒有 1" in preview
    assert "colour 1、ratio 1、fingerprint 0" in preview
    assert not (data_dir / "covers" / "missing.jpg").exists()

    output = run_tool("pull_phone_covers.py", phone_dir / "oas.db", folder, "--apply", data_dir=data_dir)
    assert "複製了 2 個檔案" in output
    assert (data_dir / "covers" / "missing.jpg").read_bytes() == b"new" and (data_dir / "covers" / "have.jpg").read_bytes() == b"old"
    assert list(tmp_path.glob("phone/pc-before-covers-*.db"))
    with Session(pc) as db:
        rows = {row.url: row for row in db.query(CoverCache).all()}
    recovered = rows[urls["failed"]]
    assert (recovered.status, recovered.error, recovered.filename, recovered.colour, recovered.ratio) == ("ok", "", "failed.jpg", "#112233", 1.5)
    assert rows[urls["missing"]].fingerprint == "bbbb" and rows[urls["missing"]].url_hash == hashes["missing"]
    have = rows[urls["have"]]
    assert (have.colour, have.ratio, have.fingerprint) == ("#112233", 1.5, "aaaa")  # 只補空的
    assert urls["short"] not in rows and urls["nofile"] not in rows


@needs_tools
def test_compare_tool_translates_ids_and_hides_private_values(tmp_path):
    phone_path, pc_path = tmp_path / "phone" / "oas.db", tmp_path / "pc" / "oas.db"
    phone, pc = make_db(phone_path), make_db(pc_path)
    with Session(phone) as db:
        db.add_all([Resource(id=1, code="a", source_name="站A", title="不該印出的標題"), Resource(id=2, code="b", source_name="站A"),
                    DuplicateDismissal(key="1,2"), DuplicateDismissal(key="1,99")])
        db.commit()
    with Session(pc) as db:
        db.add_all([Resource(id=5, code="b", source_name="站A"), Resource(id=6, code="a", source_name="站A", title="電腦的標題", link_checked_at=NOW, image_url="https://example.com/x.jpg")])
        db.commit()
    result = subprocess.run([sys.executable, "-I", str(TOOLS / "compare_phone.py"), str(phone_path), str(pc_path)], capture_output=True, text=True, encoding="utf-8",
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    assert "不該印出的標題" not in result.stdout and "電腦的標題" not in result.stdout
    assert "只在手機 1（其中電腦都有這些作品、換算得到 1）" in result.stdout
    assert "'title': 1" in result.stdout and "link_checked_at：手機較新 0、電腦較新 1" in result.stdout
    assert "需要處理" in result.stdout


def together_library(client):
    for number in range(4):
        add(client, f"p{number}", tags=["甲標籤", "乙標籤"])


def test_kept_pairs_are_stored_and_hidden(client):
    together_library(client)
    entry = client.get("/api/insights/tags").json()["paired"][0]
    assert client.post("/api/insights/tags/keep-pairs", json={"pairs": [f"{entry['target']}|{entry['source']}"]}).status_code == 200
    assert client.get("/api/insights/tags").json()["paired"] == []


def test_kept_pair_hidden_when_direction_swaps(client):
    together_library(client)
    entry = client.get("/api/insights/tags").json()["paired"][0]
    client.post("/api/insights/tags/keep-pairs", json={"pairs": [f"{entry['source']}|{entry['target']}"]})
    assert client.get("/api/insights/tags").json()["paired"] == []


def test_kept_pairs_travel_in_backup_as_union(client):
    client.post("/api/insights/tags/keep-pairs", json={"pairs": ["本機|組合"]})
    data = backup(client)
    assert data["tag_pairs_keep"] == ["本機|組合"]
    data["tag_pairs_keep"] = ["手機|組合"]
    assert client.post("/api/restore?mode=merge", json=data).status_code == 200
    assert backup(client)["tag_pairs_keep"] == ["手機|組合", "本機|組合"]
