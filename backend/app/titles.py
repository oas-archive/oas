"""顯示用的短標題：卡片、書架、側欄、搜尋建議用，原始標題（resources.title）不改。

搜尋、重複作品比對、備份都照用原始標題；作品頁主標題用短標題，完整標題在下方小字。
每一步修剪後若只剩不到 MIN_CHARS 個字（不算標點），就不套用那一步，最後太長才在字詞邊界截斷加「…」。
"""
import re
import unicodedata

from .sites import TITLE_NOISE_WORDS, TITLE_SITE_NAMES

MIN_CHARS = 2
MAX_WIDTH = 48  # 中日文字算 2、其他算 1；約等於卡片兩行

# 網站加在標題前後的名稱、標語（「… - Jable.TV | 免費高清AV在線看」「… | Pornhub」「… - 列表」）
_SITE = re.compile(
    rf"\.(?:com|tv|net|me|org|cc|to|xxx|ws)\b|^(?:列表|{TITLE_SITE_NAMES}|免費.*|J片.*|日本在線動畫.*)$|\bPorn$", re.I)
_SITE_SPLIT = re.compile(r"\s+[-|｜]\s+|\s*[|｜]\s*")
_SHORT_LABEL = 6  # 網站名稱前面的短分類（「貼圖 - 清涼寫真」「完結動畫全集」）一起去掉

# 翻譯組、語言、版本標記：括號裡出現這些字就整段去掉（不論在標題哪裡）
_NOISE = re.compile(
    r"漢化|汉化|翻[訳译譯]|中[文国國]|字幕|重嵌|個人|个人|整合|嵌字|"
    r"^(?:DL版|Digital|Chinese|English|Japanese|Korean|Decensored|Uncensored|無修正|无修正|無碼|无码|Ongoing|Colorized|"
    rf"Full Color|フルカラー|全彩版?|{TITLE_NOISE_WORDS}|整合版|無修|雜誌|杂志)$", re.I)
_BRACKET = r"[\[【〔［][^\[\]【】〔〕［］]*[\]】〕］]"
_PAREN = r"[\(（][^()（）]*[\)）]"
_CODE = re.compile(r"^[A-Za-z]{2,}\d*[-_]\d{2,}$")  # [ABCD-123]：開頭的番號不是社團（活動代號 (C102) 不算）
# 只出現在簡體或只出現在繁體的常用字，用來在兩段中文譯名裡挑繁體那段
_SIMP = set("个们这说为发会来时对过还没样爱经学关将从见里么现与开电话后边头间应长问东车门实听书写让给认点乐种气马鸟龙汉组译选读语妈恋战亲宝脑难无师妇猎辅恶缠满欢觉丽兴梦护绝")
_TRAD = set("個們這說為發會來時對過還沒樣愛經學關將從見裡麼現與開電話後邊頭間應長問東車門實聽書寫讓給認點樂種氣馬鳥龍漢組譯選讀語媽戀戰親寶腦難無師婦獵輔惡纏滿歡覺麗興夢護絕")
_VOLUME = re.compile(r"^\s*(?:vol|volume|part|no|ch|ep|episode|第)?\.?\s*\d+\s*(?:話|话|巻|卷|集|章|部)?\s*$", re.I)


def _chars(text: str) -> int:
    return len(re.sub(r"[\W_]+", "", text))


def _width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in "WF" else 1


def _cjk(text: str) -> bool:
    return bool(re.search(r"[぀-ヿ㐀-鿿]", text))


def _chinese(text: str) -> bool:
    """有漢字、沒有假名：多半是中文譯名（「羅馬拼音 | 中文譯名」的中文那段）。"""
    return bool(re.search(r"[㐀-鿿]", text)) and not re.search(r"[぀-ヿ]", text)


def _strip_site(title: str) -> str:
    pieces = _SITE_SPLIT.split(title)
    parts, seps = pieces, _SITE_SPLIT.findall(title)
    if len(parts) < 2:
        return title
    start = 1 if _SITE.search(parts[0].strip()) and not _cjk(parts[0]) else 0  # 「Netflav - 標題」
    end = next((index for index in range(start + 1, len(parts)) if _SITE.search(parts[index].strip())), len(parts))
    if end < len(parts):
        while end - 1 > start and len(parts[end - 1].strip()) <= _SHORT_LABEL:
            end -= 1
    # 用原文的分隔符號接回保留的幾段
    kept = parts[start]
    for index in range(start + 1, end):
        kept += seps[index - 1] + parts[index]
    return kept


def _strip_brackets(title: str) -> str:
    # 任何位置的翻譯組、語言、版本標記
    for pattern in (_BRACKET, _PAREN):
        cleaned = re.sub(rf"\s*{pattern}", lambda m: "" if _NOISE.search(m.group(0).strip()[1:-1].strip()) else m.group(0), title)
        title = cleaned if _chars(cleaned) >= MIN_CHARS else title
    # 開頭的活動代號、社團／作者：(C101) [社團 (作者)] (オリジナル)
    while True:
        match = re.match(rf"\s*(?:{_BRACKET}|{_PAREN})\s*", title)
        if not match or _CODE.match(match.group(0).strip()[1:-1].strip()) or _chars(title[match.end():]) < MIN_CHARS:
            break
        title = title[match.end():]
    # 結尾的作者、原作、刊名：[作者甲] (Star Harbor) (COMIC 某刊 2022年5月号)；卷數 (2) 保留
    while True:
        match = re.search(rf"\s*(?:{_BRACKET}|{_PAREN})\s*$", title)
        if not match or _VOLUME.match(match.group(0).strip()[1:-1]) or _chars(title[:match.start()]) < MIN_CHARS:
            break
        title = title[:match.start()]
    return title


def _traditional(text: str) -> int:
    return sum(char in _TRAD for char in text) - sum(char in _SIMP for char in text)


def _best(parts: list[str]) -> str:
    # 中文那段要以漢字為主（「A Traveler's Daily Life-一個旅人的日常生活」這種夾英文的不算）
    chinese = [part for part in parts if _chinese(part) and len(re.findall(r"[㐀-鿿]", part)) * 2 >= _chars(part)]
    if chinese:
        return max(chinese, key=_traditional)  # 同分取前面那段
    return next((part for part in parts if _cjk(part)), parts[0])


def _pick_part(title: str) -> str:
    """多段譯名只留一段：優先中文（繁體優先），其次有中日文的那段，都沒有就第一段。

    「|」一定是分段；禁漫常見的「中文譯名 [社團] 日文原名」以中間的方括號分段；
    「/」只在至少一段是中文時才分（「かそう101/かそうのほん…」這種是標題本身），開頭是番號的不分。
    """
    parts = [part.strip() for part in re.split(r"\s*[|｜]\s*", title) if _chars(part) >= MIN_CHARS]
    title = _best(parts) if len(parts) > 1 else title
    parts = [part.strip() for part in re.split(r"\s*\[[^\[\]]*\]\s*", title) if _chars(part) >= MIN_CHARS]
    if len(parts) > 1 and not title.startswith("[") and any(map(_chinese, parts)) and any(re.search(r"[぀-ヿ]", part) for part in parts):
        title = _strip_brackets(_best(parts))
    parts = [part.strip() for part in re.split(r"\s*[/／]\s*", title) if _chars(part) >= MIN_CHARS]
    if len(parts) > 1 and not re.match(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*-\d{3,}\b", title) and any(_chinese(part) for part in parts):
        title = _best(parts)
    return title


def _truncate(title: str) -> str:
    width = sum(_width(char) for char in title)
    if width <= MAX_WIDTH * 1.25:  # 只超過一點點的不截，截掉兩三個字換一個「…」沒有意義
        return title
    used, cut = 0, 0
    for index, char in enumerate(title):
        used += _width(char)
        if used > MAX_WIDTH - 1:  # 留一格給「…」
            break
        cut = index + 1
    head = title[:cut]
    # 往回找字詞邊界（空白、標點），不要退太多
    boundary = max((m.end() for m in re.finditer(r"[\s、，,。.!！?？~～・:：/\-]+", head)), default=0)
    if boundary >= cut * 0.6:
        head = head[:boundary]
    return head.rstrip(" 、，,。.・:：/-~～") + "…"


def short_title(title: str) -> str:
    original = re.sub(r"\s+", " ", (title or "").strip())
    text = original
    for step in (_strip_site, _strip_brackets, _pick_part):
        trimmed = re.sub(r"^(?:[-|｜/]+\s+)+|(?:\s+[-|｜/]+)+$", "", re.sub(r"\s+", " ", step(text)).strip())
        # 只剩括號標記（「[Digital]」）也不算有意義
        if _chars(trimmed) >= MIN_CHARS and not re.fullmatch(rf"(?:\s*(?:{_BRACKET}|{_PAREN}))+\s*", trimmed):
            text = trimmed
    return _truncate(text)
