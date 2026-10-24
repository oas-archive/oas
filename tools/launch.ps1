# OAS 啟動器：由「OAS 入口」資料夾中的捷徑呼叫（隱藏視窗執行）。
# 以獨立的應用程式視窗（Chrome / Edge 的 --app 模式、專用設定檔）開啟典藏館；
# 服務直接在這台電腦上執行（不需要 Docker），視窗全部關閉後自動備份並結束服務。
# 資料位置：專案資料夾旁邊已有 data\oas.db 時沿用（資料庫與封面在 data\、備份在 backups\）；
# 否則放在 %LOCALAPPDATA%\OAS（備份在其中的 backups\）。
param([switch]$KeepRunning)  # 加上 -KeepRunning 則關閉視窗後不結束服務

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot              # oas-next
$home_ = Split-Path -Parent $root                     # oas-next 的上一層（data、backups 所在）
$port = 8080
$url = "http://localhost:$port"     # 視窗開的網址
# 啟動器自己的檢查與備份直接連 127.0.0.1：這台電腦上 localhost 會先試 IPv6（::1），連線會卡到逾時才改走 IPv4
$api = "http://127.0.0.1:$port"
$profileDir = Join-Path $env:LOCALAPPDATA 'OAS\app-window'
$backend = Join-Path $root 'backend'
$venv = Join-Path $backend '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
# 免安裝版（tools/build_portable.py 打包）：資料夾裡附有 Python，網頁也已打包好，不需要另外安裝
$portable = Test-Path (Join-Path $root 'python\pythonw.exe')
if ($portable) {
    $python = Join-Path $root 'python\python.exe'
    $pythonw = Join-Path $root 'python\pythonw.exe'
}
$frontendDist = Join-Path $root 'frontend\dist\index.html'
$logDir = Join-Path $root 'logs'
if (Test-Path (Join-Path $home_ 'data\oas.db')) {
    $dataDir = Join-Path $home_ 'data'
    $backupDir = Join-Path $home_ 'backups'
} else {
    $dataDir = Join-Path $env:LOCALAPPDATA 'OAS'
    $backupDir = Join-Path $dataDir 'backups'
}

function Test-Web {
    try { (Invoke-WebRequest -Uri "$api/api/health" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 } catch { $false }
}

function Show-Error([string]$text) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($text, 'OAS 典藏館', 'OK', 'Warning') | Out-Null
    exit 1
}

function Find-Browser {
    @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

# 專用設定檔的瀏覽器行程；以命令列中的設定檔路徑辨識，不會影響你平常開的 Chrome。
function Get-AppWindows {
    @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe' OR Name='msedge.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($profileDir) })
}

function Open-AppWindow([string]$target) {
    $browser = Find-Browser
    if (-not $browser) { Start-Process $target; return $false }
    New-Item -ItemType Directory -Force $profileDir | Out-Null
    # 路徑含空格與括號（Omni-Archive System (OAS)），需轉成 file:/// 網址並加上引號，否則會被拆成多個參數。
    if (Test-Path $target) { $target = ([Uri](Resolve-Path $target).Path).AbsoluteUri }
    Start-Process $browser -ArgumentList @("--app=`"$target`"", "--user-data-dir=`"$profileDir`"", '--window-size=1440,900', '--no-first-run', '--no-default-browser-check')
    return $true
}

# 外部程式會把進度訊息寫到 stderr；PowerShell 5.1 在 Stop 模式下會誤判為錯誤，所以呼叫時改為 Continue，只看結束代碼。
function Invoke-Tool([string]$exe, [string[]]$arguments, [string]$log) {
    $ErrorActionPreference = 'Continue'
    & $exe @arguments *>> $log
    return $LASTEXITCODE -eq 0
}

# 第一次執行：建立 Python 執行環境、打包網頁（之後就不必再做）。
function Initialize-Runtime {
    New-Item -ItemType Directory -Force $logDir | Out-Null
    $log = Join-Path $logDir 'setup.log'
    if ($portable) {
        # 從網路下載的 zip 解壓後，檔案帶有「來自網際網路」標記，可能被擋下；只解除本資料夾內的檔案
        Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue
        return
    }
    if (-not (Test-Path $python)) {
        $system = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $system -or $system -like '*WindowsApps*') { Show-Error "找不到 Python。請先安裝 Python 3.11 以上（https://www.python.org），安裝時勾選「Add python.exe to PATH」，再重新點一次捷徑。" }
        if (-not (Invoke-Tool $system @('-m', 'venv', $venv) $log)) { Show-Error "建立 Python 執行環境失敗，詳細內容在 $log。" }
        if (-not (Invoke-Tool $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $backend 'requirements.txt')) $log)) {
            Remove-Item -Recurse -Force $venv -ErrorAction SilentlyContinue  # 裝到一半就刪掉，下次重新來過
            Show-Error "安裝 Python 套件失敗（可能沒有網路），詳細內容在 $log。"
        }
    }
    if (-not (Test-Path $frontendDist)) {
        $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
        if (-not $npm) { Show-Error "網頁還沒有打包，也找不到 Node.js。請先安裝 Node.js（https://nodejs.org），再重新點一次捷徑。" }
        Push-Location (Join-Path $root 'frontend')
        try {
            if (-not ((Invoke-Tool $npm @('ci', '--no-audit', '--no-fund') $log) -and (Invoke-Tool $npm @('run', 'build') $log))) { Show-Error "打包網頁失敗，詳細內容在 $log。" }
        } finally { Pop-Location }
    }
}

function Start-Server {
    New-Item -ItemType Directory -Force $logDir, $dataDir, $backupDir | Out-Null
    $env:OAS_DATA_DIR = $dataDir
    $env:OAS_BACKUP_DIR = $backupDir
    # pythonw：不會跳出黑色主控台視窗；輸出寫到 logs\server.log
    Start-Process -FilePath $pythonw -WorkingDirectory $backend -PassThru `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$port") `
        -RedirectStandardOutput (Join-Path $logDir 'server.log') -RedirectStandardError (Join-Path $logDir 'server-error.log')
}

$ready = Test-Web
$watching = Open-AppWindow ($(if ($ready) { $url } else { (Join-Path $PSScriptRoot 'starting.html') }))

# 已有另一個啟動器在監看視窗（重複點擊捷徑）：只開視窗，不重複監看。
$mutex = New-Object System.Threading.Mutex($false, 'Local\OAS-Launcher')
if (-not $mutex.WaitOne(0)) { exit 0 }

$server = $null
if (-not $ready) {
    Initialize-Runtime
    $server = Start-Server
    $deadline = (Get-Date).AddMinutes(1)
    while (-not (Test-Web)) {
        if ($server.HasExited) { Show-Error "服務啟動失敗，詳細內容在 $(Join-Path $logDir 'server-error.log')。`n`n若是 8080 埠被占用（例如舊的 Docker 版還開著），請先關閉它再試一次。" }
        if ((Get-Date) -gt $deadline) { Show-Error "服務 1 分鐘內沒有回應，詳細內容在 $(Join-Path $logDir 'server-error.log')。" }
        Start-Sleep -Milliseconds 500
    }
    # 啟動畫面會自行跳轉，這裡不再重複開啟。
}

if ($KeepRunning -or -not $watching) { exit 0 }

# 等待所有典藏館視窗關閉（瀏覽器啟動需要一點時間，先等它出現）。
$deadline = (Get-Date).AddSeconds(30)
while ((Get-AppWindows).Count -eq 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
while ((Get-AppWindows).Count -gt 0) { Start-Sleep -Seconds 3 }

# 關閉前自動備份：匯出完整 JSON 到 backups\，只保留最近 14 份自動備份（手動匯出的不受影響）。
try {
    New-Item -ItemType Directory -Force $backupDir | Out-Null
    $file = Join-Path $backupDir ('oas-auto-{0}.json' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Invoke-WebRequest -Uri "$api/api/backup" -UseBasicParsing -TimeoutSec 60 -OutFile $file
    if ((Get-Item $file).Length -lt 100) { Remove-Item $file -Force }
    Get-ChildItem $backupDir -Filter 'oas-auto-*.json' | Sort-Object Name -Descending | Select-Object -Skip 14 | Remove-Item -Force
} catch { }

# 結束典藏館服務：這次開啟的，或之前的啟動器沒能收尾而留下的。
# .venv 的 pythonw.exe 只是轉介程式，真正占用連接埠的是它啟動的 Python，所以依「占用連接埠＋命令列是 uvicorn app.main:app」
# 找出服務本體，連同轉介程式一起結束；其他程式不動。
function Stop-Server {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $listener = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" -ErrorAction SilentlyContinue
        if ($listener -and $listener.CommandLine -like '*uvicorn app.main:app*') {
            $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.ParentProcessId)" -ErrorAction SilentlyContinue
            Stop-Process -Id $listener.ProcessId -Force -ErrorAction SilentlyContinue
            if ($parent -and $parent.ExecutablePath -eq $pythonw) { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
        }
    }
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
}
Stop-Server
