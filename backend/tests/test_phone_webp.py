"""手機個人版：Pillow（Chaquopy）沒有 WebP 時，WebP 封面改用 Android 解碼器；其他格式與電腦版照舊。"""
import pytest
from PIL import Image, features

from app import covers, imaging, settings
from app.database import CoverCache, SessionLocal
from tests.test_imaging import FakeDecoder


class Decoder(FakeDecoder):
    @classmethod
    def thumbnail(cls, source, target, width, jpeg=False):
        cls.calls.append("thumbnail-jpeg" if jpeg else "thumbnail")
        with Image.open(source) as image:
            image.convert("RGB").resize((width, width)).save(target, "JPEG" if jpeg else "WEBP")


@pytest.fixture()
def phone(monkeypatch, tmp_path):
    """手機個人版：平台 android、引擎 pillow，Pillow 沒有 WebP。"""
    Decoder.calls = []
    monkeypatch.setattr(imaging, "_decoder", lambda: Decoder)
    monkeypatch.setattr(settings, "PLATFORM", "android")
    monkeypatch.setattr(settings, "IMAGE_ENGINE", "pillow")
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path)
    monkeypatch.setattr(covers, "WEBP_RETRY_PAUSE", 0)
    monkeypatch.setattr(features, "check", lambda name, *a, **k: False if name == "webp" else True)
    imaging._reviewed.clear()
    covers._thumb_failed.clear()
    return tmp_path


def webp(folder, name="a.webp", size=(800, 1200)):
    path = folder / name
    Image.new("RGB", size, (200, 30, 30)).save(path, "WEBP")
    return path


def test_webp_goes_to_the_android_decoder(phone):
    path = webp(phone)
    assert covers.image_ratio(path) == 1.5
    assert covers.image_colour(path).startswith("#")
    assert len(covers.image_fingerprint(path)) == 16
    assert {"size", "pixels"} <= set(Decoder.calls)


def test_other_formats_still_use_pillow(phone):
    path = phone / "a.png"
    Image.new("RGB", (800, 1200), (200, 30, 30)).save(path)
    assert covers.image_ratio(path) == 1.5 and covers.image_colour(path).startswith("#")
    assert Decoder.calls == []


def test_webp_thumbnail_is_a_jpeg_with_the_pillow_extension(phone):
    path = webp(phone)
    entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/webp")
    target, media_type = covers.thumbnail_file(entry, 240)
    assert target.name == "a_w240.jpg" and media_type == "image/jpeg"
    with Image.open(target) as image:
        assert image.format == "JPEG"
    assert "thumbnail-jpeg" in Decoder.calls and not list(phone.glob("*.part"))


def test_desktop_never_uses_the_android_decoder(phone, monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM", "desktop")
    assert not covers._android_webp(webp(phone))
    monkeypatch.setattr(settings, "PLATFORM", "android")
    monkeypatch.setattr(features, "check", lambda *a, **k: True)  # Pillow 有 WebP 就不用繞路
    assert not covers._android_webp(webp(phone))


def test_public_engine_is_unchanged(phone, monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_ENGINE", "android")
    assert not covers._android_webp(webp(phone))  # 公開版本來就全部走 imaging


def test_backfill_recomputes_empty_webp_data_only(client, phone):
    path = webp(phone)
    png = phone / "b.png"
    Image.new("RGB", (10, 10)).save(png)
    with SessionLocal() as db:
        db.add_all([
            CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/webp", colour="", fingerprint=""),
            CoverCache(url_hash="b", url="http://example.com/b", status="ok", filename=png.name, content_type="image/png", ratio=1.0, colour="", fingerprint=""),
        ])
        db.commit()
    assert covers.backfill_colours() >= 1
    with SessionLocal() as db:
        a, b = db.get(CoverCache, "a"), db.get(CoverCache, "b")
        assert a.colour.startswith("#") and len(a.fingerprint) == 16 and a.ratio == 1.5
        assert b.colour == "" and b.fingerprint == ""  # 不是 WebP：不碰
