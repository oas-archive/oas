"""區網模式的開啟前檢查（「整理 → 其他裝置」的檢查清單）。只限電腦本機的 API 使用，見 routers/devices.py。

- 網路類型：Windows 的 Get-NetConnectionProfile（私人／公用），只看有區網 IPv4 的網路。
- 防火牆：「OAS 區網」規則是否存在、啟用、輸入、允許、TCP、正確的埠、只有私人網路、來源 LocalSubnet；
  另外找同一個 python／pythonw 執行檔有沒有允許「公用網路」的舊規則（Windows 第一次跳「允許存取」時建的），只提醒不改。
- 轉送：netsh interface portproxy show all 有沒有聽或轉到典藏館的埠。代理軟體偵測不到，畫面只提醒。

全部是唯讀查詢：PowerShell／netsh 子行程、不經過 shell、參數不含任何請求內容（執行檔路徑與埠用環境變數傳），都有逾時。
查不到（不是 Windows、逾時、指令失敗）一律回 unknown，畫面顯示「無法偵測」，不擋使用者。

唯一會改系統的是 launch_firewall_script：以 Start-Process -Verb RunAs 開 tools/enable-lan-firewall.ps1，
Windows 會跳 UAC，由使用者自己按「是」；腳本本身會先列出要做的事。路由只在電腦本機的請求才會呼叫它。
"""
import ipaddress
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import devices

OK, FAIL, UNKNOWN = "ok", "fail", "unknown"
RULE_NAME = "OAS 區網"
QUERY_TIMEOUT = 10  # 秒：單一唯讀查詢
UAC_TIMEOUT = 180  # 秒：等使用者在 UAC 按「是」或「否」
FIREWALL_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "enable-lan-firewall.ps1"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NETWORK_SETTINGS = "ms-settings:network-status"


def is_windows() -> bool:
    return sys.platform == "win32"


def _run(args: list[str], env: dict | None = None, timeout: float = QUERY_TIMEOUT) -> subprocess.CompletedProcess | None:
    """執行一個唯讀指令（不經過 shell）。找不到程式、逾時都回 None。"""
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, env={**os.environ, **(env or {})},
                              stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def run_powershell(script: str, env: dict | None = None):
    """執行一段唯讀的 PowerShell，輸出要是 ConvertTo-Json；回傳解析後的值，失敗回 None。測試會替換這個函式。"""
    result = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                   "[Console]::OutputEncoding = [Text.Encoding]::UTF8; " + script], env)
    if result is None or result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    if not text:
        return []
    try:
        return json.loads(text)
    except ValueError:
        return None


def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ---------- 網路類型 ----------

NETWORK_SCRIPT = (
    "@(Get-NetConnectionProfile -ErrorAction Stop | ForEach-Object { "
    "[pscustomobject]@{ name = \"$($_.Name)\"; alias = \"$($_.InterfaceAlias)\"; category = \"$($_.NetworkCategory)\"; "
    "addresses = @(Get-NetIPAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue "
    "| ForEach-Object { \"$($_.IPAddress)\" }) } }) | ConvertTo-Json -Depth 3 -Compress"
)


def _lan(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(ip in network for network in devices.HOST_NETWORKS) and not ip.is_link_local


def network_status(profiles) -> dict:
    """profiles：[{name, alias, category, addresses}]。只看有區網 IPv4 的網路（虛擬網卡、VPN 沒有的不算）：
    有任何一個是「公用」就不通過；至少一個「私人」或網域就通過；其他（查不到、沒有區網）無法偵測。"""
    networks = []
    for item in _as_list(profiles):
        if not isinstance(item, dict):
            continue
        addresses = [str(value) for value in _as_list(item.get("addresses")) if _lan(str(value))]
        if not addresses:
            continue
        category = str(item.get("category") or "")
        kind = "public" if category == "Public" else "private" if category in ("Private", "DomainAuthenticated") else "other"
        networks.append({"name": str(item.get("name") or item.get("alias") or ""), "category": kind, "addresses": addresses})
    if any(network["category"] == "public" for network in networks):
        status = FAIL
    elif any(network["category"] == "private" for network in networks):
        status = OK
    else:
        status = UNKNOWN
    return {"status": status, "networks": networks, "settings_uri": NETWORK_SETTINGS}


def check_network() -> dict:
    if not is_windows():
        return network_status(None)
    profiles = run_powershell(NETWORK_SCRIPT)
    return network_status(profiles) if profiles is not None else network_status(None)


# ---------- 防火牆 ----------

# 執行檔路徑與規則名稱用環境變數傳進來（不拼進指令）；Get-NetFirewallApplicationFilter -Program 只找這些程式的規則，比列舉全部規則快
FIREWALL_SCRIPT_QUERY = (
    "$describe = { param($rule, $program) $port = $rule | Get-NetFirewallPortFilter; $address = $rule | Get-NetFirewallAddressFilter; "
    "[pscustomobject]@{ name = \"$($rule.DisplayName)\"; enabled = \"$($rule.Enabled)\"; direction = \"$($rule.Direction)\"; "
    "action = \"$($rule.Action)\"; profile = \"$($rule.Profile)\"; protocol = \"$($port.Protocol)\"; "
    "port = \"$($port.LocalPort)\"; remote = \"$(@($address.RemoteAddress) -join ',')\"; program = \"$program\" } }; "
    "$oas = @(Get-NetFirewallRule -DisplayName $env:OAS_RULE_NAME -ErrorAction SilentlyContinue | ForEach-Object { & $describe $_ '' }); "
    "$apps = @(); foreach ($program in ($env:OAS_PROGRAMS -split '\\|')) { if (-not $program) { continue }; "
    "$apps += @(Get-NetFirewallApplicationFilter -Program $program -ErrorAction SilentlyContinue | Get-NetFirewallRule "
    "-ErrorAction SilentlyContinue | ForEach-Object { & $describe $_ $program }) }; "
    "[pscustomobject]@{ oas = $oas; apps = $apps } | ConvertTo-Json -Depth 4 -Compress"
)


def python_programs() -> list[str]:
    """典藏館服務可能用到的 Python 執行檔（虛擬環境的轉介程式與真正的 Python，各有 python.exe、pythonw.exe）。"""
    found = []
    for executable in (sys.executable, getattr(sys, "_base_executable", "")):
        if not executable:
            continue
        folder = Path(executable).parent
        for name in ("python.exe", "pythonw.exe"):
            path = str(folder / name)
            if path not in found and "|" not in path:
                found.append(path)
    return found


def _profiles(value: str) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _rule_problems(rule: dict, port: int) -> list[str]:
    problems = []
    if rule.get("enabled") != "True":
        problems.append("規則沒有啟用")
    if rule.get("direction") != "Inbound" or rule.get("action") != "Allow":
        problems.append("不是「輸入、允許」的規則")
    if str(rule.get("protocol") or "").upper() != "TCP" or str(rule.get("port") or "") != str(port):
        problems.append(f"埠不是 TCP {port}")
    profiles = _profiles(rule.get("profile"))
    if profiles != {"Private"}:
        problems.append("網路類型不是只有「私人」")
    if str(rule.get("remote") or "") != "LocalSubnet":
        problems.append("來源不是只有本機子網路")
    return problems


def firewall_status(data, port: int) -> dict:
    """data：{oas: [規則], apps: [Python 的規則]}。OAS 區網要有一條完全符合的；Python 允許公用網路的另外列出（只提醒）。"""
    if not isinstance(data, dict):
        return {"status": UNKNOWN, "rule": None, "problems": [], "public_python_rules": [], "blocking_python_rules": [], "port": port}
    rules = [rule for rule in _as_list(data.get("oas")) if isinstance(rule, dict)]
    good = next((rule for rule in rules if not _rule_problems(rule, port)), None)
    problems = [] if good else (_rule_problems(rules[0], port) if rules else ["還沒有「OAS 區網」規則"])
    public, blocking = [], []
    for rule in _as_list(data.get("apps")):
        if not isinstance(rule, dict) or rule.get("enabled") != "True" or rule.get("direction") != "Inbound":
            continue
        profiles = _profiles(rule.get("profile"))
        entry = {"name": str(rule.get("name") or ""), "program": str(rule.get("program") or ""), "profile": ", ".join(sorted(profiles))}
        target = public if rule.get("action") == "Allow" and profiles & {"Public", "Any"} else             blocking if rule.get("action") == "Block" and profiles & {"Private", "Any"} else None
        if target is not None and entry not in target:  # Windows 常替同一個程式建 TCP、UDP 兩條同名規則，畫面只列一次
            target.append(entry)
    if blocking:  # 封鎖規則優先於允許規則：有它在，「OAS 區網」規則也沒用
        problems.append("有 Python 的「封鎖」規則擋住私人網路")
    return {"status": OK if good and not blocking else FAIL, "rule": good or (rules[0] if rules else None), "problems": problems,
            "public_python_rules": public, "blocking_python_rules": blocking, "port": port}


def check_firewall(port: int) -> dict:
    if not is_windows():
        return firewall_status(None, port)
    data = run_powershell(FIREWALL_SCRIPT_QUERY, {"OAS_RULE_NAME": RULE_NAME, "OAS_PROGRAMS": "|".join(python_programs())})
    return firewall_status(data, port)


# ---------- 埠轉送 ----------

PORTPROXY_LINE = re.compile(r"^\s*(\S+)\s+(\d+)\s+(\S+)\s+(\d+)\s*$")


def proxy_status(text: str | None, port: int) -> dict:
    """netsh interface portproxy show all 的輸出：有聽或轉到典藏館的埠就不通過。偵測不到代理軟體，所以沒有轉送也只是「沒發現」。"""
    if text is None:
        return {"status": UNKNOWN, "entries": [], "port": port}
    entries = []
    for line in text.splitlines():
        match = PORTPROXY_LINE.match(line)
        if match and str(port) in (match.group(2), match.group(4)):
            entries.append({"listen": f"{match.group(1)}:{match.group(2)}", "connect": f"{match.group(3)}:{match.group(4)}"})
    return {"status": FAIL if entries else OK, "entries": entries, "port": port}


def check_proxy(port: int) -> dict:
    if not is_windows():
        return proxy_status(None, port)
    result = _run(["netsh", "interface", "portproxy", "show", "all"])
    if result is None or result.returncode != 0:
        return proxy_status(None, port)
    # 標題列是系統語言（中文時是 cp950），只解析位址與埠這些 ASCII 欄位，解不出的字元換掉即可
    return proxy_status(result.stdout.decode("utf-8", "replace"), port)


# ---------- 全部 ----------

def run_checks(port: int) -> dict:
    """三項系統查詢同時跑（各自有逾時）；「已用區網模式重開」由路由用 remote_active／remote_next 判斷。"""
    with ThreadPoolExecutor(max_workers=3) as pool:
        network = pool.submit(check_network)
        firewall = pool.submit(check_firewall, port)
        proxy = pool.submit(check_proxy, port)
        return {"network": network.result(), "firewall": firewall.result(), "proxy": proxy.result()}


# ---------- 啟動防火牆設定（會改系統：只由電腦本機的請求觸發，UAC 由使用者決定） ----------

LAUNCH_SCRIPT = (
    "$arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('\"' + $env:OAS_FIREWALL_SCRIPT + '\"')); "
    "if ($env:OAS_FIREWALL_REMOVE -eq '1') { $arguments += '-Remove' }; "
    "try { Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -ErrorAction Stop; exit 0 } catch { exit 3 }"
)


def launch_firewall_script(remove: bool) -> str:
    """開系統管理員視窗執行 enable-lan-firewall.ps1（-Remove 時刪規則）。回傳 started、cancelled（UAC 按否）、missing、failed。
    等到 UAC 有結果才回來（最多 UAC_TIMEOUT 秒）；腳本視窗本身由使用者看完按 Enter 關閉，不等它。"""
    if not FIREWALL_SCRIPT.is_file():
        return "missing"
    result = _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", LAUNCH_SCRIPT],
                  {"OAS_FIREWALL_SCRIPT": str(FIREWALL_SCRIPT), "OAS_FIREWALL_REMOVE": "1" if remove else "0"}, timeout=UAC_TIMEOUT)
    if result is None:
        return "failed"
    return {0: "started", 3: "cancelled"}.get(result.returncode, "failed")
