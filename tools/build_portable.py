"""打包 Windows 免安裝版：附上 Python 官方可嵌入版與所需套件、打包好的網頁，解壓縮後雙擊就能用。

    python tools/build_portable.py          # 輸出 dist/OAS-v<版本>-windows-x64.zip

- 程式內容取自 HEAD（已 commit 的版本）；網頁取自 frontend/dist（沒有時會先執行 npm ci 與 npm run build）。
- Python 可嵌入版從 python.org 下載一次後放在 build/cache/，並核對官方公布的 MD5。
- 套件以 pip 下載 Windows 64 位元、Python 3.11 的預先編譯版本，不需要編譯器。
"""
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # oas-next
PYTHON_VERSION = "3.11.9"
PYTHON_TAG = "311"
EMBED = f"python-{PYTHON_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{EMBED}"
EMBED_MD5 = "6d9aa08531d48fcc261ba667e2df17c4"  # python.org 下載頁公布的 MD5
# 從 HEAD 放進免安裝版的檔案（目錄以 / 結尾）
INCLUDE = ["backend/app/", "tools/launch.ps1", "tools/starting.html", "tools/create-shortcut.ps1", "tools/oas.ico", "tools/oas-icon.png", "tools/oas-icon.svg"]
EXCLUDE = {"backend/app/data/tag_pack_private.json"}  # 私人翻譯補充不放進免安裝版
STARTER = '''@echo off
rem 開啟 OAS 典藏館（也可以執行 tools\\create-shortcut.ps1 在桌面建立捷徑）
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\\launch.ps1"
'''


def version() -> str:
    return re.search(r'^VERSION = "([^"]+)"', (ROOT / "backend/app/settings.py").read_text(encoding="utf-8"), re.M).group(1)


def embed_zip() -> Path:
    cache = ROOT / "build" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / EMBED
    if not target.exists():
        print(f"下載 {EMBED_URL}")
        with urllib.request.urlopen(EMBED_URL, timeout=120) as response:
            target.write_bytes(response.read())
    digest = hashlib.md5(target.read_bytes()).hexdigest()
    if digest != EMBED_MD5:
        target.unlink()
        raise SystemExit(f"{EMBED} 的 MD5 不符（{digest}），已刪除；請重新執行。")
    return target


def frontend_dist() -> Path:
    dist = ROOT / "frontend" / "dist"
    if not (dist / "index.html").exists():
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise SystemExit("找不到 frontend/dist，也找不到 npm；請先安裝 Node.js 或手動打包網頁。")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT / "frontend", check=True)
        subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)
    return dist


def build() -> Path:
    stage = ROOT / "build" / "OAS"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    # 1. Python 可嵌入版：讓它找得到套件與 backend（._pth 決定搜尋路徑）
    with zipfile.ZipFile(embed_zip()) as archive:
        archive.extractall(stage / "python")
    (stage / "python" / f"python{PYTHON_TAG}._pth").write_text(
        f"python{PYTHON_TAG}.zip\n.\nLib\\site-packages\n..\\backend\nimport site\n", encoding="ascii")

    # 2. 套件：只取 Windows 64 位元、CPython 3.11 的預先編譯版本
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-compile",
                    "--target", str(stage / "python" / "Lib" / "site-packages"), "--only-binary=:all:",
                    "--platform", "win_amd64", "--python-version", PYTHON_TAG, "--implementation", "cp",
                    "-r", str(ROOT / "backend" / "requirements.txt")], check=True)

    # 3. 程式（取自 HEAD）、網頁、說明與授權
    archive = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=ROOT, capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if member.name in EXCLUDE:
                continue
            if member.isfile() and any(member.name == rule or (rule.endswith("/") and member.name.startswith(rule)) for rule in INCLUDE):
                destination = stage / member.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(tar.extractfile(member).read())
    shutil.copytree(frontend_dist(), stage / "frontend" / "dist")
    for name in ("README.md", "LICENSE"):  # 私人版本放在 tools/public/，公開版本在根目錄
        source = ROOT / "tools" / "public" / name
        shutil.copy2(source if source.exists() else ROOT / name, stage / name)
    (stage / "開啟 OAS.cmd").write_bytes(STARTER.replace("\n", "\r\n").encode("utf-8"))
    for cache in stage.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)

    # 4. 壓縮
    out = ROOT / "dist" / f"OAS-v{version()}-windows-x64.zip"
    out.parent.mkdir(exist_ok=True)
    out.unlink(missing_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                archive.write(path, Path("OAS") / path.relative_to(stage))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # 英文版 Windows（例如 GitHub 的主機）預設無法輸出中文
    result = build()
    print(f"完成：{result}（{result.stat().st_size / 1024 / 1024:.1f} MB）")
