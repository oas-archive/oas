"""手機公開版的第一次啟動設定、封面閘門、App 內備份與翻譯包匯入（電腦公開版見 test_desktop_public.py）。"""
import json

import pytest

from app import covers, defaults, preferences, settings
from app.database import CoverCache, SessionLocal, Setting


@pytest.fixture
def public(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "EDITION", "public")
    monkeypatch.setattr(settings, "PLATFORM", "android")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path / "covers")
    with SessionLocal() as db:
        row = db.get(Setting, preferences.KEY)
        if row:
            db.delete(row)
            db.commit()
    yield tmp_path


def setup_choices(client, **changes):
    choices = {"adult_confirmed": True, "save_covers": True, "background_reader": False, **changes}
    return client.patch("/api/preferences", json=choices).json()


def test_other_editions_keep_everything_on_and_cannot_change(client):
    result = client.get("/api/preferences").json()
    assert result["setup_done"] is True and result["save_covers"] is True and result["background_reader"] is True
    assert client.patch("/api/preferences", json={"save_covers": False}).status_code == 403
    assert client.post("/api/covers/clear").status_code == 403
    assert client.get("/api/backup/files").status_code == 403
    assert client.post("/api/tag-pack/mine", json={"translate": {"a": "b"}}).status_code == 403


def test_public_needs_all_three_choices(client, public):
    first = client.get("/api/preferences").json()
    assert first["edition"] == "public" and first["setup_done"] is False
    assert first["save_covers"] is None and first["background_reader"] is None
    assert client.patch("/api/preferences", json={"save_covers": False}).json()["setup_done"] is False
    assert client.patch("/api/preferences", json={"background_reader": True}).json()["setup_done"] is False
    assert client.patch("/api/preferences", json={"adult_confirmed": True}).json()["setup_done"] is True


def test_refusing_the_age_limit_is_not_setup(client, public):
    assert setup_choices(client, adult_confirmed=False)["setup_done"] is False


def test_covers_not_downloaded_until_chosen(client, public, monkeypatch):
    assert preferences.saving_covers() is False
    with pytest.raises(covers.CoverError):
        covers.download("https://example.com/a.png")
    setup_choices(client, save_covers=False)
    assert preferences.saving_covers() is False
    submitted = []
    monkeypatch.setattr(covers._single_pool, "submit", lambda *args: submitted.append(args))
    covers.schedule("https://example.com/a.png")
    assert submitted == []
    assert client.post("/api/covers/cache").status_code == 409


def test_clear_covers_only_when_saving_is_off(client, public):
    setup_choices(client, save_covers=True)
    assert client.post("/api/covers/clear").status_code == 409
    folder = public / "covers"
    folder.mkdir()
    (folder / "abc.png").write_bytes(b"x")
    (folder / "abc_w240.jpg").write_bytes(b"x")
    with SessionLocal() as db:
        db.merge(CoverCache(url_hash="abc", url="https://example.com/abc.png", status="ok", filename="abc.png"))
        db.commit()
    client.patch("/api/preferences", json={"save_covers": False})
    assert client.post("/api/covers/clear").json()["removed"] == 2
    assert list(folder.iterdir()) == []
    with SessionLocal() as db:
        assert db.get(CoverCache, "abc") is None


def test_private_backups_are_listed_and_read(client, public):
    folder = public / "backups"
    folder.mkdir()
    (folder / "oas-auto-20261106-100000.json").write_text('{"format": "x"}', encoding="utf-8")
    (folder / "oas-backup-20261106-110000.json").write_text('{"format": "y"}', encoding="utf-8")
    (folder / "other.txt").write_text("no", encoding="utf-8")
    names = [row["name"] for row in client.get("/api/backup/files").json()]
    assert set(names) == {"oas-auto-20261106-100000.json", "oas-backup-20261106-110000.json"}
    assert client.get("/api/backup/files/oas-auto-20261106-100000.json").json() == {"format": "x"}


def test_personal_phone_lists_private_backups(client, monkeypatch, tmp_path):
    # 手機個人版也把備份放在私有空間（android_main.py 設 OAS_PRIVATE_BACKUPS=1）；電腦版沒設，仍是 403
    folder = tmp_path / "backups"
    folder.mkdir()
    (folder / "oas-auto-20261107-100000.json").write_text('{"format": "z"}', encoding="utf-8")
    monkeypatch.setattr(settings, "BACKUP_DIR", str(folder))
    assert client.get("/api/backup/files").status_code == 403
    monkeypatch.setattr(settings, "PRIVATE_BACKUPS", True)
    assert [row["name"] for row in client.get("/api/backup/files").json()] == ["oas-auto-20261107-100000.json"]
    assert client.get("/api/backup/files/oas-auto-20261107-100000.json").json() == {"format": "z"}
    assert client.get("/api/preferences").json()["edition"] == "personal"


@pytest.mark.parametrize("name", ["other.txt", "..%2Foas.db", "oas-..%2F..%2Fx.json", "oas-a%5Cb.json", "oas-missing.json"])
def test_private_backups_reject_other_files(client, public, name):
    (public / "backups").mkdir()
    (public / "backups" / "other.txt").write_text("no", encoding="utf-8")
    assert client.get(f"/api/backup/files/{name}").status_code == 404


def test_import_my_pack_writes_the_user_file(client, public, monkeypatch):
    calls = []
    monkeypatch.setattr(defaults, "sync_packs_safely", lambda db: calls.append(1))
    result = client.post("/api/tag-pack/mine", json={"translate": {"Alpha": "甲"}, "suggest": {"beta": "乙"}, "block": ["gamma"]}).json()
    assert result == {"translate": 1, "suggest": 1, "block": 1}
    saved = json.loads((public / defaults.USER_PACK_NAME).read_text(encoding="utf-8"))
    assert saved["translate"] == {"Alpha": "甲"} and saved["suggest"] == {"beta": "乙"} and saved["block"] == ["gamma"]
    assert calls == [1]


def test_import_my_pack_rejects_wrong_shape(client, public):
    assert client.post("/api/tag-pack/mine", json={"translate": {"Alpha": ["甲"]}}).status_code == 422
    assert not (public / defaults.USER_PACK_NAME).exists()
