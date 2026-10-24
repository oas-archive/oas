"""全新典藏館的預設內容：常用網站的網址規則、入門標籤翻譯包。

只在第一次啟動、資料庫完全是空的（沒有作品、網站規則、同義詞）時套用一次；之後都可以在畫面上自行修改。
OAS_SEED=off 時不套用（測試環境）。
"""
import json
import os
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import Resource, Source, TagAlias, TagBlock

# 程式有支援的網站：nhentai、紳士漫畫、Hanime1 可直接讀取資料；禁漫天堂、MissAV 有防護，請用「收錄到 OAS」書籤。
SOURCES = [
    ("其他網站", "{}", "其他"),
    ("nhentai", "https://nhentai.net/g/{}/", "漫畫"),
    ("紳士漫畫", "https://www.wnacg.com/photos-index-aid-{}.html", "漫畫"),
    ("Hanime1", "https://hanime1.me/watch?v={}", "動畫"),
    ("禁漫天堂", "https://18comic.vip/album/{}/", "漫畫"),
    ("MissAV", "https://missav.ai/{}", "影片"),
]
TAG_PACKS = [Path(__file__).with_name("data") / name for name in ("tag_pack_zh_tw.json", "tag_pack_private.json")]  # 後者只在私人版本中


def enabled() -> bool:
    return os.getenv("OAS_SEED", "on").lower() != "off"


def is_empty(db: Session) -> bool:
    return not any(db.scalar(select(func.count()).select_from(model)) for model in (Resource, Source, TagAlias))


def seed(db: Session) -> dict:
    """寫入預設內容（不檢查是否為空，由呼叫端決定）；回傳各項件數。"""
    for name, template, kind in SOURCES:
        db.add(Source(name=name, template=template, kind=kind))
    aliases, blocked = {}, set()
    for path in TAG_PACKS:
        if not path.exists():
            continue
        pack = json.loads(path.read_text(encoding="utf-8"))
        aliases.update({alias.strip().lower(): canonical.strip() for alias, canonical in pack["translate"].items() if alias.strip() and canonical.strip()})
        blocked |= {name.strip().lower() for name in pack.get("block", []) if name.strip()}
    blocked -= set(aliases)
    for alias, canonical in aliases.items():
        db.add(TagAlias(alias=alias[:100], canonical=canonical[:100]))
    for name in blocked:
        db.add(TagBlock(name=name[:100]))
    db.commit()
    return {"sources": len(SOURCES), "aliases": len(aliases), "blocked": len(blocked)}


def seed_if_empty(db: Session) -> dict | None:
    if not enabled() or not is_empty(db):
        return None
    return seed(db)
