"""公開版第一次啟動時由使用者決定的設定。其他版本（電腦、個人手機版）沒有這些選擇，維持原本的行為。"""
import json

from sqlalchemy.orm import Session

from . import settings
from .database import SessionLocal, Setting

KEY = "preferences"
CHOICES = ("adult_confirmed", "save_covers", "background_reader")


def is_public() -> bool:
    return settings.EDITION == "public"


def _stored(db: Session) -> dict:
    row = db.get(Setting, KEY)
    try:
        stored = json.loads(row.value) if row else {}
    except ValueError:
        return {}
    return stored if isinstance(stored, dict) else {}


def read(db: Session) -> dict:
    if not is_public():
        return {"edition": settings.EDITION, "setup_done": True, "adult_confirmed": True, "save_covers": True, "background_reader": True}
    stored = _stored(db)
    choices = {name: stored.get(name) if isinstance(stored.get(name), bool) else None for name in CHOICES}
    # 三項都選過才算完成，沒同意年齡限制就不算
    done = choices["adult_confirmed"] is True and None not in choices.values()
    return {"edition": settings.EDITION, "setup_done": done, **choices}


def update(db: Session, changes: dict) -> dict:
    row = db.get(Setting, KEY) or Setting(key=KEY)
    row.value = json.dumps({**_stored(db), **{name: changes[name] for name in CHOICES if changes.get(name) is not None}})
    db.add(row)
    db.commit()
    return read(db)


def saving_covers() -> bool:
    """能不能把封面存到本機：公開版要使用者選了「存」才可以，還沒選之前也不存。"""
    if not is_public():
        return True
    with SessionLocal() as db:
        return read(db)["save_covers"] is True
