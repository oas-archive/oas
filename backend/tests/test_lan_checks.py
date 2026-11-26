"""區網模式的檢查清單與防火牆按鈕（lan_checks.py、GET /api/devices/checks、POST /api/devices/firewall）。
PowerShell 與 netsh 一律換成假的結果：不真的查系統、不改防火牆、不跳 UAC。位址都是虛構的私人網段。"""
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import devices, lan_checks, settings
from app.main import app

JSON = {"Content-Type": "application/json"}
PORT = 8080


def local_client():
    return TestClient(app, client=("127.0.0.1", 50123), base_url="http://localhost:8080", headers=JSON)


def good_rule(**changes):
    rule = {"name": "OAS 區網", "enabled": "True", "direction": "Inbound", "action": "Allow", "profile": "Private",
            "protocol": "TCP", "port": "8080", "remote": "LocalSubnet", "program": ""}
    return {**rule, **changes}


def python_rule(**changes):
    rule = {"name": "python.exe", "enabled": "True", "direction": "Inbound", "action": "Allow", "profile": "Public",
            "protocol": "TCP", "port": "Any", "remote": "Any", "program": r"C:\Python\pythonw.exe"}
    return {**rule, **changes}


@pytest.fixture()
def no_system(monkeypatch):
    """任何真的子行程都算失敗：測試只能走假的 run_powershell／_run。"""
    def refuse(*args, **kwargs):
        raise AssertionError(f"測試不該執行系統指令：{args}")
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


# ---------- 網路類型 ----------

def test_private_network_passes():
    result = lan_checks.network_status([{"name": "家裡", "category": "Private", "addresses": ["192.168.50.10"]}])
    assert result["status"] == "ok" and result["networks"][0]["category"] == "private"
    assert result["settings_uri"] == "ms-settings:network-status"


def test_public_network_fails_even_with_another_private():
    result = lan_checks.network_status([
        {"name": "家裡", "category": "Private", "addresses": ["192.168.50.10"]},
        {"name": "咖啡店", "category": "Public", "addresses": ["10.20.0.5"]},
    ])
    assert result["status"] == "fail"


def test_networks_without_lan_address_are_ignored():
    # 只有 VPN／虛擬網卡（沒有區網 IPv4、或只有 169.254）是公用：不算數，剩下的私人網路通過
    result = lan_checks.network_status([
        {"name": "家裡", "category": "Private", "addresses": "192.168.50.10"},
        {"name": "未識別的網路", "category": "Public", "addresses": ["169.254.3.4"]},
        {"name": "VPN", "category": "Public", "addresses": ["100.64.1.2"]},
    ])
    assert result["status"] == "ok" and [network["name"] for network in result["networks"]] == ["家裡"]


@pytest.mark.parametrize("profiles", [None, [], "壞掉", [{"name": "x", "category": "Private", "addresses": []}]])
def test_network_unknown(profiles):
    assert lan_checks.network_status(profiles)["status"] == "unknown"


def test_domain_network_counts_as_private():
    assert lan_checks.network_status({"name": "公司", "category": "DomainAuthenticated", "addresses": ["10.1.2.3"]})["status"] == "ok"


def test_check_network_off_windows_is_unknown(monkeypatch, no_system):
    monkeypatch.setattr(lan_checks, "is_windows", lambda: False)
    assert lan_checks.check_network()["status"] == "unknown"


def test_check_network_query_failure_is_unknown(monkeypatch, no_system):
    monkeypatch.setattr(lan_checks, "is_windows", lambda: True)
    monkeypatch.setattr(lan_checks, "run_powershell", lambda script, env=None: None)
    assert lan_checks.check_network()["status"] == "unknown"


# ---------- 防火牆 ----------

def test_firewall_rule_passes():
    result = lan_checks.firewall_status({"oas": good_rule(), "apps": []}, PORT)
    assert result["status"] == "ok" and result["problems"] == [] and result["public_python_rules"] == []


@pytest.mark.parametrize("changes, problem", [
    ({"enabled": "False"}, "規則沒有啟用"),
    ({"profile": "Private, Public"}, "網路類型不是只有「私人」"),
    ({"profile": "Any"}, "網路類型不是只有「私人」"),
    ({"remote": "Any"}, "來源不是只有本機子網路"),
    ({"port": "8081"}, "埠不是 TCP 8080"),
    ({"protocol": "UDP"}, "埠不是 TCP 8080"),
    ({"action": "Block"}, "不是「輸入、允許」的規則"),
])
def test_firewall_rule_problems(changes, problem):
    result = lan_checks.firewall_status({"oas": [good_rule(**changes)], "apps": []}, PORT)
    assert result["status"] == "fail" and problem in result["problems"]


def test_firewall_one_good_rule_among_duplicates_passes():
    result = lan_checks.firewall_status({"oas": [good_rule(enabled="False"), good_rule()], "apps": []}, PORT)
    assert result["status"] == "ok"


def test_missing_firewall_rule_fails():
    result = lan_checks.firewall_status({"oas": [], "apps": []}, PORT)
    assert result["status"] == "fail" and result["rule"] is None and result["problems"] == ["還沒有「OAS 區網」規則"]


def test_python_rules_allowing_public_networks_are_reported():
    apps = [python_rule(), python_rule(protocol="UDP"), python_rule(profile="Any", name="Python"), python_rule(profile="Private", name="只有私人"),
            python_rule(enabled="False", name="停用的"), python_rule(direction="Outbound", name="輸出"),
            python_rule(action="Block", profile="Private", name="封鎖")]
    result = lan_checks.firewall_status({"oas": [good_rule()], "apps": apps}, PORT)
    assert [rule["name"] for rule in result["public_python_rules"]] == ["python.exe", "Python"]
    assert [rule["name"] for rule in result["blocking_python_rules"]] == ["封鎖"]
    assert result["status"] == "fail" and "有 Python 的「封鎖」規則擋住私人網路" in result["problems"]  # 封鎖規則會蓋過允許
    result = lan_checks.firewall_status({"oas": [good_rule()], "apps": apps[:-1]}, PORT)
    assert result["status"] == "ok" and len(result["public_python_rules"]) == 2  # 允許公用網路只提醒，不影響「已設定」


@pytest.mark.parametrize("data", [None, [], "壞掉"])
def test_firewall_unknown(data):
    assert lan_checks.firewall_status(data, PORT)["status"] == "unknown"


def test_firewall_query_passes_paths_by_environment(monkeypatch):
    seen = {}

    def fake(script, env=None):
        seen.update(script=script, env=env)
        return {"oas": [good_rule()], "apps": []}
    monkeypatch.setattr(lan_checks, "is_windows", lambda: True)
    monkeypatch.setattr(lan_checks, "run_powershell", fake)
    assert lan_checks.check_firewall(PORT)["status"] == "ok"
    assert seen["env"]["OAS_RULE_NAME"] == "OAS 區網"
    assert all(path in seen["env"]["OAS_PROGRAMS"] for path in lan_checks.python_programs())
    # 唯讀：指令裡只有 Get-，沒有任何會改設定的 cmdlet
    assert "Get-NetFirewallRule" in seen["script"]
    for verb in ("Set-", "New-", "Remove-", "Enable-", "Disable-", "Start-Process"):
        assert verb not in seen["script"]


def test_read_only_scripts_never_change_anything():
    for script in (lan_checks.NETWORK_SCRIPT, lan_checks.FIREWALL_SCRIPT_QUERY):
        for verb in ("Set-", "New-", "Remove-", "Enable-", "Disable-", "Start-Process", "Invoke-Expression"):
            assert verb not in script


def test_python_programs_lists_python_and_pythonw():
    programs = lan_checks.python_programs()
    assert programs and {name.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].lower() for name in programs} <= {"python.exe", "pythonw.exe"}


# ---------- 埠轉送 ----------

PORTPROXY = """
在 ipv4 上聽:                連線到 ipv4:

位址            連接埠      位址            連接埠
--------------- ----------  --------------- ----------
0.0.0.0         8080        127.0.0.1       8080
0.0.0.0         3390        10.0.0.9        3389
"""


def test_portproxy_to_our_port_fails():
    result = lan_checks.proxy_status(PORTPROXY, PORT)
    assert result["status"] == "fail" and result["entries"] == [{"listen": "0.0.0.0:8080", "connect": "127.0.0.1:8080"}]


def test_other_portproxy_entries_are_ignored():
    assert lan_checks.proxy_status(PORTPROXY.replace("8080", "9090"), PORT)["status"] == "ok"
    assert lan_checks.proxy_status("", PORT)["status"] == "ok"
    assert lan_checks.proxy_status(None, PORT)["status"] == "unknown"


def test_check_proxy_failure_is_unknown(monkeypatch, no_system):
    monkeypatch.setattr(lan_checks, "is_windows", lambda: True)
    monkeypatch.setattr(lan_checks, "_run", lambda *args, **kwargs: None)
    assert lan_checks.check_proxy(PORT)["status"] == "unknown"


def test_check_proxy_reads_netsh(monkeypatch, no_system):
    seen = []

    def fake(args, env=None, timeout=None):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, PORTPROXY.encode("cp950"), b"")
    monkeypatch.setattr(lan_checks, "is_windows", lambda: True)
    monkeypatch.setattr(lan_checks, "_run", fake)
    assert lan_checks.check_proxy(PORT)["status"] == "fail"
    assert seen == [["netsh", "interface", "portproxy", "show", "all"]]


# ---------- API ----------

@pytest.fixture()
def fake_system(client, monkeypatch, tmp_path, no_system):
    monkeypatch.setattr(settings, "REMOTE_FILE", tmp_path / "remote.json")
    monkeypatch.setattr(lan_checks, "is_windows", lambda: True)
    answers = {"network": [{"name": "家裡", "category": "Private", "addresses": ["192.168.50.10"]}],
               "firewall": {"oas": [good_rule()], "apps": [python_rule()]}}

    def fake_powershell(script, env=None):
        return answers["network"] if script == lan_checks.NETWORK_SCRIPT else answers["firewall"]
    monkeypatch.setattr(lan_checks, "run_powershell", fake_powershell)
    monkeypatch.setattr(lan_checks, "_run", lambda args, env=None, timeout=None: subprocess.CompletedProcess(args, 0, b"", b""))
    return answers


def test_checks_api_reports_every_item(fake_system, monkeypatch):
    monkeypatch.setattr(settings, "REMOTE", True)
    devices.set_remote_next(True)
    body = local_client().get("/api/devices/checks").json()
    assert body["network"]["status"] == "ok" and body["firewall"]["status"] == "ok" and body["proxy"]["status"] == "ok"
    assert body["firewall"]["port"] == 8080 and len(body["firewall"]["public_python_rules"]) == 1
    assert body["restart"] == {"status": "ok", "enabled": True, "enabled_next": True, "can_restart": False}
    assert body["windows"] is True


def test_checks_api_restart_needed(fake_system):
    devices.set_remote_next(True)  # 設定已開、這次啟動還沒開
    body = local_client().get("/api/devices/checks").json()
    assert body["restart"]["status"] == "fail" and body["restart"]["enabled_next"] is True


def test_checks_api_public_network(fake_system):
    fake_system["network"] = [{"name": "咖啡店", "category": "Public", "addresses": ["10.20.0.5"]}]
    assert local_client().get("/api/devices/checks").json()["network"]["status"] == "fail"


@pytest.mark.parametrize("remote_on", [False, True])
def test_new_apis_are_local_only(fake_system, monkeypatch, remote_on):
    monkeypatch.setattr(settings, "REMOTE", remote_on)
    calls = []
    monkeypatch.setattr(lan_checks, "launch_firewall_script", lambda remove: calls.append(remove) or "started")
    other = TestClient(app, client=("192.168.50.21", 50123), base_url="http://192.168.50.10:8080" if remote_on else "http://testserver", headers=JSON)
    assert other.get("/api/devices/checks").status_code in (401, 403)
    assert other.post("/api/devices/firewall", json={}).status_code in (401, 403)
    proxied = TestClient(app, client=("127.0.0.1", 50123), base_url="http://localhost:8080", headers={**JSON, "X-Forwarded-For": "192.168.50.21"})
    assert proxied.post("/api/devices/firewall", json={}).status_code in (401, 403)
    assert calls == []


def test_firewall_button_launches_the_script(fake_system, monkeypatch):
    calls = []
    monkeypatch.setattr(lan_checks, "launch_firewall_script", lambda remove: calls.append(remove) or "started")
    local = local_client()
    assert local.post("/api/devices/firewall", json={}).json() == {"result": "started"}
    assert local.post("/api/devices/firewall", json={"remove": True}).json() == {"result": "started"}
    assert calls == [False, True]


def test_firewall_button_needs_json_and_same_origin(fake_system, monkeypatch):
    calls = []
    monkeypatch.setattr(lan_checks, "launch_firewall_script", lambda remove: calls.append(remove) or "started")
    plain = TestClient(app, client=("127.0.0.1", 50123), base_url="http://localhost:8080")
    assert plain.post("/api/devices/firewall", content="{}", headers={"Content-Type": "text/plain"}).status_code >= 400
    assert local_client().post("/api/devices/firewall", json={}, headers={"Origin": "http://evil.example"}).status_code >= 400
    assert calls == []


@pytest.mark.parametrize("result, status", [("cancelled", 200), ("missing", 404), ("failed", 500)])
def test_firewall_button_results(fake_system, monkeypatch, result, status):
    monkeypatch.setattr(lan_checks, "launch_firewall_script", lambda remove: result)
    response = local_client().post("/api/devices/firewall", json={})
    assert response.status_code == status
    if status == 200:
        assert response.json() == {"result": "cancelled"}


def test_firewall_button_off_windows(fake_system, monkeypatch):
    monkeypatch.setattr(lan_checks, "is_windows", lambda: False)
    monkeypatch.setattr(lan_checks, "launch_firewall_script", lambda remove: pytest.fail("不是 Windows 不該執行"))
    assert local_client().post("/api/devices/firewall", json={}).status_code == 409


def test_checks_off_windows_are_unknown(client, monkeypatch, no_system):
    monkeypatch.setattr(lan_checks, "is_windows", lambda: False)
    body = local_client().get("/api/devices/checks").json()
    assert {body[key]["status"] for key in ("network", "firewall", "proxy")} == {"unknown"}
    assert body["windows"] is False


def test_android_has_no_checks(client, monkeypatch, no_system):
    monkeypatch.setattr(settings, "PLATFORM", "android")
    assert local_client().get("/api/devices/checks").status_code == 404
    assert local_client().post("/api/devices/firewall", json={}).status_code == 404


# ---------- 啟動腳本（只看組出來的指令，不真的執行） ----------

@pytest.mark.parametrize("remove", [False, True])
def test_launch_uses_runas_with_fixed_arguments(monkeypatch, remove):
    seen = {}

    def fake(args, env=None, timeout=None):
        seen.update(args=args, env=env, timeout=timeout)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(lan_checks, "_run", fake)
    assert lan_checks.launch_firewall_script(remove) == "started"
    assert seen["args"][0] == "powershell.exe" and "-Verb RunAs" in seen["args"][-1] and "-File" in seen["args"][-1]
    assert seen["env"]["OAS_FIREWALL_SCRIPT"].endswith("enable-lan-firewall.ps1")
    assert seen["env"]["OAS_FIREWALL_REMOVE"] == ("1" if remove else "0")


@pytest.mark.parametrize("code, result", [(3, "cancelled"), (1, "failed")])
def test_launch_reports_uac_refusal(monkeypatch, code, result):
    monkeypatch.setattr(lan_checks, "_run", lambda args, env=None, timeout=None: subprocess.CompletedProcess(args, code, b"", b""))
    assert lan_checks.launch_firewall_script(False) == result


def test_launch_without_script(monkeypatch, tmp_path):
    monkeypatch.setattr(lan_checks, "FIREWALL_SCRIPT", tmp_path / "none.ps1")
    monkeypatch.setattr(lan_checks, "_run", lambda *args, **kwargs: pytest.fail("找不到腳本時不該執行"))
    assert lan_checks.launch_firewall_script(False) == "missing"


def test_firewall_script_path_exists():
    assert lan_checks.FIREWALL_SCRIPT.is_file()


def test_firewall_script_ships_with_portable_and_public_builds():
    """免安裝版（build_portable.INCLUDE）與公開版（export_public.INCLUDE）都要帶這支腳本，而且和 backend/app/ 的相對位置不變，
    lan_checks.FIREWALL_SCRIPT（backend/app 往上兩層的 tools/）才找得到。公開 repo 沒有 export_public.py，那一份就跳過。"""
    root = Path(__file__).resolve().parents[2]
    assert lan_checks.FIREWALL_SCRIPT.parent.parent == root
    relative = lan_checks.FIREWALL_SCRIPT.relative_to(root).as_posix()
    assert relative == "tools/enable-lan-firewall.ps1"
    assert lan_checks.FIREWALL_SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")  # Windows PowerShell 5.1 要 BOM 才讀得懂中文
    checked = 0
    for name in ("build_portable.py", "export_public.py"):
        script = root / "tools" / name
        if not script.is_file():
            continue
        text = script.read_text(encoding="utf-8")
        assert f'"{relative}"' in text, f"{name} 的 INCLUDE 沒有 {relative}"
        assert '"backend/app/"' in text
        checked += 1
    assert checked >= 1
