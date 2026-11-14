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
    "倉庫與仙人掌", "修鞋店帳本", "會計師的拼圖", "十四號雨傘", "屋頂番茄紀錄", "舊電扇修理", "腳踏車店門牌", "河堤晾衣繩", "窗台番茄苗", "測量員抽屜",
    "麵粉袋鉛筆字", "警衛與收音機", "鍋爐房十二月", "郵票簿星期天", "便當店菜單", "燈泡工廠午休", "石板路皮箱", "橘子箱信紙", "洗衣店長椅", "氣象站值班表",
    "磁磚師傅筆記", "書攤折角頁", "鐵皮屋與水桶", "渡船口長凳", "毛線店鸚鵡", "文具行鑰匙", "菜市場秤錘", "時刻表草稿", "印刷廠墨水", "溫室八號花盆",
    "倉鼠與修錶師", "走廊的盆栽", "碼頭倉庫帆布", "水電行週末", "樓梯間燈泡", "豆漿店鐵門", "果菜行竹籃", "油漆行調色盤", "雜貨店玻璃罐", "鐘錶行抽屜",
]
GENERAL = ["奇幻", "冒險", "職場", "科幻", "懸疑", "日常", "戀愛", "喜劇", "治癒", "料理", "旅行", "音樂", "偵探", "魔法",
           "機器人", "貓", "海洋", "星空", "歷史", "運動"]
ARTISTS = ["左撇子繪師", "鉛筆屑先生", "慢吞吞的筆", "灰鴿子畫室", "第九張草稿", "橡皮擦大叔"]
GROUPS = ["三號倉庫工作室", "晾衣繩社", "石板路出版", "藍色門牌工房"]
PARODIES = ["原創作品", "虛構短篇集"]
CHARACTERS = ["倉庫管理員", "灰色鸚鵡", "夜班警衛"]
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
