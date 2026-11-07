"""從網址辨識來源與代碼，並讀取作品資訊（標題、封面、標籤）。

- resolve：比對「來源規則」的網址範本，把網址拆回（來源, 代碼），不需要連網。
- fetch：以誠實的 User-Agent 讀取一次頁面並解析。不規避驗證碼或 Cloudflare 等防護；
  被擋時回報「blocked」，改由使用者在自己的瀏覽器裡用「收錄書籤」帶入資料。
"""
import html
import http.client
import re
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urljoin, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import tag_suggest
from .archive import DEFAULT_SOURCE, explain_tag_rules, tag_rules
from .covers import CoverError, check_url, safe_urlopen
from .database import Resource, Source
from .pagetext import TAG_WINDOW, MetadataError, _between, _defuse, _meta, _src_after, _tags, _unique  # noqa: F401
from .site_parsers import PARSERS, parse_hanime1, parse_hentaihaven, parse_jm, parse_missav, parse_nhentai, parse_pornhub, parse_wnacg  # noqa: F401
from .sites import HOST_ALIASES, HOST_KINDS, KNOWN_COVERS, LAST_SEGMENT_HOSTS, MIRRORS, MISSING_PAGES, NUMBER_URL, PATH_REWRITES, PROTECTED_HOSTS, SHORT_CODE_URLS, TITLE_SUFFIXES, VERSION_SUFFIXES  # noqa: F401

USER_AGENT = "OAS/0.8 (personal archive metadata)"
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 12


def known_cover(url: str) -> str:
    """依網址規則推得的封面網址（例如禁漫的作品編號）；沒有規則時回傳空字串。"""
    for pattern, template in KNOWN_COVERS:
        if match := pattern.match(url.strip()):
            return template.format(match.group(1))
    return ""


def _split(url: str):
    """urlsplit，但格式壞掉（例如 http://[bad）時當成空網址：只是判斷類型、網域的地方不該因此出錯。"""
    try:
        return urlsplit(url.strip())
    except ValueError:
        return urlsplit("")


def _join(base: str, link: str) -> str:
    """urljoin，頁面裡的連結格式壞掉時回傳空字串。"""
    try:
        return urljoin(base, link)
    except ValueError:
        return ""


def _host(host: str) -> str:
    host = host.lower().removeprefix("www.")
    for pattern, canonical in (*MIRRORS, *HOST_ALIASES):
        if pattern.match(host):
            return canonical
    return host


def mirrored(url: str) -> bool:
    """網址是不是有鏡像網域或分類路徑的網站（禁漫、MissAV）：啟動時的整理只處理這些，不動其他網站。"""
    host = (_split(url).hostname or "").lower().removeprefix("www.")
    return any(pattern.match(host) for pattern, _ in MIRRORS)


def _normalise(url: str) -> str:
    parts = _split(url)
    host = _host(parts.hostname or "")
    path = parts.path
    if host in PATH_REWRITES:  # 同一件作品的不同頁面（見 sites.py）
        path = re.sub(*PATH_REWRITES[host], path)
    return f"{host}{path}" + (f"?{parts.query}" if parts.query else "")


# 網站頁面（不是作品）：類型設為「網站」，也不需要封面。包含
# - 首頁：missav.ws、missav.ws/dm40/cn 這類，路徑只有語言（cn、zh-tw）或 MissAV 的分站代號（dm40）；
#   語言只認常見的代碼，免得 x.com/ab 這種兩個字母的帳號頁被當成首頁。
# - 列表、分區頁：Pornhub 的 /explore、FC2 的搜尋頁、論壇的 forum-23-2.html、播放清單、分類、標籤頁（可以帶查詢字串）。
#   論壇文章（thread-…）、影片頁、創作者頁都不算。
SITE_KIND = "網站"
# 不是作品的頁面：網站頁面與創作者頁。仍收在典藏裡，但不出現在首頁推薦、繼續閱讀、深潛、隨機抽這些「挑下一本」的地方
NON_WORK_KINDS = (SITE_KIND, "創作者")
# 「原創」是「沒有原作」的意思（nhentai 的 original）：不當成一部原作來排名、算口味，也不算分類錯誤
ORIGINAL_NAMES = {"原創", "original"}
# 需要真正封面的類型：其他類型（文章、論壇串、創作者頁、網站）用網站圖示當封面就夠了
COVER_KINDS = ("漫畫", "動畫", "影片")
# 網站圖示當成的封面（favicon、apple-touch-icon、icon_2x.png、禁漫的 logo）：不是作品的封面，
# 顯示成佔位圖、不拿來比對重複，一般作品只有這種封面時算缺封面
ICON_URL = re.compile(r"favicon|apple-touch-icon|/icons?[_-]?[\w-]*\.(?:png|ico|svg|webp)(?:[?#]|$)|\.ico(?:[?#]|$)|/media/logo/", re.I)


def icon_url(url: str) -> bool:
    return bool(url and ICON_URL.search(url))
LANGUAGE_SEGMENTS = {"cn", "tw", "hk", "zh", "zh-cn", "zh-tw", "zh-hk", "zh-hans", "zh-hant", "en", "ja", "jp", "ko", "kr",
                     "th", "vi", "de", "fr", "es", "pt", "ru", "it"}
SITE_HOME_SEGMENT = re.compile(r"^(?:dm\d+|index\.(?:html?|php))$", re.I)
SITE_SECTION_SEGMENT = re.compile(r"^(?:explore|search(?:es)?|forum|forum-\d+(?:-\d+)?\.html|categor(?:y|ies)|tags?|genres?|playlists?|list|rankings?)$", re.I)


# 創作者頁：女優、演員、模特兒、作者、頻道（路徑裡這一段後面接名稱或編號）
CREATOR_SEGMENT = re.compile(r"^(?:actress(?:es)?|actors?|pornstars?|models?|channels|artists?|fanclubs|creators?)$", re.I)


def creator_page(url: str) -> bool:
    segments = [segment for segment in _split(url).path.split("/") if segment]
    return any(CREATOR_SEGMENT.match(segment) for segment in segments[:-1])


def page_kind(url: str) -> str:
    """只看網址就能判斷的類型：網站頁面、創作者頁、看網域就知道的動畫網站；判斷不出來回傳空字串（沿用來源的類型）。"""
    if site_page(url):
        return SITE_KIND
    if creator_page(url):
        return "創作者"
    host = (_split(url).hostname or "").lower()
    return next((kind for name, kind in HOST_KINDS if name in host), "")


def site_page(url: str) -> bool:
    parts = _split(url)
    if parts.scheme not in ("http", "https"):
        return False
    segments = [segment for segment in parts.path.split("/") if segment]
    if any(SITE_SECTION_SEGMENT.match(segment) for segment in segments):
        return True
    return not parts.query and all(segment.lower() in LANGUAGE_SEGMENTS or SITE_HOME_SEGMENT.match(segment) for segment in segments)


def resolve(db: Session, url: str) -> tuple[str, str]:
    """網址 → (來源, 代碼)。比對各來源範本（忽略 http/https、www、結尾斜線），取最具體的範本；都不符合時歸為直接連結。"""
    target = _normalise(url)
    best: tuple[int, str, str] | None = None
    for source in db.scalars(select(Source)).all():
        template = source.template.strip()
        if "{}" not in template or template == "{}":
            continue
        prefix, _, suffix = _normalise(template.replace("{}", "\x00")).partition("\x00")
        if "?" in prefix:  # 範本本身帶查詢字串（例如 watch?v={}）：參數不一定排第一（watch?list=…&v=…），其他參數（fbclid 等）忽略
            base, _, param = prefix.partition("?")
            name = param.rstrip("=")
            if param.endswith("=") and "&" not in name and target.startswith(base + "?"):
                values = parse_qs(target.split("?", 1)[1].split("#", 1)[0]).get(name)
                if values and values[0] and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), source.name, values[0])
            continue
        elif suffix:  # 範本在代碼後還有內容（例如 /g/{}/、-aid-{}.html）：代碼是單一路徑段，其後的子頁面忽略
            pattern = re.escape(prefix) + r"([^/?#]+?)" + re.escape(suffix.rstrip("/")) + r"(?:[/?#].*)?$"
        else:  # 代碼在網址最後（例如 missav 的 fc2-ppv-…、Pornhub 的 view_video.php?viewkey=…）：可含斜線，查詢字串也是代碼的一部分
            pattern = re.escape(prefix) + r"(.+?)/?(?:#.*)?$"
        match = re.match(pattern, target)
        if match and (best is None or len(prefix) > best[0]):
            code = match.group(1)
            if any(host in template for host in LAST_SEGMENT_HOSTS):
                segments = code.split("?", 1)[0].strip("/").split("/")
                # 女優、分類這類頁面保留「類別/名稱」（actresses/某女優），其他只取最後一段的番號
                for index, segment in enumerate(segments[:-1]):
                    if CREATOR_SEGMENT.match(segment) or SITE_SECTION_SEGMENT.match(segment):
                        segments = segments[index:]
                        break
                else:
                    segments = segments[-1:]
                code = "/".join(segments)
            best = (len(prefix), source.name, code)
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
            final_url = response.geturl() or url
    except urllib.error.HTTPError as error:
        protected = error.code in (403, 429, 503) and "cloudflare" in (error.headers.get("server") or "").lower()
        raise MetadataError(
            "此網站有防護，無法由伺服器讀取。請在該頁面使用「收錄到 OAS」書籤。" if protected else f"來源回應 HTTP {error.code}",
            blocked=protected, status=error.code,
        ) from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as error:
        raise MetadataError(f"連線失敗：{getattr(error, 'reason', error)}") from error
    for marker, message in MISSING_PAGES:  # 作品不存在時轉到錯誤頁（HTTP 200），等同「找不到」
        if marker in final_url:
            raise MetadataError(message, status=404)
    try:
        return body.decode(charset, "replace")
    except LookupError:  # 網站宣告了不存在的編碼
        return body.decode("utf-8", "replace")


def clean_title(title: str) -> str:
    title = html.unescape((title or "")[:2000]).strip()  # 比這更長的不是標題
    for suffix in TITLE_SUFFIXES:
        title = re.sub(suffix, "", title, flags=re.I)
    return re.sub(r"\s+", " ", title).strip()


def _title_element(page: str) -> str:
    start = re.search(r"<title>", page, re.I)
    end = re.compile(r"</title>", re.I).search(page, start.end()) if start else None
    return page[start.end():end.start()] if end else ""


def parse_generic(page: str) -> dict:
    title = _meta(page, "og:title") or _title_element(page)
    keywords = _meta(page, "keywords")
    return {"title": title, "image_url": _meta(page, "og:image"), "tags": [part for part in re.split(r"[,，]", keywords) if part.strip()][:20]}


def page_image(url: str, page: str, kind: str = "") -> tuple[str, str]:
    """頁面的代表圖：先找 og:image / twitter:image（創作者頁通常是頭像），沒有時改用網站圖示。回傳（網址, image_kind）。"""
    page = _defuse(page)
    if kind == "創作者":  # 創作者頁先找頭像元素（例如 Pornhub 的 id="getAvatar"、class="avatar"），沒有 og:image 時也找得到
        for tag in _tags(page, "img", 1000):
            src = re.search(r'\ssrc="(https?://[^"]+)"', tag)
            if src and (re.search(r'id="getAvatar"', tag) or re.search(r'class="[^"]*\bavatar\b', tag, re.I)):
                return src.group(1), "avatar"
    image = _meta(page, "og:image") or _meta(page, "twitter:image")
    if image:
        return _join(url, image), "avatar" if kind == "創作者" else ""
    icons = []
    for tag in _tags(page, "link"):
        rel = (re.search(r'rel="([^"]+)"', tag, re.I) or [None, ""])[1].lower()
        href = (re.search(r'href="([^"]+)"', tag, re.I) or [None, ""])[1]
        if not href or "icon" not in rel:
            continue
        size = re.search(r'sizes="(\d+)x', tag)
        score = (1000 if "apple-touch" in rel else 0) + (int(size.group(1)) if size else 16)
        icons.append((score, html.unescape(href)))
    if icons:
        return _join(url, max(icons)[1]), "icon"
    parts = _split(url)
    return f"{parts.scheme}://{parts.netloc}/favicon.ico", "icon"


def parse(url: str, page: str) -> dict:
    host = _host(_split(url).hostname or "")  # 鏡像網域（18comic.org、jmcomic1.me）用同一個解析規則
    data = PARSERS.get(host, parse_generic)(_defuse(page))
    image = data.get("image_url") or ""
    result = {
        "title": clean_title(data.get("title") or ""),
        "image_url": _join(url, image) if image else "",
        "tags": _unique(data.get("tags") or [])[:60],
    }
    if data.get("categories"):
        result["categories"] = data["categories"]
    if data.get("canonical"):
        result["canonical"] = _join(url, data["canonical"])
    if result["title"] and not result["image_url"]:
        result["image_url"] = known_cover(result.get("canonical") or url)
    return result


def cover_exists(image: str) -> bool:
    """依編號推得的封面在不在（禁漫的封面伺服器：作品存在時回傳圖片，不存在時回傳空白）。連不上時丟出 MetadataError。"""
    try:
        check_url(image)
        request = urllib.request.Request(image, headers={"User-Agent": USER_AGENT})
        with safe_urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.headers.get_content_type().startswith("image/") and bool(response.read(64))
    except urllib.error.HTTPError as error:
        if error.code in (404, 410):
            return False
        raise MetadataError(f"封面伺服器回應 HTTP {error.code}", status=error.code) from error
    except (CoverError, OSError, http.client.HTTPException, ValueError) as error:
        raise MetadataError(f"連線失敗：{getattr(error, 'reason', error)}") from error


# 只輸入作品編號（俗稱「神的語言」）：純數字當成 N 網；N、W、JM 開頭的是對應的漫畫網站。影片與其他網站要完整網址
BARE_CODE = re.compile(r"^(?:(n|w|jm)\s*[#:：-]?\s*|#\s*)?(\d{1,7})$", re.I)


def expand_code(text: str) -> str:
    """作品編號 → 作品頁網址；其他輸入原樣回傳。"""
    match = BARE_CODE.match(text.strip())
    return SHORT_CODE_URLS[(match.group(1) or "n").lower()][1].format(match.group(2)) if match else text.strip()


def work_key(source_name: str, code: str) -> str:
    """判斷「同一件作品」用的編號：同一部影片的不同版本（無碼、字幕）算同一件。"""
    for suffix in VERSION_SUFFIXES.get(source_name, ()):
        if code.lower().endswith(suffix):
            return code[:-len(suffix)]
    return code


def find_existing(db: Session, source_name: str, code: str) -> Resource | None:
    """典藏裡有沒有這件作品；已收錄的是其他版本也算。"""
    base = work_key(source_name, code)
    candidates = [base, *(base + suffix for suffix in VERSION_SUFFIXES.get(source_name, ()))]
    return db.scalars(select(Resource).where(Resource.source_name == source_name, Resource.code.in_(candidates)).limit(1)).first()


def describe(db: Session, url: str, fetch: bool = True, title: str = "", html_page: str = "") -> dict:
    url = expand_code(url)
    if not re.match(r"^https?://", url, re.I):
        raise MetadataError("請貼完整網址，或漫畫編號（N123456、W123456、JM123456，純數字算 N）")
    try:
        urlsplit(url)
    except ValueError as error:
        raise MetadataError("網址格式不正確") from error
    source_name, code = resolve(db, url)
    duplicate = find_existing(db, source_name, code)
    result = {
        "url": url, "source_name": source_name, "code": code, "title": "", "image_url": "", "tags": [],
        "duplicate": {"id": duplicate.id, "title": duplicate.title or duplicate.code} if duplicate else None,
        "fetched": False, "blocked": False, "message": "", "similar": [],
        "tag_changes": [], "tag_dropped": [],  # 收錄預覽：哪些標籤依規則換了名稱、哪些被封鎖略過
        "tag_suggestions": [],  # 典藏還沒有、但可能就是某個現有標籤的新標籤（只提示，不自動改）
    }
    if fetch or html_page:
        try:
            parsed = parse(url, html_page or _get(url))  # html_page：手機 App 在隱藏的瀏覽器讀到的頁面（有防護的網站伺服器讀不到）
            parsed.setdefault("categories", {})
            canonical = parsed.pop("canonical", "")
            if canonical and (key := resolve(db, canonical))[0] == source_name and key[1] != code:  # 章節編號 → 作品的正式編號
                source_name, code = key
                duplicate = find_existing(db, source_name, code)
                result.update(source_name=source_name, code=code,
                              duplicate={"id": duplicate.id, "title": duplicate.title or duplicate.code} if duplicate else None)
            parsed["tags"], result["tag_changes"], result["tag_dropped"] = explain_tag_rules(*tag_rules(db), parsed["tags"])  # 套用你的同義詞與封鎖清單
            result.update(parsed, fetched=True)
            result["tag_suggestions"] = tag_suggest.suggest_for(db, parsed["tags"], parsed["categories"])
            result["similar"] = similar_titles(db, parsed["title"], exclude=duplicate.id if duplicate else None)
        except MetadataError as error:
            result.update(message=str(error), blocked=error.blocked)
    if not result["title"] and title:  # 分享或書籤帶來的頁面標題：去掉網站名稱
        result["title"] = clean_title(title)
    if not result["image_url"]:
        result["image_url"] = known_cover(url)
    return result


def similar_titles(db: Session, title: str, exclude: int | None = None, limit: int = 3) -> list[dict]:
    """標題（去掉【】[] 裡的社團、語言標記後）相同的既有作品，用來提醒「可能是同一本的不同版本」。"""
    from .duplicates import _title_key  # 避免循環匯入

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

    from .duplicates import _title_key

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
