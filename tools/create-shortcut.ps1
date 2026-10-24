# 在桌面建立「OAS 典藏館」捷徑（第一次安裝時執行一次）：在檔案總管對這個檔案按右鍵 →「用 PowerShell 執行」。
# 捷徑會以隱藏視窗執行 launch.ps1，並使用 oas.ico 圖示。移動專案資料夾後需要重新執行。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'OAS 典藏館.lnk'))
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell1.0\powershell.exe'
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'launch.ps1')`""
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = "$(Join-Path $PSScriptRoot 'oas.ico'),0"
$shortcut.WindowStyle = 7  # 最小化，不閃出主控台視窗
$shortcut.Save()
Write-Host "已在桌面建立「OAS 典藏館」捷徑。雙擊即可開啟（第一次會自動準備執行環境，約需一兩分鐘）。"
