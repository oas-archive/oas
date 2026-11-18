"""開發工具：寫入前備份放固定位置（../backups/手機/寫入前/），只留最近 10 份，不碰其他檔案。"""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "dev"
if not (TOOLS / "db_copy.py").exists():  # 公開版沒有 tools/dev
    pytest.skip("tools/dev 不在這個版本", allow_module_level=True)
sys.path.insert(0, str(TOOLS))
import db_copy  # noqa: E402


def make_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("create table t (x)")
        conn.execute("insert into t values (1)")
    return path


def test_default_dir_is_beside_data(tmp_path):
    db = make_db(tmp_path / "data" / "oas.db")
    copy = db_copy.before_write_copy(db, "pc-before-sync")
    assert copy.parent == (tmp_path / "backups" / "手機" / "寫入前").resolve()
    assert copy.is_absolute() and copy.name.startswith("pc-before-sync-")
    with sqlite3.connect(copy) as conn:
        assert conn.execute("select x from t").fetchone() == (1,)


def test_backup_dir_override(tmp_path):
    db = make_db(tmp_path / "data" / "oas.db")
    copy = db_copy.before_write_copy(db, "pc-before-covers", tmp_path / "elsewhere")
    assert copy.parent == (tmp_path / "elsewhere").resolve()


def test_keeps_latest_and_leaves_other_files(tmp_path):
    db = make_db(tmp_path / "data" / "oas.db")
    folder = tmp_path / "backups" / "手機" / "寫入前"
    folder.mkdir(parents=True)
    for index in range(12):
        old = folder / f"pc-before-sync-202601{index:02d}.db"
        old.write_bytes(b"x")
        os.utime(old, (1_000_000 + index, 1_000_000 + index))
    (folder / "pc-before-sync-20260100.db-wal").write_bytes(b"w")
    keep = folder / "phone-before.db"
    keep.write_bytes(b"keep")
    note = folder / "說明.txt"
    note.write_text("留著", encoding="utf-8")
    copy = db_copy.before_write_copy(db, "pc-before-sync")
    left = sorted(path.name for path in folder.glob("pc-before-*.db"))
    assert len(left) == db_copy.KEEP_BEFORE_WRITE and copy.name in left
    assert "pc-before-sync-20260100.db" not in left  # 最舊的刪掉
    assert not (folder / "pc-before-sync-20260100.db-wal").exists()  # 連同 -wal
    assert keep.read_bytes() == b"keep" and note.exists()
