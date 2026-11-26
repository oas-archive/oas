# OAS 區網模式的 Windows 防火牆規則（說明與風險見 README「家裡其他裝置也能看」）。
# 由擁有者自己執行：雙擊或右鍵「用 PowerShell 執行」，會跳出系統管理員確認（UAC）。程式不會自己改防火牆。
# 建立／更新規則「OAS 區網」：輸入、允許、TCP、啟動器用的埠（tools/launch.ps1 的 $port）、只在「私人」網路、只接受本機子網路的來源。
#   .\enable-lan-firewall.ps1          建立或更新規則
#   .\enable-lan-firewall.ps1 -Remove  刪除規則（關閉區網模式後可用）
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$ruleName = 'OAS 區網'
$port = 8080  # 要和 tools/launch.ps1 的 $port 相同（backend/tests/test_launcher.py 會檢查）

function Wait-Key {
    Write-Host ''
    Read-Host '按 Enter 關閉這個視窗' | Out-Null
}

$identity = [System.Security.Principal.WindowsPrincipal][System.Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '改防火牆需要系統管理員權限，接下來 Windows 會詢問是否允許（UAC）。'
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Remove) { $arguments += '-Remove' }
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments
    } catch {
        Write-Host '沒有取得系統管理員權限，防火牆沒有變更。'
        Wait-Key
    }
    exit
}

$existing = @(Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)

try {
    if ($Remove) {
        Write-Host "將要做的事：刪除防火牆規則「$ruleName」（目前有 $($existing.Count) 條）。"
        if ($existing.Count) {
            $existing | Remove-NetFirewallRule
            Write-Host "完成：已刪除「$ruleName」。其他裝置之後連不到這台電腦的典藏館。"
        } else {
            Write-Host "沒有「$ruleName」這條規則，不用刪。"
        }
    } else {
        Write-Host "將要做的事：建立防火牆規則「$ruleName」"
        Write-Host "  方向：輸入（其他裝置連進來）　動作：允許　通訊協定：TCP　埠：$port"
        Write-Host '  網路類型：只有「私人」　來源：只有同一個區網（本機子網路）'
        if ($existing.Count) { Write-Host "  已經有 $($existing.Count) 條同名規則，會先刪掉再重建（等於更新）。" }
        if ($existing.Count) { $existing | Remove-NetFirewallRule }
        New-NetFirewallRule -DisplayName $ruleName -Description 'OAS 典藏館區網模式：家裡其他裝置連到這台電腦（tools/enable-lan-firewall.ps1 建立）' `
            -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -Profile Private -RemoteAddress LocalSubnet | Out-Null
        $rule = Get-NetFirewallRule -DisplayName $ruleName
        $filter = $rule | Get-NetFirewallPortFilter
        $address = $rule | Get-NetFirewallAddressFilter
        Write-Host ''
        Write-Host "完成：「$ruleName」已啟用＝$($rule.Enabled)、網路類型＝$($rule.Profile)、TCP 埠＝$($filter.LocalPort)、來源＝$($address.RemoteAddress)"
        # 只讀：提醒目前的網路類型；是「公用」時這條規則不會生效
        $public = @(Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object { $_.NetworkCategory -eq 'Public' })
        foreach ($item in $public) {
            Write-Host "注意：網路「$($item.Name)」目前是「公用」，這條規則在那裡不生效。家裡的網路請到 Windows 設定 → 網路和網際網路 → 內容，改成「私人網路」。"
        }
    }
} catch {
    Write-Host "失敗：$($_.Exception.Message)"
}
Wait-Key
