"""還原備份改成「規則讀一次、標籤一次查好」（archive.TagWriter）後，結果要和原本每件呼叫 assign_tags 完全相同：
同一份備份、同一個起始資料庫，分別用兩種寫法還原（合併與取代），逐表比對（含編號）。資料全部虛構。"""
import pytest
from sqlalchemy import text

from app import archive, undo
from app.database import Base, SessionLocal, engine, initialise_database

# 每次執行時間不同的欄位不比對
VOLATILE = {"collection_items": {"added_at"}, "collections": {"created_at"}, "undo_log": {"created_at"}}
# 關聯表沒有編號，比對內容（依主鍵排序）：一次 flush 與逐一 flush 寫入實體列的先後不同，查詢都不依賴這個順序
ORDER = {"resource_tags": "resource_id, tag_id", "collection_items": "collection_id, resource_id"}
TABLES = ["resources", "tags", "resource_tags", "sources", "collections", "collection_items", "tag_aliases", "tag_blocklist",
          "duplicate_dismissals", "settings", "undo_log", "jobs"]


class OldWriter:
    """改之前的寫法：每件作品各自套規則、每個標籤各查一次。"""

    def __init__(self, db, categories=None):
        self.db, self.categories = db, categories

    def assign(self, resource, values):
        archive.assign_tags(self.db, resource, values, categories=self.categories)


def item(code, tags, source="虛構站甲", **extra):
    return {"code": code, "source_name": source, "title": f"虛構作品 {code}", "tags": tags,
            "created_at": "2025-03-01T00:00:00+00:00", "updated_at": "2025-03-02T00:00:00+00:00", **extra}


LOCAL = {
    "sources": [{"name": "虛構站甲", "template": "https://example.com/a/{}", "kind": "漫畫"}],
    "resources": [item("L1", ["本機標籤", "共用標籤", "Mixed Case"]), item("L2", ["共用標籤"], link_checked_at="2025-05-01T00:00:00+00:00"),
                  item("B1", ["本機版本的標籤"])],
    "tag_aliases": {"舊寫法": "本機標籤"}, "tag_blocklist": ["本機封鎖"],
    "collections": [{"name": "虛構書單", "position": 1, "items": [{"source_name": "虛構站甲", "code": "L1"}]}],
}

BACKUP = {
    "sources": [{"name": "虛構站甲", "template": "https://example.com/a/{}", "kind": "漫畫"},
                {"name": "虛構站乙", "template": "https://example.org/b/{}", "kind": "影片"},
                {"name": "壞範本", "template": "javascript:alert(1)", "kind": ""}],
    "resources": [
        item("B1", ["不會寫入"], link_checked_at="2025-06-01T00:00:00+00:00", link_dead=True),  # 本機已有：只更新巡檢
        item("L2", ["不會寫入"], link_checked_at="2025-04-01T00:00:00+00:00"),  # 備份的巡檢較舊：不動
        item("B2", ["共用標籤", "新標籤甲", "新標籤甲", "  新標籤乙  ", "", "STAR HARBOR", "宣傳字", "鏈頭"]),
        item("B3", "新標籤甲，新標籤丙, 共用標籤", source="虛構站乙"),  # 舊格式：逗號分隔的字串
        item("B4", ["mixed case", "Mixed Case", "作者甲", "x" * 150]),
        item("B5", ["star harbor", "舊寫法", "本機封鎖", "新標籤丙"], source=""),  # 沒有來源：歸到其他網站
        item("B2", ["重複的作品不寫入"]),
        item("", ["沒有代碼不寫入"]),
        item("B6", [], rating=9, status="已讀"),
    ],
    "tag_categories": {"作者甲": "artist", "新標籤乙": "group", "本機標籤": "parody"},
    "tag_aliases": {"Star Harbor": "星港傳說", "鏈頭": "鏈中", "鏈中": "鏈尾", "舊寫法": "不會蓋掉本機的規則"},
    "tag_blocklist": ["宣傳字", " 本機封鎖 "],
    "collections": [{"name": "虛構書單", "position": 1, "items": [{"source_name": "虛構站甲", "code": "B2"}, {"source_name": "虛構站甲", "code": "L1"}]},
                    {"name": "新的虛構書單", "position": 2, "query": "", "items": [{"source_name": "其他網站", "code": "B5"}]}],
    "tag_health_keep": ["新標籤甲"], "tag_pairs_keep": ["共用標籤|新標籤甲"],
    "duplicate_dismissals": [[{"source_name": "虛構站甲", "code": "B2"}, {"source_name": "虛構站乙", "code": "B3"}],
                             [{"source_name": "虛構站甲", "code": "B2"}, {"source_name": "虛構站甲", "code": "不存在"}]],
}


def restore(data, mode):
    with SessionLocal() as db:
        report = archive.import_records(
            db, data["sources"], data["resources"], mode=mode, tag_categories=data.get("tag_categories"), tag_aliases=data.get("tag_aliases"),
            tag_blocklist=data.get("tag_blocklist"), collections=data.get("collections"), tag_health_keep=data.get("tag_health_keep"),
            tag_pairs_keep=data.get("tag_pairs_keep"), duplicate_dismissals=data.get("duplicate_dismissals"))
        db.commit()
    return report


def dump() -> dict:
    tables = {}
    with engine.connect() as connection:
        for table in TABLES:
            result = connection.execute(text(f"SELECT * FROM {table} ORDER BY {ORDER.get(table, 'rowid')}"))
            keep = [index for index, name in enumerate(result.keys()) if name not in VOLATILE.get(table, set())]
            tables[table] = [tuple(row[index] for index in keep) for row in result]
    return tables


def run(mode, writer, monkeypatch):
    Base.metadata.drop_all(engine)
    initialise_database()
    restore(LOCAL, "merge")
    with SessionLocal() as db:
        undo.record(db, "created", "虛構的復原紀錄", {"ids": [1]})
        db.commit()
    with monkeypatch.context() as patch:
        patch.setattr(archive, "TagWriter", writer)
        report = restore(BACKUP, mode)
    return report, dump()


@pytest.mark.parametrize("mode", ["merge", "replace"])
def test_restore_matches_the_per_item_writer(mode, monkeypatch):
    old_report, old_tables = run(mode, OldWriter, monkeypatch)
    new_report, new_tables = run(mode, archive.TagWriter, monkeypatch)
    assert new_report == old_report
    for table in TABLES:
        assert new_tables[table] == old_tables[table], table
    assert len(new_tables["resource_tags"]) > 10 and new_tables["undo_log"] == ([] if mode == "replace" else old_tables["undo_log"])


def test_restore_twice_adds_nothing_the_second_time(monkeypatch):
    run("merge", archive.TagWriter, monkeypatch)
    first = dump()
    report = restore(BACKUP, "merge")
    assert report["created"] == 0 and dump() == first
