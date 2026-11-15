"""電腦公開版的第一次啟動：標記檔分辨公開版、平台決定要問的題目、選「不存」時的舊封面。

四個版本共用同一套程式：電腦私人版、手機個人版、手機公開版的行為不能變（手機公開版另見 test_preferences.py）。
"""
import importlib.util
import shutil

import pytest

from app import covers, preferences, settings
from app.database import CoverCache, Resource, SessionLocal, Setting

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


# ---------- 標記檔 × OAS_EDITION × 平台 ----------

def load_settings_copy(tmp_path, monkeypatch, marker, edition, platform):
    """把 settings.py 複製到暫存資料夾再載入：標記檔只放在暫存資料夾，不會留在工作樹。"""
    folder = tmp_path / "app"
    folder.mkdir()
    shutil.copy(settings.__file__, folder / "settings.py")
    if marker:
        (folder / settings.PUBLIC_MARKER).write_text("OAS public edition\n", encoding="utf-8")
    for name, value in (("OAS_EDITION", edition), ("OAS_PLATFORM", platform)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("oas_settings_copy", folder / "settings.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("platform, expected_platform", [(None, "desktop"), ("desktop", "desktop"), ("android", "android")])
@pytest.mark.parametrize("marker, edition, expected_edition", [
    (False, None, "personal"),  # 擁有者的私人 repo：沒有標記檔，照舊是 personal
    (True, None, "public"),  # 公開 repo、免安裝版：有標記檔就是公開版
    (False, "", "personal"),  # 設成空字串等於沒設
    (True, "", "public"),
    (True, "personal", "personal"),  # 環境變數優先於標記檔
    (False, "public", "public"),  # 手機公開版：android_main.py 用環境變數指定
    (True, "public", "public"),
])
def test_edition_and_platform_detection(tmp_path, monkeypatch, marker, edition, expected_edition, platform, expected_platform):
    module = load_settings_copy(tmp_path, monkeypatch, marker, edition, platform)
    assert module.EDITION == expected_edition
    assert module.PLATFORM == expected_platform


def test_marker_must_be_a_file(tmp_path):
    (tmp_path / settings.PUBLIC_MARKER).mkdir()
    assert settings.detect_edition(None, tmp_path) == "personal"


@pytest.mark.parametrize("value", ["", "Android", "ios", "desktop"])
def test_unknown_platform_is_desktop(value):
    assert settings.detect_platform(value) == "desktop"


def test_tests_run_as_desktop_personal():
    """公開 repo 的 app 資料夾有標記檔；conftest.py 明確設定版本，測試在兩邊都以電腦私人版為起點。"""
    assert settings.EDITION == "personal" and settings.PLATFORM == "desktop"


# ---------- 各版本的設定 ----------

def clear_preferences():
    with SessionLocal() as db:
        row = db.get(Setting, preferences.KEY)
        if row:
            db.delete(row)
            db.commit()


@pytest.fixture
def edition(monkeypatch, tmp_path):
    """切換成指定的版本與平台；封面與備份放在暫存資料夾。"""
    monkeypatch.setattr(settings, "BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path / "covers")
    covers._remote_failed.clear()
    clear_preferences()

    def use(name, platform):
        monkeypatch.setattr(settings, "EDITION", name)
        monkeypatch.setattr(settings, "PLATFORM", platform)
        return tmp_path

    return use


def test_desktop_public_asks_two_questions(client, edition):
    edition("public", "desktop")
    first = client.get("/api/preferences").json()
    assert first == {
        "edition": "public", "platform": "desktop", "questions": ["adult_confirmed", "save_covers"], "cover_epoch": 0,
        "setup_done": False, "adult_confirmed": None, "save_covers": None, "background_reader": False,
    }
    assert client.patch("/api/preferences", json={"save_covers": False}).json()["setup_done"] is False
    done = client.patch("/api/preferences", json={"adult_confirmed": True}).json()
    assert done["setup_done"] is True and done["save_covers"] is False and done["background_reader"] is False


def test_desktop_public_ignores_background_reader(client, edition):
    edition("public", "desktop")
    result = client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": True, "background_reader": True}).json()
    assert result["setup_done"] is True and result["background_reader"] is False
    assert client.patch("/api/preferences", json={"background_reader": True}).json()["background_reader"] is False


def test_desktop_public_refusing_the_age_limit_is_not_setup(client, edition):
    edition("public", "desktop")
    assert client.patch("/api/preferences", json={"adult_confirmed": False, "save_covers": True}).json()["setup_done"] is False


def test_phone_public_still_needs_three(client, edition):
    edition("public", "android")
    first = client.get("/api/preferences").json()
    assert first["platform"] == "android" and first["questions"] == ["adult_confirmed", "save_covers", "background_reader"]
    assert first["background_reader"] is None
    assert client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": True}).json()["setup_done"] is False
    assert client.patch("/api/preferences", json={"background_reader": False}).json()["setup_done"] is True


def test_phone_public_keeps_choices_made_before(client, edition):
    """手機公開版更新後，之前存的三題照樣有效。"""
    edition("public", "android")
    with SessionLocal() as db:
        db.merge(Setting(key=preferences.KEY, value='{"adult_confirmed": true, "save_covers": false, "background_reader": true}'))
        db.commit()
    result = client.get("/api/preferences").json()
    assert result["setup_done"] is True and result["save_covers"] is False and result["background_reader"] is True


@pytest.mark.parametrize("platform", ["desktop", "android"])
def test_personal_editions_unchanged(client, edition, platform):
    """電腦私人版與手機個人版：沒有第一次啟動、全部開著、不能改設定、不能一次刪封面、可以存封面。"""
    edition("personal", platform)
    result = client.get("/api/preferences").json()
    assert result == {
        "edition": "personal", "platform": platform, "questions": [], "cover_epoch": 0,
        "setup_done": True, "adult_confirmed": True, "save_covers": True, "background_reader": True,
    }
    assert client.patch("/api/preferences", json={"save_covers": False}).status_code == 403
    assert client.post("/api/covers/clear").status_code == 403
    assert client.post("/api/tag-pack/mine", json={"translate": {"a": "b"}}).status_code == 403
    assert preferences.saving_covers() is True


# ---------- 手機才有的功能 ----------

def test_desktop_public_has_no_app_only_features(client, edition):
    folder = edition("public", "desktop") / "backups"
    folder.mkdir()
    (folder / "oas-auto-20261112-100000.json").write_text('{"format": "x"}', encoding="utf-8")
    assert client.get("/api/backup/files").status_code == 403
    assert client.get("/api/backup/files/oas-auto-20261112-100000.json").status_code == 403
    assert client.post("/api/tag-pack/mine", json={"translate": {"Alpha": "甲"}}).status_code == 403


def test_phone_public_keeps_app_only_features(client, edition, monkeypatch):
    from app import defaults

    folder = edition("public", "android") / "backups"
    folder.mkdir()
    (folder / "oas-auto-20261112-100000.json").write_text('{"format": "x"}', encoding="utf-8")
    monkeypatch.setattr(defaults, "sync_packs_safely", lambda db: None)
    assert [row["name"] for row in client.get("/api/backup/files").json()] == ["oas-auto-20261112-100000.json"]
    assert client.post("/api/tag-pack/mine", json={"translate": {"Alpha": "甲"}}).status_code == 200


# ---------- 舊封面 ----------

OLD_A = "https://img.example.com/aaa.png"
OLD_B = "https://img.example.com/bbb.png"


def store_old_covers(folder):
    """模擬更新前已經下載的封面：兩張封面、一張縮圖，另有一筆下載失敗的紀錄（不算張數）。"""
    folder.mkdir(exist_ok=True)
    a, b = covers.url_hash(OLD_A), covers.url_hash(OLD_B)
    (folder / f"{a}.png").write_bytes(PNG)
    (folder / f"{a}_w240.jpg").write_bytes(b"x" * 10)
    (folder / f"{b}.png").write_bytes(PNG)
    with SessionLocal() as db:
        for url, key in ((OLD_A, a), (OLD_B, b)):
            db.merge(CoverCache(url_hash=key, url=url, status="ok", filename=f"{key}.png", content_type="image/png", size=len(PNG)))
        db.merge(CoverCache(url_hash=covers.url_hash("https://img.example.com/ccc.png"), url="https://img.example.com/ccc.png", status="failed"))
        db.commit()


def test_stored_covers_reports_what_clear_would_remove(client, edition):
    folder = edition("public", "desktop") / "covers"
    assert client.get("/api/covers/stored").json() == {"covers": 0, "files": 0, "bytes": 0}
    store_old_covers(folder)
    assert client.get("/api/covers/stored").json() == {"covers": 2, "files": 3, "bytes": 2 * len(PNG) + 10}


def test_desktop_public_clear_after_choosing_not_to_save(client, edition):
    folder = edition("public", "desktop") / "covers"
    store_old_covers(folder)
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": True})
    refused = client.post("/api/covers/clear")
    assert refused.status_code == 409 and "這台電腦" in refused.json()["detail"]
    assert len(list(folder.iterdir())) == 3  # 還在存封面時不會刪
    client.patch("/api/preferences", json={"save_covers": False})
    response = client.post("/api/covers/clear")
    assert response.json() == {"removed": 3, "covers": 2, "cover_epoch": 1}
    assert response.headers["clear-site-data"] == '"cache"'
    assert list(folder.iterdir()) == []
    assert client.get("/api/covers/stored").json() == {"covers": 0, "files": 0, "bytes": 0}


def test_clear_needs_a_post(client, edition):
    folder = edition("public", "desktop") / "covers"
    store_old_covers(folder)
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": False})
    assert client.get("/api/covers/clear").status_code >= 400  # 只有 POST 會刪
    assert len(list(folder.iterdir())) == 3


def test_phone_public_clear_unchanged(client, edition):
    folder = edition("public", "android") / "covers"
    store_old_covers(folder)
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": True, "background_reader": False})
    assert client.post("/api/covers/clear").json()["detail"] == "要先設定不把封面存到手機"
    client.patch("/api/preferences", json={"save_covers": False})
    response = client.post("/api/covers/clear")
    assert response.json()["removed"] == 3 and "clear-site-data" not in response.headers


def test_keeping_old_covers_only_stops_new_ones(client, edition, monkeypatch):
    """選「不存」但不刪：舊封面照樣由本機提供，新的封面只轉給畫面、不存檔。"""
    folder = edition("public", "desktop") / "covers"
    store_old_covers(folder)
    with SessionLocal() as db:
        old = Resource(code="https://example.com/work/old", source_name="測試", title="虛構舊作品", image_url=OLD_A)
        new = Resource(code="https://example.com/work/new", source_name="測試", title="虛構新作品", image_url="https://img.example.com/new.png")
        db.add_all([old, new])
        db.commit()
        old_id, new_id = old.id, new.id
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": False})
    calls = []

    def fetch(url):
        calls.append(url)
        return PNG, "image/png"

    monkeypatch.setattr(covers, "fetch_image", fetch)
    assert client.get(f"/api/covers/{old_id}").status_code == 200 and calls == []
    response = client.get(f"/api/covers/{new_id}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert calls == ["https://img.example.com/new.png"]
    assert len(list(folder.iterdir())) == 3
    with pytest.raises(covers.CoverError, match="這台電腦"):
        covers.download("https://img.example.com/other.png")
    assert client.post("/api/covers/cache").status_code == 409


def test_desktop_public_saves_nothing_before_choosing(client, edition):
    """已經在用的人更新後：選完之前不下載新封面。"""
    edition("public", "desktop")
    assert preferences.saving_covers() is False
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": True})
    assert preferences.saving_covers() is True


def test_clear_bumps_the_cover_epoch_each_time_and_preferences_report_it(client, edition):
    """清除全部封面後版本加 1（前端放進封面網址，瀏覽器暫存的舊封面作廢）；沒清除時版本不變、網址穩定。"""
    edition("public", "desktop")
    assert client.get("/api/preferences").json()["cover_epoch"] == 0
    client.patch("/api/preferences", json={"adult_confirmed": True, "save_covers": False})
    assert client.get("/api/preferences").json()["cover_epoch"] == 0
    assert client.post("/api/covers/clear").json()["cover_epoch"] == 1
    assert client.get("/api/preferences").json()["cover_epoch"] == 1
    assert client.post("/api/covers/clear").json()["cover_epoch"] == 2
    assert client.get("/api/preferences").json()["cover_epoch"] == 2


def test_personal_edition_reports_cover_epoch_too(client, edition):
    edition("personal", "desktop")
    assert client.get("/api/preferences").json()["cover_epoch"] == 0
