"""Local cover cache.

Covers are downloaded once from the saved image URL and served from the Docker volume.
No anti-bot circumvention: a plain request with an honest User-Agent. When the source refuses,
the API redirects the browser to the original URL, which behaves exactly as before caching existed.
"""
import hashlib
import ipaddress
import socket
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

from . import settings
from .database import CoverCache, Resource, SessionLocal

COVER_DIR = settings.COVER_DIR
MAX_BYTES = 10 * 1024 * 1024
TIMEOUT_SECONDS = 12
RETRY_FAILED_AFTER = timedelta(hours=24)
USER_AGENT = "OAS/0.3 (personal archive cover cache)"
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif"}

_batch_lock = Lock()
batch_state = {"running": False, "queued": 0, "done": 0, "ok": 0, "failed": 0, "started_at": None, "finished_at": None}


class CoverError(Exception):
    pass


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def cover_file(entry: CoverCache) -> Path:
    return COVER_DIR / entry.filename


def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CoverError("只接受 http / https 圖片網址")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except socket.gaierror as error:
        raise CoverError(f"無法解析主機：{parts.hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise CoverError("不下載內部網路位址的圖片")


class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
    """轉址時對新網址再跑一次 check_url，避免外部網址轉址到內部網路（127.0.0.1、Docker 主機、路由器等）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            check_url(newurl)
        except CoverError as error:
            raise urllib.error.URLError(f"轉址目標被拒絕：{error}") from error
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirect)


def safe_urlopen(request: urllib.request.Request, timeout: float):
    """對外抓取一律用這個：與 urlopen 相同，但每次轉址都會重新檢查目標位址。呼叫前仍需先對原始網址跑 check_url。"""
    return _opener.open(request, timeout=timeout)


def download(url: str) -> tuple[bytes, str]:
    """Return (content, content_type). Replaced in tests."""
    check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"})
    try:
        with safe_urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                raise CoverError(f"來源回傳的不是圖片（{content_type}）")
            content = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise CoverError(f"來源拒絕（HTTP {error.code}）") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CoverError(f"連線失敗：{getattr(error, 'reason', error)}") from error
    if len(content) > MAX_BYTES:
        raise CoverError("圖片超過 10 MB")
    if not content:
        raise CoverError("來源回傳空檔案")
    return content, content_type


THUMB_WIDTHS = (240, 360, 480)


def image_ratio(path: Path) -> float | None:
    """高 / 寬；只讀檔頭，不解碼整張圖。"""
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        return round(height / width, 4) if width and height else None
    except Exception:
        return None


def image_colour(path: Path) -> str:
    """封面主色，供環境光使用。回傳 #rrggbb；無法取色時回傳空字串。

    以小尺寸量化成 8 色，依「佔比 × 彩度」挑選，避開近黑、近白與灰階背景；
    最後把明度與彩度收斂到深色介面上看得清楚、又不刺眼的範圍。
    """
    import colorsys

    try:
        from PIL import Image

        with Image.open(path) as image:
            image.draft("RGB", (96, 96))  # JPEG 直接以縮小比例解碼，速度快很多
            image.seek(0)
            small = image.convert("RGB")
            small.thumbnail((72, 72))
        palette = small.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        colours = palette.getpalette()[:24]
        counts = sorted(palette.getcolors() or [], reverse=True)
    except Exception:
        return ""
    total = sum(count for count, _ in counts) or 1
    best, best_score = None, -1.0
    for count, index in counts:
        r, g, b = (value / 255 for value in colours[index * 3:index * 3 + 3])
        hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
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


def backfill_colours() -> int:
    """替尚未分析主色的已快取封面取色。於背景執行緒呼叫，不阻擋 API 啟動。"""
    updated = 0
    try:
        with SessionLocal() as db:
            for entry in db.scalars(select(CoverCache).where(CoverCache.status == "ok", CoverCache.colour.is_(None))).all():
                entry.colour = image_colour(cover_file(entry))
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
    rows = db.execute(select(CoverCache.url_hash, CoverCache.ratio, CoverCache.colour).where(
        CoverCache.url_hash.in_(list(hashes)), CoverCache.status == "ok")).all()
    return {hashes[key]: (ratio, colour or None) for key, ratio, colour in rows}


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
    if width not in THUMB_WIDTHS:
        return original, entry.content_type
    target = COVER_DIR / f"{entry.url_hash}_w{width}.webp"
    if target.exists():
        return target, "image/webp"
    try:
        from PIL import Image

        with Image.open(original) as image:
            if image.width <= width:
                return original, entry.content_type
            image.seek(0)  # 動態 GIF 只取第一格
            converted = image.convert("RGBA" if image.mode in ("RGBA", "LA", "P") else "RGB")
            converted.thumbnail((width, width * 3))
            temporary = target.with_suffix(".part")
            converted.save(temporary, "WEBP", quality=80, method=4)
            temporary.replace(target)
        return target, "image/webp"
    except Exception:
        return original, entry.content_type


def fresh_failure(entry: CoverCache | None) -> bool:
    if entry is None or entry.status != "failed":
        return False
    fetched = entry.fetched_at if entry.fetched_at.tzinfo else entry.fetched_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched < RETRY_FAILED_AFTER


def ensure_cover(db: Session, url: str, force: bool = False) -> CoverCache:
    """Return the cache entry for url, downloading when missing (or failed long enough ago)."""
    key = url_hash(url)
    entry = db.get(CoverCache, key)
    if entry and entry.status == "ok" and cover_file(entry).exists():
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


def cached_entry(db: Session, url: str) -> CoverCache | None:
    entry = db.get(CoverCache, url_hash(url))
    return entry if entry and entry.status == "ok" and cover_file(entry).exists() else None


def _cache_in_background(url: str) -> None:
    try:
        with SessionLocal() as db:
            ensure_cover(db, url)
    finally:
        with _batch_lock:
            _in_flight.discard(url)


def schedule(url: str) -> None:
    """Queue a single download unless it is already running, recently failed, or a batch is active."""
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
    entries = {entry.url_hash: entry for entry in db.scalars(select(CoverCache)).all()}
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
    with SessionLocal() as db:
        return ensure_cover(db, url, force=force).status == "ok"


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
