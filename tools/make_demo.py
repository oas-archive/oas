"""產生一份虛構的示範典藏館（作品、標籤、封面全部是假的），用於截圖、展示與試用。

    python tools/make_demo.py <輸出資料夾>
    之後以 OAS_DATA_DIR=<輸出資料夾> 啟動服務即可看到示範內容。

- 網站使用虛構的「示範書庫」「示範影音」（example.com / example.org），不連到任何真實網站。
- 封面以程式繪製抽象圖案，經由正式的封面快取流程寫入（比例、主色、縮圖都照常產生）。
- 同一個輸出資料夾已經有資料時會停止，避免覆蓋。
"""
import argparse
import io
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"

TITLES = [
    "星海旅人", "雨夜的咖啡館", "第七號月台的約定", "紙飛機與夏日", "魔法師的見習日誌", "雲端圖書館", "機械少女與貓",
    "深海燈塔", "放學後的天文社", "時間旅行的便利商店", "北境的歌聲", "秘密花園的鑰匙", "銀色列車", "最後一封情書",
    "月光下的料理教室", "風之谷的郵差", "偵探與午後紅茶", "失落王國的地圖", "螢火蟲之夜", "冬日的溫室", "迷宮書店",
    "藍色海岸線", "龍與麵包師傅", "鐘塔上的少年", "異世界的早餐", "櫻花與劍", "沉睡的星座", "未來日記本",
    "琥珀色的午後", "森林裡的音樂盒", "空中花園", "白色的燈籠", "旅行商人與狐狸", "海邊的小鎮", "黃昏的鋼琴",
    "第二次的夏天", "迴廊之夢", "雪國的郵局", "星屑的願望", "城市的另一邊",
]
GENERAL = ["奇幻", "冒險", "校園", "科幻", "懸疑", "日常", "戀愛", "喜劇", "治癒", "料理", "旅行", "音樂", "偵探", "魔法",
           "機器人", "貓", "海洋", "星空", "歷史", "運動"]
ARTISTS = ["森川葉", "藍野夕", "白石凪", "春日井透", "千早湊", "高原澪"]
GROUPS = ["星屑工房", "午後茶會", "北風社", "燈塔出版"]
PARODIES = ["原創作品", "幻想物語集"]
CHARACTERS = ["艾莉絲", "小鈴", "黑貓先生"]
LANGUAGES = ["中文", "日文"]
PALETTES = [("#2b2d6e", "#e07a5f"), ("#14213d", "#fca311"), ("#1d3557", "#a8dadc"), ("#3d405b", "#f2cc8f"), ("#22223b", "#c9ada7"),
            ("#283618", "#dda15e"), ("#03045e", "#90e0ef"), ("#432818", "#ffe6a7"), ("#2d3142", "#ef8354"), ("#1b263b", "#e0e1dd")]


def cover_png(seed: int, ratio: float) -> bytes:
    """抽象圖案的封面：漸層背景＋幾個半透明的圓與斜帶。"""
    from PIL import Image, ImageDraw

    rng = random.Random(seed)
    width = 480
    height = int(width * ratio)
    top, bottom = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in rng.choice(PALETTES))
    image = Image.new("RGB", (width, height))
    pixels = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        pixels.line([(0, y), (width, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for _ in range(rng.randint(3, 6)):
        r = rng.randint(width // 10, width // 2)
        x, y = rng.randint(-r // 2, width), rng.randint(-r // 2, height)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, rng.randint(18, 60)))
    offset = rng.randint(0, height)
    draw.polygon([(0, offset), (width, offset - width // 2), (width, offset - width // 2 + 40), (0, offset + 40)], fill=(255, 255, 255, 40))
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def build(out: Path, seed: int = 7) -> dict:
    os.environ["OAS_DATA_DIR"] = str(out)
    os.environ.pop("OAS_DATABASE_URL", None)
    os.environ.pop("OAS_COVER_DIR", None)
    sys.path.insert(0, str(BACKEND))
    from sqlalchemy import func, select  # noqa: E402

    from app import covers  # noqa: E402
    from app.database import Collection, Resource, SessionLocal, Source, Tag, collection_items, initialise_database  # noqa: E402

    initialise_database()
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if db.scalar(select(func.count(Resource.id))):
            raise SystemExit(f"{out} 已經有資料，請換一個空的資料夾。")
        db.add_all([Source(name="示範書庫", template="https://example.com/works/{}", kind="漫畫"),
                    Source(name="示範影音", template="https://example.org/v/{}", kind="動畫")])
        tags: dict[str, Tag] = {}

        def tag(name: str, category: str = "tag") -> Tag:
            if name not in tags:
                tags[name] = Tag(name=name, category=category)
                db.add(tags[name])
            return tags[name]

        resources = []
        for index, title in enumerate(TITLES):
            video = index % 7 == 3
            artist, group = rng.choice(ARTISTS), rng.choice(GROUPS)
            names = [tag(name) for name in rng.sample(GENERAL, rng.randint(3, 6))]
            names += [tag(artist, "artist"), tag(group, "group"), tag(rng.choice(PARODIES), "parody"), tag(rng.choice(LANGUAGES), "language")]
            if rng.random() < 0.3:
                names.append(tag(rng.choice(CHARACTERS), "character"))
            status = rng.choices(["未讀", "閱讀中", "已完食", "暫時擱置"], weights=[5, 2, 4, 1])[0]
            created = now - timedelta(days=rng.randint(3, 540), hours=rng.randint(0, 23))
            opened = None if status == "未讀" and rng.random() < 0.8 else created + timedelta(days=rng.randint(0, 30))
            resource = Resource(
                code=str(100100 + index * 37), source_name="示範影音" if video else "示範書庫",
                title=f"[{group}] {title}" if rng.random() < 0.3 else title,
                rating=rng.choice([0, 3, 3.5, 4, 4.5, 5]) if status != "未讀" else 0,
                status=status, kind="動畫" if video else "漫畫",
                progress=f"第 {rng.randint(2, 12)} 話" if status == "閱讀中" else "",
                note="很喜歡結尾的轉折，之後想再看一次。" if rng.random() < 0.15 else "",
                created_at=created, updated_at=created + timedelta(days=rng.randint(0, 20)), last_opened_at=opened,
                finished_at=opened if status == "已完食" else None,
                image_url=f"https://example.com/covers/{index + 1}.png",
                link_dead=index == 11,
            )
            resource.tags = names
            db.add(resource)
            resources.append(resource)
        # 同一本的另一個版本：示範「重複作品」偵測
        twin = Resource(code="200001", source_name="示範影音", title=resources[0].title, kind="漫畫", status="未讀",
                        created_at=now - timedelta(days=40), updated_at=now - timedelta(days=40), image_url=resources[0].image_url)
        twin.tags = list(resources[0].tags)[:3]
        db.add(twin)
        db.flush()
        weekend = Collection(name="週末要看", query="", position=1)
        db.add_all([weekend, Collection(name="未讀的奇幻", query="status=未讀&tag=奇幻", position=2)])
        db.flush()
        db.execute(collection_items.insert(), [{"collection_id": weekend.id, "resource_id": r.id, "added_at": now} for r in resources[:6]])
        db.commit()

        original = covers.download
        covers.download = lambda url: (cover_png(int(url.rsplit("/", 1)[1].split(".")[0]), rng.choice([1.3, 1.4, 1.42, 1.5])), "image/png")
        try:
            for resource in resources:
                covers.ensure_cover(db, resource.image_url, force=True)
        finally:
            covers.download = original
        return {"resources": len(resources) + 1, "tags": len(tags)}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 英文版 Windows 預設無法輸出中文
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = build(args.out.resolve())
    print(f"已建立示範典藏館：{args.out}（作品 {result['resources']} 件、標籤 {result['tags']} 個）")


if __name__ == "__main__":
    main()
