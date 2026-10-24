from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, REAL, String, Table, Text, create_engine, event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from . import settings

DATABASE_URL = settings.DATABASE_URL
if DATABASE_URL.startswith("sqlite:///") and DATABASE_URL != "sqlite:///:memory:":
    Path(DATABASE_URL.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)  # 第一次執行時建立資料夾
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


resource_tags = Table(
    "resource_tags",
    Base.metadata,
    Column("resource_id", ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Resource(Base):
    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(500), nullable=False)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False, default="其他網站")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_url: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    rating: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="未讀")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_kind: Mapped[str] = mapped_column(String(10), nullable=False, default="")
    link_dead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 來源頁面已失效（作品被下架等）  # ""＝封面／avatar＝創作者頭像／icon＝網站圖示
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="")  # 漫畫／影片／動畫／創作者／其他；空白時沿用來源的類型
    progress: Mapped[str] = mapped_column(String(40), nullable=False, default="")  # 閱讀進度，例如「第 3 話」「p.45」
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 最近一次標記為「已完食」的時間
    link_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # 定期巡檢最後檢查來源的時間
    tags: Mapped[list["Tag"]] = relationship(secondary=resource_tags, back_populates="resources", lazy="selectin")
    collections: Mapped[list["Collection"]] = relationship(secondary="collection_items", back_populates="resources", lazy="selectin")


# 「來源 + 代碼」唯一規則：新資料庫由 create_all 建立，既有資料庫由 initialise_database 補建。
UNIQUE_SOURCE_CODE = Index("uq_resources_source_code", Resource.source_name, Resource.code, unique=True)
Index("ix_resources_status", Resource.status)
Index("ix_resources_updated_at", Resource.updated_at)


class Collection(Base):
    """自訂書單：使用者手動整理的作品清單，例如「週末要看」「最愛」。"""
    __tablename__ = "collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 智慧書單：儲存篩選條件（與網址相同的格式，例如 status=未讀&tag=純愛向），內容依條件即時計算；空白為手動書單。
    query: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    resources: Mapped[list["Resource"]] = relationship(secondary="collection_items", back_populates="collections")


collection_items = Table(
    "collection_items",
    Base.metadata,
    Column("collection_id", ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True),
    Column("resource_id", ForeignKey("resources.id", ondelete="CASCADE"), primary_key=True),
    Column("added_at", DateTime(timezone=True), nullable=False, default=utcnow),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    # 分類：tag（一般）／artist（作者）／group（社團）／parody（原作）／character（角色）／category（類別）
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="tag")
    resources: Mapped[list[Resource]] = relationship(secondary=resource_tags, back_populates="tags")


class TagAlias(Base):
    """同義詞：寫入標籤時，alias（不分大小寫）一律換成 canonical。例如 big breasts → 巨乳。"""
    __tablename__ = "tag_aliases"

    alias: Mapped[str] = mapped_column(String(100), primary_key=True)  # 小寫
    canonical: Mapped[str] = mapped_column(String(100), nullable=False)


class TagBlock(Base):
    """封鎖清單：網站附加的宣傳字等，寫入標籤時直接略過，整理後不會再跑回來。"""
    __tablename__ = "tag_blocklist"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)  # 小寫


class Setting(Base):
    """少量的系統設定與背景作業進度（例如定期巡檢上次完成的時間）。"""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Job(Base):
    """背景工作清單（scheduler.py）：補新收錄（fill）、補齊作品資訊（refresh）、巡檢來源（linkcheck）。

    做完就刪除；典藏館關掉再開會從清單接著做。同一件作品同一種工作只排一次。
    """
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_kind_resource", "kind", "resource_id", unique=True), Index("ix_jobs_order", "priority", "id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    host: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="")  # 補齊作品資訊的模式：all / missing / images
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 越小越先做
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 被限流而重試的次數
    ready_at: Mapped[float] = mapped_column(REAL, nullable=False, default=0.0)  # 最早可執行的時間（Unix 秒）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class UndoEntry(Base):
    """復原紀錄：刪除、合併、批次修改等動作執行前的狀態（JSON），按「復原」時寫回；保留一小時。"""
    __tablename__ = "undo_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class DuplicateDismissal(Base):
    """重複作品偵測中，使用者確認「不是重複」的組合（以排序後的作品 id 串成 key）。"""
    __tablename__ = "duplicate_dismissals"

    key: Mapped[str] = mapped_column(String(400), primary_key=True)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    template: Mapped[str] = mapped_column(String(2000), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="")


class CoverCache(Base):
    """封面快取紀錄：以圖片網址的 SHA-256 為鍵，檔案存放於 OAS_COVER_DIR。"""
    __tablename__ = "cover_cache"

    url_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok / failed
    filename: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    ratio: Mapped[float | None] = mapped_column(REAL)  # 高 / 寬，供前端瀑布流預留版面
    colour: Mapped[str | None] = mapped_column(String(7))  # 封面主色 #rrggbb；"" 表示已分析但無法取色


def _add_missing_columns() -> None:
    """create_all 不會替既有資料表加欄位；這裡補上後續版本新增的欄位。"""
    from sqlalchemy import inspect, text

    additions = {
        "cover_cache": {"ratio": "REAL", "colour": "VARCHAR(7)"},
        "resources": {"kind": "VARCHAR(20) NOT NULL DEFAULT ''", "image_kind": "VARCHAR(10) NOT NULL DEFAULT ''", "link_dead": "BOOLEAN NOT NULL DEFAULT 0",
                      "progress": "VARCHAR(40) NOT NULL DEFAULT ''", "finished_at": "DATETIME", "link_checked_at": "DATETIME"},
        "collections": {"query": "TEXT NOT NULL DEFAULT ''"},
        "sources": {"kind": "VARCHAR(20) NOT NULL DEFAULT ''"},
        "tags": {"category": "VARCHAR(20) NOT NULL DEFAULT 'tag'"},
    }
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table, columns in additions.items():
            if not inspector.has_table(table):
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def initialise_database() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    for index in Resource.__table__.indexes:
        try:
            index.create(engine, checkfirst=True)
        except DBAPIError:
            # 既有資料若已含重複「來源 + 代碼」，略過建立唯一索引；API 層仍會檢查重複。
            pass
