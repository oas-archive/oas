"""打包 Windows 免安裝版：附上 Python 官方可嵌入版與所需套件、打包好的網頁，解壓縮後雙擊就能用。

    python tools/build_portable.py          # 輸出 dist/OAS-v<版本>-windows-x64.zip

- 程式內容取自 HEAD（已 commit 的版本）；網頁取自 frontend/dist（沒有、或比最新的前端 commit 舊時，會先執行 npm ci 與 npm run build）。
- Python 可嵌入版從 python.org 下載一次後放在 build/cache/，並核對官方簽署的 SHA-256。
- 套件以 pip 下載 Windows 64 位元、Python 3.13 的預先編譯版本，不需要編譯器。執行本腳本的 Python 版本不必相同
  （pip 依 --python-version 取內嵌版用的 wheel），但要 3.11 以上。
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
PYTHON_VERSION = "3.13.17"  # 3.13 最後一個附 Windows 可嵌入版的修正版（3.13.18 起只發原始碼）
PYTHON_TAG = "".join(PYTHON_VERSION.split(".")[:2])  # "313"：._pth 檔名與 pip 的 --python-version
EMBED = f"python-{PYTHON_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{EMBED}"
# 來源：python.org 隨檔案發布的 Sigstore 簽章包（下載網址加上 .sigstore）裡簽署的摘要，簽署者 thomas@python.org（3.13 發布負責人，
# 見 python.org/downloads/metadata/sigstore/ 與 PEP 719），也記在 Sigstore 公開紀錄 Rekor（logIndex 3358594795，2026-10-21）。
# 2026-11-12 查證：sigstore verify identity 驗證通過，自行下載算出的值相同。下載頁不公布 SHA-256，所以不從那裡取；換 PYTHON_VERSION 時要一起換。
EMBED_SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
# 從 HEAD 放進免安裝版的檔案（目錄以 / 結尾）
INCLUDE = ["backend/app/", "tools/launch.ps1", "tools/enable-lan-firewall.ps1", "tools/starting.html", "tools/create-shortcut.ps1", "tools/oas.ico", "tools/oas-icon.png", "tools/oas-icon.svg"]
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
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != EMBED_SHA256:
        target.unlink()
        raise SystemExit(f"{EMBED} 的 SHA-256 不符（{digest}），已刪除；請重新執行。")
    return target


def frontend_dist() -> Path:
    dist = ROOT / "frontend" / "dist"
    index = dist / "index.html"
    changed = subprocess.run(["git", "log", "-1", "--format=%ct", "--", "frontend"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    if not index.exists() or index.stat().st_mtime < int(changed or 0):
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

    # 2. 套件：只取 Windows 64 位元、與內嵌版同版本（PYTHON_TAG）CPython 的預先編譯版本；用主機的 pip 下載，主機版本不必相同
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
    for name in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):  # 私人版本放在 tools/public/，公開版本在根目錄
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
