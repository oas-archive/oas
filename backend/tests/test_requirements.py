"""手機的相依要全部鎖版本，兩個版本除了 Pillow 要相同：不然同一份原始碼今天和明天打包出來會不一樣。"""
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not (BACKEND / "requirements-android-public.txt").exists(), reason="這份原始碼沒有手機版")


def pins(name: str) -> dict[str, str]:
    lines = [line.strip() for line in (BACKEND / name).read_bytes().decode("ascii").splitlines()]  # pip 在 Windows 用系統編碼讀，只能有 ASCII
    rows = [line for line in lines if line and not line.startswith("#")]
    assert all(re.fullmatch(r"[A-Za-z0-9_.\[\]-]+==[^=\s]+", row) for row in rows), rows
    return dict(row.lower().split("==") for row in rows)


@pytest.mark.skipif(not (BACKEND / "requirements-android.txt").exists(), reason="公開版只有公開版的套件清單")
def test_phone_versions_differ_only_by_pillow():
    personal, public = pins("requirements-android.txt"), pins("requirements-android-public.txt")
    assert "pillow" not in public
    assert {name: version for name, version in personal.items() if name != "pillow"} == public


def test_phone_runs_pydantic_v1_with_the_same_web_framework_as_the_computer():
    desktop, public = pins("requirements.txt"), pins("requirements-android-public.txt")
    assert public["pydantic"].startswith("1.")
    for name in ("fastapi", "starlette", "sqlalchemy"):
        assert public[name] == desktop[name], name
