"""Local cover cache.

Covers are downloaded once from the saved image URL and served from the local cover folder (settings.COVER_DIR).
No anti-bot circumvention: a plain request with an honest User-Agent. The browser never loads the source directly
(the backend fetches every image, see stored_cover / remote_image); when the source refuses, the cover is a 404.
"""
import functools
import hashlib
import http.client
import io
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import imaging, preferences, settings
from .batching import chunks
from .database import CoverCache, Resource, SessionLocal

COVER_DIR = settings.COVER_DIR
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 12  # 每次等待（連線、等下一段資料）的上限
# 一張圖的總時限，涵蓋連線、等回應標頭、每次轉址與讀內容：慢速來源每次只送一點點，也不能長時間佔住抓圖名額（REMOTE_SLOTS）。
# 不含查 DNS（check_url 的 getaddrinfo 無法設逾時）。
TOTAL_SECONDS = 15
CHUNK_BYTES = 64 * 1024
_clock = time.monotonic  # 測試換成假的時鐘
RETRY_FAILED_AFTER = timedelta(hours=24)
USER_AGENT = "OAS/0.3 (personal archive cover cache)"
# 只收點陣圖：SVG 等可以內嵌程式的格式從典藏館自己的網址提供時，會以典藏館的身分執行
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
              "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico"}  # ico：網站頁面以網站圖示當封面
# 提供封面時附上：不准瀏覽器猜類型，就算直接開啟也不執行任何程式
COVER_HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}

_batch_lock = Lock()
batch_state = {"running": False, "queued": 0, "done": 0, "ok": 0, "failed": 0, "started_at": None, "finished_at": None}


class CoverError(Exception):
    pass


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cover_file(entry: CoverCache) -> Path:
    return COVER_DIR / entry.filename


def check_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        hostname, port = parts.hostname, parts.port  # 連接埠不是數字時，讀取 port 才會丟 ValueError
    except ValueError as error:
        raise CoverError("網址格式不正確") from error
    if parts.scheme not in ("http", "https") or not hostname:
        raise CoverError("只接受 http / https 圖片網址")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, port or 443)}
    except (OSError, UnicodeError) as error:  # 主機名稱太長等情況是 UnicodeError
        raise CoverError(f"無法解析主機：{hostname}") from error
    if not all(public_address(address) for address in addresses):
        raise CoverError("不下載內部網路位址的圖片")


def public_address(address: str) -> bool:
    """只有公開網際網路位址算數；內部網路、本機、電信商 NAT（100.64.0.0/10）、保留與群播位址都不算。"""
    ip = ipaddress.ip_address(address.split("%")[0])
    return ip.is_global and not ip.is_multicast


def _arm(sock, deadline: float) -> None:
    """下一次等待最多只等剩下的總時間（也不超過 TIMEOUT_SECONDS）；時間用完就中止。"""
    remaining = deadline - _clock()
    if remaining <= 0:
        raise TimeoutError(f"超過總時限 {TOTAL_SECONDS} 秒")
    sock.settimeout(min(remaining, TIMEOUT_SECONDS))


class _DeadlineRaw(io.RawIOBase):
    """回應的底層讀取：每次向連線要資料前都重設等待上限。

    回應標頭也是一段一段讀的，只在讀內容時檢查不夠：來源可以每幾秒送一個位元組的標頭。不另開執行緒計時。"""

    def __init__(self, raw, sock, deadline: float):
        super().__init__()
        self._raw, self._sock, self._deadline = raw, sock, deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        _arm(self._sock, self._deadline)
        return self._raw.readinto(buffer)

    def close(self):
        if not self.closed:
            self._raw.close()  # 交還 makefile 的參照，連線才會真的關閉
        super().close()


class _DeadlineResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, deadline: float, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp = io.BufferedReader(_DeadlineRaw(self.fp.detach(), sock, deadline))


class _WithinDeadline:
    """有截止時間（fetch_image 設在 request.oas_deadline）時：連線後設好等待上限，回應改用 _DeadlineResponse。

    沒有截止時間的呼叫（作品頁、巡檢）行為不變。"""

    def __init__(self, *args, deadline: float | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._deadline = deadline
        if deadline is not None:
            self.response_class = functools.partial(_DeadlineResponse, deadline=deadline)

    def connect(self):
        super().connect()
        if self._deadline is not None:
            _arm(self.sock, self._deadline)  # 送出請求也受總時限限制


class _PeerChecked:
    """連線建立後檢查實際連到的位址。check_url 與真正連線各查一次 DNS，惡意網域可以在兩次之間換成內部位址（DNS rebinding）。"""

    def connect(self):
        super().connect()
        peer = self.sock.getpeername()[0]
        if not public_address(peer):
            self.close()
            raise OSError(f"實際連線的位址 {peer} 是內部網路，已中止")


class _PeerCheckedHTTP(_PeerChecked, _WithinDeadline, http.client.HTTPConnection):
    pass


class _PeerCheckedHTTPS(_PeerChecked, _WithinDeadline, http.client.HTTPSConnection):
    pass


class _ProxiedHTTP(_WithinDeadline, http.client.HTTPConnection):
    pass


class _ProxiedHTTPS(_WithinDeadline, http.client.HTTPSConnection):
    pass


def _through_proxy(request: urllib.request.Request) -> bool:
    # 經過代理伺服器時連到的是代理（常在本機），由代理解析網址，這裡無法檢查
    return request.has_proxy() or getattr(request, "_tunnel_host", None) is not None


def _open_within(handler, connection, request, **kwargs):
    """每次連線（含轉址後的新連線）都只等剩下的總時間；時間用完就不再連線。"""
    deadline = getattr(request, "oas_deadline", None)
    if deadline is not None:
        remaining = deadline - _clock()
        if remaining <= 0:
            raise urllib.error.URLError(f"超過總時限 {TOTAL_SECONDS} 秒")
        request.timeout = min(remaining, TIMEOUT_SECONDS)
        connection = functools.partial(connection, deadline=deadline)
    return handler.do_open(connection, request, **kwargs)


class _CheckedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return _open_within(self, _ProxiedHTTP if _through_proxy(request) else _PeerCheckedHTTP, request)


class _CheckedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return _open_within(self, _ProxiedHTTPS if _through_proxy(request) else _PeerCheckedHTTPS, request,
                            context=self._context)


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """轉址時對新網址再跑一次 check_url，避免外部網址轉址到內部網路（127.0.0.1、路由器等）。

    有截止時間時，時間用完就不再轉址，新的請求沿用同一個截止時間（不是每次轉址重新計時）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        deadline = getattr(req, "oas_deadline", None)
        if deadline is not None and deadline - _clock() <= 0:
            raise urllib.error.URLError(f"轉址時已超過總時限 {TOTAL_SECONDS} 秒")
        try:
            check_url(newurl)
        except CoverError as error:
            raise urllib.error.URLError(f"轉址目標被拒絕：{error}") from error
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and deadline is not None:
            new.oas_deadline = deadline
        return new


_opener = urllib.request.build_opener(_CheckedRedirect, _CheckedHTTPHandler, _CheckedHTTPSHandler)


def safe_urlopen(request: urllib.request.Request, timeout: float):
    """對外抓取一律用這個：與 urlopen 相同，但每次轉址都會重新檢查目標位址，連線後也檢查實際連到的位址。呼叫前仍需先對原始網址跑 check_url。"""
    return _opener.open(request, timeout=timeout)


def download(url: str) -> tuple[bytes, str]:
    """Return (content, content_type). Replaced in tests."""
    if not preferences.saving_covers():  # 要存檔的下載都經過這裡：公開版選了不存，就不會把圖片抓下來存起來
        raise CoverError(f"已設定不把封面存到{preferences.device_name()}")
    return fetch_image(url)


def _read_within(response, deadline: float) -> bytes:
    """分段讀到結束或超過 MAX_BYTES；超過截止時間就中止。

    read1 每次只等一次底層資料，不會為了湊滿一段而一直等下去；每次等待的上限由 _DeadlineRaw 設定。"""
    read = getattr(response, "read1", None) or response.read
    chunks, size = [], 0
    while size <= MAX_BYTES:
        remaining = deadline - _clock()
        if remaining <= 0:
            raise CoverError(f"讀取超過 {TOTAL_SECONDS} 秒")
        chunk = read(CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def fetch_image(url: str) -> tuple[bytes, str]:
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    deadline = _clock() + TOTAL_SECONDS
    request.oas_deadline = deadline  # 連線、回應標頭、每次轉址與讀內容共用這個截止時間
    try:
        with safe_urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if content_type not in EXTENSIONS:
                raise CoverError(f"只接受 JPEG、PNG、WebP、GIF、AVIF、ICO 圖片（來源回傳 {content_type}）")
            content = _read_within(response, deadline)
    except urllib.error.HTTPError as error:
        raise CoverError(f"來源拒絕（HTTP {error.code}）") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as error:  # 網址含空白、回應中途斷線是 HTTPException
        if _clock() >= deadline:  # 等待上限已縮成剩下的時間，逾時多半是總時限用完
            raise CoverError(f"超過總時限 {TOTAL_SECONDS} 秒") from error
        raise CoverError(f"連線失敗：{getattr(error, 'reason', error)}") from error
    if len(content) > MAX_BYTES:
        raise CoverError("圖片超過 10 MB")
    if not content:
        raise CoverError("來源回傳空檔案")
    return content, content_type


# 主畫面不直接連外部網站（所有版本）：外部網址可以轉址回這支手機的其他埠，瀏覽器會帶著通行碼 cookie 跟過去，
# WebView 的 shouldInterceptRequest 攔不到轉址（實機測過）。所以圖片一律由這裡抓，每次轉址都檢查目標，也不帶 cookie。
REMOTE_SLOTS = threading.BoundedSemaphore(8)  # 捲動時不要讓慢的來源佔滿 API 的執行緒
REMOTE_WAIT_SECONDS = 20
REMOTE_RETRY_SECONDS = 600
_remote_failed: dict[str, float] = {}


def remote_image(url: str) -> tuple[bytes, str]:
    """抓一張圖但不存檔。剛失敗過的不再連線，畫面每次重畫才不會一直打擾來源網站。"""
    failed_at = _remote_failed.get(url)
    if failed_at is not None and time.monotonic() - failed_at < REMOTE_RETRY_SECONDS:
        raise CoverError("剛才讀取失敗")
    if not REMOTE_SLOTS.acquire(timeout=REMOTE_WAIT_SECONDS):
        raise CoverError("同時讀取的圖片太多")
    try:
        result = fetch_image(url)
    except CoverError:
        if len(_remote_failed) >= 5000:
            _remote_failed.clear()
        _remote_failed[url] = time.monotonic()
        raise
    finally:
        REMOTE_SLOTS.release()
    _remote_failed.pop(url, None)
    return result


def stored_cover(db: Session, url: str) -> CoverCache | None:
    """可以存封面、但這張還沒存：現在就抓來存好再給畫面。抓不到回傳 None。

    24 小時內失敗過的不再連線，也不排隊等名額，整頁都是壞封面時才不會卡住其他請求。"""
    if fresh_failure(db.get(CoverCache, url_hash(url))):
        return None
    if not REMOTE_SLOTS.acquire(timeout=REMOTE_WAIT_SECONDS):
        return None
    try:
        entry = ensure_cover(db, url)
    finally:
        REMOTE_SLOTS.release()
    return entry if usable(entry) else None


THUMB_WIDTHS = (240, 360, 480)
THUMB_BACKFILL_WIDTH = 240  # 背景、相似作品、側欄都用這個寬度，先產生好，第一次打開作品時才不必現做
THUMB_BACKFILL_PAUSE = 0.1  # 每張之間休息一下，不和畫面搶 CPU
_thumb_failed: set[tuple[str, int]] = set()  # 做不出縮圖的（例如手機讀不了 WebP 原圖）：這次執行不再重試，直接用原圖


# 封面來自任何網站，只解碼網頁常見的格式（ICO 是網站圖示）：PSD、TIFF、JPEG2000 這類解碼器不該碰到網路上來的檔案。
IMAGE_FORMATS = ("JPEG", "PNG", "GIF", "ICO", "WEBP")
MAX_IMAGE_PIXELS = 25_000_000  # 超過 2 倍（5000 萬像素）時 Pillow 直接拒絕，防圖片炸彈把記憶體用光


def open_image(path: Path):
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    return Image.open(path, formats=IMAGE_FORMATS)


def _android() -> bool:
    return settings.IMAGE_ENGINE == "android"


def _thumb_format() -> tuple[str, str, str]:
    """（Pillow 格式、副檔名、媒體類型）。手機（Chaquopy）的 Pillow 沒有編進 WebP，存檔會失敗，改存 JPEG。"""
    if _android():
        extension = imaging.thumbnail_extension()
        return extension.upper(), extension, imaging.MEDIA_TYPES[extension]
    from PIL import features

    return ("WEBP", "webp", "image/webp") if features.check("webp") else ("JPEG", "jpg", "image/jpeg")


def image_ratio(path: Path) -> float | None:
    """高 / 寬；只讀檔頭，不解碼整張圖。"""
    if _android():
        return imaging.ratio(path)
    try:
        from PIL import Image

        with open_image(path) as image:
            width, height = image.size
        return round(height / width, 4) if width and height else None
    except Exception:
        return None


def image_colour(path: Path) -> str:
    """封面主色，供環境光使用。回傳 #rrggbb；無法取色時回傳空字串。

    以小尺寸量化成 8 色，依「佔比 × 彩度」挑選，避開近黑、近白與灰階背景；
    最後把明度與彩度收斂到深色介面上看得清楚、又不刺眼的範圍。
    """
    if _android():
        return imaging.colour(path)
    try:
        from PIL import Image

        with open_image(path) as image:
            image.draft("RGB", (96, 96))  # JPEG 直接以縮小比例解碼，速度快很多
            image.seek(0)
            small = image.convert("RGB")
            small.thumbnail((72, 72))
        palette = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        colours = palette.getpalette()[:24]
        counts = sorted(palette.getcolors() or [], reverse=True)
    except Exception:
        return ""
    return imaging.pick_colour([(count, tuple(colours[index * 3:index * 3 + 3])) for count, index in counts])


def image_fingerprint(path: Path) -> str:
    """封面的差異雜湊（dHash）：縮成 9×8 灰階，比較左右相鄰像素的明暗，得到 64 位元。

    同一張圖即使重新壓縮、縮放或換了網址，雜湊也只差幾個位元，用來找出封面一樣的重複作品。
    回傳 16 位十六進位；無法讀圖時回傳空字串。
    """
    if _android():
        return imaging.fingerprint(path)
    try:
        from PIL import Image

        with open_image(path) as image:
            image.draft("L", (64, 64))
            image.seek(0)
            pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes())
    except Exception:
        return ""
    return imaging.fingerprint_of(pixels)


def backfill_colours() -> int:
    """替尚未分析主色或雜湊的已快取封面補上。於背景執行緒呼叫，不阻擋 API 啟動。"""
    updated = 0
    try:
        with SessionLocal() as db:
            for entry in db.scalars(select(CoverCache).where(
                    CoverCache.status == "ok", CoverCache.colour.is_(None) | CoverCache.fingerprint.is_(None))).all():
                if entry.colour is None:
                    entry.colour = image_colour(cover_file(entry))
                if entry.fingerprint is None:
                    entry.fingerprint = image_fingerprint(cover_file(entry))
                updated += 1
                if updated % 50 == 0:
                    db.commit()
            db.commit()
    except Exception:  # 補色只是錦上添花；失敗時下次啟動再試
        pass
    return updated


def backfill_ratios() -> int:
    """替 v0.4 以前快取、尚無比例的封面補上比例。"""
    updated = 0
    with SessionLocal() as db:
        for entry in db.scalars(select(CoverCache).where(CoverCache.status == "ok", CoverCache.ratio.is_(None))).all():
            ratio = image_ratio(cover_file(entry))
            if ratio:
                entry.ratio = ratio
                updated += 1
        db.commit()
    return updated


def metadata_for(db: Session, urls: list[str]) -> dict[str, tuple[float | None, str | None]]:
    """以圖片網址查詢已快取封面的（比例, 主色）。"""
    hashes = {url_hash(url): url for url in urls if url}
    if not hashes:
        return {}
    found = {}
    for batch in chunks(hashes):  # 待整理清單可能列出全部作品，分批才不會超過參數上限
        for key, ratio, colour in db.execute(select(CoverCache.url_hash, CoverCache.ratio, CoverCache.colour).where(
                CoverCache.url_hash.in_(batch), CoverCache.status == "ok")):
            found[hashes[key]] = (ratio, colour or None)
    return found


def with_ratios(db: Session, items: list[dict]) -> list[dict]:
    """補上 cover_ratio（瀑布流預留版面）與 cover_colour（環境光）。"""
    known = metadata_for(db, [item["image_url"] for item in items])
    for item in items:
        item["cover_ratio"], item["cover_colour"] = known.get(item["image_url"], (None, None))
    return items


def thumbnail_file(entry: CoverCache, width: int) -> tuple[Path, str]:
    """Return a downscaled WebP of a cached cover, creating it once. Falls back to the original on any error.

    瀑布流只需約 360px 寬的縮圖；原圖常達 1000px 以上，逐張解碼是捲動卡頓的主因之一。
    """
    original = cover_file(entry)
    if width not in THUMB_WIDTHS or (entry.url_hash, width) in _thumb_failed:
        return original, entry.content_type
    temporary = None
    try:
        kind, extension, media_type = _thumb_format()
        target = COVER_DIR / f"{entry.url_hash}_w{width}.{extension}"
        if target.exists():
            return target, media_type
        temporary = target.with_name(f"{target.name}.{threading.get_ident()}.part")  # 背景與請求可能同時做同一張
        if _android():
            if not imaging.make_thumbnail(original, temporary, width):
                _thumb_failed.add((entry.url_hash, width))  # 原圖夠小，不必再開檔判斷
                return original, entry.content_type
            temporary.replace(target)
            return target, media_type
        from PIL import Image

        with open_image(original) as image:
            if image.width <= width:
                _thumb_failed.add((entry.url_hash, width))  # 原圖夠小，不必再開檔判斷
                return original, entry.content_type
            image.seek(0)  # 動態 GIF 只取第一格
            image.draft("RGB", (width, width * 3))  # JPEG 直接以小尺寸解碼，快很多
            converted = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
            converted.thumbnail((width, width * 3))
            if kind == "JPEG" and converted.mode == "RGBA":  # JPEG 沒有透明：鋪在深色底上，和畫面底色相近
                flattened = Image.new("RGB", converted.size, (18, 24, 39))
                flattened.paste(converted, mask=converted.getchannel("A"))
                converted = flattened
            options = {"quality": 80, "method": 4} if kind == "WEBP" else {"quality": 82, "optimize": True}
            converted.save(temporary, kind, **options)
            temporary.replace(target)
        return target, media_type
    except Exception:
        _thumb_failed.add((entry.url_hash, width))
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        return original, entry.content_type


def backfill_thumbnails(pause: float = THUMB_BACKFILL_PAUSE) -> int:
    """替已快取的封面預先產生小縮圖。於背景執行緒呼叫；已有的只查一下檔案，之後新封面在第一次用到或下次啟動時補上。"""
    made = 0
    try:
        with SessionLocal() as db:
            entries = db.scalars(select(CoverCache).where(CoverCache.status == "ok")).all()
            db.expunge_all()
        _kind, extension, _media_type = _thumb_format()
        for entry in entries:
            if (COVER_DIR / f"{entry.url_hash}_w{THUMB_BACKFILL_WIDTH}.{extension}").exists():
                continue
            path, _media = thumbnail_file(entry, THUMB_BACKFILL_WIDTH)
            if path.name != entry.filename:
                made += 1
            time.sleep(pause)
    except Exception:  # 只是加速用，失敗時用到才現做
        pass
    return made


def fresh_failure(entry: CoverCache | None) -> bool:
    if entry is None or entry.status != "failed":
        return False
    fetched = entry.fetched_at if entry.fetched_at.tzinfo else entry.fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < RETRY_FAILED_AFTER


def ensure_cover(db: Session, url: str, force: bool = False) -> CoverCache:
    """Return the cache entry for url, downloading when missing (or failed long enough ago)."""
    key = url_hash(url)
    entry = db.get(CoverCache, key)
    if usable(entry):
        return entry
    if fresh_failure(entry) and not force:
        return entry
    if entry is None:
        entry = CoverCache(url_hash=key, url=url[:2000], status="failed")
        db.add(entry)
    try:
        content, content_type = download(url)
        filename = key + EXTENSIONS.get(content_type, ".img")
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        temporary = COVER_DIR / (filename + ".part")
        temporary.write_bytes(content)
        temporary.replace(COVER_DIR / filename)
        entry.status, entry.filename, entry.content_type, entry.size, entry.error = "ok", filename, content_type, len(content), ""
        entry.ratio = image_ratio(COVER_DIR / filename)
        entry.colour = image_colour(COVER_DIR / filename)
        entry.fingerprint = image_fingerprint(COVER_DIR / filename)
    except CoverError as error:
        entry.status, entry.error = "failed", str(error)[:300]
    entry.fetched_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        # 同一網址被並行請求同時下載：以先寫入者為準。
        db.rollback()
        return db.get(CoverCache, key)
    return entry


_in_flight: set[str] = set()
# 單張背景下載使用獨立的小型執行緒池，避免佔用 API 的請求執行緒。
_single_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="oas-cover")


# 下載失敗的原因裡，代表「這個封面網址已經壞了」的：來源說找不到（404、410，例如 Hanime1 有時效的縮圖網址過期），
# 或回傳的不是圖片、是空檔案（例如禁漫已下架作品的封面）。被擋（403）、連線失敗則可能只是暫時的，瀏覽器直接載入也可能看得到。
BROKEN_ERRORS = re.compile(r"HTTP 40[49]|HTTP 410|來源回傳 text/|空檔案")


def broken_urls(db: Session) -> set[str]:
    """封面已經壞掉的網址（見 BROKEN_ERRORS）。"""
    return {url for url, error in db.execute(select(CoverCache.url, CoverCache.error).where(CoverCache.status == "failed")) if BROKEN_ERRORS.search(error or "")}


def usable(entry: CoverCache | None) -> bool:
    # 舊版收下的 SVG 等格式不再提供，下次會重新下載（並被拒絕）
    return bool(entry and entry.status == "ok" and entry.content_type in EXTENSIONS and cover_file(entry).exists())


def cached_entry(db: Session, url: str) -> CoverCache | None:
    entry = db.get(CoverCache, url_hash(url))
    return entry if usable(entry) else None


def _cache_in_background(url: str) -> None:
    try:
        with SessionLocal() as db:
            ensure_cover(db, url)
    finally:
        with _batch_lock:
            _in_flight.discard(url)


def schedule(url: str) -> None:
    """Queue a single download unless it is already running, recently failed, or a batch is active."""
    if not preferences.saving_covers():
        return
    with _batch_lock:
        if url in _in_flight or batch_state["running"]:
            return
        _in_flight.add(url)
    with SessionLocal() as db:
        if fresh_failure(db.get(CoverCache, url_hash(url))):
            with _batch_lock:
                _in_flight.discard(url)
            return
    _single_pool.submit(_cache_in_background, url)


def stats(db: Session) -> dict:
    urls = {url for url in db.scalars(select(Resource.image_url).where(Resource.image_url != "")).all()}
    entries = {row.url_hash: row for row in db.execute(select(CoverCache.url_hash, CoverCache.status, CoverCache.size))}  # 維護頁每幾秒查一次，只取要用的欄位
    cached = failed = size = 0
    for url in urls:
        entry = entries.get(url_hash(url))
        if entry and entry.status == "ok":
            cached += 1
            size += entry.size
        elif entry and entry.status == "failed":
            failed += 1
    return {
        "with_image": len(urls), "cached": cached, "failed": failed, "pending": len(urls) - cached - failed,
        "bytes": size, "batch": dict(batch_state),
    }


def _cache_one(url: str, force: bool) -> bool:
    """批次裡的單張：出任何錯都只算這張失敗，不中斷其他張的進度。"""
    try:
        with SessionLocal() as db:
            return ensure_cover(db, url, force=force).status == "ok"
    except Exception:  # noqa: BLE001
        return False


def run_batch(urls: list[str], force: bool = False, workers: int = 4) -> None:
    with _batch_lock:
        batch_state.update(running=True, queued=len(urls), done=0, ok=0, failed=0,
                           started_at=datetime.now(timezone.utc).isoformat(), finished_at=None)
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ok in pool.map(lambda url: _cache_one(url, force), urls):
                with _batch_lock:
                    batch_state["done"] += 1
                    batch_state["ok" if ok else "failed"] += 1
    finally:
        with _batch_lock:
            batch_state.update(running=False, finished_at=datetime.now(timezone.utc).isoformat())


def stored_summary(db: Session) -> dict:
    """已經存在本機的封面：clear_all 會刪掉的範圍（公開版改成不存時，畫面用來問要不要刪、顯示張數）。
    covers 是存好的封面張數（含已不在典藏裡的作品），files 與 bytes 是資料夾裡的檔案（含縮圖）。"""
    covers = db.query(CoverCache).filter(CoverCache.status == "ok").count()
    files = size = 0
    if COVER_DIR.is_dir():
        for path in COVER_DIR.iterdir():
            if path.is_file():
                files += 1
                size += path.stat().st_size
    return {"covers": covers, "files": files, "bytes": size}


def clear_all(db: Session) -> int:
    """刪除所有存在本機的封面與縮圖、以及它們的紀錄（公開版改成不存封面時）。回傳刪掉的檔案數。"""
    removed = 0
    if COVER_DIR.is_dir():
        for path in COVER_DIR.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
    db.query(CoverCache).delete()
    db.commit()
    _thumb_failed.clear()
    return removed


def urls_to_cache(db: Session, retry_failed: bool) -> list[str]:
    urls = sorted({url for url in db.scalars(select(Resource.image_url).where(Resource.image_url != "")).all()})
    entries = {entry.url_hash: entry for entry in db.scalars(select(CoverCache)).all()}
    result = []
    for url in urls:
        entry = entries.get(url_hash(url))
        if entry is None or (entry.status == "ok" and not cover_file(entry).exists()):
            result.append(url)
        elif entry.status == "failed" and (retry_failed or not fresh_failure(entry)):
            result.append(url)
    return result
