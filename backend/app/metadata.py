"""從網址辨識來源與代碼，並讀取作品資訊（標題、封面、標籤）。

- resolve：比對「來源規則」的網址範本，把網址拆回（來源, 代碼），不需要連網。
- fetch：以誠實的 User-Agent 讀取一次頁面並解析。不規避驗證碼或 Cloudflare 等防護；
  被擋時回報「blocked」，改由使用者在自己的瀏覽器裡用「收錄書籤」帶入資料。
"""
import html
import re
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import DEFAULT_SOURCE, normalise_tags
from .covers import CoverError, check_url, safe_urlopen
from .database import Resource, Source

USER_AGENT = "OAS/0.8 (personal archive metadata)"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 12
TITLE_SUFFIXES = [
    r"\s*»\s*nhentai.*$", r"\s*-\s*H動漫.*$", r"\s*-\s*Hanime1\.me\s*$", r"\s*-\s*紳士漫畫.*$", r"\s*[|-]\s*禁漫天堂.*$", r"\s*-\s*MissAV.*$",
]


class MetadataError(Exception):
    def __init__(self, message: str, blocked: bool = False, status: int | None = None):
        super().__init__(message)
        self.blocked = blocked
        self.status = status  # HTTP 狀態碼（連線失敗時為 None）

    @property
    def rate_limited(self) -> bool:
        """被來源限流或暫時擋下（429、503 或 Cloudflare 防護），等一段時間再試通常就好。"""
        return self.blocked or self.status in (429, 503)


# 有防護、伺服器讀不到的網站：背景補資料與來源巡檢都直接略過，請改用「收錄到 OAS」書籤。
PROTECTED_HOSTS = ("18comic", "jmcomic", "missav")


def _normalise(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    return f"{host}{parts.path}" + (f"?{parts.query}" if parts.query else "")


def resolve(db: Session, url: str) -> tuple[str, str]:
    """網址 → (來源, 代碼)。比對各來源範本（忽略 http/https、www、結尾斜線），取最具體的範本；都不符合時歸為直接連結。"""
    target = _normalise(url)
    best: tuple[int, str, str] | None = None
    for source in db.scalars(select(Source)).all():
        template = source.template.strip()
        if "{}" not in template or template == "{}":
            continue
        prefix, _, suffix = _normalise(template.replace("{}", "\x00")).partition("\x00")
        if "?" in prefix:  # 範本本身帶查詢字串（例如 watch?v={}），代碼到下一個 & 為止，順便去掉 fbclid 等追蹤參數
            pattern = re.escape(prefix) + r"([^&#]+)"
        elif suffix:  # 範本在代碼後還有內容（例如 /g/{}/、-aid-{}.html）：代碼是單一路徑段，其後的子頁面忽略
            pattern = re.escape(prefix) + r"([^/?#]+?)" + re.escape(suffix.rstrip("/")) + r"(?:[/?#].*)?$"
        else:  # 代碼在網址最後（例如 missav 的 dm18/fc2-ppv-…）：可含斜線
            pattern = re.escape(prefix) + r"(.+?)/?(?:[?#].*)?$"
        match = re.match(pattern, target)
        if match and (best is None or len(prefix) > best[0]):
            best = (len(prefix), source.name, match.group(1))
    if best:
        return best[1], best[2]
    return DEFAULT_SOURCE, url.strip()


def _get(url: str) -> str:
    try:
        check_url(url)
    except CoverError as error:
        raise MetadataError(str(error)) from error
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with safe_urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as error:
        protected = error.code in (403, 429, 503) and "cloudflare" in (error.headers.get("server") or "").lower()
        raise MetadataError(
            "此網站有防護，無法由伺服器讀取。請在該頁面使用「收錄到 OAS」書籤。" if protected else f"來源回應 HTTP {error.code}",
            blocked=protected, status=error.code,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise MetadataError(f"連線失敗：{getattr(error, 'reason', error)}") from error
    return body.decode(charset, "replace")


def _meta(page: str, name: str) -> str:
    for pattern in (
        rf'<meta[^>]+(?:property|name)="{re.escape(name)}"[^>]+content="([^"]*)"',
        rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="{re.escape(name)}"',
    ):
        match = re.search(pattern, page, re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def clean_title(title: str) -> str:
    title = html.unescape(title or "").strip()
    for suffix in TITLE_SUFFIXES:
        title = re.sub(suffix, "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip()


def _unique(values) -> list[str]:
    seen, result = set(), []
    for value in values:
        value = html.unescape(re.sub(r"<[^>]+>", "", value)).replace("\xa0", " ").strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result


def parse_nhentai(page: str) -> dict:
    cover = re.search(r'id="cover".*?<img[^>]+alt="([^"]*)"[^>]*?src="([^"]+)"', page, re.S)
    pairs = re.findall(r'href="/(parody|character|tag|artist|group|language|category)/[^"]+"[^>]*>.*?<span class="name[^"]*">(?:<!--[^>]*-->)*([^<]+)</span>', page, re.S)
    categories = {html.unescape(name).strip(): namespace for namespace, name in pairs}
    return {
        "title": cover.group(1) if cover else _meta(page, "og:title"), "image_url": cover.group(2) if cover else _meta(page, "og:image"),
        "tags": [name for _, name in pairs], "categories": categories,
    }


def parse_wnacg(page: str) -> dict:
    title = re.search(r"<h2>(.*?)</h2>", page, re.S)
    title = html.unescape(title.group(1)).strip() if title else ""
    cover = re.search(rf'<img[^>]+alt="{re.escape(html.escape(title, quote=True))}"[^>]+src="([^"]+)"', page) if title else None
    cover = cover or re.search(r'<img[^>]+src="(//[^"]*qy0\.ru/data/t/[^"]+)"', page)
    image = re.sub(r"^/+", "https://", cover.group(1)) if cover else ""
    return {"title": title, "image_url": image, "tags": re.findall(r'class="tagshow"[^>]*>([^<]+)</a>', page)}


def parse_hanime1(page: str) -> dict:
    return {
        "title": _meta(page, "og:title"), "image_url": _meta(page, "og:image"),
        "tags": re.findall(r'class="single-video-tag"[^>]*>\s*<a[^>]*>([^<&]+)', page),
    }


def parse_generic(page: str) -> dict:
    title = _meta(page, "og:title") or (re.search(r"<title>(.*?)</title>", page, re.S | re.I) or [None, ""])[1]
    keywords = _meta(page, "keywords")
    return {"title": title, "image_url": _meta(page, "og:image"), "tags": [part for part in re.split(r"[,，]", keywords) if part.strip()][:20]}


def page_image(url: str, page: str, kind: str = "") -> tuple[str, str]:
    """頁面的代表圖：先找 og:image / twitter:image（創作者頁通常是頭像），沒有時改用網站圖示。回傳（網址, image_kind）。"""
    if kind == "創作者":  # 創作者頁先找頭像元素（例如 Pornhub 的 id="getAvatar"、class="avatar"），沒有 og:image 時也找得到
        for tag in re.findall(r"<img[^>]+>", page, re.I | re.S):
            src = re.search(r'\ssrc="(https?://[^"]+)"', tag)
            if src and (re.search(r'id="getAvatar"', tag) or re.search(r'class="[^"]*\bavatar\b', tag, re.I)):
                return src.group(1), "avatar"
    image = _meta(page, "og:image") or _meta(page, "twitter:image")
    if image:
        return urljoin(url, image), "avatar" if kind == "創作者" else ""
    icons = []
    for tag in re.findall(r"<link[^>]+>", page, re.I):
        rel = (re.search(r'rel="([^"]+)"', tag, re.I) or [None, ""])[1].lower()
        href = (re.search(r'href="([^"]+)"', tag, re.I) or [None, ""])[1]
        if not href or "icon" not in rel:
            continue
        size = re.search(r'sizes="(\d+)x', tag)
        score = (1000 if "apple-touch" in rel else 0) + (int(size.group(1)) if size else 16)
        icons.append((score, html.unescape(href)))
    if icons:
        return urljoin(url, max(icons)[1]), "icon"
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico", "icon"


PARSERS = {"nhentai.net": parse_nhentai, "wnacg.com": parse_wnacg, "hanime1.me": parse_hanime1}


def parse(url: str, page: str) -> dict:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    data = PARSERS.get(host, parse_generic)(page)
    image = data.get("image_url") or ""
    result = {
        "title": clean_title(data.get("title") or ""),
        "image_url": urljoin(url, image) if image else "",
        "tags": _unique(data.get("tags") or [])[:60],
    }
    if data.get("categories"):
        result["categories"] = data["categories"]
    return result


# 只輸入作品編號（俗稱「神的語言」）時當成 N 網的作品，例如 123456 或 #123456
BARE_CODE = re.compile(r"^#?\s*(\d{1,7})$")


def expand_code(text: str) -> str:
    """純數字的作品編號 → N 網作品頁網址；其他輸入原樣回傳。"""
    match = BARE_CODE.match(text.strip())
    return f"https://nhentai.net/g/{match.group(1)}/" if match else text.strip()


def describe(db: Session, url: str, fetch: bool = True) -> dict:
    url = expand_code(url)
    if not re.match(r"^https?://", url, re.I):
        raise MetadataError("請輸入以 http:// 或 https:// 開頭的網址，或 N 網的作品編號（純數字）")
    source_name, code = resolve(db, url)
    duplicate = db.scalar(select(Resource).where(Resource.source_name == source_name, Resource.code == code))
    result = {
        "url": url, "source_name": source_name, "code": code, "title": "", "image_url": "", "tags": [],
        "duplicate": {"id": duplicate.id, "title": duplicate.title or duplicate.code} if duplicate else None,
        "fetched": False, "blocked": False, "message": "", "similar": [],
    }
    if fetch:
        try:
            parsed = parse(url, _get(url))
            parsed.pop("categories", None)
            parsed["tags"] = normalise_tags(db, parsed["tags"])  # 套用你的同義詞與封鎖清單
            result.update(parsed, fetched=True)
            result["similar"] = similar_titles(db, parsed["title"], exclude=duplicate.id if duplicate else None)
        except MetadataError as error:
            result.update(message=str(error), blocked=error.blocked)
    return result


def similar_titles(db: Session, title: str, exclude: int | None = None, limit: int = 3) -> list[dict]:
    """標題（去掉【】[] 裡的社團、語言標記後）相同的既有作品，用來提醒「可能是同一本的不同版本」。"""
    from .insights import _title_key  # 避免循環匯入

    key = _title_key(title)
    if not key:
        return []
    found = []
    for row in _title_index(db).get(key, []):
        if row["id"] != exclude:
            found.append(row)
            if len(found) >= limit:
                break
    return found


_title_cache: dict = {"key": None, "index": {}}


def _title_index(db: Session) -> dict[str, list[dict]]:
    """標題比對用的索引（標題 key → 作品），作品有增刪改時才重建，不必每次抓取都掃過全部作品。

    背景補資料會保留 updated_at 寫入標題，所以快取鍵另外加上「所有標題的總長度」，補上標題時索引才會跟著重建。
    """
    from sqlalchemy import func

    from .insights import _title_key

    cache_key = tuple(db.execute(select(
        func.count(Resource.id), func.max(Resource.id), func.max(Resource.updated_at), func.coalesce(func.sum(func.length(Resource.title)), 0),
    )).one())
    if _title_cache["key"] != cache_key:
        index: dict[str, list[dict]] = {}
        for row in db.execute(select(Resource.id, Resource.title, Resource.source_name, Resource.code)).all():
            key = _title_key(row.title)
            if key:
                index.setdefault(key, []).append({"id": row.id, "title": row.title or row.code, "source_name": row.source_name})
        _title_cache.update(key=cache_key, index=index)
    return _title_cache["index"]
