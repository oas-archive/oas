"""手機版進入點：設定資料位置後，在背景執行緒啟動與電腦版相同的服務（只開放給手機自己）。"""
import os
from pathlib import Path


def start(files_dir: str, backup_dir: str, port: int, token: str, edition: str) -> None:
    import oas_web

    data = Path(files_dir) / "oas"
    os.environ["OAS_DATA_DIR"] = str(data / "data")
    os.environ["OAS_BACKUP_DIR"] = backup_dir  # App 的備份都存在那裡（App 私有空間 files/oas/backups），才看得到最後備份時間
    os.environ["OAS_PRIVATE_BACKUPS"] = "1"  # 備份中心可以列出並還原那裡的備份（檔案選擇器看不到私有空間）
    os.environ["OAS_EDITION"] = edition
    os.environ["OAS_PLATFORM"] = "android"  # 問哪幾題、手機才有的功能看平台（settings.PLATFORM）
    if edition == "public":
        os.environ["OAS_IMAGE_ENGINE"] = "android"  # 公開版沒有帶 Pillow，用系統內建的解碼器（imaging.py）
    os.environ["OAS_FRONTEND_DIR"] = str(Path(oas_web.__file__).parent)
    os.environ["OAS_CORS_ORIGINS"] = f"http://127.0.0.1:{port}"
    os.environ["OAS_API_TOKEN"] = token  # 只有 App 自己的 WebView 有這個 cookie，手機上的其他 App 連進來會被拒絕

    # 先確認埠還沒被別的程式佔用：uvicorn 綁不上時只會丟 SystemExit: 1，畫面上看不出原因
    import socket
    with socket.socket() as probe:
        # 與 uvicorn 相同允許重用：剛結束的連線（TIME_WAIT）不算佔用，只有真的有程式在監聽才算
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"{port} 埠已被手機上的其他 App 佔用，典藏館無法啟動。請關掉那個 App 後再開啟典藏館。") from error

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
