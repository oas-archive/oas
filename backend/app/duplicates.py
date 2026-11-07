"""重複作品：找出標題相同或相近、網址相同、封面相同（含不同網址但幾乎一樣的圖）的作品組。

標題先正規化（全半形、大小寫、去掉括號裡的社團／漢化組／語言標記），再拆成「|」「/」分隔的各段譯名分別比對，
所以「羅馬拼音 | 中文譯名」與只有羅馬拼音的另一個翻譯版本也會被抓到。
封面以 64 位元的差異雜湊（dHash）比對，雜湊在封面快取時算好存在 cover_cache.fingerprint。
比對都先分桶，只在同一桶裡兩兩比較，典藏數千件也不會每次請求都做 O(n²) 的計算。
"""
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import cache
from .covers import url_hash
from .metadata import SITE_KIND, icon_url
from .database import CoverCache, DuplicateDismissal, Resource

MIN_KEY = 6  # 太短的標題（或其中一段）不比，以免誤判
COMMON_KEY = 4  # 同一段標題出現在這麼多件以上時，多半是網站名稱、標語，不拿來比
SIMILAR_RATIO = 0.9  # 兩段標題的相似度（difflib）達到這個比例視為相近
COVER_DISTANCE = 7  # 兩張封面的 dHash 相差這麼多位元以內視為同一張圖（實測同圖 0～7、不同圖 11 以上）
COVER_DIFFERENT = 12  # 相差這麼多位元以上就是明顯不同的圖：標題相同或相近也不算重複（同名的不同版本、不同漢化）

_BRACKETS = r"[\[\(【{〔].*?[\]\)】}〕]"
_OPEN_TAIL = r"[\[\(【{〔][^\]\)】}〕]*$"  # 被截斷、沒有收尾的括號，例如標題結尾的「[Chine」
_SPLIT = re.compile(r"\s*[|/]\s*")
_VOLUME_MARKS = set("上中下前後后續续完全外")
_SITE_TAIL = re.compile(r"(\s+comics|\s+-\s+列表)\s*$")  # 禁漫天堂、紳士漫畫的標題結尾
# 括號裡的卷數、集數（[Case. 7]、(2)、【第3話】）：比對前括號會整段去掉，所以另外記下來，數字不同的不算同一本
_VOLUME_BRACKET = re.compile(r"[\[\(【〔]\s*(?:case|vol|volume|part|no|ch|chapter|ep|episode|第)?\s*\.?\s*(\d+)\s*(?:話|话|巻|卷|集|章|部)?\s*[\]\)】〕]", re.I)


# 括號裡的前後編（(Zenpen)、【後編】）：同樣會被去掉，另外記下來
_PART_BRACKET = re.compile(r"[\[\(【〔]\s*(zenpen|chuuhen|chuhen|kouhen|kohen|kanketsuhen|前編|中編|後編|后编|前篇|中篇|後篇|后篇|完結編|完结篇|上|中|下)\s*[\]\)】〕]", re.I)
# 段落結尾的羅馬數字或數字（Tooi Hoshi no Uta. III、Ⅱ、早餐X雪2）：只差這幾個字時會被當成寫法差異，其實是不同集；
# 「羅馬拼音 | 中文」只有其中一段寫了集數也算（上傳者常只在譯名加上 2）
_ROMAN_TAIL = re.compile(r"(?<![a-z0-9])(i{1,3}|iv|vi{0,3}|ix|x|\d{1,3})\W*$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
# 影片番號（ABCD-191、FC2-PPV-1234567）：兩件都有番號又不一樣，就一定是不同作品
_CODE = re.compile(r"(?<![a-z0-9])(fc2-?ppv-?\d{5,8}|[a-z]{2,6}-\d{2,5})(?!\d)", re.I)
_PART_NAMES = {"zenpen": "前", "前編": "前", "前篇": "前", "上": "前", "chuuhen": "中", "chuhen": "中", "中編": "中", "中篇": "中", "中": "中",
               "kouhen": "後", "kohen": "後", "後編": "後", "后编": "後", "後篇": "後", "后篇": "後", "下": "後",
               "kanketsuhen": "完", "完結編": "完", "完结篇": "完"}


def _codes(title: str) -> set[str]:
    return {re.sub(r"-", "", code).upper() for code in _CODE.findall(unicodedata.normalize("NFKC", title or ""))}


def _volumes(title: str) -> str:
    """集數記號：括號裡的數字與前後編、各段結尾的羅馬數字、影片番號；兩件不一樣就不算同一本。"""
    text = unicodedata.normalize("NFKC", title or "")
    numbers = _VOLUME_BRACKET.findall(text)
    parts = sorted({_PART_NAMES[word.lower()] for word in _PART_BRACKET.findall(text)})
    romans = sorted({_ROMAN.get(match.group(1)) or int(match.group(1)) for segment in _SPLIT.split(_normalise(title).strip())
                     if (match := _ROMAN_TAIL.search(segment.strip()))})
    return ";".join([",".join(numbers), "".join(parts), ",".join(map(str, romans)), ",".join(sorted(_codes(title)))])


def _apart(a: str, b: str) -> bool:
    """封面相同或相近時也要分開的兩件：番號都有但不同，或集數記號都有但不同（同系列封面常常很像）。"""
    codes_a, codes_b = _codes(a), _codes(b)
    if codes_a and codes_b and not codes_a & codes_b:
        return True
    marks_a, marks_b = _volumes(a).split(";")[:3], _volumes(b).split(";")[:3]
    return any(x and y and x != y for x, y in zip(marks_a, marks_b))


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text)


def _normalise(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()  # 全形轉半形、｜→|、（）→()
    text = _SITE_TAIL.sub("", text)
    text = re.sub(_BRACKETS, " ", text)
    return re.sub(_OPEN_TAIL, " ", text)


def _title_key(title: str) -> str:
    """整個標題的比對鍵（去掉括號標記與標點）；收錄時提醒「可能是同一本」也用這個。"""
    text = _compact(_normalise(title))
    return text if len(text) >= MIN_KEY else ""


def title_keys(title: str) -> tuple[set[str], str]:
    """整個標題，加上「|」「/」分隔的每一段譯名，各自的比對鍵；另外回傳最後一段（有分段時）。

    最後一段可能是網站標語（「… | Watch free Hentai HD」），由 find_pairs 依同來源的出現次數判斷要不要略過。
    """
    text = _normalise(title)
    parts = [_compact(part) for part in _SPLIT.split(text)]
    keys = {key for key in [_compact(text), *parts] if len(key) >= MIN_KEY}
    return keys, parts[-1] if len(parts) > 1 else ""


def _url_key(code: str) -> str:
    if not code.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(code.strip())
    except ValueError:  # 格式壞掉的網址不拿來比對
        return ""
    host = re.sub(r"^www\d*\.", "", parts.netloc.lower())
    return f"{host}{parts.path.rstrip('/')}{'?' + parts.query if parts.query else ''}".lower()


def group_key(ids) -> str:
    return ",".join(str(item_id) for item_id in sorted(set(ids)))


def _similar(a: str, b: str) -> bool:
    # 數字不同多半是不同卷、不同集（「第 1 話」與「第 2 話」），不算重複
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return False
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    # 只多出一段字（「續集」「after」「ni」「外传」）多半是續作或番外，不算重複；
    # 相近指的是個別字不同，例如簡繁體、羅馬拼音寫法（ō／ou）
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "delete") and max(i2 - i1, j2 - j1) > 1:
            return False
        if tag != "equal" and _VOLUME_MARKS.intersection(a[i1:i2] + b[j1:j2]):  # 上／下、前編／後編
            return False
    return matcher.ratio() >= SIMILAR_RATIO


def _real_cover(row: Resource) -> bool:
    """可以拿來比對重複的封面：網站圖示（多件共用同一張，或圖示長得很像）與網站頁面的封面都不算。"""
    return bool(row.image_url) and row.image_kind != "icon" and not icon_url(row.image_url) and row.kind != SITE_KIND


def _cover_hashes(db: Session, resources: list[Resource]) -> dict[int, int]:
    """作品 id → 封面 dHash；網站圖示、還沒快取或還沒算好雜湊的封面略過。"""
    wanted = {row.id: url_hash(row.image_url.strip()) for row in resources if _real_cover(row)}
    if not wanted:
        return {}
    known = dict(db.execute(select(CoverCache.url_hash, CoverCache.fingerprint).where(
        CoverCache.url_hash.in_(set(wanted.values())), CoverCache.status == "ok")).all())
    hashes = {}
    for item_id, key in wanted.items():
        value = known.get(key)
        if value:
            number = int(value, 16)
            if 8 <= bin(number).count("1") <= 56:  # 幾乎全白、全黑或單色的圖分不出差別
                hashes[item_id] = number
    return hashes


def find_pairs(db: Session, resources: list[Resource]) -> list[tuple[int, int, str]]:
    """回傳 (作品 a, 作品 b, 理由)；理由是 title、similar_title、url、cover、cover_image 其中之一。"""
    pairs: list[tuple[int, int, str]] = []
    matched: set[tuple[int, int]] = set()  # 已經因為標題或封面網址相同成組的，不再重複列出「相近」

    hashes = _cover_hashes(db, resources)

    def covers_differ(a: int, b: int) -> bool:
        """兩件都有封面雜湊、而且明顯是不同的圖。封面還沒抓到的不算（只能看標題）。"""
        return a in hashes and b in hashes and bin(hashes[a] ^ hashes[b]).count("1") >= COVER_DIFFERENT

    def exact(reason: str, keys_of) -> None:
        index: dict[str, list[int]] = defaultdict(list)
        for row in resources:
            for key in keys_of(row):
                index[key].append(row.id)
        for ids in index.values():
            if len(ids) > 1:
                # 每件連到前面第一件可以配對的（多半就是第一件）：和以前一樣只列新的那對，標過「不是重複」的不會被串回來
                found = [(next(earlier for earlier in ids[:i] if not (reason == "title" and covers_differ(earlier, b))), b)
                         for i, b in enumerate(ids) if i and any(not (reason == "title" and covers_differ(earlier, b)) for earlier in ids[:i])]
                pairs.extend((a, b, reason) for a, b in found)
                if reason != "url":
                    matched.update((min(a, b), max(a, b)) for a, b in found)

    parsed = {row.id: title_keys(row.title) for row in resources}
    # 同一來源有兩件以上用同一段當結尾，那段是網站標語（「… - Jable.TV | J片 AV看到飽」），不拿來比
    tails: dict[tuple[str, str], int] = defaultdict(int)
    for row in resources:
        if parsed[row.id][1]:
            tails[(row.source_name, parsed[row.id][1])] += 1
    keys: dict[int, set[str]] = {}
    for row in resources:
        item_keys, tail = parsed[row.id]
        keys[row.id] = item_keys - {tail} if tails.get((row.source_name, tail), 0) > 1 else item_keys
    counts: dict[str, int] = defaultdict(int)
    for item_keys in keys.values():
        for key in item_keys:
            counts[key] += 1
    keys = {item_id: {key for key in item_keys if counts[key] < COMMON_KEY} for item_id, item_keys in keys.items()}

    volumes = {row.id: _volumes(row.title) for row in resources}
    exact("title", lambda row: [f"{key}#{volumes[row.id]}" for key in keys[row.id]])
    exact("url", lambda row: [key] if (key := _url_key(row.code)) else [])
    # 網站圖示多件共用同一張，不能當成重複的依據
    exact("cover", lambda row: [row.image_url.strip()] if _real_cover(row) else [])
    titles = {row.id: row.title for row in resources}
    pairs[:] = [pair for pair in pairs if pair[2] != "cover" or not _apart(titles[pair[0]], titles[pair[1]])]

    # 標題相近：以開頭、結尾各 4 個字分桶，只比同一桶裡的
    buckets: dict[str, set[tuple[int, str]]] = defaultdict(set)
    for item_id, item_keys in keys.items():
        for key in item_keys:
            buckets["^" + key[:4]].add((item_id, key))
            buckets["$" + key[-4:]].add((item_id, key))
    seen = set(matched)
    for members in buckets.values():
        members = sorted(members)
        for index, (a, key_a) in enumerate(members):
            for b, key_b in members[index + 1:]:
                if a != b and (a, b) not in seen and key_a != key_b and volumes[a] == volumes[b] and not covers_differ(a, b) and _similar(key_a, key_b):
                    seen.add((a, b))
                    pairs.append((a, b, "similar_title"))

    # 封面圖片：64 位元切成 8 段，相差 7 位元以內的兩張圖至少有一段完全相同（鴿籠原理）
    by_chunk: dict[tuple[int, int], list[int]] = defaultdict(list)
    for item_id, number in hashes.items():
        for chunk in range(8):
            by_chunk[(chunk, (number >> (chunk * 8)) & 0xFF)].append(item_id)
    seen = set(matched)
    for ids in by_chunk.values():
        for index, a in enumerate(ids):
            for b in ids[index + 1:]:
                pair = (min(a, b), max(a, b))
                if pair not in seen and bin(hashes[a] ^ hashes[b]).count("1") <= COVER_DISTANCE and not _apart(titles[a], titles[b]):
                    seen.add(pair)
                    pairs.append((*pair, "cover_image"))
    return pairs


def duplicates(db: Session) -> dict:
    """所有重複組；資料沒變時用上次的結果（手機上全部重算要 0.8 秒，作品頁每次打開都會查）。"""
    return cache.cached("duplicates", lambda: _duplicates(db), copied=False)  # 只讀：作品頁每次打開都會查，不必每次複製整份


def _duplicates(db: Session) -> dict:
    from .insights import _card, _resources  # 避免循環匯入

    resources = _resources(db)
    parent = {row.id: row.id for row in resources}

    def find(item_id: int) -> int:
        while parent[item_id] != item_id:
            parent[item_id] = parent[parent[item_id]]
            item_id = parent[item_id]
        return item_id

    # 「不是重複」以成對關係記住：標過的作品彼此不再算重複；之後有新作品和它們相符，只列新的那對，不會讓整組重新出現
    dismissed = set(db.scalars(select(DuplicateDismissal.key)).all())
    dismissed_sets = [set(int(part) for part in key.split(",") if part.isdigit()) for key in dismissed]
    reasons: dict[int, set[str]] = defaultdict(set)
    for a, b, reason in find_pairs(db, resources):
        if any(a in ids and b in ids for ids in dismissed_sets):
            continue
        parent[find(a)] = find(b)
        reasons[a].add(reason)
        reasons[b].add(reason)

    clusters: dict[int, list[Resource]] = defaultdict(list)
    for row in resources:
        clusters[find(row.id)].append(row)
    groups = []
    for rows in clusters.values():
        if len(rows) < 2 or group_key(row.id for row in rows) in dismissed:
            continue
        found = set().union(*(reasons[row.id] for row in rows))
        groups.append({"key": group_key(row.id for row in rows), "reasons": sorted(found), "items": _card(db, rows)})
    groups.sort(key=lambda group: (-len(group["items"]), group["items"][0]["id"]))
    return {"groups": groups, "dismissed": len(dismissed)}


def dismissed_groups(db: Session) -> list[dict]:
    """標成「不是重複」的組合，可以在重複作品頁撤銷；組內作品已刪到只剩一件的不列出。"""
    from .insights import _card

    groups = []
    for key in db.scalars(select(DuplicateDismissal.key).order_by(DuplicateDismissal.key)).all():
        ids = [int(part) for part in key.split(",") if part.isdigit()]
        rows = db.scalars(select(Resource).options(selectinload(Resource.tags)).where(Resource.id.in_(ids)).order_by(Resource.id)).all()
        if len(rows) >= 2:
            groups.append({"key": key, "items": _card(db, rows)})
    return groups


def duplicates_of(db: Session, item_id: int) -> dict:
    """單一作品所在的重複組（還沒標成「不是重複」的）；作品頁用來提醒「可能和這幾件重複」。"""
    for group in duplicates(db)["groups"]:
        if any(item["id"] == item_id for item in group["items"]):
            return {"key": group["key"], "reasons": group["reasons"], "items": [item for item in group["items"] if item["id"] != item_id]}
    return {"key": "", "reasons": [], "items": []}
