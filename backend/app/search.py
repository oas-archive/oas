"""典藏館的搜尋：拆詞、寬鬆比對（全形半形、大小寫、簡繁、異體字、同義詞）與相關度排序。

參考大型網站的做法（Algolia、電商站內搜尋）：
- 輸入用空白分成好幾個詞，每個詞都要符合（AND）；`-詞` 排除、`"整句"` 當成一個詞。
- 比對前先把兩邊「摺疊」成同一種寫法，搜「star harbor」（同義詞）或「星港传说」（簡體）都能找到「星港傳說」。
- 依符合的欄位與方式加權排序：代碼、標題完全相同最前面，再來是標題開頭、標籤完全相同、標題包含…筆記最後。
- 找不到時，拿英數詞和現有標籤、標題的單字比對，差一個字母的當成「你是不是要找」。
典藏只有幾千件，全部在 Python 裡算即可（手機上也一樣），不需要全文索引。
"""
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .archive import resolve_alias, tag_rules
from .database import Resource, SessionLocal, Tag, resource_tags
from .titles import short_title
from .zhconv import TABLE, VARIANTS

logger = logging.getLogger(__name__)
_FOLD = str.maketrans(TABLE)
_TOKEN = re.compile(r'(-?)"([^"]+)"|(\S+)')
_WORD = re.compile(r"[a-z0-9]+")

# 每個詞在各欄位的得分：取最高的一項
EXACT_CODE, EXACT_TITLE, EXACT_TAG, PREFIX_TITLE, PREFIX_TAG, IN_TITLE, IN_TAG, IN_SOURCE, IN_NOTE = 100, 90, 70, 60, 45, 40, 30, 15, 10


def fold(text: str) -> str:
    """比對用的寫法：全形轉半形、小寫、簡體與日文新字體轉繁體、異體字統一、連續空白變一個。"""
    text = unicodedata.normalize("NFKC", text or "").lower().translate(_FOLD).translate(VARIANTS)
    return " ".join(text.split())


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)      # 原文（顯示與「你是不是要找」用）
    excluded: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.terms and not self.excluded


def parse(q: str) -> Query:
    """拆詞：空白分開；"整句" 不拆；開頭是 - 的詞（例如 -NTR）表示排除。單獨一個 - 當成普通字。"""
    query = Query()
    for minus, phrase, word in _TOKEN.findall(q or ""):
        text, negative = (phrase, bool(minus)) if phrase else (word[1:], True) if word.startswith("-") and len(word) > 1 else (word, False)
        text = text.strip()
        if text:
            (query.excluded if negative else query.terms).append(text)
    return query


@dataclass
class _Doc:
    id: int
    updated: float
    code: str
    title: str
    short: str
    tags: list[str]
    source: str
    note: str


@lru_cache(maxsize=50_000)
def _fold_cached(text: str) -> str:
    return fold(text)


@lru_cache(maxsize=20_000)
def _short_folded(title: str) -> str:
    return fold(short_title(title))


def _docs(db: Session, ids: set[int] | None = None) -> list[_Doc]:
    """只讀比對要用的欄位（不載入整個作品物件），摺疊結果與短標題用快取，典藏幾千件每次只要幾十毫秒。"""
    names: dict[int, list[str]] = {}
    for resource_id, name in db.execute(select(resource_tags.c.resource_id, Tag.name).join(Tag, Tag.id == resource_tags.c.tag_id)):
        names.setdefault(resource_id, []).append(_fold_cached(name))
    rows = db.execute(select(Resource.id, Resource.updated_at, Resource.code, Resource.title, Resource.source_name, Resource.note))
    return [
        _Doc(row.id, row.updated_at.timestamp() if row.updated_at else 0, _fold_cached(row.code or ""), _fold_cached(row.title or ""),
             _short_folded(row.title or ""), names.get(row.id, []), _fold_cached(row.source_name or ""), _fold_cached(row.note or ""))
        for row in rows if ids is None or row.id in ids
    ]


def _variants(term: str, aliases: dict[str, str]) -> set[str]:
    """一個詞的所有寫法：摺疊後的原文，加上同義詞規則換過去的名稱（例如 star harbor → 星港傳說）。"""
    found = {fold(term)}
    for value in (term, fold(term)):
        target = resolve_alias(aliases, value)
        if target.lower() != value.lower():
            found.add(fold(target))
    return {value for value in found if value}


def _score(doc: _Doc, variants: set[str]) -> int:
    best = 0
    for value in variants:
        if doc.code == value:
            return EXACT_CODE
        if value == doc.title or value == doc.short:
            best = max(best, EXACT_TITLE)
        elif value in doc.tags:
            best = max(best, EXACT_TAG)
        elif doc.title.startswith(value) or doc.short.startswith(value):
            best = max(best, PREFIX_TITLE)
        elif any(tag.startswith(value) for tag in doc.tags):
            best = max(best, PREFIX_TAG)
        elif value in doc.title or value in doc.code:
            best = max(best, IN_TITLE)
        elif any(value in tag for tag in doc.tags):
            best = max(best, IN_TAG)
        elif value in doc.source:
            best = max(best, IN_SOURCE)
        elif value in doc.note:
            best = max(best, IN_NOTE)
    return best


def rank(db: Session, q: str, ids: set[int] | None = None) -> list[tuple[int, int]]:
    """符合的作品 id 與分數（高到低，同分時最近更新的在前）；ids 限定只在這些作品裡找；q 是空的就回傳空清單。"""
    query = parse(q)
    if query.empty:
        return []
    aliases, _ = tag_rules(db)
    joined = " ".join(query.terms)
    if len(query.terms) > 1 and fold(resolve_alias(aliases, joined)) != fold(joined):
        query.terms = [joined]  # 整串剛好是同義詞的原文（例如 star harbor 沒加引號），當成一個詞
    wanted = [_variants(term, aliases) for term in query.terms]
    unwanted = [_variants(term, aliases) for term in query.excluded]
    hits = []
    for doc in _docs(db, ids):
        if any(_score(doc, variants) for variants in unwanted):
            continue
        scores = [_score(doc, variants) for variants in wanted]
        if all(scores):
            hits.append((doc, sum(scores)))
    hits.sort(key=lambda hit: (-hit[1], -hit[0].updated, -hit[0].id))
    return [(doc.id, score) for doc, score in hits]


def load_ordered(db: Session, ids: list[int]) -> list[Resource]:
    """依 ids 的順序載入作品（含標籤）。"""
    found = {resource.id: resource for resource in db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(ids)))}
    return [found[resource_id] for resource_id in ids if resource_id in found]


def match_ids(db: Session, q: str) -> list[int]:
    return [resource_id for resource_id, _ in rank(db, q)]


def _distance_one(a: str, b: str) -> bool:
    """兩個字串只差一個字母（多一個、少一個、換一個或相鄰兩個對調）。"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


def tag_matcher(db: Session, text: str):
    """比對標籤名稱（整串輸入，例如「star harbor」）：同義詞規則換得到的名稱也算。
    回傳的函式給標籤名稱，得到（3 完全相同／2 開頭／1 包含／0 不符，同義詞命中時的原文）。"""
    text = text.strip().lstrip("-")
    aliases, _ = tag_rules(db)
    folded, target = fold(text), fold(resolve_alias(aliases, text))

    def match(name: str) -> tuple[int, str | None]:
        key = fold(name)
        level = 3 if key in (folded, target) else 2 if key.startswith(folded) else 1 if folded in key else 0
        return level, text if key == target != folded else None
    return match


def did_you_mean(db: Session, q: str) -> str | None:
    """找不到作品時：把拼錯一個字母的英數詞換成典藏裡真的有的字（例如 stra harbor → star harbor）。"""
    query = parse(q)
    words: dict[str, int] = {}
    for doc in _docs(db):
        for text in (doc.title, *doc.tags):
            for word in _WORD.findall(text):
                words[word] = words.get(word, 0) + 1
    changed, fixed = False, []
    for term in query.terms:
        parts = []
        for part in fold(term).split(" "):
            if len(part) >= 4 and _WORD.fullmatch(part) and part not in words:
                close = sorted((word for word in words if _distance_one(part, word)), key=lambda word: -words[word])
                if close:
                    part, changed = close[0], True
            parts.append(part)
        fixed.append(" ".join(parts))
    if not changed:
        return None
    text = " ".join(f'"{term}"' if " " in term else term for term in fixed)
    return " ".join([text, *(f"-{term}" for term in query.excluded)])


def warm_up() -> None:
    """啟動時在背景先把所有標題摺疊、算好短標題，第一次搜尋不用等（失敗不影響服務）。"""
    try:
        with SessionLocal() as db:
            _docs(db)
    except Exception:  # noqa: BLE001
        logger.exception("搜尋快取預熱失敗")
