"""環境設定：版本、資料位置、允許的來源與主機名稱。所有環境變數集中在這裡讀取。

資料預設放在 %LOCALAPPDATA%\\OAS（Windows）或 ~/.local/share/oas；啟動器以 OAS_DATA_DIR、OAS_BACKUP_DIR 指定其他位置。
"""
import os
from pathlib import Path

VERSION = "0.20.83"


def _split(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "OAS"
    return Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "oas"


# 資料：資料庫、封面快取、備份
DATA_DIR = Path(os.getenv("OAS_DATA_DIR") or _default_data_dir())
DATABASE_URL = os.getenv("OAS_DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'oas.db').as_posix()}"
COVER_DIR = Path(os.getenv("OAS_COVER_DIR") or DATA_DIR / "covers")
BACKUP_DIR = os.getenv("OAS_BACKUP_DIR") or str(DATA_DIR / "backups")
# 打包好的網頁（npm run build 的輸出）；存在時由 API 一併提供，不需要另外的網頁伺服器
FRONTEND_DIR = Path(os.getenv("OAS_FRONTEND_DIR") or Path(__file__).resolve().parents[2] / "frontend" / "dist")

# 另外允許的來源（完整來源，例如 http://127.0.0.1:4000）：CORS 允許清單，寫入的 Origin 檢查也放行。預設沒有：網頁與 API 同源，
# 開發模式的 Vite（5173）也是代理 /api 而不是跨來源呼叫。注意 security.reject_cross_site_api 仍會擋下瀏覽器標成
# Sec-Fetch-Site: cross-site／same-site 的 /api 請求（現行瀏覽器都會送），所以列在這裡的網頁在瀏覽器裡實際上還是呼叫不到；
# 只對不送這個標頭的客戶端有作用。手機版 android_main.py 設的是 App 自己的來源（同源，本來就放行）。
CORS_ORIGINS = _split(os.getenv("OAS_CORS_ORIGINS", ""))
# 典藏館只從本機開啟；其他主機名稱（例如解析到 127.0.0.1 的惡意網域，即 DNS rebinding）一律拒絕。
ALLOWED_HOSTS = [host.lower() for host in _split(os.getenv("OAS_ALLOWED_HOSTS", "localhost,127.0.0.1"))]
# 手機版：同一支手機上的其他 App 也連得到 127.0.0.1，所以 App 每次啟動產生一組通行碼，只放在自己 WebView 的 cookie；
# 有設定時，/api 的請求都要帶這個 cookie（健康檢查除外）。電腦版不設定，行為不變。
API_TOKEN = os.getenv("OAS_API_TOKEN") or ""
PRIVATE_BACKUPS = os.getenv("OAS_PRIVATE_BACKUPS") == "1"  # 手機版：備份在 App 私有空間，備份中心要能列出來還原（android_main.py 設定）
# 版本：OAS_EDITION 有設定時以它為準；沒設定時，app 資料夾裡有標記檔就是公開版（匯出公開版時才放進去），否則是 personal。
# 公開版有第一次啟動的選擇，見 preferences.py。標記檔只看有沒有，內容不讀。
APP_DIR = Path(__file__).resolve().parent
PUBLIC_MARKER = "public_edition.txt"


def detect_edition(env: str | None, app_dir: Path) -> str:
    if env:
        return env
    return "public" if (app_dir / PUBLIC_MARKER).is_file() else "personal"


def detect_platform(env: str | None) -> str:
    """OAS_PLATFORM=android 是手機 App（android_main.py 設定），其他一律當電腦。問哪幾題、哪些手機才有的功能開放看這個。"""
    return "android" if env == "android" else "desktop"


EDITION = detect_edition(os.getenv("OAS_EDITION"), APP_DIR)
PLATFORM = detect_platform(os.getenv("OAS_PLATFORM"))
TOKEN_COOKIE = "oas_token"


def detect_remote(env: str | None, platform: str) -> bool:
    """區網模式：OAS_REMOTE=1 才開，預設關閉。手機 App 一律不開（服務本來就在手機裡，用通行碼）。"""
    return env == "1" and platform != "android"


# 區網模式：家裡其他裝置可以用瀏覽器連到這台電腦（要配對）。啟動器讀 REMOTE_FILE（設定頁的開關寫的）決定要不要設 OAS_REMOTE=1
# 並改綁 0.0.0.0；關閉時一切與沒有這個功能時相同。規則在 access.py、devices.py。
REMOTE = detect_remote(os.getenv("OAS_REMOTE"), PLATFORM)
REMOTE_FILE = DATA_DIR / "remote.json"  # {"enabled": true/false}：下次啟動要不要開區網模式
DEVICE_COOKIE = "oas_device"
IMAGE_ENGINE = os.getenv("OAS_IMAGE_ENGINE") or "pillow"  # "android"：手機公開版用系統內建的解碼器，見 imaging.py
