"""請求層級的防護：跨來源設定、主機名稱限制、擋下其他網站送來的寫入請求、手機版的通行碼、安全標頭。"""
import hmac
import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import settings

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
HOST_HEADER = re.compile(r"([a-z0-9.-]+)(?::\d{1,5})?", re.I)
OPEN_PATHS = {"/api/health"}  # 手機版不用通行碼的路徑：App 等服務就緒時用
# 網頁只跑自己的程式、只連自己的 API，圖片也只准本機：所有版本的封面與預覽都由後端抓（covers.stored_cover、covers.remote_image），
# 畫面不直接連外部網站。外部網址可以轉址回手機上的其他埠，瀏覽器會帶著通行碼 cookie 跟過去。style 要 unsafe-inline：畫面有用 style 屬性。
def content_security_policy() -> str:
    return "; ".join((
        "default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data: blob:",
        "font-src 'self' data:", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'",
    ))


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",  # 開外部連結、載封面時，不讓對方知道是從典藏館來的
    "Cross-Origin-Resource-Policy": "same-origin",  # 其他網站不能把封面嵌進去，藉此判斷你收藏了什麼
}

# 電腦版的啟動畫面（tools/starting.html）是 file:// 頁面，等服務就緒時用 no-cors 的 fetch 探測。
# 上面的 CORP same-origin 會讓瀏覽器擋下這種跨來源回應（ERR_BLOCKED_BY_RESPONSE.NotSameOrigin），啟動畫面就永遠不會跳轉，
# 所以只有這個路徑改成 cross-origin：回應是空的 204、不經過資料庫、不在 /api 底下，其他網站最多只能知道「典藏館正在執行」。
# 手機版仍要通行碼（不在 OPEN_PATHS），沒帶的回 401 且維持 same-origin。
LAUNCH_PROBE_PATH = "/launch-probe"


async def reject_unknown_host(request: Request, call_next):
    """Host 必須完全是允許的主機名稱（可帶連接埠），防 DNS rebinding。

    不用 TrustedHostMiddleware：它只看第一個冒號之前，「127.0.0.1:8080/x?」這種夾著路徑的標頭也會放行。
    """
    match = HOST_HEADER.fullmatch(request.headers.get("host", ""))
    if "*" not in settings.ALLOWED_HOSTS and (match is None or match.group(1).lower() not in settings.ALLOWED_HOSTS):
        return PlainTextResponse("Invalid host header", status_code=400)
    return await call_next(request)


def _origin(url: str):
    """把來源拆成（scheme, 主機名稱, 連接埠），沒寫連接埠時補預設值；不是單純的 http(s)://主機[:埠] 就回 None。"""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None
    return parts.scheme, parts.hostname.lower(), port or (443 if parts.scheme == "https" else 80)


def is_own_origin(request: Request, origin: str) -> bool:
    """Origin 要和這次請求的來源完全相同（scheme、主機、連接埠都比），或是 OAS_CORS_ORIGINS 明確列出的來源。

    列在 OAS_CORS_ORIGINS 的來源在瀏覽器裡仍會被 reject_cross_site_api 擋下（Sec-Fetch-Site 是 cross-site／same-site），
    這裡放行只對不送這個標頭的客戶端有用，見 settings.py。

    自己的來源用 Host 推得：桌面捷徑是 http://localhost:8080、手機是 http://127.0.0.1:<埠>、
    npm run dev 經代理時是 http://localhost:5173（vite.config.js 保留原 Host）。Host 已由 reject_unknown_host 檢查過。
    只比主機名稱不夠：同一台電腦其他連接埠的網頁（例如另一個專案的開發伺服器）也是 localhost。
    """
    parsed = _origin(origin)
    if parsed is None:
        return False
    host = request.headers.get("host", "")
    if HOST_HEADER.fullmatch(host) and parsed == _origin(f"{request.scope['scheme']}://{host}"):
        return True
    return parsed in {_origin(allowed) for allowed in settings.CORS_ORIGINS}


async def reject_cross_site_writes(request: Request, call_next):
    """會改資料的請求只接受典藏館自己的網頁送出（防止你瀏覽的其他網站在背景操作典藏館）。

    - 帶有其他來源的 Origin 一律拒絕（含同一台電腦其他連接埠的網頁），見 is_own_origin。
    - 必須是 Content-Type: application/json：其他網站要送這種請求，瀏覽器會先詢問（CORS 預檢），而預設不允許任何跨來源。
      不檢查的話，不帶 Content-Type 的 POST 不需預檢，FastAPI 仍會把內容當 JSON 解析（例如還原備份並清空典藏）。
    """
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and not is_own_origin(request, origin):
            return JSONResponse({"detail": "拒絕來自其他網站的請求"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            return JSONResponse({"detail": "請求格式必須是 JSON（Content-Type: application/json）"}, status_code=415)
    return await call_next(request)


CROSS_SITE_FETCH = {"cross-site", "same-site"}


async def reject_cross_site_api(request: Request, call_next):
    """/api 拒絕其他網站觸發的請求（瀏覽器的 Sec-Fetch-Site 是 cross-site 或 same-site），讀取也一樣。

    擋的是：其他網頁用 <img src=".../api/covers/preview?url=…"> 之類叫典藏館連外（佔住抓圖名額）、或連結到 /api/backup。
    same-site 也要擋：localhost 的不同連接埠算同一個 site，同機另一個開發伺服器（例如 5173）的網頁送來就是 same-site。
    合法的請求都不是這兩種：
    - 典藏館自己的網頁（桌面 localhost:8080、手機 WebView 127.0.0.1:<埠>）呼叫 /api、載入封面是 same-origin。
    - npm run dev 時網頁與代理的 /api 都在 localhost:5173，也是 same-origin。
    - 「收錄到 OAS」書籤是開 /?add=… 的頁面導覽，不是 /api；開好的頁面再呼叫 /api 是 same-origin。
    - 在網址列直接開是 none；手機 App 外殼（HttpURLConnection）、啟動器與工具腳本不帶這個標頭，都放行。
    沒有這個標頭的舊瀏覽器靠 Origin 檢查與 CORS 擋寫入，讀取的回應也讀不到。
    """
    if request.scope["path"].startswith("/api/") or request.scope["path"] == "/api":
        if request.headers.get("sec-fetch-site", "").strip().lower() in CROSS_SITE_FETCH:
            return JSONResponse({"detail": "拒絕其他網站觸發的請求"}, status_code=403)
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
    response.headers.setdefault("Content-Security-Policy", content_security_policy())
    return response


def launch_probe() -> Response:
    """啟動畫面的就緒探測：只回空的 204，只有這裡放寬 CORP（見 LAUNCH_PROBE_PATH）。"""
    return Response(status_code=204, headers={"Cross-Origin-Resource-Policy": "cross-origin", "Cache-Control": "no-store"})


def install(app: FastAPI) -> None:
    # 要在網頁的「其他路徑都回 index.html」之前登記（main.py 先呼叫這裡，最後才掛網頁）
    app.add_api_route(LAUNCH_PROBE_PATH, launch_probe, methods=["GET"], include_in_schema=False)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_methods=["*"], allow_headers=["*"], allow_credentials=False,
    )
    app.middleware("http")(reject_cross_site_writes)
    app.middleware("http")(reject_cross_site_api)  # 健康檢查也套用：只有 App 外殼與啟動器會叫它，都不帶 Sec-Fetch-Site
    app.middleware("http")(require_token)
    app.middleware("http")(reject_unknown_host)  # 後加的在外層：先擋主機名稱
    app.middleware("http")(add_security_headers)  # 最外層：被擋下的回應也要有標頭
