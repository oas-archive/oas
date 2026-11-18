"""手機↔電腦同步的三方比對（基準＝上次同步完的電腦資料）。資料皆為虛構；tools/dev 不在公開版，沒有就略過。"""
import json
import sqlite3
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.database import Collection, Resource, Tag, TagAlias, TagBlock
from .test_full_sync import NOW, TOOLS, guard_port, make_db, needs_tools, phone_backup, run_tool, stamp  # noqa: F401

pytestmark = needs_tools

PHONE_DEFAULT = dict(title="", note="", image_url="", rating=0, status="未讀", kind="", progress="", pages=None, format="",
                     last_opened_at=None, finished_at=None, image_kind="", tags=[])


def pe(code, source="站A", **over):
    """手機備份裡的一件作品；預設和 setup 建的電腦作品一樣（＝沒改）。"""
    return {"source_name": source, "code": code, **PHONE_DEFAULT, **over}


def base_path(tmp_path):
    return tmp_path / "backups" / "手機" / "sync-base" / "base.db"


def add_row(db, code, tags=(), source="站A", **fields):
    resource = Resource(code=code, source_name=source, **fields)
    for name in tags:
        resource.tags.append(db.query(Tag).filter_by(name=name).first() or Tag(name=name))
    db.add(resource)
    db.flush()
    return resource


def setup(tmp_path, rows, with_base=True):
    """電腦資料庫放 rows（代碼 → 欄位），並（預設）把它存成基準。"""
    data_dir = tmp_path / "pc"
    engine = make_db(data_dir / "oas.db")
    with Session(engine) as db:
        for code, fields in rows.items():
            add_row(db, code, **fields)
        db.commit()
    if with_base:
        snapshot_base(tmp_path, data_dir)
    return data_dir, engine


def snapshot_base(tmp_path, data_dir):
    base_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    source, target = sqlite3.connect(data_dir / "oas.db"), sqlite3.connect(base_path(tmp_path))
    source.backup(target)
    source.close()
    target.close()


def state(engine):
    with Session(engine) as db:
        return {item.code: {"title": item.title, "rating": item.rating, "status": item.status, "tags": sorted(tag.name for tag in item.tags)}
                for item in db.query(Resource).all()}


def sync(tmp_path, data_dir, resources, *extra, apply=True, expect=0, **fields):
    path = phone_backup(tmp_path, resources, **fields)
    return run_tool("sync_from_phone.py", path, *(["--apply"] if apply else []), *extra, data_dir=data_dir, expect=expect)


def test_phone_only_change_is_taken(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1, tags=["甲"]), "b": dict(title="乙")})
    preview = sync(tmp_path, data_dir, [pe("a", rating=4, tags=["甲", "新"]), pe("b", title="乙")], apply=False)
    assert "只有手機改 1" in preview and "預演結束" in preview and state(engine)["a"]["rating"] == 1
    sync(tmp_path, data_dir, [pe("a", rating=4, tags=["甲", "新"]), pe("b", title="乙")])
    assert state(engine)["a"] == {"title": "", "rating": 4.0, "status": "未讀", "tags": ["新", "甲"]}


def test_pc_only_change_is_kept(tmp_path):
    """舊行為會把電腦改的評分蓋回手機的舊值。"""
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1), "b": dict(rating=2)})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="a").one().rating = 5
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a", rating=1), pe("b", rating=2)])
    assert "只有電腦改 1" in output
    assert state(engine)["a"]["rating"] == 5


def test_both_changed_different_fields_merge(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1, title="舊")})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="a").one().title = "電腦新標題"
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a", rating=3, title="舊")])
    assert "兩邊都改（不同欄位） 1" in output
    assert state(engine)["a"]["title"] == "電腦新標題" and state(engine)["a"]["rating"] == 3


def conflict_scene(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1, title="秘密標題"), "b": dict(rating=2)})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="a").one().rating = 5
        db.commit()
    return data_dir, engine, [pe("a", rating=3, title="秘密標題"), pe("b", rating=2)]


def test_conflict_stops_without_writing(tmp_path):
    data_dir, engine, rows = conflict_scene(tmp_path)
    before = (data_dir / "oas.db").read_bytes()
    output = sync(tmp_path, data_dir, rows, expect=2)
    assert "站A / a｜rating｜基準 1.0｜電腦 5.0｜手機 3.0" in output and "沒有寫入" in output
    assert "秘密標題" not in output
    assert (data_dir / "oas.db").read_bytes() == before and not list(tmp_path.rglob("pc-before-sync-*.db"))
    preview = sync(tmp_path, data_dir, rows, apply=False, expect=2)  # 預演也停下並列出
    assert "衝突 1 個" in preview


@pytest.mark.parametrize("side,expected", [("phone", 3.0), ("pc", 5.0)])
def test_prefer_decides_conflicts(tmp_path, side, expected):
    data_dir, engine, rows = conflict_scene(tmp_path)
    output = sync(tmp_path, data_dir, rows, "--prefer", side)
    assert "衝突 1 個已照 --prefer 決定" in output
    assert state(engine)["a"]["rating"] == expected
    assert state(engine)["b"]["rating"] == 2.0


def test_choices_file_decides_per_item(tmp_path):
    data_dir, engine, rows = conflict_scene(tmp_path)
    template = tmp_path / "choices.json"
    sync(tmp_path, data_dir, rows, "--write-choices", template, expect=2)
    entries = json.loads(template.read_text(encoding="utf-8"))
    assert entries == [{"source_name": "站A", "code": "a", "field": "rating", "use": ""}]
    entries[0]["use"] = "phone"
    template.write_text(json.dumps(entries), encoding="utf-8")
    sync(tmp_path, data_dir, rows, "--choices", template)
    assert state(engine)["a"]["rating"] == 3.0


def test_pc_delete_is_not_resurrected(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": {}, "b": dict(title="要刪")})
    with Session(engine) as db:
        db.delete(db.query(Resource).filter_by(code="b").one())
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a"), pe("b", title="要刪")])
    assert "電腦刪除 1" in output and set(state(engine)) == {"a"}


def test_phone_delete_deletes_on_pc(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": {}, "b": {}})
    output = sync(tmp_path, data_dir, [pe("a")])
    assert "手機刪除 1" in output and "手機刪除、電腦也會刪 1 件" in output and set(state(engine)) == {"a"}
    assert list(tmp_path.glob("backups/手機/寫入前/pc-before-sync-*.db"))  # 刪掉的可以從同步前的副本復原


def test_delete_versus_edit_is_a_conflict(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": {}, "b": {}})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="b").one().rating = 4
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a")], expect=2)
    assert "手機刪除、電腦有改" in output and set(state(engine)) == {"a", "b"}
    sync(tmp_path, data_dir, [pe("a")], "--prefer", "pc")
    assert state(engine)["b"]["rating"] == 4.0


def test_new_on_both_sides_are_added(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": {}})
    with Session(engine) as db:
        add_row(db, "pc-new", rating=2)
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a"), pe("phone-new", rating=3)])
    assert "只有電腦新增 1" in output and "只有手機新增 1" in output
    assert {code: item["rating"] for code, item in state(engine).items()} == {"a": 0.0, "pc-new": 2.0, "phone-new": 3.0}


def test_tags_merge_per_tag(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(tags=["甲", "乙"])})
    with Session(engine) as db:
        item = db.query(Resource).filter_by(code="a").one()
        item.tags.append(Tag(name="丙"))
        db.commit()
    sync(tmp_path, data_dir, [pe("a", tags=["乙"])])  # 手機拿掉「甲」，電腦新增「丙」
    assert state(engine)["a"]["tags"] == ["丙", "乙"]


def test_link_check_time_takes_newer_side(tmp_path):
    data_dir, engine = setup(tmp_path, {"phone-newer": dict(link_checked_at=NOW - timedelta(days=20)),
                                        "pc-newer": dict(link_checked_at=NOW - timedelta(days=1), link_dead=True)})
    sync(tmp_path, data_dir, [pe("phone-newer", link_checked_at=stamp(NOW - timedelta(days=2)), link_dead=True),
                              pe("pc-newer", link_checked_at=stamp(NOW - timedelta(days=3)), link_dead=False)])
    with Session(engine) as db:
        rows = {item.code: item for item in db.query(Resource).all()}
    assert rows["phone-newer"].link_dead is True and rows["phone-newer"].link_checked_at.replace(tzinfo=None) == (NOW - timedelta(days=2)).replace(tzinfo=None)
    assert rows["pc-newer"].link_dead is True  # 電腦的較新：連失效標記一起保留，不算衝突


def test_no_base_keeps_old_behaviour_then_builds_base(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=5), "pc-only": {}}, with_base=False)
    rows = [pe("a", rating=2)]
    preview = sync(tmp_path, data_dir, rows, apply=False)
    assert "沒有基準" in preview and not base_path(tmp_path).exists()
    output = sync(tmp_path, data_dir, rows)
    assert "這次以手機為準" in output and "已更新基準" in output
    assert state(engine) == {"a": {"title": "", "rating": 2.0, "status": "未讀", "tags": []}}  # 以手機為準：電腦的評分、電腦獨有的作品都被蓋掉
    assert base_path(tmp_path).exists() and (base_path(tmp_path).parent / "phone-last.json").exists()
    again = sync(tmp_path, data_dir, rows, apply=False)  # 剛同步完再預演：沒有差異
    assert "三邊沒有差異" in again and "更新作品 0；刪除作品 0；新增作品 0" in again


def test_base_is_rotated_and_matches_pc_after_apply(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1)})
    sync(tmp_path, data_dir, [pe("a", rating=4)])
    folder = base_path(tmp_path).parent
    assert len(list(folder.glob("base-*.db"))) == 1  # 上一份改名加時間
    connection = sqlite3.connect(base_path(tmp_path))
    assert connection.execute("select rating from resources").fetchone()[0] == 4.0
    connection.close()


def test_stale_phone_does_not_revert_pc_changes(tmp_path):
    """電腦改的值還沒推到手機，手機仍是舊樣子：下次同步不能把它當成手機的修改而改回去。"""
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1), "b": {}})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="a").one().rating = 5
        add_row(db, "pc-new", rating=2)
        db.commit()
    rows = [pe("a", rating=1), pe("b")]
    sync(tmp_path, data_dir, rows)
    assert state(engine)["a"]["rating"] == 5.0
    again = sync(tmp_path, data_dir, rows)  # 手機還是舊樣子
    assert "刪除作品 0" in again
    assert state(engine)["a"]["rating"] == 5.0 and "pc-new" in state(engine)


def test_aliases_blocklist_and_lists(tmp_path):
    data_dir = tmp_path / "pc"
    engine = make_db(data_dir / "oas.db")
    with Session(engine) as db:
        one, two = add_row(db, "a"), add_row(db, "b")
        db.add_all([TagAlias(alias="x", canonical="甲"), TagAlias(alias="y", canonical="乙"), TagAlias(alias="z", canonical="丙"),
                    TagBlock(name="壞"), TagBlock(name="更壞")])
        collection = Collection(name="清單", position=0, query="")
        collection.resources.extend([one, two])
        db.add(collection)
        db.commit()
    snapshot_base(tmp_path, data_dir)
    with Session(engine) as db:  # 電腦：新增別名、封鎖、改 z、清單加新作品
        db.add_all([TagAlias(alias="pc-alias", canonical="電"), TagBlock(name="電腦封鎖")])
        db.get(TagAlias, "z").canonical = "丙電"
        new = add_row(db, "c")
        listing = db.query(Collection).one()
        listing.resources.append(new)
        db.commit()
    phone = dict(tag_aliases={"x": "甲手", "y": "乙", "z": "丙"}, tag_blocklist=["壞", "更壞"],  # 手機：改 x、刪 y 以外沒動（y 還在）、z 沒動
                 collections=[{"name": "清單", "position": 0, "query": "", "items": [{"source_name": "站A", "code": "a"}]}])  # 手機從清單拿掉 b
    del phone["tag_aliases"]["y"]  # 手機刪掉 y
    phone["tag_blocklist"] = ["壞"]  # 手機移除「更壞」
    output = sync(tmp_path, data_dir, [pe("a"), pe("b")], **phone)
    assert "同義詞：手機改 2、電腦改 2" in output
    connection = sqlite3.connect(data_dir / "oas.db")
    assert dict(connection.execute("select alias, canonical from tag_aliases")) == {"x": "甲手", "z": "丙電", "pc-alias": "電"}
    assert sorted(row[0] for row in connection.execute("select name from tag_blocklist")) == ["壞", "電腦封鎖"]
    members = sorted(row[0] for row in connection.execute("select r.code from collection_items i join resources r on r.id = i.resource_id"))
    assert members == ["a", "c"]  # 手機拿掉 b、電腦加了 c
    connection.close()
    clash = tmp_path / "clash"
    clash.mkdir()
    data2, _engine2 = setup(clash, {"a": {}})
    with Session(_engine2) as db:
        db.add(TagAlias(alias="q", canonical="一"))
        db.commit()
    snapshot_base(clash, data2)
    with Session(_engine2) as db:
        db.get(TagAlias, "q").canonical = "二"
        db.commit()
    stop = sync(clash, data2, [pe("a")], tag_aliases={"q": "三"}, expect=2)
    assert "同義詞 q｜指向｜基準 一｜電腦 二｜手機 三" in stop


def test_compare_tool_three_way_report(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1, title="不該印出"), "b": {}, "c": {}})
    with Session(engine) as db:
        db.query(Resource).filter_by(code="a").one().rating = 5
        db.query(Resource).filter_by(code="b").one().note = "電腦改"
        db.commit()
    phone_dir = tmp_path / "phone"
    phone_engine = make_db(phone_dir / "oas.db")
    with Session(phone_engine) as db:
        add_row(db, "a", rating=3, title="不該印出")
        add_row(db, "b")
        add_row(db, "new")
        db.commit()
    result = subprocess.run([sys.executable, "-I", str(TOOLS / "compare_phone.py"), str(phone_dir / "oas.db"), str(data_dir / "oas.db"),
                             "--base", str(base_path(tmp_path))], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "三方比對" in out and "只有電腦改 1" in out and "只有手機新增 1" in out and "手機刪除 1" in out
    assert "衝突 1" in out and "站A / a｜rating" in out and "不該印出" not in out
    assert "兩邊都改的衝突" in out


# ---------- 2026-11-15 健檢：標籤分類、書單順序、基準日誌、種類、壞頁數 ----------

def categories(data_dir):
    connection = sqlite3.connect(data_dir / "oas.db")
    try:
        return dict(connection.execute("select name, category from tags"))
    finally:
        connection.close()


def category_scene(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(tags=["甲", "乙"])})
    return data_dir, engine


def set_category(engine, name, category):
    with Session(engine) as db:
        db.query(Tag).filter_by(name=name).one().category = category
        db.commit()


def test_tag_category_pc_only_change_is_kept(tmp_path):
    """舊行為：手機的分類直接蓋掉電腦剛改的分類。"""
    data_dir, engine = category_scene(tmp_path)
    set_category(engine, "甲", "artist")
    output = sync(tmp_path, data_dir, [pe("a", tags=["甲", "乙"])], tag_categories={"甲": "tag", "乙": "tag"})
    assert "標籤分類：手機改 0、電腦改 1" in output
    assert categories(data_dir) == {"甲": "artist", "乙": "tag"}


def test_tag_category_phone_only_change_is_taken(tmp_path):
    data_dir, engine = category_scene(tmp_path)
    output = sync(tmp_path, data_dir, [pe("a", tags=["甲", "乙"])], tag_categories={"甲": "tag", "乙": "group"})
    assert "標籤分類：手機改 1、電腦改 0" in output
    assert categories(data_dir) == {"甲": "tag", "乙": "group"}


def test_tag_category_conflict_and_prefer(tmp_path):
    data_dir, engine = category_scene(tmp_path)
    set_category(engine, "甲", "artist")
    rows, phone = [pe("a", tags=["甲", "乙"])], dict(tag_categories={"甲": "group", "乙": "tag"})
    stop = sync(tmp_path, data_dir, rows, expect=2, **phone)
    assert "標籤分類 甲｜分類｜基準 tag｜電腦 artist｜手機 group" in stop
    assert categories(data_dir)["甲"] == "artist"  # 停下時沒寫入
    sync(tmp_path, data_dir, rows, "--prefer", "phone", **phone)
    assert categories(data_dir)["甲"] == "group"


def test_tag_category_choices_file(tmp_path):
    data_dir, engine = category_scene(tmp_path)
    set_category(engine, "甲", "artist")
    rows, phone = [pe("a", tags=["甲", "乙"])], dict(tag_categories={"甲": "group", "乙": "tag"})
    template = tmp_path / "choices.json"
    sync(tmp_path, data_dir, rows, "--write-choices", template, expect=2, **phone)
    entries = json.loads(template.read_text(encoding="utf-8"))
    assert entries == [{"kind": "category", "name": "甲", "field": "分類", "use": ""}]
    entries[0]["use"] = "pc"
    template.write_text(json.dumps(entries), encoding="utf-8")
    sync(tmp_path, data_dir, rows, "--choices", template, **phone)
    assert categories(data_dir)["甲"] == "artist"


def test_tag_category_without_base_follows_phone(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(tags=["甲"])}, with_base=False)
    set_category(engine, "甲", "artist")
    sync(tmp_path, data_dir, [pe("a", tags=["甲"])], tag_categories={"甲": "group"})
    assert categories(data_dir) == {"甲": "group"}


def position_scene(tmp_path):
    data_dir = tmp_path / "pc"
    engine = make_db(data_dir / "oas.db")
    with Session(engine) as db:
        one = add_row(db, "a")
        for name, position in (("清單一", 0), ("清單二", 1)):
            collection = Collection(name=name, position=position, query="")
            collection.resources.append(one)
            db.add(collection)
        db.commit()
    snapshot_base(tmp_path, data_dir)
    return data_dir, engine


def phone_lists(first, second):
    return [{"name": "清單一", "position": first, "query": "", "items": [{"source_name": "站A", "code": "a"}]},
            {"name": "清單二", "position": second, "query": "", "items": [{"source_name": "站A", "code": "a"}]}]


def positions(data_dir):
    connection = sqlite3.connect(data_dir / "oas.db")
    try:
        return dict(connection.execute("select name, position from collections"))
    finally:
        connection.close()


def test_collection_position_pc_only_and_phone_only(tmp_path):
    data_dir, engine = position_scene(tmp_path)
    with Session(engine) as db:
        db.query(Collection).filter_by(name="清單一").one().position = 5  # 電腦調整順序
        db.commit()
    output = sync(tmp_path, data_dir, [pe("a")], collections=phone_lists(0, 1))  # 手機沒動
    assert "書單順序：手機改 0、電腦改 1" in output and positions(data_dir) == {"清單一": 5, "清單二": 1}
    output = sync(tmp_path, data_dir, [pe("a")], collections=phone_lists(5, 9))  # 之後手機只改清單二
    assert "書單順序：手機改 1、電腦改 0" in output and positions(data_dir) == {"清單一": 5, "清單二": 9}


def test_collection_position_conflict(tmp_path):
    data_dir, engine = position_scene(tmp_path)
    with Session(engine) as db:
        db.query(Collection).filter_by(name="清單二").one().position = 7
        db.commit()
    rows, lists = [pe("a")], phone_lists(0, 3)
    stop = sync(tmp_path, data_dir, rows, expect=2, collections=lists)
    assert "書單順序 清單二｜position｜基準 1｜電腦 7｜手機 3" in stop and positions(data_dir)["清單二"] == 7
    sync(tmp_path, data_dir, rows, "--prefer", "phone", collections=lists)
    assert positions(data_dir)["清單二"] == 3


def test_base_replacement_clears_stale_wal_and_shm(tmp_path):
    data_dir, engine = setup(tmp_path, {"a": dict(rating=1)})
    base = base_path(tmp_path)
    for suffix in ("-wal", "-shm"):  # 舊基準留下的日誌：換新基準前一定要清掉
        Path(f"{base}{suffix}").write_bytes(b"stale")
    sync(tmp_path, data_dir, [pe("a", rating=4)])
    assert not Path(f"{base}-wal").exists() and not Path(f"{base}-shm").exists()
    connection = sqlite3.connect(base)
    try:
        assert connection.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert connection.execute("select rating from resources").fetchone()[0] == 4.0
    finally:
        connection.close()
    assert not Path(f"{base}-wal").exists()  # 開啟基準後也沒有跟著長出日誌（不是 WAL 模式）


def test_format_only_for_comics(tmp_path):
    data_dir, engine = setup(tmp_path, {"anime": dict(kind="動畫"), "comic": dict(kind="漫畫")})
    sync(tmp_path, data_dir, [pe("anime", kind="動畫", format="單本"), pe("comic", kind="漫畫", format="合集")])
    connection = sqlite3.connect(data_dir / "oas.db")
    try:
        assert dict(connection.execute("select code, format from resources")) == {"anime": "", "comic": "合集"}
    finally:
        connection.close()


def test_bad_pages_do_not_abort_sync(tmp_path):
    from .test_full_sync import TOOLS as tools_dir
    sys.path.insert(0, str(tools_dir))
    try:
        import sync_threeway
    finally:
        sys.path.remove(str(tools_dir))
    assert sync_threeway.norm("pages", "abc") is None and sync_threeway.norm("pages", "12") == 12
    assert sync_threeway.norm("pages", 3.9) == 3 and sync_threeway.norm("pages", "") is None and sync_threeway.norm("pages", [1]) is None
    data_dir, engine = setup(tmp_path, {"a": dict(pages=100)})
    output = sync(tmp_path, data_dir, [pe("a", pages="一百頁")])
    assert "已寫入" in output
