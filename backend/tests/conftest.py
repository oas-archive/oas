import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="oas-test-")
os.environ["OAS_DATA_DIR"] = _tmp
os.environ["OAS_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["OAS_COVER_DIR"] = f"{_tmp}/covers"
os.environ["OAS_LINKCHECK"] = "off"
os.environ["OAS_JOBS"] = "off"
os.environ["OAS_SEED"] = "off"
os.environ["OAS_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import fetcher  # noqa: E402
from app.database import Base, engine, initialise_database  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    initialise_database()
    # 與前端 api() 相同，一律帶 JSON 的 Content-Type（會改資料的請求沒有它會被拒絕）
    with TestClient(app, headers={"Content-Type": "application/json"}) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def instant_fetcher(monkeypatch):
    """測試不等待：各網站的請求間隔與限流冷卻都設為 0，並清掉上一個測試留下的網站狀態。"""
    fetcher.reset()
    for name in ("SLOW_INTERVAL", "OTHER_INTERVAL", "BACKOFF_SECONDS"):
        monkeypatch.setattr(fetcher, name, 0)
    yield
    fetcher.reset()
