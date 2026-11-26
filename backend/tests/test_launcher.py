"""啟動器 tools/launch.ps1 的區網模式判斷。

從 launch.ps1 只取出 Test-RemoteMode、Get-ServerArguments 兩個函式，用 Windows PowerShell 執行（不啟動服務、不碰防火牆）；
沒有 powershell.exe 的環境（Linux、macOS）跳過。確認：沒有 remote.json 時 uvicorn 參數和舊版逐字相同；
remote.json 的判斷和後端 devices.remote_next 一致；防火牆腳本的埠和啟動器相同。
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app import devices, settings

TOOLS = Path(__file__).resolve().parents[2] / "tools"
LAUNCH = TOOLS / "launch.ps1"
FIREWALL = TOOLS / "enable-lan-firewall.ps1"
POWERSHELL = shutil.which("powershell")

# 舊版（沒有區網模式時）Start-Server 傳給 pythonw 的參數，逐字
OLD_ARGUMENTS = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8080"]

CASES = {
    "missing": None,
    "on": b'{"enabled": true}',
    "off": b'{"enabled": false}',
    "string": b'{"enabled": "true"}',
    "number": b'{"enabled": 1}',
    "wrong-case": b'{"Enabled": true}',
    "list": b'[{"enabled": true}]',
    "bare-true": b"true",
    "garbage": b"not json",
    "empty": b"",
    "bom": b'\xef\xbb\xbf{"enabled": true}',
    "spaces": b'  {\r\n  "enabled" : true , "other": 1 }\r\n',
}

HARNESS = r"""
param([string]$Launch, [string]$Root)
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Launch, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw "launch.ps1 有語法錯誤" }
foreach ($name in 'Test-RemoteMode', 'Get-ServerArguments') {
    $function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name }, $true)
    if (-not $function) { throw "launch.ps1 沒有 $name" }
    . ([ScriptBlock]::Create($function.Extent.Text))
}
$result = @{}
foreach ($folder in Get-ChildItem -LiteralPath $Root -Directory) {
    $remote = Test-RemoteMode $folder.FullName
    $result[$folder.Name] = @{ remote = $remote; arguments = @(Get-ServerArguments $remote 8080) }
}
$result | ConvertTo-Json -Depth 4 -Compress
"""


@pytest.fixture(scope="module")
def launcher_results(tmp_path_factory):
    if not POWERSHELL:
        pytest.skip("沒有 Windows PowerShell")
    root = tmp_path_factory.mktemp("launcher")
    for name, content in CASES.items():
        (root / name).mkdir()
        if content is not None:
            (root / name / "remote.json").write_bytes(content)
    harness = root.parent / "launcher-harness.ps1"
    harness.write_text(HARNESS, encoding="utf-8-sig")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness),
                             "-Launch", str(LAUNCH), "-Root", str(root)], capture_output=True, timeout=120)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return root, json.loads(result.stdout.decode("utf-8"))


def test_without_remote_json_arguments_are_unchanged(launcher_results):
    _, results = launcher_results
    assert results["missing"] == {"remote": False, "arguments": OLD_ARGUMENTS}


def test_enabled_binds_all_ipv4(launcher_results):
    _, results = launcher_results
    assert results["on"]["remote"] is True
    assert results["on"]["arguments"] == ["-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]


def test_every_other_file_means_off(launcher_results):
    _, results = launcher_results
    for name in CASES:
        if name not in ("on", "spaces"):
            assert results[name] == {"remote": False, "arguments": OLD_ARGUMENTS}, name


def test_same_decision_as_backend(launcher_results, monkeypatch):
    root, results = launcher_results
    for name in CASES:
        monkeypatch.setattr(settings, "REMOTE_FILE", root / name / "remote.json")
        assert results[name]["remote"] is devices.remote_next(), name


def test_launcher_uses_the_helpers():
    source = LAUNCH.read_text(encoding="utf-8-sig")
    assert "-ArgumentList (Get-ServerArguments $remote $port)" in source
    assert "if ($remote) { $env:OAS_REMOTE = '1' }" in source
    # 就緒檢查與備份仍連本機
    assert '$api = "http://127.0.0.1:$port"' in source
    assert "OAS_ALLOWED_HOSTS" not in source and "forwarded-allow-ips" not in source


def test_scripts_are_utf8_with_bom_and_firewall_port_matches():
    launch = LAUNCH.read_bytes()
    firewall = FIREWALL.read_bytes()
    assert launch.startswith(b"\xef\xbb\xbf") and firewall.startswith(b"\xef\xbb\xbf")
    port = re.compile(rb"^\$port = (\d+)", re.M)
    assert port.search(launch).group(1) == port.search(firewall).group(1)
    text = firewall.decode("utf-8-sig")
    for part in ("-Direction Inbound", "-Action Allow", "-Protocol TCP", "-LocalPort $port", "-Profile Private", "-RemoteAddress LocalSubnet", "-Verb RunAs"):
        assert part in text, part
