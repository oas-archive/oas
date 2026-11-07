"""全新典藏館的預設內容：常用網站的網址規則、入門標籤翻譯包。

網站規則只在第一次啟動、資料庫完全是空的（沒有作品、網站規則、同義詞）時套用一次；之後都可以在畫面上自行修改。
翻譯包每次啟動都比對一次（sync_packs）：之後新增的條目會補進既有典藏，使用者改過或刪掉的同義詞不再動。
OAS_SEED=off 時都不套用（測試環境）。
"""
import json
import logging
import os
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import settings
from .sites import SOURCES
from .archive import resolve_alias, tag_rules
from .database import PackEntry, Resource, Source, Tag, TagAlias, TagBlock, resource_tags

log = logging.getLogger("uvicorn.error")  # 與伺服器訊息印在一起

TAG_PACKS = [Path(__file__).with_name("data") / name for name in ("tag_pack_zh_tw.json", "tag_pack_private.json")]  # 後者只在私人版本中
USER_PACK_NAME = "tag_pack_private.json"  # 使用者自己的翻譯包：放資料夾，更新程式不會蓋掉
USER_PACK_TEMPLATE = {
    "_說明": "你自己的標籤翻譯，更新版本不會動到這個檔案。translate 寫「原文: 譯名」，suggest 寫只提示、不自動套用的同義詞，block 寫要封鎖的標籤。存檔後重新啟動典藏館就會套用。",
    "translate": {},
    "suggest": {},
    "block": [],
}


def pack_paths() -> list[Path]:
    """所有翻譯包，後面的覆蓋前面的：內建的翻譯包，最後是使用者自己的。"""
    return [*TAG_PACKS, settings.DATA_DIR / USER_PACK_NAME]


def ensure_user_pack() -> None:
    """第一次啟動時建立空的使用者翻譯包；已經有了就不動。"""
    path = settings.DATA_DIR / USER_PACK_NAME
    if enabled() and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(USER_PACK_TEMPLATE, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def write_user_pack(translate: dict[str, str], suggest: dict[str, str], block: list[str]) -> None:
    """取代使用者自己的翻譯包：手機使用者看不到 App 的資料夾，要從畫面匯入。先寫到暫存檔，寫到一半失敗不會弄壞舊的。"""
    path = settings.DATA_DIR / USER_PACK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps({**USER_PACK_TEMPLATE, "translate": translate, "suggest": suggest, "block": block}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_pack(path: Path) -> dict:
    """讀一個翻譯包；不存在或格式錯誤時當成空的：使用者自己編輯的檔案寫錯，不能影響其他翻譯。記事本存檔可能帶 BOM。"""
    try:
        pack = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        log.warning("翻譯包 %s 讀不出來，已略過：%s", path.name, error)
        return {}
    return pack if isinstance(pack, dict) else {}


def pack_section(pack: dict, name: str) -> dict[str, str]:
    section = pack.get(name)
    return {key: value for key, value in section.items() if isinstance(value, str)} if isinstance(section, dict) else {}


def enabled() -> bool:
    return os.getenv("OAS_SEED", "on").lower() != "off"


def is_empty(db: Session) -> bool:
    return not any(db.scalar(select(func.count()).select_from(model)) for model in (Resource, Source, TagAlias))


def load_packs(paths: list[Path] | None = None) -> tuple[dict[str, str], set[str]]:
    """讀取翻譯包：（{小寫原文: 譯名}，{小寫封鎖名稱}）；後面的翻譯包覆蓋前面的，有翻譯的名稱不封鎖。"""
    aliases, blocked = {}, set()
    for path in pack_paths() if paths is None else paths:
        pack = read_pack(path)
        aliases.update({alias.strip().lower()[:100]: canonical.strip()[:100] for alias, canonical in pack_section(pack, "translate").items() if alias.strip() and canonical.strip()})
        names = pack.get("block")
        blocked |= {name.strip().lower()[:100] for name in names if isinstance(name, str) and name.strip()} if isinstance(names, list) else set()
    return aliases, blocked - set(aliases)


def _tags_named(db: Session, name: str) -> list[Tag]:
    """名稱（不分大小寫）等於 name 的標籤；同義詞與封鎖清單都不分大小寫。"""
    return [tag for tag in db.scalars(select(Tag)).all() if tag.name.lower() == name]


def _merge_into(db: Session, alias: str, target: str) -> int:
    """把名為 alias 的既有標籤併入 target（insights.merge_tag：直接寫關聯表，不動作品的修改時間）；回傳併了幾個標籤。"""
    from .insights import merge_tag

    merged = 0
    for tag in _tags_named(db, alias):
        if tag.name != target:
            merge_tag(db, tag.name, target)
            db.flush()
            merged += 1
    return merged


def sync_packs(db: Session, paths: list[Path] | None = None) -> dict:
    """把翻譯包裡這個典藏還沒處理過的條目補進同義詞與封鎖清單（每次啟動執行，第二次起什麼都不做）。

    - 新的翻譯：原文還沒有同義詞、也沒被封鎖才加；已經有的一律當成使用者的設定。加了之後，名為原文的既有標籤併入譯名。
    - 處理過的翻譯：翻譯包改了譯名，而同義詞仍是上次寫入的名稱（使用者沒動過）時才跟著改，舊譯名的標籤併入新譯名。
    - 新的封鎖：加進封鎖清單，並從作品上拿掉同名標籤；原文已有使用者自己的同義詞時不封鎖。
    """
    translate, block = load_packs(paths)
    records = {entry.alias: entry for entry in db.scalars(select(PackEntry)).all()}
    aliases, blocked = tag_rules(db)
    counts = {"aliases": 0, "updated": 0, "blocked": 0, "merged": 0, "removed": 0}

    def record(alias: str, kind: str, canonical: str = "", applied: str = "") -> None:
        entry = records.get(alias)
        if entry is None:
            entry = records[alias] = PackEntry(alias=alias, kind=kind)
            db.add(entry)
        entry.kind, entry.canonical, entry.applied = kind, canonical, applied

    for alias, canonical in translate.items():
        entry = records.get(alias)
        if entry is not None and entry.kind == "translate":
            if entry.canonical == canonical:
                continue
            if aliases.get(alias) == entry.applied:  # 使用者沒動過這條同義詞：跟著翻譯包改
                target = resolve_alias(aliases, canonical)
                db.get(TagAlias, alias).canonical = target
                db.flush()
                if entry.applied.lower() not in blocked:
                    counts["merged"] += _merge_into(db, entry.applied.lower(), target)
                counts["updated"] += 1
                aliases, blocked = tag_rules(db)
                record(alias, "translate", canonical, target)
            else:
                entry.canonical = canonical  # 使用者改過或刪掉：記下新譯名，之後不再比對
            continue
        target = resolve_alias(aliases, canonical)
        if alias not in aliases and alias not in blocked:
            db.add(TagAlias(alias=alias, canonical=target))
            db.flush()
            aliases[alias] = target
            counts["aliases"] += 1
            if target.lower() not in blocked:
                merged = _merge_into(db, alias, target)
                if merged:
                    counts["merged"] += merged
                    aliases, blocked = tag_rules(db)
        record(alias, "translate", canonical, target)

    for name in sorted(block):
        entry = records.get(name)
        if entry is not None and entry.kind == "block":
            continue
        if name in aliases and entry is not None and aliases[name] == entry.applied:
            db.delete(db.get(TagAlias, name))  # 翻譯包把原本的翻譯改成封鎖，而使用者沒動過那條同義詞
            db.flush()
            del aliases[name]
        if name not in blocked and name not in aliases:
            db.add(TagBlock(name=name))
            blocked.add(name)
            counts["blocked"] += 1
            for tag in _tags_named(db, name):
                db.execute(delete(resource_tags).where(resource_tags.c.tag_id == tag.id))  # 直接寫關聯表，不動作品的修改時間
                db.delete(tag)
                counts["removed"] += 1
            db.flush()
        record(name, "block")
    db.commit()
    return counts


def sync_packs_safely(db: Session) -> dict | None:
    """啟動時呼叫：有補東西就記一行 log；失敗只記 log，不擋住服務啟動。"""
    if not enabled():
        return None
    try:
        counts = sync_packs(db)
    except Exception:  # noqa: BLE001 — 翻譯包是輔助功能，任何錯誤都不該讓典藏館開不起來
        db.rollback()
        log.exception("翻譯包同步失敗（已略過）")
        return None
    if any(counts.values()):
        log.info("翻譯包同步：新增同義詞 %(aliases)d、跟著翻譯包更新 %(updated)d、新增封鎖 %(blocked)d、併入既有標籤 %(merged)d、拿掉封鎖標籤 %(removed)d", counts)
    return counts


def seed(db: Session) -> dict:
    """寫入預設內容（不檢查是否為空，由呼叫端決定）；回傳各項件數。翻譯包走 sync_packs，條目同時記錄為已處理。"""
    for name, template, kind in SOURCES:
        db.add(Source(name=name, template=template, kind=kind))
    db.flush()
    counts = sync_packs(db)
    return {"sources": len(SOURCES), "aliases": counts["aliases"], "blocked": counts["blocked"]}


def seed_if_empty(db: Session) -> dict | None:
    if not enabled() or not is_empty(db):
        return None
    return seed(db)
