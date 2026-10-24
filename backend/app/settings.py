"""環境設定：版本、資料位置、允許的來源與主機名稱。所有環境變數集中在這裡讀取。

直接在電腦上執行時，資料預設放在 %LOCALAPPDATA%\\OAS（Windows）或 ~/.local/share/oas；
Docker 以 OAS_DATA_DIR=/data 指定掛載的資料磁碟區。
"""
import os
from pathlib import Path

VERSION = "0.15.9"


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

# 允許跨來源讀取 API 的網頁（開發模式的 Vite 與 Docker 的網頁伺服器）
CORS_ORIGINS = _split(os.getenv("OAS_CORS_ORIGINS", "http://localhost:5173,http://localhost:8080"))
# 典藏館只從本機開啟；其他主機名稱（例如解析到 127.0.0.1 的惡意網域，即 DNS rebinding）一律拒絕。
ALLOWED_HOSTS = [host.lower() for host in _split(os.getenv("OAS_ALLOWED_HOSTS", "localhost,127.0.0.1"))]
