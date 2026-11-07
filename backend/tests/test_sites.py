"""網站名稱只能出現在 sites.py、site_parsers.py、alternatives.py：被點名時才能快速移除。"""
import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
ALLOWED = {"sites.py", "site_parsers.py", "alternatives.py"}
NAMES = re.compile(r"nhentai|wnacg|紳士|hanime|18comic|禁漫|jmcomic|missav|pornhub|hentai ?haven|qy0\.ru|fourhoi", re.I)


def literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body
                  and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant)}
    return [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings]


def test_site_names_live_in_one_place():
    found = [f"{path.name}:{node.lineno}: {node.value[:60]}" for path in APP.rglob("*.py") if path.name not in ALLOWED
             for node in literals(path) if NAMES.search(node.value)]
    assert found == []


def test_old_names_still_work():
    from app import batch_plan, defaults, fetcher, metadata, sites
    assert metadata.PARSERS and metadata.PROTECTED_HOSTS is sites.PROTECTED_HOSTS and metadata.MetadataError
    assert fetcher.SLOW_HOSTS is sites.SLOW_HOSTS and defaults.SOURCES is sites.SOURCES and batch_plan.SHORT_CODE_URLS is sites.SHORT_CODE_URLS
