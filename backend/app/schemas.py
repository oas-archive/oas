"""API 的輸入格式（請求內容的欄位與限制）。"""
from typing import Literal

from pydantic import BaseModel, Field

from .archive import DEFAULT_SOURCE

StatusValue = Literal["未讀", "閱讀中", "已完食", "暫時擱置", "棄坑"]


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
    tags: list[str] = []


class QuickInput(BaseModel):
    rating: float | None = Field(default=None, ge=0, le=5)
    status: StatusValue | None = None
    link_dead: bool | None = None
    progress: str | None = Field(default=None, max_length=40)


class RelinkInput(BaseModel):
    source_name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=500)


class BatchInput(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    action: Literal["status", "rating", "add_tags", "remove_tags", "kind", "delete"]
    status: StatusValue | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    tags: list[str] = []
    kind: str | None = Field(default=None, max_length=20)


class MergeInput(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=50)


# ---------- 收錄 ----------

class MetadataInput(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    fetch: bool = True


class ImportInput(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=300)


# ---------- 書單 ----------

class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    query: str = Field(default="", max_length=1000)  # 有值＝智慧書單（儲存的篩選條件）


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    query: str | None = Field(default=None, max_length=1000)


class CollectionItemsInput(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    action: Literal["add", "remove"] = "add"


# ---------- 標籤與重複作品 ----------

class TagMergeInput(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)


class TagRemoveInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    block: bool = False  # 同時加入封鎖清單，之後補資料不會再加回來


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: Literal["tag", "artist", "group", "parody", "character", "category", "language"]


class DismissInput(BaseModel):
    ids: list[int] = Field(min_length=2, max_length=50)


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
