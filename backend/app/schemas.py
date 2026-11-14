"""API 的輸入格式（請求內容的欄位與限制）。"""
from typing import Literal

from pydantic import VERSION, BaseModel, Field, constr

from .archive import DEFAULT_SOURCE

# 手機版（Android）沒有 pydantic v2 需要的 pydantic-core（Rust 編譯），改用純 Python 的 v1；電腦版仍用 v2。
# 兩版只有這幾處寫法不同，其他地方照常使用 BaseModel 與 Field。
PYDANTIC_V1 = VERSION.startswith("1.")

if PYDANTIC_V1:
    from pydantic import validator as _validator
else:
    from pydantic import field_validator as _validator


def Items(*, min_length: int, max_length: int, default=...):  # 與 Field 同樣當成欄位設定使用，所以用大寫開頭
    """清單的件數限制：v2 叫 min_length／max_length，v1 叫 min_items／max_items。default 省略時為必填。"""
    if PYDANTIC_V1:
        return Field(default, min_items=min_length, max_items=max_length)
    return Field(default, min_length=min_length, max_length=max_length)


def Pairs(field: str, *, max_length: int):
    """字典的件數限制（v1 的 Field 不能限制字典大小，兩版都改用驗證函式）。在類別裡寫 check_x = Pairs("x", max_length=…)。"""
    def check(cls, value):
        if len(value) > max_length:
            raise ValueError(f"最多 {max_length} 項")
        return value
    return _validator(field, allow_reuse=True)(check) if PYDANTIC_V1 else _validator(field)(check)


def dump(model: BaseModel, **options) -> dict:
    """model_dump（v2）／dict（v1）。"""
    return model.dict(**options) if PYDANTIC_V1 else model.model_dump(**options)

class PreferencesPatch(BaseModel):
    adult_confirmed: bool | None = None
    save_covers: bool | None = None
    background_reader: bool | None = None


class UserPack(BaseModel):
    """使用者匯入的翻譯包：格式和資料夾裡的 tag_pack_private.json 相同。"""
    translate: dict[str, str] = {}
    suggest: dict[str, str] = {}
    block: list[str] = []


StatusValue = Literal["未讀", "閱讀中", "已完食", "暫時擱置", "棄坑"]

# 標籤名稱與分類（作者、角色…）的長度上限：資料庫欄位是 100 字、分類 20 字，但 SQLite 不強制，
# 已存在的長標籤編輯後原樣送回不能被擋，所以名稱給寬一點，與標題同為 500。
TagName = constr(max_length=500)
TagCategory = constr(max_length=40)


# ---------- 作品 ----------

class ResourceInput(BaseModel):
    code: str = Field(min_length=1, max_length=500)
    source_name: str = Field(default=DEFAULT_SOURCE, min_length=1, max_length=120)
    title: str = Field(default="", max_length=500)
    note: str = ""
    image_url: str = Field(default="", max_length=2000)
    rating: float = Field(default=0, ge=0, le=5)
    status: StatusValue = "未讀"
    kind: str = Field(default="", max_length=20)
    progress: str = Field(default="", max_length=40)
    # 編輯時送回作品現有的全部標籤（合併、多次補資料會累積），件數比照批次操作的 2000
    tags: list[TagName] = Items(min_length=0, max_length=2000, default=[])
    tag_categories: dict[TagName, TagCategory] = {}  # 讀作品頁得到的分類（作者、角色…），只標記還是一般標籤的；不存進作品本身
    check_categories = Pairs("tag_categories", max_length=2000)


class FillBlanksInput(BaseModel):
    """分享或書籤帶來的作品已在典藏中：這次讀到的資料，只補進那一件的空白欄位（見 resources.fill_blanks）。"""
    title: str = Field(default="", max_length=500)
    image_url: str = Field(default="", max_length=2000)
    # 表單上的標籤（分享帶來的最多 60 個、自己加的）與讀作品頁得到的（metadata.parse 最多 60 個）取聯集
    tags: list[TagName] = Items(min_length=0, max_length=300, default=[])
    tag_categories: dict[TagName, TagCategory] = {}
    check_categories = Pairs("tag_categories", max_length=300)


class QuickInput(BaseModel):
    rating: float | None = Field(default=None, ge=0, le=5)
    status: StatusValue | None = None
    link_dead: bool | None = None
    progress: str | None = Field(default=None, max_length=40)


class RelinkInput(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=500)


class BatchInput(BaseModel):
    ids: list[int] = Items(min_length=1, max_length=2000)
    action: Literal["status", "rating", "add_tags", "remove_tags", "kind", "delete"]
    status: StatusValue | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    tags: list[str] = []
    kind: str | None = Field(default=None, max_length=20)


class MergeInput(BaseModel):
    ids: list[int] = Items(min_length=1, max_length=50)


# ---------- 收錄 ----------

class MetadataInput(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    fetch: bool = True
    title: str = Field(default="", max_length=2000)  # 分享帶來的頁面標題，回傳時去掉網站名稱
    html: str = Field(default="", max_length=4_000_000)  # 手機 App 在隱藏的瀏覽器讀到的作品頁（有防護的網站伺服器讀不到），有就不另外連線


class ImportPlanInput(BaseModel):
    text: str = Field(min_length=1, max_length=100000)  # 300 個長網址貼得下


class ImportSearchInput(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    sites: list[str] = []


class ImportInput(BaseModel):
    urls: list[str] = Items(min_length=1, max_length=300)


# ---------- 書單 ----------

class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    query: str = Field(default="", max_length=1000)  # 有值＝智慧書單（儲存的篩選條件）


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    query: str | None = Field(default=None, max_length=1000)


class CollectionItemsInput(BaseModel):
    ids: list[int] = Items(min_length=1, max_length=2000)
    action: Literal["add", "remove"] = "add"


# ---------- 標籤與重複作品 ----------

class TagMergeInput(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)


class TagRuleInput(BaseModel):
    alias: str = Field(min_length=1, max_length=100)  # 原文（比對時不分大小寫）
    canonical: str = Field(min_length=1, max_length=100)  # 統一後的名稱


class TagUnblockInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TagRemoveInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    block: bool = False  # 同時加入封鎖清單，之後補資料不會再加回來


class TagRemoveManyInput(BaseModel):
    names: list[str] = Items(min_length=1, max_length=20000)
    block: bool = False


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: Literal["tag", "artist", "group", "parody", "character", "category", "language"]


class TagSuggestSkipInput(BaseModel):
    source: str = Field(default="", max_length=100)  # 兩個都留空且 skip=false：恢復全部略過的建議
    target: str = Field(default="", max_length=100)
    skip: bool = True


class TagPairsKeepInput(BaseModel):
    pairs: list[str] = Items(min_length=0, max_length=2000)  # 手機版的 pydantic v1 要用 Items，Field 的 max_length 會變成限制每一項的長度


class TagKeepInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    keep: bool = True


class DismissInput(BaseModel):
    ids: list[int] = Items(min_length=2, max_length=50)


class PageInput(BaseModel):
    id: int
    html: str = Field(min_length=1, max_length=4_000_000)  # 手機 App 在隱藏的瀏覽器讀到的作品頁


class RetryInput(BaseModel):
    ids: list[int] = Items(min_length=0, max_length=2000)  # 空清單代表整組重試


class UndismissInput(BaseModel):
    key: str = Field(min_length=1, max_length=400)


# ---------- 來源與備份 ----------

class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: str = Field(min_length=1, max_length=2000)


class BackupPayload(BaseModel):
    format: str
    version: int
    sources: list[dict] = []
    resources: list[dict] = []
    tag_categories: dict = {}
    tag_aliases: dict = {}
    tag_blocklist: list[str] = []
    collections: list[dict] = []
    tag_health_keep: list[str] = []
    tag_pairs_keep: list[str] = []
    duplicate_dismissals: list[list[dict]] = []
