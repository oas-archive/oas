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
        connection.execute(text("INSERT INTO resources (id, code, source_name, title, note, image_url, rating, status, created_at, updated_at, image_kind, link_dead, kind, progress) "
                                "VALUES (1, 'IDX-1', '虛構站', '', '', '', 0, '未讀', '2025-01-01', '2025-01-01', '', 0, '', '')"))
        connection.execute(text("INSERT INTO tags (id, name, category) VALUES (1, '虛構標籤', 'tag')"))
        connection.execute(text("INSERT INTO resource_tags (resource_id, tag_id) VALUES (1, 1)"))
    assert INDEX not in indexes() and "SCAN" in plan()
    initialise_database()
    initialise_database()  # 再啟動一次不會出錯
    assert INDEX in indexes() and INDEX in plan()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT resource_id FROM resource_tags WHERE tag_id = 1")).scalars().all() == [1]
