"""禁漫換編號偵測與候選（方案 B，只在手機版「背景讀取」開著時由前端呼叫；電腦版不變，巡檢仍略過禁漫）。

禁漫會換作品編號，舊網址失效；電腦直接連會被 Cloudflare 擋，所以讀頁交給手機 App 的看不到的瀏覽器（pageReader.js）。
這裡只管「讀回來的 HTML 怎麼判斷」，不連外，也不嘗試繞過驗證：讀到挑戰頁、空白或逾時就當作這次沒讀到，不標失效。

發現換了編號
- 每輪最多 ROUND_LIMIT（10）件：超過 7 天沒巡檢的禁漫作品，依「沒巡檢過的先、最久沒查的先」取（`targets`）。
- 前端把舊作品頁讀回來（HTML 開頭帶 App 外殼加的 `<!--oas-url:最後的網址-->`），`record` 判斷：
  dead＝最後的網址是 MISSING_PAGES 認得的錯誤頁（/error/album_missing）→ 標 link_dead 並記巡檢時間；
  alive＝有作品標題（parse_jm）→ 只記巡檢時間；
  unknown＝頁面載入了但認不出來（例如未開放的作品轉到首頁、舊版 App 沒帶最後網址）→ 只記巡檢時間，不標失效
  （不記的話下一輪又排在最前面，永遠輪不到後面的作品）；
  challenge＝空白、防護驗證頁（Just a moment…）→ 什麼都不記，前端停掉這一輪剩下的禁漫。
- 只有明確的「作品不存在」才標失效，其餘一律不動。

找新編號候選
- 前端讀禁漫搜尋頁（關鍵字沿用待整理的 jmQuery），`parse_search` 解析：作品連結 `/album/<編號>/`，標題在 `.video-title`
  （沒有 `.video-title` 時退而用連結的 title 屬性）；一張卡片的範圍是這個連結到下一個不同編號的連結之前，找不到標題的不列。
- `similarity` 排序（0～1，越高越像）：標題先去掉 [社團]、【】、(場次) 等括號並做 `search.fold`（全半形、大小寫、簡繁、異體字）、
  只留文字與數字；兩邊各取「整串」與「用 / ｜ | 切開的每一段」的最大值。分數＝ 0.5×SequenceMatcher 比例 ＋ 0.5×字元二連詞的 Dice 係數；
  一邊（至少 4 字）完整包含在另一邊時，分數至少是 0.6 ＋ 0.3×（短的長度／長的長度）。
- 回傳前 CANDIDATE_LIMIT（5）名、相似度至少 MIN_SIMILARITY（0.2）的候選，每個附：已在典藏的 owned_id、依編號推得的封面。
  不自動替換：換編號由使用者在畫面按確認，走既有的 `/api/resources/{id}/relink`（可復原）。
"""
import html as htmllib
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from urllib.parse import unquote

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import linkcheck, metadata, refresh
from .database import Resource, Source
from .search import fold
from .site_parsers import parse_jm
from .sites import JM_SOURCE, MISSING_PAGES

ROUND_LIMIT = 10  # 一輪最多交給手機讀幾件舊作品頁：保守起見，先不要一次讀太多
CANDIDATE_LIMIT = 5
MIN_SIMILARITY = 0.2

URL_MARK = re.compile(r"<!--oas-url:([^>]*?)-->")
CHALLENGE_TITLE = re.compile(r"just a moment|attention required|請稍候|请稍候|checking your browser", re.I)
CHALLENGE_BODY = re.compile(r"enable javascript and cookies to continue|verify you are human", re.I)
ALBUM_LINK = re.compile(r'<a\b[^>]*?href="(?:https?://[^"/]+)?/album/(\d+)(?:[/?#][^"]*)?"[^>]*>', re.I)
TITLE_NODE = re.compile(r'class="[^"]*\bvideo-title\b[^"]*"[^>]*>(.*?)</', re.S | re.I)
TITLE_ATTR = re.compile(r'\btitle="([^"]*)"', re.I)
TAGS = re.compile(r"<[^>]+>")
BRACKETS = re.compile(r"\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）")
SEGMENTS = re.compile(r"\s*[/｜|]\s*")


# ---------- 發現換了編號 ----------

def targets(db: Session, limit: int = ROUND_LIMIT) -> list[tuple[int, str]]:
    """這一輪要讀的禁漫作品 [(作品 id, 作品頁網址)]：超過巡檢間隔沒查、還沒標失效的，最多 limit 件。"""
    template = db.scalar(select(Source.template).where(Source.name == JM_SOURCE)) or ""
    cutoff = datetime.now(timezone.utc) - timedelta(days=linkcheck.INTERVAL_DAYS)
    rows = db.execute(
        select(Resource.id, Resource.code, Resource.source_name)
        .where(Resource.source_name == JM_SOURCE, Resource.link_dead.is_(False), Resource.kind != metadata.SITE_KIND,
               or_(Resource.link_checked_at.is_(None), Resource.link_checked_at < cutoff))
        .order_by(Resource.link_checked_at.is_not(None), Resource.link_checked_at, Resource.id)
        .limit(limit)
    ).all()
    result = []
    for row in rows:
        url = refresh.page_url(row, template)
        if url.startswith(("http://", "https://")):
            result.append((row.id, url))
    return result


def is_challenge(page: str) -> bool:
    """空白或防護驗證頁（Cloudflare 的「Just a moment…」等）。"""
    if not (page or "").strip():
        return True
    return bool(CHALLENGE_TITLE.search(metadata._title_element(page)) or CHALLENGE_BODY.search(page[:30000]))


def final_url(page: str) -> str:
    """App 外殼在 HTML 前面加的最後網址（轉址之後）；舊版 App 沒有就回空字串。"""
    mark = URL_MARK.search(page[:2000])
    return unquote(mark.group(1)) if mark else ""


def classify_page(page: str) -> str:
    """challenge／dead／alive／unknown，意思見檔頭。"""
    if is_challenge(page):
        return "challenge"
    if any(marker in final_url(page) for marker, _ in MISSING_PAGES):
        return "dead"
    if parse_jm(page).get("title", "").strip():
        return "alive"
    return "unknown"


def record(db: Session, resource_id: int, page: str) -> dict:
    """手機讀回舊作品頁：判斷並記下巡檢時間（dead 另標失效）；challenge 什麼都不記。作品不存在或不是禁漫時丟 LookupError。"""
    row = db.get(Resource, resource_id)
    if row is None or row.source_name != JM_SOURCE:
        raise LookupError("找不到這件作品")
    result = classify_page(page)
    if result != "challenge":
        values = {"link_checked_at": datetime.now(timezone.utc), "updated_at": row.updated_at}  # 不動 updated_at，書庫排序不變
        if result == "dead":
            values["link_dead"] = True
        db.execute(Resource.__table__.update().where(Resource.id == resource_id).values(**values))
        db.commit()
    return {"result": result}


# ---------- 找新編號候選 ----------

def parse_search(page: str) -> list[dict]:
    """禁漫搜尋結果頁 → [{code, title}]（依頁面順序、編號不重複、沒有標題的不列）。"""
    links = list(ALBUM_LINK.finditer(page or ""))
    rows, seen = [], set()
    for index, link in enumerate(links):
        code = link.group(1)
        if code in seen:
            continue
        end = next((later.start() for later in links[index + 1:] if later.group(1) != code), len(page))  # 這張卡片到下一個不同編號的連結為止
        node = TITLE_NODE.search(page[link.start():end])
        raw = TAGS.sub("", node.group(1)) if node else (TITLE_ATTR.search(link.group(0)) or [None, ""])[1]
        title = re.sub(r"\s+", " ", htmllib.unescape(raw)).strip()
        if title:
            seen.add(code)
            rows.append({"code": code, "title": title})
    return rows


def _plain(title: str) -> str:
    return re.sub(r"[\W_]+", "", fold(BRACKETS.sub(" ", htmllib.unescape(title or ""))))


def _bigrams(text: str) -> Counter:
    return Counter(text[i:i + 2] for i in range(len(text) - 1)) if len(text) > 1 else Counter({text: 1})


def _score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    first, second = _bigrams(a), _bigrams(b)
    dice = 2 * sum((first & second).values()) / (sum(first.values()) + sum(second.values()))
    score = 0.5 * SequenceMatcher(None, a, b).ratio() + 0.5 * dice
    short, long = sorted((a, b), key=len)
    if len(short) >= 4 and short in long:
        score = max(score, 0.6 + 0.3 * len(short) / len(long))
    return score


def _variants(title: str) -> set[str]:
    stripped = BRACKETS.sub(" ", title or "")
    return {_plain(title), *(_plain(part) for part in SEGMENTS.split(stripped))} - {""}


def similarity(old: str, new: str) -> float:
    """兩個標題的相似度 0～1（做法見檔頭）。"""
    return max((_score(a, b) for a in _variants(old) for b in _variants(new)), default=0.0)


def candidates(db: Session, resource_id: int, page: str) -> dict:
    """搜尋頁讀回來之後排出候選：{"status": ok／empty／blocked, "candidates": [...]}。blocked＝防護驗證頁，現在讀不到。"""
    resource = db.get(Resource, resource_id)
    if resource is None or resource.source_name != JM_SOURCE:
        raise LookupError("找不到這件作品")
    if is_challenge(page):
        return {"status": "blocked", "candidates": []}
    template = db.scalar(select(Source.template).where(Source.name == JM_SOURCE)) or ""
    owned = {code: rid for code, rid in db.execute(select(Resource.code, Resource.id).where(Resource.source_name == JM_SOURCE))}
    ranked = []
    for row in parse_search(page):
        if row["code"] == resource.code.strip():
            continue  # 舊編號自己（還在搜尋結果裡就不是換編號）
        score = similarity(resource.title, row["title"])
        if score < MIN_SIMILARITY:
            continue
        url = template.replace("{}", row["code"])
        ranked.append({"source_name": JM_SOURCE, "code": row["code"], "title": row["title"], "url": url, "thumb": metadata.known_cover(url),
                       "similarity": round(score, 2), "owned_id": owned.get(row["code"])})
    ranked.sort(key=lambda row: -row["similarity"])
    top = ranked[:CANDIDATE_LIMIT]
    return {"status": "ok" if top else "empty", "candidates": top}
