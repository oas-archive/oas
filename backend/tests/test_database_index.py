"""resource_tags 的（標籤, 作品）索引：新資料庫建表時就有，舊資料庫啟動時補上，重複啟動不出錯。"""
from sqlalchemy import text

from app.database import Base, engine, initialise_database

INDEX = "ix_resource_tags_tag_resource"


def indexes() -> set[str]:
    with engine.connect() as connection:
        return set(connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'resource_tags'")).scalars())


def plan() -> str:
    # 連線池裡的連線快取了 EXPLAIN 的敘述，建索引後不會重新規劃（一般查詢會）；換新連線看
    engine.dispose()
    with engine.connect() as connection:
        # 啟動時的 PRAGMA optimize 會替資料表建統計；測試的表只有 1 筆，較新的 SQLite（GitHub 的 Linux）看了統計會選全表掃描，
        # 這對 1 筆資料是對的，但這裡要看的是「索引能不能用」：先清掉統計再規劃（2026-11-18 CI 失敗的原因）
        if connection.execute(text("SELECT 1 FROM sqlite_master WHERE name = 'sqlite_stat1'")).first():
            connection.execute(text("DELETE FROM sqlite_stat1"))
            connection.execute(text("ANALYZE sqlite_schema"))  # 讓這個連線重新讀統計（已清空）
            connection.commit()
        return " ".join(row[-1] for row in connection.execute(text("EXPLAIN QUERY PLAN SELECT resource_id FROM resource_tags WHERE tag_id = 1")))


def test_new_database_has_the_tag_index():
    Base.metadata.drop_all(engine)
    initialise_database()
    assert INDEX in indexes() and INDEX in plan()


def test_old_database_gets_the_index_on_startup_and_restarts_are_harmless():
    Base.metadata.drop_all(engine)
    initialise_database()
    with engine.begin() as connection:  # 舊版建的資料庫：沒有這個索引，已經有資料
        connection.execute(text(f"DROP INDEX {INDEX}"))
        connection.execute(text("INSERT INTO resources (id, code, source_name, title, note, image_url, rating, status, created_at, updated_at, image_kind, link_dead, kind, progress, format) "
                                "VALUES (1, 'IDX-1', '虛構站', '', '', '', 0, '未讀', '2025-01-01', '2025-01-01', '', 0, '', '', '')"))
        connection.execute(text("INSERT INTO tags (id, name, category) VALUES (1, '虛構標籤', 'tag')"))
        connection.execute(text("INSERT INTO resource_tags (resource_id, tag_id) VALUES (1, 1)"))
    assert INDEX not in indexes() and "SCAN" in plan()
    initialise_database()
    initialise_database()  # 再啟動一次不會出錯
    with engine.begin() as connection:  # 模擬較新 SQLite 的 PRAGMA optimize：替這張只有 1 筆的表建統計（不清掉的話會選全表掃描）
        connection.execute(text("ANALYZE"))
    assert INDEX in indexes() and INDEX in plan()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT resource_id FROM resource_tags WHERE tag_id = 1")).scalars().all() == [1]
