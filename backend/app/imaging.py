"""封面解碼：取色與指紋的共用算法，以及用 Android 內建解碼器的做法（手機公開版，OAS_IMAGE_ENGINE=android）。

電腦版與個人版繼續用 Pillow，程式在 covers.py。這個檔案不能 import Pillow：公開版的 APK 沒有帶。
封面來自任何網站，所以解碼前先做三件事：看檔頭只放行常見格式（不信任副檔名）、PNG 擋下超大的附加區塊、
記下正在處理哪一張。解碼器讓 App 當掉時，下次啟動就知道是哪一張，兩次之後不再碰它，改由網頁直接顯示原圖。
"""
import colorsys
import json
import struct
import threading
from contextlib import contextmanager
from pathlib import Path

# PNG 的附加區塊（文字、EXIF、色彩設定）：Android 解碼 10 MB 的區塊要 20 秒以上、吃掉 1 GB 記憶體，正常封面用不到這麼大
MAX_PNG_CHUNK = 1024 * 1024
MAX_PNG_METADATA = 2 * 1024 * 1024
MAX_PNG_CHUNKS = 200_000
MAX_ICO_BYTES = 1024 * 1024  # ICO 裡面可以包 PNG，區塊檢查碰不到；網站圖示不會這麼大
MAX_DECODING = 2  # 同時解碼的張數：每張解碼都可能要幾十 MB
PNG_IMAGE_CHUNKS = {b"IDAT", b"fdAT"}
# 同一張圖讓 App 中斷幾次後不再解碼。一次可能只是剛好被滑掉，所以不是一次就放棄
MAX_STRIKES = 2
MEDIA_TYPES = {"webp": "image/webp", "jpg": "image/jpeg"}


class ImageRejected(Exception):
    pass


def pick_colour(counts: list[tuple[int, tuple[int, int, int]]]) -> str:
    """依「佔比 × 彩度」挑主色，避開近黑、近白與灰階背景，再把明度與彩度收斂到深色介面上看得清楚、又不刺眼的範圍。
    counts：（像素數, (r, g, b)），依序比較，分數相同時前面的優先。挑不出來回傳空字串。"""
    total = sum(count for count, _ in counts) or 1
    best, best_score = None, -1.0
    for count, (r, g, b) in counts:
        hue, lightness, saturation = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if lightness < 0.12 or lightness > 0.93:
            continue
        score = (count / total) * (0.15 + saturation) * (1 - abs(lightness - 0.55))
        if score > best_score:
            best, best_score = (hue, lightness, saturation), score
    if best is None:
        return ""
    hue, lightness, saturation = best
    if saturation < 0.12:  # 幾乎是灰階：保留中性但帶一點冷色，避免環境光變髒
        hue, saturation = 0.64, 0.18
    r, g, b = colorsys.hls_to_rgb(hue, min(max(lightness, 0.58), 0.72), min(max(saturation, 0.18), 0.72))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def fingerprint_of(grey: list[int]) -> str:
    """差異雜湊（dHash）：9×8 的灰階，比較左右相鄰像素的明暗，得到 64 位元。"""
    value = 0
    for row in range(8):
        for column in range(8):
            value = value << 1 | (grey[row * 9 + column] > grey[row * 9 + column + 1])
    return f"{value:016x}"


def median_cut(pixels: list[tuple[int, int, int]], colours: int = 8) -> list[tuple[int, tuple[int, int, int]]]:
    """把像素分成幾個色塊：每次挑體積最大的一塊，沿著差異最大的色頻從中間切開。回傳（像素數, 平均色），多的在前。"""
    boxes = [list(pixels)]

    def ranges(box):
        return [max(pixel[channel] for pixel in box) - min(pixel[channel] for pixel in box) for channel in range(3)]

    def volume(box):
        red, green, blue = ranges(box)
        return (red + 1) * (green + 1) * (blue + 1)

    while len(boxes) < colours:
        candidates = [(volume(box), index) for index, box in enumerate(boxes) if max(ranges(box)) > 0]
        if not candidates:
            break
        _, index = max(candidates)
        spread = ranges(boxes[index])
        channel = spread.index(max(spread))
        box = sorted(boxes.pop(index), key=lambda pixel: pixel[channel])
        # 只切在數值改變的地方：同一個顏色拆到兩塊，平均起來會變成圖上沒有的顏色
        edges = [position for position in range(1, len(box)) if box[position - 1][channel] != box[position][channel]]
        middle = min(edges, key=lambda position: abs(position - len(box) // 2))
        boxes += [box[:middle], box[middle:]]
    result = [(len(box), tuple(sum(pixel[channel] for pixel in box) // len(box) for channel in range(3))) for box in boxes if box]
    return sorted(result, reverse=True)


def sniff(path: Path) -> str:
    """看檔頭判斷格式；不是允許的格式回傳空字串。"""
    with open(path, "rb") as stream:
        head = stream.read(12)
    if len(head) < 12:
        return ""
    if head[:3] == b"\xff\xd8\xff":
        return "JPEG"
    if head[:4] == b"\x89PNG":
        return "PNG"
    if head[:4] == b"GIF8":
        return "GIF"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[:4] == b"\0\0\1\0":
        return "ICO"
    return ""


def png_is_reasonable(path: Path) -> bool:
    """只走過區塊標頭，不解碼。影像資料多大都可以（尺寸另外擋），其他區塊太大就不解碼。"""
    metadata = chunks = 0
    with open(path, "rb") as stream:
        stream.seek(8)
        while True:
            header = stream.read(8)
            if len(header) < 8:
                return True  # 被截斷的檔案交給解碼器判斷
            length, kind = struct.unpack(">I4s", header)
            chunks += 1
            if chunks > MAX_PNG_CHUNKS:
                return False
            if kind not in PNG_IMAGE_CHUNKS:
                metadata += length
                if length > MAX_PNG_CHUNK or metadata > MAX_PNG_METADATA:
                    return False
            if kind == b"IEND":
                return True
            stream.seek(length + 4, 1)


_guard_lock = threading.Lock()
_decoder_slots = threading.BoundedSemaphore(MAX_DECODING)
_reviewed: set[Path] = set()


def _strikes_file(folder: Path) -> Path:
    return folder / ".decode-strikes.json"


def _read_strikes(folder: Path) -> dict[str, int]:
    try:
        data = json.loads(_strikes_file(folder).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _review(folder: Path) -> None:
    """這次啟動第一次解碼前：上次留下來的標記，代表 App 在解碼途中中斷。呼叫端持有 _guard_lock。"""
    if folder in _reviewed:
        return
    _reviewed.add(folder)
    leftovers = list(folder.glob("*.decoding"))
    if not leftovers:
        return
    strikes = _read_strikes(folder)
    for name in {marker.name.rsplit(".", 2)[0] for marker in leftovers}:
        strikes[name] = strikes.get(name, 0) + 1
    temporary = _strikes_file(folder).with_suffix(".part")
    temporary.write_text(json.dumps(strikes), encoding="utf-8")
    temporary.replace(_strikes_file(folder))
    for marker in leftovers:
        marker.unlink(missing_ok=True)


def is_blocked(path: Path) -> bool:
    with _guard_lock:
        _review(path.parent)
        return _read_strikes(path.parent).get(path.name, 0) >= MAX_STRIKES


@contextmanager
def _decoding(path: Path):
    """檔案也放在封面資料夾，清除封面時會一起清掉。"""
    with _decoder_slots:
        marker = path.with_name(f"{path.name}.{threading.get_ident()}.decoding")
        marker.touch()  # 排到了才放標記：中斷時只算真的在解碼的那幾張
        try:
            yield
        finally:
            marker.unlink(missing_ok=True)


def _decoder():
    """Android 的解碼器（android/app/src/public/java/.../CoverDecoder.java）。"""
    from java import jclass

    return jclass("io.github.oasarchive.oas.CoverDecoder")


def _accept(path: Path) -> None:
    if is_blocked(path):
        raise ImageRejected("這張圖之前讓 App 中斷過")
    kind = sniff(path)
    if not kind:
        raise ImageRejected("不是允許的圖片格式")
    if kind == "PNG" and not png_is_reasonable(path):
        raise ImageRejected("PNG 的附加資料太大")
    if kind == "ICO" and path.stat().st_size > MAX_ICO_BYTES:
        raise ImageRejected("ICO 太大")


def _pixels(path: Path, width: int, height: int, factor: int) -> list[tuple[int, int, int]]:
    """先解到 factor 倍大再平均：直接縮到很小會失真，同一張圖換尺寸就認不出來。"""
    _accept(path)
    with _decoding(path):
        values = _decoder().pixels(str(path), width, height, factor)
    return [(value >> 16 & 255, value >> 8 & 255, value & 255) for value in (int(item) & 0xffffffff for item in values)]


def ratio(path: Path) -> float | None:
    try:
        _accept(path)
        with _decoding(path):
            width, height = _decoder().size(str(path))
        return round(height / width, 4) if width and height else None
    except Exception:  # noqa: BLE001  解碼器的錯誤是 Java 例外
        return None


def colour(path: Path) -> str:
    try:
        return pick_colour(median_cut(_pixels(path, 72, 72, 2)))
    except Exception:  # noqa: BLE001
        return ""


def fingerprint(path: Path) -> str:
    try:
        pixels = _pixels(path, 9, 8, 16)
    except Exception:  # noqa: BLE001
        return ""
    return fingerprint_of([(r * 299 + g * 587 + b * 114) // 1000 for r, g, b in pixels])


def thumbnail_extension() -> str:
    """Android 11 起才有新的 WebP 寫法，更舊的存 JPEG。"""
    return _decoder().thumbnailExtension()


def make_thumbnail(source: Path, target: Path, width: int) -> bool:
    """做好存到 target 回傳 True；原圖已經夠小回傳 False。做不出來丟例外，由 covers.py 改用原圖。"""
    _accept(source)
    with _decoding(source):
        decoder = _decoder()
        original_width, _height = decoder.size(str(source))
        if original_width <= width:
            return False
        decoder.thumbnail(str(source), str(target), width)
    return True
