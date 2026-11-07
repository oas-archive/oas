"""程式認得的網站：網址、搜尋頁、簡稱、限流與防護設定都放這裡（解析規則在 site_parsers.py）。

要拿掉某個網站：刪掉這裡與 site_parsers.py 的相關項目，alternatives.py 有用到的也要改；步驟見 docs/架構.md。
其他程式不直接寫網站名稱，tests/test_sites.py 會檢查。
"""
import re
import urllib.parse

# 程式有支援的網站：nhentai、紳士漫畫、Hanime1 可直接讀取資料；禁漫天堂、MissAV 有防護，請用「收錄到 OAS」書籤。
SOURCES = [
    ("其他網站", "{}", "其他"),
    ("nhentai", "https://nhentai.net/g/{}/", "漫畫"),
    ("紳士漫畫", "https://www.wnacg.com/photos-index-aid-{}.html", "漫畫"),
    ("Hanime1", "https://hanime1.me/watch?v={}", "動畫"),
    ("禁漫天堂", "https://18comic.vip/album/{}/", "漫畫"),
    ("MissAV", "https://missav.ai/{}", "影片"),
    ("HentaiHaven", "https://hentaihaven.xxx/{}", "動畫"),
    ("Pornhub", "https://www.pornhub.com/{}", "影片"),
]

# 只輸入數字時（俗稱「神的語言」）當成這個網站的作品編號
NUMBER_URL = "https://nhentai.net/g/{}/"
NHENTAI_SOURCE, WNACG_SOURCE, JM_SOURCE = "nhentai", "紳士漫畫", "禁漫天堂"
SLOW_HOSTS = ("nhentai.net", "wnacg.com", "hanime1.me")

TITLE_SUFFIXES = [
    r"\s*»\s*nhentai.*$", r"\s*-\s*H動漫.*$", r"\s*-\s*Hanime1\.me\s*$", r"\s*-\s*紳士漫畫.*$", r"\s*[|-]\s*禁漫天堂.*$", r"\s*-\s*MissAV.*$",
    r"\s*\|\s*H漫內頁瀏覽.*$", r"\s+Comics\s*-?\s*$",  # 禁漫頁面標題（分享時帶進來的）結尾的「Comics - 禁漫天堂」
    r"\s*[|-]\s*Hentai Haven\b.*$", r"\s*-\s*Pornhub\.com\s*$",
]
TITLE_SITE_NAMES = r"Pornhub|SpankBang|XVIDEOS|JKF\b.*|.*捷克論壇|Myself\b.*|FC2成人影音|Netflav|禁漫天堂|紳士漫畫|nhentai|MissAV|Hanime1|Hentai Haven|JAV HD Porn"
TITLE_NOISE_WORDS = "禁漫天堂"

# 有防護、伺服器讀不到的網站：背景補資料與來源巡檢都直接略過，請改用「收錄到 OAS」書籤。
PROTECTED_HOSTS = ("18comic", "jmcomic", "missav")

# 有防護的網站裡，封面網址有固定規則、圖片伺服器也讀得到的：作品頁被擋，但封面可以依編號直接推得
KNOWN_COVERS = (
    (re.compile(r"^https?://[^/]*(?:18comic|jmcomic)[^/]*/(?:album|photo)/(\d+)", re.I), "https://cdn-msp.18comic.vip/media/albums/{}.jpg"),
    (re.compile(r"^https?://[^/]*missav[^/]*/(?:[^/?#]+/)*([a-z0-9]+(?:-[a-z0-9]+)+)/?(?:[?#].*)?$", re.I), "https://fourhoi.com/{}/cover-n.jpg"),
)

# 同一個網站的鏡像網域（分享時常是 missav.ws、18comic.org 這類）：比對網址規則前換成範本用的網域，才認得出是同一件
MIRRORS = ((re.compile(r"^(?:18comic|jmcomic\d*)\.[a-z]+$"), "18comic.vip"), (re.compile(r"^missav\d*\.[a-z]+$"), "missav.ai"))
# 語言或手機版的子網域，和主網域是同一個網站。和 MIRRORS 不同的是啟動時不會回頭整理舊資料
HOST_ALIASES = ((re.compile(r"^m\.wnacg\.com$"), "wnacg.com"), (re.compile(r"^[a-z]{2}\.pornhub\.com$"), "pornhub.com"))
# 代碼只取網址最後一段的網站：MissAV 的網址前面常多一段分類（dm26/abc-745、cn/abc-745），番號才是作品
LAST_SEGMENT_HOSTS = ("missav",)
# 同一件作品的不同頁面：禁漫的閱讀頁和作品頁、紳士的分頁、閱讀與下載頁、Hanime1 的下載頁
PATH_REWRITES = {
    "18comic.vip": (r"^/photo/(\d+)", r"/album/\1"),
    "wnacg.com": (r"^/(?:photos-index-page-\d+|photos-slide|photos-gallery|download-index)-aid-(\d+)", r"/photos-index-aid-\1"),
    "hanime1.me": (r"^/download$", "/watch"),
}
# 作品不存在時會轉到的錯誤頁（HTTP 200）
MISSING_PAGES = (("/error/album_missing", "禁漫：作品不存在"),)
# 「其他網站」裡能從網域看出類型的（例如 hentaihaven 的其他網域是動畫）
HOST_KINDS = (("hentaihaven", "動畫"), ("hanime", "動畫"), ("myself-bbs", "動畫"),
              ("jable", "影片"), ("javgg", "影片"), ("netflav", "影片"), ("av01", "影片"), ("spankbang", "影片"), ("xvideos", "影片"), ("javhdporn", "影片"))

# 編號簡寫（N123456、W123456、JM123456 與「N：」分段）：只有漫畫網站。影片與其他網站只收完整網址
# 簡寫 → （來源名稱, 作品頁）
SHORT_CODE_URLS = {
    "n": ("nhentai", NUMBER_URL),
    "w": ("紳士漫畫", "https://www.wnacg.com/photos-index-aid-{}.html"),
    "jm": ("禁漫天堂", "https://18comic.vip/album/{}/"),
}
SECTION_ALIASES = {"n": "n", "n網": "n", "nhentai": "n", "w": "w", "紳士": "w", "紳士漫畫": "w", "wnacg": "w", "jm": "jm", "禁漫": "jm", "禁漫天堂": "jm", "18comic": "jm"}
# 寫了會說明「要貼完整網址」的分段標題（不分大小寫）；其他文字不算分段標題
NO_SHORT_CODE_HEADERS = {"missav", "ma", "hanime", "hanime1", "h", "hentaihaven", "hh", "pornhub", "ph", "動畫", "影片", "人", "找"}
# 沒指定網站的標題文字到這些網站找
COMIC_SEARCH = ["nhentai", "紳士漫畫", "禁漫天堂"]
SEARCH_PAGES = {
    "nhentai": "https://nhentai.net/search/?q={}",
    "紳士漫畫": "https://www.wnacg.com/search/?q={}&f=_all&s=create_time_DESC&syn=yes",
    "禁漫天堂": "https://18comic.vip/search/photos?search_query={}",
}
# 同一部影片的不同版本：網址尾端不同，但算同一件，不重複收錄
VERSION_SUFFIXES = {"MissAV": ("-uncensored-leak", "-chinese-subtitle", "-english-subtitle")}
