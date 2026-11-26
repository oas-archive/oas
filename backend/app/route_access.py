"""路由分類表：每一條路由（方法＋路徑範本）屬於哪一級權限。只在區網模式（settings.REMOTE）判斷其他裝置能不能用時查這張表。

級別：
- PUBLIC：不用登入。健康檢查、配對（POST /api/pair）、網頁路徑（/{path:path}：還沒配對的裝置只會拿到配對頁，frontend.py 自己判斷）。
- READ：已配對的裝置都能用（唯讀、標準）。瀏覽、搜尋、首頁、統計、封面。
- STANDARD：「標準」級裝置才能用。新增、編輯、評分、書單、標籤整理、復原、背景工作。
- LOCAL：只限電腦本機，任何裝置都不給。還原備份、下載備份、清除封面、改來源網址規則、匯入翻譯包、裝置管理。

新增路由一定要加進這張表：tests/test_route_access.py 會列舉 app 的所有路由，沒分類（或表裡有已不存在的路由）就失敗。
拿不準就放 LOCAL（最嚴）。GET 但會連外或洩漏整份收藏的（封面預覽、備份）不能放 READ。
"""
from starlette.routing import Match, WebSocketRoute

PUBLIC = "public"
READ = "read"
STANDARD = "standard"
LOCAL = "local"
LEVELS = (PUBLIC, READ, STANDARD, LOCAL)

# 裝置級別 → 可以用的路由級別
DEVICE_LEVELS = {"readonly": {PUBLIC, READ}, "standard": {PUBLIC, READ, STANDARD}}

_READ = [
    "/launch-probe",
    "/api/preferences", "/api/undo/latest", "/api/search/suggest",
    "/api/resources", "/api/resources/{resource_id}", "/api/resources/{resource_id}/related",
    "/api/collections",
    "/api/tags/categories", "/api/tags", "/api/tags/stats", "/api/tags/rules", "/api/tags/suggestions", "/api/tags/profile",
    "/api/deep-dive", "/api/dashboard", "/api/explore", "/api/home", "/api/insights/taste", "/api/insights/tags",
    "/api/duplicates", "/api/duplicates/of/{resource_id}", "/api/duplicates/dismissed", "/api/stats",
    "/api/sources", "/api/covers/status", "/api/covers/stored", "/api/covers/{resource_id}",
    "/api/attention", "/api/attention/summary", "/api/intake/status", "/api/refresh/status", "/api/linkcheck/status",
    "/api/linkcheck/jm/targets", "/api/system/status",
    "/api/devices/me",
    # 網頁的主程式（frontend.py 的 /assets）：配對後才給。其他網頁路徑見 _PUBLIC
    "/assets",
]

_STANDARD_GET = [
    "/api/covers/preview",  # 會替對方連外抓圖：新增作品時用，唯讀裝置不需要
]

_STANDARD_WRITE = [
    ("POST", "/api/undo/{entry_id}"),
    ("POST", "/api/resources"), ("PATCH", "/api/resources/{resource_id}"), ("PATCH", "/api/resources/{resource_id}/quick"),
    ("POST", "/api/resources/{resource_id}/open"), ("POST", "/api/resources/{resource_id}/fill-blanks"),
    ("POST", "/api/resources/{resource_id}/alternatives"), ("POST", "/api/resources/{resource_id}/site-icon"),
    ("POST", "/api/resources/{resource_id}/relink"), ("POST", "/api/resources/batch"), ("DELETE", "/api/resources/{resource_id}"),
    ("POST", "/api/resources/{resource_id}/merge"),
    ("POST", "/api/collections"), ("PATCH", "/api/collections/{collection_id}"), ("DELETE", "/api/collections/{collection_id}"),
    ("POST", "/api/collections/{collection_id}/items"),
    ("POST", "/api/tags/merge"), ("PUT", "/api/tags/rules"), ("DELETE", "/api/tags/rules"), ("POST", "/api/tags/suggestions/skip"),
    ("POST", "/api/tags/unblock"), ("POST", "/api/tags/remove"), ("POST", "/api/tags/remove-many"), ("PATCH", "/api/tags/category"),
    ("POST", "/api/tags/cleanup"),
    ("POST", "/api/insights/tags/keep-pairs"), ("POST", "/api/insights/tags/keep"),
    ("POST", "/api/duplicates/dismiss"), ("POST", "/api/duplicates/undismiss"),
    ("POST", "/api/covers/cache"),
    ("POST", "/api/metadata"), ("POST", "/api/import/parse"), ("POST", "/api/import/search"), ("POST", "/api/import/urls"),
    ("POST", "/api/attention/retry"), ("POST", "/api/attention/same-code"), ("POST", "/api/attention/fetch-manual"),
    ("POST", "/api/attention/jm-candidates"), ("POST", "/api/attention/fill-page"),
    ("POST", "/api/refresh"), ("POST", "/api/refresh/stop"), ("POST", "/api/linkcheck/start"), ("POST", "/api/linkcheck/stop"),
    ("POST", "/api/linkcheck/jm/result"),
]

_LOCAL = [
    ("GET", "/api/backup"), ("GET", "/api/backup/fingerprint"), ("GET", "/api/backup/files"), ("GET", "/api/backup/files/{name}"),
    ("POST", "/api/restore"),
    ("POST", "/api/covers/clear"),
    ("PUT", "/api/sources"),
    ("POST", "/api/tag-pack/mine"),
    ("PATCH", "/api/preferences"),  # 公開版第一次啟動的設定（年齡、用途）
    # 裝置管理與區網模式開關（routers/devices.py）
    ("GET", "/api/devices"), ("PATCH", "/api/devices/{device_id}"), ("POST", "/api/devices/{device_id}/revoke"),
    ("POST", "/api/devices/revoke-all"), ("GET", "/api/devices/events"),
    ("GET", "/api/devices/remote"), ("PUT", "/api/devices/remote"),
    ("GET", "/api/devices/pairing"), ("POST", "/api/devices/pairing"), ("DELETE", "/api/devices/pairing"),
    ("GET", "/api/devices/checks"),  # 讀這台電腦的網路類型、防火牆規則（系統資訊）
    ("POST", "/api/devices/firewall"),  # 開系統管理員視窗改防火牆
]

_PUBLIC = [
    ("GET", "/api/health"),  # 服務就緒檢查（手機版本來就不用通行碼）
    ("POST", "/api/pair"),  # 新裝置輸入配對碼；有自己的限速，見 devices.py
    ("GET", "/{path:path}"),  # 網頁：還沒配對的裝置只拿得到配對頁與 pair/ 底下的檔案（frontend.py 判斷），主程式在 /assets
]

ROUTE_LEVELS: dict[tuple[str, str], str] = {
    **{("GET", path): READ for path in _READ},
    **{("GET", path): STANDARD for path in _STANDARD_GET},
    **{key: STANDARD for key in _STANDARD_WRITE},
    **{key: LOCAL for key in _LOCAL},
    **{key: PUBLIC for key in _PUBLIC},
}


def route_keys(routes) -> list[tuple[str, str]]:
    """列出 app 的所有（方法, 路徑範本）。Mount（/assets）只當 GET；HEAD 跟著 GET，不另外列。"""
    keys = []
    for route in routes:
        if isinstance(route, WebSocketRoute):
            # WebSocket 不經過 HTTP middleware（access.require_access、Host 與跨站檢查都不會跑），加了等於沒有任何防護。
            # 真的要用，先把這些防護改成純 ASGI middleware（同時處理 scope["type"] == "websocket"），再改這裡。
            raise TypeError(f"不支援 WebSocket 路由（{route.path}）：它會略過 HTTP middleware 的驗證，見 route_access.route_keys")
        methods = getattr(route, "methods", None)
        if methods is None:  # Mount
            keys.append(("GET", route.path))
            continue
        keys.extend((method, route.path) for method in sorted(methods) if method != "HEAD")
    return keys


def classify(scope) -> str:
    """這次請求屬於哪一級。找不到路由（會回 404）當 READ；路徑對但方法不對（會回 405）當 LOCAL；表裡沒有的也當 LOCAL。"""
    method = "GET" if scope["method"] == "HEAD" else scope["method"]
    partial = False
    for route in scope["app"].routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = route.path
            return ROUTE_LEVELS.get((method if getattr(route, "methods", None) else "GET", path), LOCAL)
        if match == Match.PARTIAL:
            partial = True
    return LOCAL if partial else READ
