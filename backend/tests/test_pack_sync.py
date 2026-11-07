"""翻譯包每次啟動補進既有典藏（defaults.sync_packs）。資料皆為虛構。"""
import json

from sqlalchemy import select

from app import defaults
from app.database import PackEntry, Resource, SessionLocal, TagAlias, TagBlock


def add(client, code, tags):
    response = client.post("/api/resources", json={"code": code, "source_name": "站A", "tags": tags})
    assert response.status_code == 201, response.text
    return response.json()


def pack(tmp_path, translate, block=()):
    path = tmp_path / "pack.json"
    path.write_text(json.dumps({"translate": translate, "block": list(block)}, ensure_ascii=False), encoding="utf-8")
    return [path]


def sync(paths):
    with SessionLocal() as db:
        return defaults.sync_packs(db, paths)


def rules():
    with SessionLocal() as db:
        return dict(db.execute(select(TagAlias.alias, TagAlias.canonical)).all()), set(db.scalars(select(TagBlock.name)).all())


def tags_of(client, rid):
    return client.get(f"/api/resources/{rid}").json()["tags"]


def updated_at(rid):
    with SessionLocal() as db:
        return db.get(Resource, rid).updated_at


def test_new_entries_are_added_and_existing_tags_merged(client, tmp_path):
    work = add(client, "1", ["Fluffy Cat", "自訂"])
    before = updated_at(work["id"])
    counts = sync(pack(tmp_path, {"fluffy cat": "毛茸貓", "Tiny Dog": "小狗"}))
    assert counts["aliases"] == 2 and counts["merged"] == 1
    assert rules()[0] == {"fluffy cat": "毛茸貓", "tiny dog": "小狗"}
    assert tags_of(client, work["id"]) == ["毛茸貓", "自訂"]
    assert updated_at(work["id"]) == before  # 併入標籤不改作品的修改時間
    assert add(client, "2", ["TINY DOG"])["tags"] == ["小狗"]


def test_second_run_does_nothing(client, tmp_path):
    paths = pack(tmp_path, {"fluffy cat": "毛茸貓"}, ["ad text"])
    sync(paths)
    assert not any(sync(paths).values())


def test_existing_aliases_and_blocks_are_user_settings(client, tmp_path):
    with SessionLocal() as db:
        db.merge(TagAlias(alias="fluffy cat", canonical="我的貓"))
        db.add(TagBlock(name="tiny dog"))
        db.commit()
    counts = sync(pack(tmp_path, {"fluffy cat": "毛茸貓", "tiny dog": "小狗"}))
    assert counts["aliases"] == 0
    aliases, blocked = rules()
    assert aliases == {"fluffy cat": "我的貓"} and "tiny dog" in blocked
    with SessionLocal() as db:
        assert {entry.alias for entry in db.scalars(select(PackEntry))} == {"fluffy cat", "tiny dog"}  # 仍記錄為已處理


def test_user_deleted_or_changed_alias_is_left_alone(client, tmp_path):
    sync(pack(tmp_path, {"fluffy cat": "毛茸貓", "tiny dog": "小狗"}))
    with SessionLocal() as db:
        db.delete(db.get(TagAlias, "fluffy cat"))
        db.get(TagAlias, "tiny dog").canonical = "我的狗"
        db.commit()
    counts = sync(pack(tmp_path, {"fluffy cat": "毛茸貓", "tiny dog": "幼犬"}))
    assert not any(counts.values())
    assert rules()[0] == {"tiny dog": "我的狗"}
    assert not any(sync(pack(tmp_path, {"fluffy cat": "毛茸貓", "tiny dog": "幼犬"})).values())


def test_renamed_translation_is_followed_and_old_tag_merged(client, tmp_path):
    sync(pack(tmp_path, {"tiny dog": "小狗"}))
    work = add(client, "1", ["tiny dog", "自訂"])
    assert work["tags"] == ["小狗", "自訂"]
    before = updated_at(work["id"])
    counts = sync(pack(tmp_path, {"tiny dog": "幼犬"}))
    assert counts["updated"] == 1 and counts["merged"] == 1
    assert rules()[0]["tiny dog"] == "幼犬"
    assert tags_of(client, work["id"]) == ["幼犬", "自訂"]
    assert updated_at(work["id"]) == before
    assert not any(sync(pack(tmp_path, {"tiny dog": "幼犬"})).values())


def test_new_block_entry_removes_tag_from_works(client, tmp_path):
    work = add(client, "1", ["Ad Text", "自訂"])
    counts = sync(pack(tmp_path, {}, ["ad text"]))
    assert counts["blocked"] == 1 and counts["removed"] == 1
    assert "ad text" in rules()[1]
    assert tags_of(client, work["id"]) == ["自訂"]
    assert add(client, "2", ["AD TEXT"])["tags"] == []


def test_translation_resolves_through_alias_chain(client, tmp_path):
    with SessionLocal() as db:
        db.add(TagAlias(alias="小狗", canonical="幼犬"))
        db.commit()
    sync(pack(tmp_path, {"tiny dog": "小狗"}))
    assert rules()[0]["tiny dog"] == "幼犬"
    with SessionLocal() as db:
        assert db.get(PackEntry, "tiny dog").applied == "幼犬"


def test_seeded_library_is_recorded_and_not_synced_again(client):
    with SessionLocal() as db:
        defaults.seed(db)
        assert db.scalar(select(PackEntry).limit(1)) is not None
        assert not any(defaults.sync_packs(db).values())


def test_startup_sync_failure_is_only_logged(client, monkeypatch):
    monkeypatch.setattr(defaults, "enabled", lambda: True)
    monkeypatch.setattr(defaults, "load_packs", lambda paths=None: (_ for _ in ()).throw(ValueError("壞掉的翻譯包")))
    with SessionLocal() as db:
        assert defaults.sync_packs_safely(db) is None


def test_entry_removed_from_pack_keeps_its_synonym(client, tmp_path):
    """條目從翻譯包移走後，已經寫進資料庫的同義詞要留著。"""
    from app import defaults
    from app.database import SessionLocal, TagAlias

    pack = tmp_path / "pack.json"
    pack.write_text('{"translate": {"old name": "譯名"}}', encoding="utf-8")
    with SessionLocal() as db:
        defaults.sync_packs(db, [pack])
        pack.write_text('{"translate": {}}', encoding="utf-8")
        defaults.sync_packs(db, [pack])
        assert db.get(TagAlias, "old name").canonical == "譯名"
