"""請求層級的防護：跨來源設定、主機名稱限制、擋下其他網站送來的寫入請求。"""
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from . import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def reject_cross_site_writes(request: Request, call_next):
    """會改資料的請求只接受典藏館自己的網頁送出（防止你瀏覽的其他網站在背景操作典藏館）。

    - 帶有其他網站的 Origin 一律拒絕。
    - 必須是 Content-Type: application/json：其他網站要送這種請求，瀏覽器會先詢問（CORS 預檢），而預檢只允許本機網頁。
      不檢查的話，不帶 Content-Type 的 POST 不需預檢，FastAPI 仍會把內容當 JSON 解析（例如還原備份並清空典藏）。
    """
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and (urlsplit(origin).hostname or "").lower() not in settings.ALLOWED_HOSTS:
            return JSONResponse({"detail": "拒絕來自其他網站的請求"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"detail": "請求格式必須是 JSON（Content-Type: application/json）"}, status_code=415)
    return await call_next(request)


def install(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.ALLOWED_HOSTS)
    app.middleware("http")(reject_cross_site_writes)
