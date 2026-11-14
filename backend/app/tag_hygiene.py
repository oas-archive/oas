"""讀作品頁得到的標籤先過一道：擁有者 2026-11-10 回報「框框裡的字、DL版、超長的敘述被當成標籤」。

- 名稱類（作者、社團、原作、角色…，網站有給分類的）：名稱本身再長也保留（作品名的羅馬拼音本來就長）；作者、社團去掉後面括號裡的別名，角色、原作的括號是在區分作品，保留。
  「甲乙丙子 (HEIKO、甲乙桃子) (甲乙ヘイコ（…）)」→「甲乙丙子」、「月影工房（サンプル企画）」→「月影工房」。
- 一般標籤：同樣去掉括號別名；含【】[] 這類框框、是版本或語言標記（DL版、Digital…）、翻譯組名（…漢化）、
  或太長（多半是作品標題、系列名或宣傳句）就不收。
- 「太長」分兩種（2026-11-11）：含中日韓文字的，去掉空白超過 MAX_CHARS 個字就不收（11/10 回報的標題、系列名、宣傳句都是中日文）；
  純英數的名稱一個字只算一個字母、多詞標籤本來就長（double vaginal penetration 有 24 個字母），20 字上限會把網站明確給的
  正常標籤丟掉，所以改看詞數與字數：超過 MAX_WORDS 個詞或 MAX_LATIN_CHARS 個字才當成整句英文宣傳不收。
- 同人展場次（2026-11-11 擁有者回報 C102 這類被收成標籤）：整個名稱就是場次才不收（C99、c102、Ｃ１０２、C102夏、C102新刊、
  COMIC1☆20、例大祭20、サンクリ65、コミティア140、紅楼夢19、こみトレ40）；CG、3D 這類一般標籤不受影響。
  alternatives.search_query 與 titles 是依括號位置去掉場次，沒有可共用的場次規則，所以寫在這裡（EVENT）。
- 罩杯尺寸（2026-11-11 擁有者：對作品沒有意義）：整個名稱是一個字母加 cup／カップ／罩杯才不收（C cup、G-cup、Fカップ、E罩杯）；
  CG、cupless 這類不受影響（CUP_SIZE）。
"""
import re
import unicodedata

MAX_CHARS = 20  # 含中日韓文字的標籤
MAX_WORDS = 5  # 純英數的標籤：正常的英文標籤最多三四個詞，整句宣傳才會更多
MAX_LATIN_CHARS = 40
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯＀-￯]")  # 假名、漢字、韓文、全形字
KEEP_PARENS = {"character", "parody"}
_ALIAS = re.compile(r"\s*[\(（].*$")
_FRAME = re.compile(r"[\[\]【】〔〕［］]")
_VERSION = re.compile(r"^(?:DL版|Digital|Decensored|Uncensored|無修正|无修正|Ongoing|Colorized|Full Color|フルカラー|全彩版|整合版|雜誌|杂志)$", re.I)
_GROUP = re.compile(r"漢化|汉化|翻訳|翻译|翻譯|重嵌|嵌字")
# 比對前先做 NFKC，全形「Ｃ１０２」也算
EVENT = re.compile(r"^(?:(?:C|コミケ|コミックマーケット)\s?\d{2,3}(?:夏|冬)?|COMIC1☆\s?\d{1,2}|(?:博麗神社)?例大祭\s?\d{1,2}"
                   r"|サンクリ\s?\d{1,4}|コミティア\s?\d{2,3}|紅楼夢\s?\d{1,2}|こみトレ\s?\d{1,2})(?:\s?新刊)?$", re.I)
CUP_SIZE = re.compile(r"^[A-Z][\s\-]?(?:cup|カップ|罩杯)$", re.I)  # 同樣先做 NFKC


def _length(name: str) -> int:
    return len(re.sub(r"\s+", "", name))


def _too_long(name: str) -> bool:
    if _CJK.search(name):
        return _length(name) > MAX_CHARS
    return len(re.split(r"[\s\-_/]+", name.strip())) > MAX_WORDS or _length(name) > MAX_LATIN_CHARS


def is_event(name: str) -> bool:
    """整個名稱就是同人展場次（C102、例大祭20…）。"""
    return bool(EVENT.match(unicodedata.normalize("NFKC", name).strip()))


def is_cup_size(name: str) -> bool:
    """整個名稱就是罩杯尺寸（C cup、Fカップ…）。"""
    return bool(CUP_SIZE.match(unicodedata.normalize("NFKC", name).strip()))


def junk(name: str) -> bool:
    """一般標籤不該收的：框框、版本標記、翻譯組、同人展場次、罩杯尺寸、太長。"""
    return bool(_FRAME.search(name) or _VERSION.match(name) or _GROUP.search(name)) or is_event(name) or is_cup_size(name) or _too_long(name)


def clean(tags: list[str], categories: dict[str, str] | None = None) -> tuple[list[str], dict[str, str]]:
    """回傳（整理後的標籤, 分類）；分類的鍵跟著換成整理後的名稱。"""
    categories = categories or {}
    result, kept = [], {}
    for raw in tags:
        name = (raw or "").strip()
        category = categories.get(name, "tag")
        short = _ALIAS.sub("", name).strip()
        # 角色、原作的括號是在區分作品（「警衛（某作品）」），不是別名，保留
        if short and not short.startswith(("(", "（")) and category not in KEEP_PARENS:
            name = short
        if not name:
            continue
        if category == "tag" and junk(name):
            continue
        if (raw or "").strip() in categories:  # 網站有明確給分類的（含 tag）照原樣保留
            kept[name] = category
        result.append(name)
    return result, kept
