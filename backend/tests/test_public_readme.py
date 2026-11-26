"""公開 README（私人 repo 的 tools/public/README.md）裡的本機圖片與連結，匯出後都要找得到；手機版標記要成對。
公開 repo 沒有 tools/public/ 與 export_public.py，整個檔案跳過。"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "tools" / "public" / "README.md"
pytestmark = pytest.mark.skipif(not README.is_file() or not (ROOT / "tools" / "export_public.py").is_file(), reason="只在私人 repo 檢查")


def exported_path(path: str, export_public) -> Path | None:
    """README 引用的公開版路徑 → 私人 repo 裡的來源檔（照 export_public 的 RENAME、PUBLIC_FILES、INCLUDE）。"""
    for source, target in export_public.RENAME.items():
        if path.startswith(target):
            return ROOT / (source + path[len(target):])
    for source, target in export_public.PUBLIC_FILES.items():
        if path == target:
            return ROOT / source
    if export_public.allowed(path, export_public.include_rules()):
        return ROOT / path
    return None


def test_readme_local_links_are_exported():
    sys.path.insert(0, str(ROOT / "tools"))
    import export_public
    text = README.read_text(encoding="utf-8")
    links = set(re.findall(r'\]\(([^)#\s]+)\)', text)) | set(re.findall(r'src="([^"]+)"', text))
    local = sorted(link for link in links if not re.match(r"[a-z]+://|\.\./", link))
    assert local, "README 沒有任何本機連結，比對規則可能壞了"
    for link in local:
        source = exported_path(link, export_public)
        assert source is not None, f"{link} 不會被匯出"
        assert source.is_file(), f"{link} 的來源 {source.relative_to(ROOT)} 不存在"


def test_readme_android_markers_pair():
    sys.path.insert(0, str(ROOT / "tools"))
    import export_public
    content = README.read_bytes()
    for keep in (True, False):
        export_public.strip_block("README.md", content, export_public.ANDROID_BEGIN, export_public.ANDROID_END, keep_inside=keep, label="手機版")
