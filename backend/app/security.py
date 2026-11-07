"""請求層級的防護：跨來源設定、主機名稱限制、擋下其他網站送來的寫入請求、手機版的通行碼、安全標頭。"""
import hmac
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

from . import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HOST_HEADER = re.compile(r"([a-z0-9.-]+)(?::\d{1,5})?", re.I)
OPEN_PATHS = {"/api/health"}  # 手機版不用通行碼的路徑：App 等服務就緒時用
# 網頁只跑自己的程式、只連自己的 API；封面還沒存到本機時會直接載入原網站的圖片，所以圖片不限來源。
# style 要 unsafe-inline：畫面有用 style 屬性。
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: http: https:",
    "font-src 'self' data:", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'",
))
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",  # 開外部連結、載封面時，不讓對方知道是從典藏館來的
    "Cross-Origin-Resource-Policy": "same-origin",  # 其他網站不能把封面嵌進去，藉此判斷你收藏了什麼
}
API_DOC_PATHS = {"/docs", "/redoc"}  # FastAPI 的 API 文件頁要載外部程式，不套用 CSP


async def reject_unknown_host(request: Request, call_next):
    """Host 必須完全是允許的主機名稱（可帶連接埠），防 DNS rebinding。

    不用 TrustedHostMiddleware：它只看第一個冒號之前，「127.0.0.1:8080/x?」這種夾著路徑的標頭也會放行。
    """
    match = HOST_HEADER.fullmatch(request.headers.get("host", ""))
    if "*" not in settings.ALLOWED_HOSTS and (match is None or match.group(1).lower() not in settings.ALLOWED_HOSTS):
        return PlainTextResponse("Invalid host header", status_code=400)
    return await call_next(request)


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


async def require_token(request: Request, call_next):
    """手機版：除了 OPEN_PATHS，沒有帶正確通行碼 cookie 的請求一律拒絕（擋同一支手機上的其他 App 讀取或清空典藏）。

    路徑要用 scope["path"]（實際路由用的值）；request.url 是用 Host 標頭組出來的，可以被偽造。
    """
    if settings.API_TOKEN and request.scope["path"] not in OPEN_PATHS:
        if not hmac.compare_digest(request.cookies.get(settings.TOKEN_COOKIE, "").encode(), settings.API_TOKEN.encode()):
            return JSONResponse({"detail": "缺少典藏館的通行碼"}, status_code=401)
    return await call_next(request)


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if request.scope["path"] not in API_DOC_PATHS:
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
    return response


def install(app: FastAPI) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )
    app.middleware("http")(reject_cross_site_writes)
    app.middleware("http")(require_token)
    app.middleware("http")(reject_unknown_host)  # 後加的在外層：先擋主機名稱
    app.middleware("http")(add_security_headers)  # 最外層：被擋下的回應也要有標頭
