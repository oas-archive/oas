"""讀網頁用的共用工具：不針對任何網站。各網站的解析在 site_parsers.py，網站資料在 sites.py。"""
import html
import re
from collections.abc import Iterator
from itertools import islice


class MetadataError(Exception):
    def __init__(self, message: str, blocked: bool = False, status: int | None = None):
        super().__init__(message)
        self.blocked = blocked
        self.status = status  # HTTP 狀態碼（連線失敗時為 None）

    @property
    def rate_limited(self) -> bool:
        """被來源限流或暫時擋下（429、503 或 Cloudflare 防護），等一段時間再試通常就好。"""
        return self.blocked or self.status in (429, 503)

    @property
    def gone(self) -> bool:
        """網站明確回應「找不到」（404、410）：作品已下架，重試也不會好（與巡檢來源同一標準）。"""
        return self.status in (404, 410)


# 正規表達式會佔住整個服務，惡意頁面不能讓它跑很久：解析時限制標籤數量與長度，不用會反覆掃到頁尾的寫法。
TAG_WINDOW = 8192
_TAG_BODY = re.compile(r'[^>"]*(?:"[^"]*"[^>"]*)*>')


def _defuse(page: str) -> str:
    """丟掉一段沒有 > 卻塞滿 < 的文字：每個 < 都要往後掃到底，是解析變慢的主因；真實網頁不會這樣，其餘內容照常解析。"""
    return ">".join(part if part.count("<") <= 50 else "" for part in page.split(">"))


def _tags(page: str, name: str, limit: int = 500) -> Iterator[str]:
    """頁面裡的 <name …> 標籤全文（引號裡可以有 >）；只看前 limit 個，每個最長 TAG_WINDOW 字元。"""
    for match in islice(re.finditer(rf"<{name}\b", page, re.I), limit):
        window = page[match.start():match.start() + TAG_WINDOW]
        end = _TAG_BODY.match(window, match.end() - match.start())
        if end:
            yield window[:end.end()]
        elif ">" in window:  # 引號沒有成對
            yield window[:window.index(">") + 1]


def _between(page: str, opening: str, closing: str) -> str | None:
    """第一個 opening 與之後第一個 closing 之間的文字；找不到時回傳 None。"""
    start = page.find(opening)
    end = page.find(closing, start + len(opening)) if start >= 0 else -1
    return page[start + len(opening):end] if end >= 0 else None


def _src_after(tag: str, position: int = 0) -> str:
    found = re.compile(r'src="([^"]+)"').search(tag, position)
    return found.group(1) if found else ""


def _meta(page: str, name: str) -> str:
    wanted = re.compile(rf'(?:property|name)="{re.escape(name)}"', re.I)
    for tag in _tags(page, "meta"):
        content = re.search(r'content="([^"]*)"', tag, re.I) if wanted.search(tag) else None
        if content:
            return html.unescape(content.group(1)).strip()
    return ""


def _unique(values) -> list[str]:
    seen, result = set(), []
    for value in values:
        value = html.unescape(re.sub(r"<[^>]+>", "", value)).replace("\xa0", " ").strip()
        if value and value.lower() not in seen:
            seen.add(value.lower())
            result.append(value)
    return result
