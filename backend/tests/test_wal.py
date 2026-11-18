"""SQLite WAL：開啟、快照備份不漏資料、還原不受舊 -wal 影響、結構版本號。資料都是虛構的。"""
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from app.database import DATABASE_URL, SCHEMA_VERSION, Base, Resource, SessionLocal, engine, initialise_database, replace_database_file, snapshot_database

DB_FILE = Path(DATABASE_URL.removeprefix("sqlite:///"))


def fresh():
    Base.metadata.drop_all(engine)
    initialise_database()


def add(count: int, prefix: str = "x") -> None:
    with SessionLocal() as db:
        for number in range(count):
            db.add(Resource(code=f"{prefix}{number}", source_name="測試站", title=f"虛構作品 {number}"))
        db.commit()


def count_in(path) -> int:
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        return conn.execute("select count(*) from resources").fetchone()[0]
    finally:
        conn.close()


def test_connection_uses_wal_and_normal_sync():
    fresh()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert connection.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_user_version_is_set_and_never_lowered():
    fresh()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION
    with engine.begin() as connection:
        connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION + 5}"))
    initialise_database()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION + 5
        connection.execute(text("PRAGMA user_version = 0"))
        connection.commit()
    initialise_database()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA user_version")).scalar() == SCHEMA_VERSION


def test_snapshot_right_after_a_big_write_has_everything(tmp_path):
    fresh()
    add(2000)
    target = tmp_path / "snap.db"
    snapshot_database(target)  # 不 checkpoint：最新寫入可能還在 -wal
    assert count_in(target) == 2000
    assert not Path(f"{target}-wal").exists() or Path(f"{target}-wal").stat().st_size == 0
    assert sqlite3.connect(target).execute("pragma integrity_check").fetchone()[0] == "ok"


def test_plain_copy_of_main_file_alone_can_miss_data(tmp_path):
    """說明為什麼不能只複製 oas.db：WAL 檔還在時，只複製主檔會少資料（若這台環境剛好已 checkpoint 就略過）。"""
    fresh()
    add(500)
    wal = Path(f"{DB_FILE}-wal")
    if not wal.exists() or wal.stat().st_size == 0:
        return
    copy = tmp_path / "naive.db"
    copy.write_bytes(DB_FILE.read_bytes())
    try:
        copied = count_in(copy)
    except sqlite3.OperationalError:  # 連資料表都還在 -wal 裡
        return
    if copied == 500:  # -wal 檔 checkpoint 後不會變空，主檔可能已經是最新（平行跑測試時較常遇到）：這次示範不成立就略過
        pytest.skip("這次寫入已經 checkpoint 進主檔")
    # 只複製主檔拿到的不是現在的資料：可能少了還在 -wal 的新寫入，也可能是前一項測試留下、已被刪掉的舊資料
    assert copied != 500


def test_replace_ignores_stale_wal(tmp_path):
    fresh()
    add(3, "old")
    snapshot = tmp_path / "before.db"
    snapshot_database(snapshot)
    add(50, "later")  # 還原前又寫了一批：這些在舊 -wal 裡，不能套到還原後的檔案上
    replace_database_file(snapshot)
    with SessionLocal() as db:
        assert db.query(Resource).count() == 3
    assert count_in(DB_FILE) == 3
    add(1, "after")
    with SessionLocal() as db:
        assert db.query(Resource).count() == 4


def test_readonly_open_of_a_copy_without_shm(tmp_path):
    """唯讀工具（mode=ro）讀備份複本：沒有 -shm、-wal 也要能讀。"""
    fresh()
    add(7)
    copy = tmp_path / "ro.db"
    snapshot_database(copy)
    assert count_in(copy) == 7


def test_tool_copy_database_has_latest_writes(tmp_path):
    """tools/dev/db_copy.copy_database（不依賴 app、吃路徑）：服務還連著、寫入還在 -wal 時複製也完整，並清掉目標旁舊的 -wal。"""
    import importlib.util

    module = Path(__file__).resolve().parents[2] / "tools" / "dev" / "db_copy.py"
    if not module.exists():
        import pytest
        pytest.skip("tools/dev 不在這個版本")
    spec = importlib.util.spec_from_file_location("db_copy", module)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    fresh()
    add(800)
    target = tmp_path / "tool.db"
    Path(f"{target}-wal").write_bytes(b"stale")  # 舊的殘留不能套到新檔上
    tool.copy_database(DB_FILE, target)
    assert count_in(target) == 800
    assert not Path(f"{target}-wal").exists() or Path(f"{target}-wal").read_bytes() != b"stale"
