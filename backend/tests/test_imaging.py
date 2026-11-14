"""手機公開版的封面解碼（imaging.py）：Android 的部分用 Pillow 假扮，測檔頭、PNG 區塊、中斷後跳過、不靠 Pillow。"""
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest
from PIL import Image

from app import covers, imaging, settings
from app.database import CoverCache


class FakeDecoder:
    """和 CoverDecoder.java 相同的介面。"""
    calls: list[str] = []

    @classmethod
    def size(cls, path):
        cls.calls.append("size")
        with Image.open(path) as image:
            return list(image.size)

    @classmethod
    def thumbnailExtension(cls):
        return "webp"

    @classmethod
    def thumbnail(cls, source, target, width):
        cls.calls.append("thumbnail")
        with Image.open(source) as image:
            image.convert("RGB").resize((width, width)).save(target, "WEBP")

    @classmethod
    def pixels(cls, path, width, height, factor):
        cls.calls.append("pixels")
        with Image.open(path) as image:
            data = image.convert("RGB").resize((width, height)).tobytes()
        return [struct.unpack(">i", b"\xff" + data[index:index + 3])[0] for index in range(0, len(data), 3)]


@pytest.fixture()
def android(monkeypatch, tmp_path):
    FakeDecoder.calls = []
    monkeypatch.setattr(imaging, "_decoder", lambda: FakeDecoder)
    monkeypatch.setattr(settings, "IMAGE_ENGINE", "android")
    monkeypatch.setattr(covers, "COVER_DIR", tmp_path)
    imaging._reviewed.clear()
    covers._thumb_failed.clear()
    return tmp_path


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png_with(*chunks: bytes) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)) + b"".join(chunks) + png_chunk(b"IDAT", zlib.compress(b"\0\0")) + png_chunk(b"IEND", b"")


def picture(folder: Path, name="a.png", size=(800, 1200), colour=(200, 30, 30)) -> Path:
    path = folder / name
    Image.new("RGB", size, colour).save(path)
    return path


@pytest.mark.parametrize(("head", "kind"), [(b"\xff\xd8\xff\xe0", "JPEG"), (b"\x89PNG\r\n\x1a\n", "PNG"), (b"GIF89a", "GIF"), (b"RIFF\0\0\0\0WEBP", "WEBP"), (b"\0\0\1\0", "ICO"),
                                            (b"<svg xmlns=", ""), (b"II*\0", ""), (b"8BPS", ""), (b"BM", ""), (b"<html>", "")])
def test_only_common_formats_by_header(tmp_path, head, kind):
    path = tmp_path / "named.jpg"  # 副檔名不算數
    path.write_bytes(head.ljust(32, b"\1"))
    assert imaging.sniff(path) == kind


def test_short_or_empty_file_is_not_an_image(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"")
    assert imaging.sniff(path) == ""
    path.write_bytes(b"\x89PNG")
    assert imaging.sniff(path) == ""


def test_png_large_metadata_is_refused_but_image_data_is_not(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(png_with())
    assert imaging.png_is_reasonable(path)
    path.write_bytes(png_with(png_chunk(b"IDAT", b"\0" * (imaging.MAX_PNG_CHUNK * 3))))
    assert imaging.png_is_reasonable(path)
    path.write_bytes(png_with(png_chunk(b"fdAT", b"\0" * (imaging.MAX_PNG_CHUNK * 3))))
    assert imaging.png_is_reasonable(path)
    path.write_bytes(png_with(png_chunk(b"eXIf", b"\0" * (imaging.MAX_PNG_CHUNK + 1))))
    assert not imaging.png_is_reasonable(path)
    path.write_bytes(png_with(png_chunk(b"iCCP", b"\0" * 500_000)))  # 正常的色彩設定檔
    assert imaging.png_is_reasonable(path)
    path.write_bytes(png_with(*[png_chunk(b"tEXt", b"\0" * 1000)] * (imaging.MAX_PNG_METADATA // 1000 + 1)))  # 拆成很多小區塊也一樣
    assert not imaging.png_is_reasonable(path)


def test_png_claiming_huge_chunk_or_cut_short_does_not_loop(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I4s", 0xffffffff, b"tEXt"))
    assert not imaging.png_is_reasonable(path)
    path.write_bytes(png_with()[:20])
    assert imaging.png_is_reasonable(path)  # 交給解碼器判斷
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + png_chunk(b"tEXt", b"") * (imaging.MAX_PNG_CHUNKS + 1))
    assert not imaging.png_is_reasonable(path)


def test_android_engine_fills_cover_data(android):
    path = picture(android)
    assert covers.image_ratio(path) == 1.5
    assert covers.image_colour(path).startswith("#")
    assert len(covers.image_fingerprint(path)) == 16
    assert not list(android.glob("*.decoding"))


def test_android_engine_makes_thumbnails(android):
    path = picture(android)
    entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/png")
    target, media_type = covers.thumbnail_file(entry, 240)
    assert target.name == "a_w240.webp" and media_type == "image/webp"
    with Image.open(target) as image:
        assert image.format == "WEBP"
    FakeDecoder.calls = []
    assert covers.thumbnail_file(entry, 240)[0] == target and FakeDecoder.calls == []  # 有了就不再做
    assert not list(android.glob("*.part")) and not list(android.glob("*.decoding"))


def test_small_cover_uses_the_original(android):
    path = picture(android, size=(100, 140))
    entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/png")
    assert covers.thumbnail_file(entry, 240) == (path, "image/png")
    assert FakeDecoder.calls == ["size"]


def test_refused_files_never_reach_the_decoder(android):
    for name, content in (("page.jpg", b"<html>login page, not an image</html>"), ("big.png", png_with(png_chunk(b"eXIf", b"\0" * (imaging.MAX_PNG_CHUNK + 1))))):
        path = android / name
        path.write_bytes(content)
        assert covers.image_ratio(path) is None and covers.image_colour(path) == "" and covers.image_fingerprint(path) == ""
        entry = CoverCache(url_hash=name, url="http://example.com/x", status="ok", filename=name, content_type="image/png")
        assert covers.thumbnail_file(entry, 240) == (path, "image/png")
    assert FakeDecoder.calls == []


def test_decoder_error_falls_back_to_the_original(android, monkeypatch):
    path = picture(android)

    def broken(*args):
        raise RuntimeError("Java 例外")

    monkeypatch.setattr(FakeDecoder, "pixels", broken)
    monkeypatch.setattr(FakeDecoder, "thumbnail", broken)
    assert covers.image_colour(path) == "" and covers.image_fingerprint(path) == ""
    entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/png")
    assert covers.thumbnail_file(entry, 240) == (path, "image/png")
    assert not list(android.glob("*.decoding"))  # 出錯不是中斷，不留標記


def crash_during_decode(folder: Path, name: str) -> None:
    """解碼中途 App 消失：標記留在資料夾，下次啟動才看得到。"""
    (folder / f"{name}.12345.decoding").touch()
    imaging._reviewed.discard(folder)


def test_two_crashes_stop_decoding_that_cover(android):
    path = picture(android)
    crash_during_decode(android, path.name)
    assert covers.image_ratio(path) == 1.5  # 第一次可能只是剛好被滑掉，再試一次
    crash_during_decode(android, path.name)
    FakeDecoder.calls = []
    assert covers.image_ratio(path) is None and covers.image_colour(path) == "" and covers.image_fingerprint(path) == ""
    entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename=path.name, content_type="image/png")
    assert covers.thumbnail_file(entry, 240) == (path, "image/png")
    assert FakeDecoder.calls == [] and not list(android.glob("*.decoding"))
    other = picture(android, "b.png")
    assert covers.image_ratio(other) == 1.5  # 其他封面不受影響


def test_broken_strike_file_is_treated_as_empty(android):
    path = picture(android)
    imaging._strikes_file(android).write_text("不是 JSON", encoding="utf-8")
    assert covers.image_ratio(path) == 1.5
    crash_during_decode(android, path.name)
    assert not imaging.is_blocked(path)
    assert imaging._read_strikes(android) == {path.name: 1}


def test_clearing_covers_also_clears_decode_records(client, android):
    from app.database import SessionLocal

    path = picture(android)
    crash_during_decode(android, path.name)
    assert not imaging.is_blocked(path)
    (android / "b.png.1.decoding").touch()
    with SessionLocal() as db:
        covers.clear_all(db)
    assert list(android.iterdir()) == []


def test_median_cut_finds_the_main_colours():
    pixels = [(200, 30, 30)] * 600 + [(30, 30, 200)] * 300 + [(10, 10, 10)] * 100
    assert [colour for _, colour in imaging.median_cut(pixels)[:3]] == [(200, 30, 30), (30, 30, 200), (10, 10, 10)]
    assert imaging.median_cut([(5, 5, 5)] * 10) == [(10, (5, 5, 5))]


def test_pick_colour_prefers_saturated_over_dark_and_white():
    red = imaging.pick_colour([(600, (10, 10, 10)), (100, (250, 250, 250)), (300, (200, 30, 30))])
    r, g, b = (int(red[index:index + 2], 16) for index in (1, 3, 5))
    assert r > g and r > b
    assert imaging.pick_colour([(10, (0, 0, 0)), (10, (255, 255, 255))]) == ""


def test_pillow_is_the_default_and_android_code_does_not_need_it(tmp_path):
    """公開版的 APK 沒有 Pillow：把 Pillow 擋掉，Android 的路徑一樣要能跑完。電腦版與個人版預設還是 Pillow。"""
    assert settings.IMAGE_ENGINE == "pillow" or "OAS_IMAGE_ENGINE" in os.environ
    backend = Path(__file__).resolve().parents[1]
    code = f"""
import sys
sys.modules["PIL"] = None
sys.path.insert(0, {str(backend)!r})
from pathlib import Path
from app import covers, imaging, settings
assert settings.IMAGE_ENGINE == "android"
class Decoder:
    @staticmethod
    def size(path): return [800, 1200]
    @staticmethod
    def thumbnailExtension(): return "webp"
    @staticmethod
    def thumbnail(source, target, width): Path(target).write_bytes(b"made")
    @staticmethod
    def pixels(path, width, height, factor): return [-3612642] * (width * height)
imaging._decoder = lambda: Decoder
folder = Path({str(tmp_path)!r})
covers.COVER_DIR = folder
path = folder / "a.png"
path.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"\\0" * 32)
assert covers.image_ratio(path) == 1.5 and covers.image_colour(path).startswith("#") and len(covers.image_fingerprint(path)) == 16
assert covers._thumb_format()[1] == "webp"
from app.database import CoverCache
entry = CoverCache(url_hash="a", url="http://example.com/a", status="ok", filename="a.png", content_type="image/png")
target, media = covers.thumbnail_file(entry, 240)
assert target.read_bytes() == b"made" and media == "image/webp"
print("ok")
"""
    env = {**os.environ, "OAS_IMAGE_ENGINE": "android", "OAS_DATA_DIR": str(tmp_path / "data"), "PYTHONUTF8": "1"}
    env.pop("OAS_DATABASE_URL", None)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", env=env)
    assert result.stdout.strip().endswith("ok"), result.stderr[-800:]


def test_large_ico_is_refused_small_one_is_decoded(android):
    big = android / "big.ico"
    big.write_bytes(b"\0\0\1\0" + b"\0" * (imaging.MAX_ICO_BYTES + 1))
    assert covers.image_ratio(big) is None and FakeDecoder.calls == []
    Image.new("RGB", (64, 64), (200, 30, 30)).save(android / "tiny.ico", "ICO")
    assert covers.image_ratio(android / "tiny.ico") == 1.0


def test_only_a_few_decodes_run_at_once(android, monkeypatch):
    import threading
    import time

    path = picture(android)
    running, peak, lock = [0], [0], threading.Lock()

    def slow(*args):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        time.sleep(0.05)
        with lock:
            running[0] -= 1
        return [800, 1200]

    monkeypatch.setattr(FakeDecoder, "size", slow)
    threads = [threading.Thread(target=covers.image_ratio, args=(path,)) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] <= imaging.MAX_DECODING
