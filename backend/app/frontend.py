"""由 API 直接提供打包好的網頁，不需要另外的網頁伺服器：/assets 長期快取，其他路徑都回 index.html（單頁應用）。"""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


class _ImmutableFiles(StaticFiles):
    """打包後的檔名帶內容雜湊，內容變了檔名就變，可以長期快取。"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _public_file(root: Path, path: str) -> Path | None:
    """網址路徑對應到網頁資料夾裡的檔案；不在資料夾內或格式可疑時回傳 None。

    先只用文字判斷，通過後才碰檔案系統：Windows 會把「\\主機\\分享」這類路徑當成網路位置，並自動送出登入憑證（NTLM），
    所以含反斜線、冒號（磁碟代號、資料流）、NUL、開頭為 /、空白段落或 . / .. 的路徑一律不處理。
    """
    if not path or path.startswith("/") or any(mark in path for mark in ("\\", ":", "\x00")):
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    target = root.joinpath(*parts)
    if not target.is_file():
        return None
    resolved = target.resolve()
    return resolved if root in resolved.parents else None  # 連結指到資料夾外也不行


def install(app: FastAPI, directory: Path) -> bool:
    """directory 裡有 index.html 時掛上網頁並回傳 True；沒有（例如還沒打包、開發時改用 npm run dev）則不做事。須在所有 API 之後呼叫。"""
    root = directory.resolve()
    index = root / "index.html"
    if not index.is_file():
        return False
    if (root / "assets").is_dir():
        app.mount("/assets", _ImmutableFiles(directory=root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "找不到這個 API")
        target = _public_file(root, path)
        if target is not None:  # favicon 等 public 檔案
            return FileResponse(target)
        # 首頁每次都向伺服器確認，更新後重新整理就能拿到新版本
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True
