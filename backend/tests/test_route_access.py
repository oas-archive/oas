"""路由分類表（route_access.py）：app 的每一條路由都要有級別，表裡也不能留已經不存在的路由。"""
from fastapi import FastAPI

from app import frontend, route_access
from app.main import app


def _all_keys(tmp_path) -> set[tuple[str, str]]:
    keys = set(route_access.route_keys(app.routes))
    # 網頁的路由只有打包過（frontend/dist 有 index.html）才掛上；測試另外掛一份，確定也被分類
    (tmp_path / "assets").mkdir(exist_ok=True)
    (tmp_path / "index.html").write_text("<!doctype html>", encoding="utf-8")
    pages = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # 和 main.py 一樣不提供 API 文件頁
    assert frontend.install(pages, tmp_path)
    keys.update(route_access.route_keys(pages.routes))
    return keys


def test_every_route_is_classified(tmp_path):
    missing = sorted(key for key in _all_keys(tmp_path) if key not in route_access.ROUTE_LEVELS)
    assert missing == [], f"這些路由還沒分類，請加進 route_access.py：{missing}"


def test_no_stale_entries(tmp_path):
    stale = sorted(key for key in route_access.ROUTE_LEVELS if key not in _all_keys(tmp_path))
    assert stale == [], f"分類表裡有已不存在的路由：{stale}"


def test_levels_are_known():
    assert set(route_access.ROUTE_LEVELS.values()) <= set(route_access.LEVELS)


def test_dangerous_routes_are_local_only():
    # 計畫 6.1：還原、下載備份、清除封面、改來源規則、匯入翻譯包，任何裝置都不給
    for key in [("POST", "/api/restore"), ("GET", "/api/backup"), ("GET", "/api/backup/files/{name}"), ("POST", "/api/covers/clear"),
                ("PUT", "/api/sources"), ("POST", "/api/tag-pack/mine")]:
        assert route_access.ROUTE_LEVELS[key] == route_access.LOCAL, key


def test_reads_never_include_writes():
    # 唯讀級只能用 GET；會寫入的方法一定不是 READ
    for (method, path), level in route_access.ROUTE_LEVELS.items():
        if level == route_access.READ:
            assert method == "GET", (method, path)


def test_classify_resolves_route_templates():
    def scope(method, path):
        return {"type": "http", "method": method, "path": path, "root_path": "", "app": app, "headers": [], "query_string": b""}

    assert route_access.classify(scope("GET", "/api/resources/12")) == route_access.READ
    assert route_access.classify(scope("HEAD", "/api/resources/12")) == route_access.LOCAL  # FastAPI 的 GET 路由不收 HEAD（405）
    assert route_access.classify(scope("PATCH", "/api/resources/12")) == route_access.STANDARD
    assert route_access.classify(scope("POST", "/api/restore")) == route_access.LOCAL
    assert route_access.classify(scope("GET", "/api/backup/files/oas-x.json")) == route_access.LOCAL
    assert route_access.classify(scope("PUT", "/api/restore")) == route_access.LOCAL  # 方法不對（405）當最嚴
    assert route_access.classify(scope("GET", "/api/health")) == route_access.PUBLIC


def test_no_websocket_routes():
    # WebSocket 會略過 HTTP middleware（驗證、Host、跨站檢查），app 不能有
    from starlette.routing import WebSocketRoute
    assert not [route for route in app.routes if isinstance(route, WebSocketRoute)]


def test_route_keys_refuses_websocket():
    import pytest
    from starlette.routing import WebSocketRoute

    async def handler(websocket):
        pass

    with pytest.raises(TypeError):
        route_access.route_keys([WebSocketRoute("/ws", handler)])
