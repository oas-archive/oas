"""各網站作品頁的解析規則。要拿掉某個網站：刪掉它的函式與 PARSERS 那一項，並改 sites.py（見 docs/架構.md）。"""
import html
import re
from urllib.parse import unquote

from .pagetext import MetadataError, _between, _meta, _src_after, _tags, _unique


def parse_nhentai(page: str) -> dict:
    alt, image = "", ""
    start = page.find('id="cover"')
    for tag in _tags(page[start:], "img", 20) if start >= 0 else ():
        found = re.search(r'alt="([^"]*)"', tag)
        if found and (image := _src_after(tag, found.end())):
            alt = found.group(1)
            break
    pairs = re.findall(r'href="/(parody|character|tag|artist|group|language|category)/[^"]+"[^>]*>.{0,1000}?<span class="name[^"]*">(?:<!--[^>]*-->)*([^<]+)</span>', page, re.S)
    categories = {html.unescape(name).strip(): namespace for namespace, name in pairs}
    return {
        "title": alt if image else _meta(page, "og:title"), "image_url": image or _meta(page, "og:image"),
        "tags": [name for _, name in pairs], "categories": categories,
    }


def parse_wnacg(page: str) -> dict:
    title = _between(page, "<h2>", "</h2>")
    if title is None and "不存在" in page:  # 「您要訪問的漫畫不存在！」：可能已刪除，也可能要登入才看得到，分不出來，所以不當成下架
        raise MetadataError("紳士：訪客看不到這本（可能已刪除，或要登入才看得到）")
    title = html.unescape(title).strip() if title else ""
    wanted, cover, fallback = f'alt="{html.escape(title, quote=True)}"', "", ""
    for tag in _tags(page, "img", 1000):
        if title and wanted in tag and (cover := _src_after(tag, tag.index(wanted))):
            break
        fallback = fallback or (re.search(r'src="(//[^"]*qy0\.ru/data/t/[^"]+)"', tag) or [None, ""])[1]
    image = re.sub(r"^/+", "https://", cover or fallback)
    return {"title": title, "image_url": image, "tags": re.findall(r'class="tagshow"[^>]*>([^<]+)</a>', page)}


def parse_hanime1(page: str) -> dict:
    """Hanime1：標籤在 single-video-tag；作者列（id="video-artist-name"）是製作公司，連結帶著這部的類型（裏番、泡麵番、3DCG、Motion Anime…）。
    類型標成「類別」、公司標成「社團」，可以拿來篩選；頁首選單也有各類型的連結，但不在作者列，不會混進來。"""
    tags = re.findall(r'class="single-video-tag"[^>]*>\s*<a[^>]*>([^<&]+)', page)
    artist = re.search(r'<a[^>]*id="video-artist-name"[^>]*href="[^"]*?genre=([^"&]+)"[^>]*>\s*([^<]+?)\s*</a>', page)
    categories = {}
    if artist:
        genre, studio = unquote(html.unescape(artist.group(1))).strip(), html.unescape(artist.group(2)).strip()
        tags += [genre, studio]
        categories = {genre: "category", studio: "group"}
    return {"title": _meta(page, "og:title"), "image_url": _meta(page, "og:image"), "tags": tags, "categories": categories}


def parse_jm(page: str) -> dict:
    """禁漫作品頁（伺服器有時讀得到，例如在手機上）：標題在 id="book-name"，標籤、作者、登場人物在 data-type 的區塊。
    頁面的 og:image 是網站 logo、keywords 混著全站分類、.tag-block 是側欄熱門標籤，都不採用；封面由編號推得。
    未開放的作品會轉到首頁（沒有 book-name），當成沒讀到。canonical 是正式的作品編號（章節編號會轉到這裡）。"""
    heading = re.search(r'<h1[^>]*id="book-name"[^>]*>', page)
    end = page.find("</h1>", heading.end()) if heading else -1
    if end < 0:
        return {"title": "", "image_url": "", "tags": []}

    def block(kind: str) -> list[str]:
        found = re.search(rf'<span[^>]*data-type="{kind}"[^>]*>', page)
        close = page.find("</span>", found.end()) if found else -1
        return re.findall(r"<a[^>]*>([^<]+)</a>", page[found.end():close]) if close >= 0 else []
    authors, actors, tags = block("author"), block("actor"), block("tags")
    canonical = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', page)
    categories = {html.unescape(name).strip(): namespace for namespace, names in (("artist", authors), ("character", actors)) for name in names}
    return {"title": page[heading.end():end], "image_url": "", "tags": [*tags, *authors, *actors], "categories": categories,
            "canonical": canonical.group(1) if canonical else ""}


def parse_hentaihaven(page: str) -> dict:
    """HentaiHaven：類型是作品資訊裡 data-variant="chip" 的 /series/ 連結（手機版與桌面版各一份，去重）；選單裡的 /series/ 連結沒有 chip，不採用。"""
    genres = re.findall(r'<a[^>]*data-variant="chip"[^>]*href="/series/[^"]*"[^>]*>([^<]+)</a>', page)
    studios = _unique(re.findall(r'<a[^>]*data-variant="chip"[^>]*href="/studio/[^"]*"[^>]*>([^<]+)</a>', page))  # 有些作品頁有「Studio」
    return {"title": _meta(page, "og:title"), "image_url": _meta(page, "og:image"), "tags": [*genres, *studios],
            "categories": {name: "group" for name in studios}}


# 畫質、認證標記，不是內容（看連結不看文字：手機上的頁面會翻成中文）
PORNHUB_NOISE = re.compile(r"^/hd$|[?&]c=(?:105|138|139|482)\b|verified", re.I)


def parse_pornhub(page: str) -> dict:
    """Pornhub 影片頁：影片下方的 Categories（data-label="category"）與 Tags（data-label="tag"）；上傳者與 Pornstars 標成作者。
    電腦版頁面只看 video-detailed-info 那一塊（推薦影片裡的上傳者不在那裡）；手機版沒有那一塊，看整頁，上傳者在 usernameWrapper。"""
    marker = 'class="video-detailed-info"'
    info = page[page.find(marker):] if marker in page else page

    def labelled(label: str) -> list[str]:
        names = []
        for tag, name in re.findall(rf'(<a[^>]*data-label="{label}"[^>]*>)(?:\s*<span>)?\s*([^<]+?)\s*(?:</span>\s*)?</a>', info):
            href = (re.search(r'href="([^"]*)"', tag) or [None, ""])[1]
            if not PORNHUB_NOISE.search(href) and "data-translated" not in tag:  # 手機版另有一份機器翻譯的標籤，只留原文那份
                names.append(name)
        return names
    uploader = (re.search(r'class="usernameWrap.{0,2000}?<a[^>]*href="/(?:model|channels|users|pornstar)/[^"]*"[^>]*>([^<]+)</a>', info, re.S) if marker in page
                else re.search(r'class="usernameWrapper">\s*<a[^>]*href="/(?:model|channels|users|pornstar)/[^"]*"[^>]*>([^<]+)</a>', page))
    people = _unique([*([uploader.group(1)] if uploader else []), *labelled("pornstar")])
    return {"title": _meta(page, "og:title"), "image_url": _meta(page, "og:image"), "tags": [*labelled("category"), *labelled("tag"), *people],
            "categories": {name: "artist" for name in people}}


MISSAV_KINDS = {"actresses": "artist", "genres": "tag", "tags": "tag", "series": "tag", "makers": "group", "labels": "group", "directors": "artist"}


def parse_missav(page: str) -> dict:
    """MissAV 影片頁（伺服器通常被擋，手機上偶爾讀得到）：資料在 class="text-secondary" 的各列（女優、類型、系列、發行商、標籤…），
    依連結路徑分類，不看文字標籤（網站語言不同時文字會變）。頁首選單的 /genres/ 連結不在這些列裡。"""
    names, categories = [], {}
    for part in page.split('<div class="text-secondary">')[1:101]:
        row = part.split("</div>", 1)[0] if "</div>" in part else ""
        for kind, name in re.findall(r'<a[^>]*href="[^"]*/(actresses|genres|tags|series|makers|labels|directors)/[^"]*"[^>]*>([^<]+)</a>', row):
            names.append(name)
            if MISSAV_KINDS[kind] != "tag":
                categories[html.unescape(name).strip()] = MISSAV_KINDS[kind]
    return {"title": _meta(page, "og:title"), "image_url": _meta(page, "og:image"), "tags": names, "categories": categories}


PARSERS = {"nhentai.net": parse_nhentai, "wnacg.com": parse_wnacg, "hanime1.me": parse_hanime1, "18comic.vip": parse_jm,
           "hentaihaven.xxx": parse_hentaihaven, "pornhub.com": parse_pornhub, "missav.ai": parse_missav}
