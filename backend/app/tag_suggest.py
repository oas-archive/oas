"""標籤建議併入：找出還不在規則裡、但看起來就是某個現有標籤的標籤（英文、簡體、寫法不同），建議建立規則並合併。

每次即時計算（典藏幾千件、幾千個標籤時一次不到一秒），收錄、補資料後打開整理頁就是最新結果；
只用本機資料：同義詞規則、翻譯包（含只用來提示的 suggest 字典）、簡繁字表、作品上的標籤共現。

建議的來源（信心由高到低），每個標籤只給最可能的一個：
- rule     已有規則把它換成別的名稱，但典藏裡的標籤還沒換（例如從備份還原時帶進來的）。
- variant  和既有規則或翻譯包的原文只差大小寫、空白、連字號、單複數或異體字。
- same     和另一個現有標籤只差大小寫、空白、連字號、單複數或異體字。
- s2t      把簡體字（或日文新字體）轉成繁體後就是現有標籤；沒有現有標籤、而且轉法確定時，建議改成繁體。
- hint     翻譯包 suggest 字典裡的常見同義詞（例：強姦 → 強暴），只提示、不會自動套用。
- together 英文或簡體標籤幾乎總是和某個繁體標籤一起出現在同樣的作品上，可能是同一個意思（信心低）。

寧可少給：作者、社團、角色的名稱維持原文，只比對大小寫與空白；目標被封鎖、或你按過「略過」的不再建議。
"""
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import defaults, zhconv
from .archive import resolve_alias, tag_rules
from .database import PackEntry, Setting, Tag, resource_tags

SKIP_KEY = "tag_suggest_skip"  # 按過「略過」的建議：["原文小寫\t目標", …]
NAMED_CATEGORIES = {"artist", "group", "character"}  # 名稱維持原文：不翻譯、不轉繁體、不看共現
CONFIDENCE = {"rule": 0.99, "variant": 0.95, "same": 0.9, "s2t": 0.9, "s2t_rename": 0.7, "hint": 0.75, "together": 0.5}
TOGETHER_MIN = 3  # 共現：來源標籤至少要有這麼多件作品
TOGETHER_SHARE = 0.9  # 來源標籤的作品裡，九成以上也帶著目標標籤
TOGETHER_REVERSE = 0.5  # 目標標籤的作品裡，至少一半也帶著來源標籤（否則目標只是很常見的標籤）


def level(score: float) -> str:
    return "high" if score >= 0.85 else "medium" if score >= 0.65 else "low"


def _has_cjk(text: str) -> bool:
    return bool(re.search(r"[㐀-鿿]", text))


def _singular(word: str) -> str:
    if len(word) <= 3 or not word.isascii():
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    return word[:-1] if word.endswith("s") and not word.endswith("ss") else word


def name_key(name: str) -> str:
    """比對用的名稱：全形轉半形、不分大小寫、英文單複數、異體字，並去掉空白與標點。"""
    text = unicodedata.normalize("NFKC", name).lower().translate(zhconv.VARIANTS)
    words = re.split(r"[\s\-_·・.,:：;!！?？'\"’/|｜()（）\[\]]+", text)
    return "".join(_singular(word) for word in words if word)


def _pack_dictionaries(paths: list[Path] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """翻譯包的（翻譯 {小寫原文: 譯名}，提示 {小寫原文: 建議併入的名稱}）；提示只用在這裡，啟動時不會自動套用。"""
    translate, _ = defaults.load_packs(paths)
    hints: dict[str, str] = {}
    for path in defaults.pack_paths() if paths is None else paths:
        hints.update({alias.strip().lower(): target.strip() for alias, target in defaults.pack_section(defaults.read_pack(path), "suggest").items() if alias.strip() and target.strip()})
    return translate, hints


def skipped(db: Session) -> list[str]:
    row = db.get(Setting, SKIP_KEY)
    return json.loads(row.value) if row and row.value else []


def set_skip(db: Session, source: str, target: str, skip: bool = True) -> int:
    """記住（或取消）略過一筆建議；回傳目前略過的筆數。source 與 target 都留空且 skip=false 時清除全部。"""
    entries = set(skipped(db))
    if not source and not target and not skip:
        entries = set()
    else:
        (entries.add if skip else entries.discard)(f"{source.strip().lower()}\t{target.strip()}")
    row = db.get(Setting, SKIP_KEY) or Setting(key=SKIP_KEY)
    row.value = json.dumps(sorted(entries), ensure_ascii=False)
    db.merge(row)
    db.commit()
    return len(entries)


class _Context:
    """一次計算要用的資料：現有標籤、規則、翻譯包、比對用的索引。"""

    def __init__(self, db: Session, paths: list[Path] | None = None):
        self.aliases, self.blocked = tag_rules(db)
        counts = dict(db.execute(
            select(Tag.name, func.count(resource_tags.c.resource_id)).join(resource_tags, resource_tags.c.tag_id == Tag.id).group_by(Tag.name)
        ).all())
        self.categories = dict(db.execute(select(Tag.name, Tag.category)).all())
        self.counts = {name: counts.get(name, 0) for name in self.categories}
        self.canonicals = {canonical for canonical in self.aliases.values()} | {resolve_alias(self.aliases, alias) for alias in self.aliases}
        translate, self.hints = _pack_dictionaries(paths)
        # 使用者刪掉的翻譯包同義詞（已記錄在 pack_entries、但同義詞不在了）不再拿來建議
        removed = set(db.scalars(select(PackEntry.alias)).all()) - set(self.aliases)
        self.translate = {alias: target for alias, target in translate.items() if alias not in removed}
        self.skip = set(skipped(db))
        # 比對用索引：規則與翻譯包的原文、現有標籤
        self.dictionary: dict[str, tuple[str, str, str]] = {}  # 比對用名稱 → (原文, 最後的名稱, 出處)
        for alias, target in self.translate.items():
            self.dictionary.setdefault(name_key(alias), (alias, resolve_alias(self.aliases, target), "翻譯包"))
        for alias in self.aliases:
            self.dictionary[name_key(alias)] = (alias, resolve_alias(self.aliases, alias), "規則")
        self.by_key: dict[str, list[str]] = defaultdict(list)
        for name in self.categories:
            self.by_key[name_key(name)].append(name)
        self.lower_names = {name.lower(): name for name in self.categories}

    def final(self, name: str) -> str:
        return resolve_alias(self.aliases, name)

    def preferred(self, names: list[str]) -> str:
        """同一組寫法裡要留下的名稱：你整理過的名稱優先，其次是繁體、中文、件數多、較短的。"""
        return sorted(names, key=lambda name: (name not in self.canonicals, zhconv.has_simplified(name), not _has_cjk(name),
                                               -self.counts.get(name, 0), len(name), name))[0]

    def suggest(self, name: str, category: str = "tag") -> dict | None:
        """一個標籤最可能的併入對象（現有標籤，或收錄表單裡典藏還沒有的新標籤）。"""
        named = category in NAMED_CATEGORIES
        found = self._find(name, named)
        if not found:
            return None
        method, target, reason = found
        if target == name or target.lower() in self.blocked or f"{name.lower()}\t{target}" in self.skip:
            return None
        score = CONFIDENCE[method]
        return {
            "source": name, "source_count": self.counts.get(name, 0), "category": category,
            "target": target, "target_count": self.counts.get(target, 0), "target_exists": target in self.categories,
            "method": method.replace("_rename", ""), "score": score, "confidence": level(score), "reason": reason,
        }

    def _find(self, name: str, named: bool) -> tuple[str, str, str] | None:
        lower, key = name.lower(), name_key(name)
        if lower in self.aliases and self.final(name) != name:
            return "rule", self.final(name), f"已有規則「{lower} → {self.final(name)}」，但這個標籤還沒換過去"
        if name in self.canonicals:
            return None  # 你整理過的名稱（有規則指向它），不再建議改掉
        if key and key in self.dictionary:
            alias, target, origin = self.dictionary[key]
            if alias != lower and target != name:
                return "variant", target, f"和{origin}的「{alias} → {target}」只差大小寫、空白、連字號或單複數"
        others = [other for other in self.by_key.get(key, []) if other != name]
        if key and others:
            target = self.preferred([name, *others])
            if target != name:
                return "same", self.final(target), f"和現有的「{target}」只差大小寫、空白、連字號、單複數或異體字"
        if named:
            return None
        if zhconv.has_simplified(name):
            for candidate in zhconv.candidates(name):
                if candidate == name:
                    continue
                match = self.lower_names.get(candidate.lower()) or (self.final(candidate) if candidate.lower() in self.aliases else None)
                if not match:
                    variants = [other for other in self.by_key.get(name_key(candidate), []) if other != name]
                    match = self.preferred(variants) if variants else None
                if match and match != name:
                    return "s2t", self.final(match), f"簡體字轉成繁體是「{candidate}」，典藏裡已有「{self.final(match)}」"
            converted = zhconv.to_traditional(name)
            if converted and converted != name and _has_cjk(name):
                return "s2t_rename", converted, f"簡體字，建議改成繁體「{converted}」"
        hint = self.hints.get(lower) or self.hints.get(zhconv.to_traditional(lower) or lower)
        if hint:
            target = self.final(hint)
            if target != name and (target in self.categories or target in self.canonicals):
                return "hint", target, f"常見同義詞：「{name}」通常就是「{target}」"
        return None


def _together(db: Session, ctx: _Context, taken: set[str]) -> list[dict]:
    """英文或簡體的一般標籤，幾乎總是和某個繁體標籤一起出現：可能是同一個意思（信心低，只在沒有其他依據時列出）。"""
    owners: dict[str, set[int]] = defaultdict(set)
    for resource_id, name in db.execute(select(resource_tags.c.resource_id, Tag.name).join(Tag, Tag.id == resource_tags.c.tag_id)).all():
        owners[name].add(resource_id)
    result = []
    for name, ids in owners.items():
        if name in taken or name in ctx.canonicals or len(ids) < TOGETHER_MIN or ctx.categories.get(name, "tag") != "tag":
            continue
        if _has_cjk(name) and not zhconv.has_simplified(name):
            continue  # 繁體標籤之間的共現在「標籤健檢 → 幾乎總是一起出現」
        together: Counter = Counter()
        for other, other_ids in owners.items():
            if other != name and ctx.categories.get(other, "tag") == "tag" and _has_cjk(other) and not zhconv.has_simplified(other):
                both = len(ids & other_ids)
                if both >= TOGETHER_SHARE * len(ids) and both >= TOGETHER_REVERSE * len(other_ids):
                    together[other] = both
        if len(together) != 1:
            continue  # 同時符合好幾個時分不出是哪一個，寧可不建議
        target, both = together.most_common(1)[0]
        if target.lower() in ctx.blocked or f"{name.lower()}\t{target}" in ctx.skip:
            continue
        score = CONFIDENCE["together"]
        result.append({
            "source": name, "source_count": len(ids), "category": "tag", "target": target, "target_count": len(owners[target]),
            "target_exists": True, "method": "together", "score": score, "confidence": level(score),
            "reason": f"「{name}」的 {len(ids)} 件作品裡有 {both} 件也帶著「{target}」，可能是同一個意思",
        })
    return result


def _follow_chains(items: list[dict], ctx: _Context) -> None:
    """目標本身也有建議時（卖淫 → 賣淫、賣淫 → 賣春）直接建議到最後，信心取兩者較低的；繞回自己的不處理。"""
    by_source = {entry["source"]: entry for entry in items}
    for entry in items:
        seen, current = {entry["source"]}, entry
        while (step := by_source.get(current["target"])) is not None and step["target"] not in seen:
            seen.add(current["target"])
            entry["reason"] += f"；「{current['target']}」也建議併入「{step['target']}」"
            entry["score"] = min(entry["score"], step["score"])
            current = step
        if current is not entry:
            entry.update(target=current["target"], target_count=current["target_count"], target_exists=current["target_exists"], confidence=level(entry["score"]))


def suggestions(db: Session, paths: list[Path] | None = None) -> dict:
    """整理頁的建議清單：信心高的在前，同信心時件數多的在前。"""
    ctx = _Context(db, paths)
    items = []
    for name, category in ctx.categories.items():
        if ctx.counts.get(name, 0) == 0:
            continue  # 沒有作品用的空標籤：標籤健檢會建議清除
        if (entry := ctx.suggest(name, category)) is not None:
            items.append(entry)
    items += _together(db, ctx, {entry["source"] for entry in items})
    _follow_chains(items, ctx)
    items.sort(key=lambda entry: (-entry["score"], -entry["source_count"], entry["source"].lower()))
    return {"items": items, "total": len(items), "skipped": len(ctx.skip),
            "by_confidence": dict(Counter(entry["confidence"] for entry in items))}


def suggest_for(db: Session, names: list[str], categories: dict[str, str] | None = None) -> list[dict]:
    """收錄表單：帶入的標籤裡，典藏還沒有、但可能就是某個現有標籤的。"""
    ctx = _Context(db)
    entries = []
    for name in dict.fromkeys(names):
        if name in ctx.categories:
            continue  # 已經是典藏裡的標籤，整理頁會列出
        if (entry := ctx.suggest(name, (categories or {}).get(name, "tag"))) is not None:
            entries.append(entry)
            if (step := ctx.suggest(entry["target"], entry["category"])) is not None:
                entries.append(step)  # 目標本身也有建議時一起帶上，_follow_chains 會接到最後，之後再拿掉
    _follow_chains(entries, ctx)
    return [{key: entry[key] for key in ("source", "target", "target_exists", "confidence", "reason")}
            for entry in entries if entry["source"] in names and entry["source"] not in ctx.categories]
