"""公開版第一次啟動時由使用者決定的設定。其他版本（電腦私人版、手機個人版）沒有這些選擇，維持原本的行為。

要問哪幾題看平台：手機問年齡與用途、封面存不存、背景讀取三題；電腦沒有背景讀取（沒有 App 外殼），只問前兩題，
背景讀取回報 false（不適用）。回傳的 questions 列出這台要問的題目，畫面依它顯示。
"""
import json

from sqlalchemy.orm import Session

from . import settings
from .database import SessionLocal, Setting

KEY = "preferences"
CHOICES = ("adult_confirmed", "save_covers", "background_reader")
DESKTOP_CHOICES = ("adult_confirmed", "save_covers")


def is_public() -> bool:
    return settings.EDITION == "public"


def is_android() -> bool:
    return settings.PLATFORM == "android"


def questions() -> tuple[str, ...]:
    """這台要問的題目；不是公開版就沒有。"""
    if not is_public():
        return ()
    return CHOICES if is_android() else DESKTOP_CHOICES


def device_name() -> str:
    """訊息裡指這台裝置的說法。"""
    return "手機" if is_android() else "這台電腦"


def _stored(db: Session) -> dict:
    row = db.get(Setting, KEY)
    try:
        stored = json.loads(row.value) if row else {}
    except ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


def read(db: Session) -> dict:
    base = {"edition": settings.EDITION, "platform": settings.PLATFORM, "questions": list(questions())}
    if not is_public():
        return {**base, "setup_done": True, "adult_confirmed": True, "save_covers": True, "background_reader": True}
    stored = _stored(db)
    asked = questions()
    choices = {name: stored.get(name) if isinstance(stored.get(name), bool) else None for name in asked}
    # 要問的題目都選過才算完成，沒同意年齡限制就不算
    done = choices["adult_confirmed"] is True and None not in choices.values()
    if "background_reader" not in asked:
        choices["background_reader"] = False  # 電腦不適用，視為關閉
    return {**base, "setup_done": done, **choices}


def update(db: Session, changes: dict) -> dict:
    row = db.get(Setting, KEY) or Setting(key=KEY)
    # 只存這台要問的題目：電腦送來的背景讀取直接忽略
    row.value = json.dumps({**_stored(db), **{name: changes[name] for name in questions() if changes.get(name) is not None}})
    db.add(row)
    db.commit()
    return read(db)


def saving_covers() -> bool:
    """能不能把封面存到本機：公開版要使用者選了「存」才可以，還沒選之前也不存。"""
    if not is_public():
        return True
    with SessionLocal() as db:
        return read(db)["save_covers"] is True
