"""由 API 直接提供打包好的網頁，不需要另外的網頁伺服器：/assets 長期快取，其他路徑都回 index.html（單頁應用）。

區網模式：還沒配對的其他裝置只拿得到配對頁（pair.html）與它用的檔案（pair/、favicon 這類非 HTML 檔），
主程式（index.html 與 /assets）要配對後才給。/assets 的權限由 access.py 擋（route_access.py 是 READ）；
其他網頁路徑在 route_access.py 是 PUBLIC，由這裡自己判斷有沒有配對。
"""
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import access, devices, settings


UNSAFE_PATH_MARKS = ("\\", ":", "\x00")  # Windows 會把這類路徑當成網路位置或資料流，碰到檔案系統前就要擋下
CLOSED_PATHS = {"docs", "docs/oauth2-redirect", "redoc", "openapi.json"}  # FastAPI 的 API 文件已關閉（main.py），回 404 而不是首頁
PAIR_PAGE = "pair.html"  # 配對頁（frontend/pair.html 打包後）；程式在 pair/ 底下
NO_CACHE = {"Cache-Control": "no-cache"}


class _ImmutableFiles(StaticFiles):
    """打包後的檔名帶內容雜湊，內容變了檔名就變，可以長期快取。"""

    async def get_response(self, path, scope):
        if any(mark in scope["path"] for mark in UNSAFE_PATH_MARKS):
            raise HTTPException(404)
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _public_file(root: Path, path: str) -> Path | None:
    """網址路徑對應到網頁資料夾裡的檔案；不在資料夾內或格式可疑時回傳 None。

    先只用文字判斷，通過後才碰檔案系統：Windows 會把「\\主機\\分享」這類路徑當成網路位置，並自動送出登入憑證（NTLM），
    所以含反斜線、冒號（磁碟代號、資料流）、NUL、開頭為 /、空白段落或 . / .. 的路徑一律不處理。
    """
    if not path or path.startswith("/") or any(mark in path for mark in UNSAFE_PATH_MARKS):
        return None
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    target = root.joinpath(*parts)
    if not target.is_file():
        return None
    resolved = target.resolve()
    return resolved if root in resolved.parents else None  # 連結指到資料夾外也不行


def _unpaired_device(request: Request) -> tuple[bool, str | None]:
    """這次請求是不是「區網模式下、還沒配對（或已失效）的其他裝置」。回傳（是否未配對, 要順延的 cookie token）。

    只讀 devices.py 的判斷，不另外定規則：手機版、區網模式沒開、電腦本機都算已通過。
    """
    if settings.API_TOKEN or not devices.remote_active() or devices.is_local_request(request.scope, request.headers):
        return False, None
    token = request.cookies.get(settings.DEVICE_COOKIE, "")
    device, refreshed = devices.authenticate(token, devices.client_host(request.scope))
    if device is None:
        return True, None
    return False, token if refreshed else None


def install(app: FastAPI, directory: Path) -> bool:
    """directory 裡有 index.html 時掛上網頁並回傳 True；沒有（例如還沒打包、開發時改用 npm run dev）則不做事。須在所有 API 之後呼叫。"""
    root = directory.resolve()
    index = root / "index.html"
    if not index.is_file():
        return False
    if (root / "assets").is_dir():
        app.mount("/assets", _ImmutableFiles(directory=root / "assets"), name="assets")

    pair_page = root / PAIR_PAGE

    @app.get("/{path:path}", include_in_schema=False)
    def page(path: str, request: Request):
        unpaired, refresh_token = _unpaired_device(request)
        if path == "api" or path.startswith("api/") or path in CLOSED_PATHS:
            if unpaired:  # 和 access.py 一樣回 401：沒配對的裝置分不出哪些 API 存在
                return JSONResponse({"detail": "這台裝置還沒配對，或已經失效", "pair": True}, status_code=401)
            raise HTTPException(404, "找不到這個 API")
        target = _public_file(root, path)
        if unpaired:
            # 只給配對頁要用的檔案；任何 HTML（含 index.html）與其他路徑都回配對頁。沒有打包配對頁時不給任何網頁
            if target is not None and target.suffix.lower() not in (".html", ".htm"):
                return FileResponse(target)
            if not pair_page.is_file():
                raise HTTPException(401, "這台裝置還沒配對")
            return FileResponse(pair_page, headers=NO_CACHE)
        if target is not None:  # favicon 等 public 檔案
            response = FileResponse(target)
        else:  # 首頁每次都向伺服器確認，更新後重新整理就能拿到新版本
            response = FileResponse(index, headers=NO_CACHE)
        if refresh_token:  # 有用就順延 90 天（和 API 一樣，見 access.py）
            access.set_device_cookie(response, refresh_token)
        return response

    return True
