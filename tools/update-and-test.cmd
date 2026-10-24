@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\update-and-test.log
echo OAS 更新與測試 %date% %time% > "%LOG%"
echo 執行中，請稍候...
if not exist backend\.venv\Scripts\python.exe (
  echo [錯誤] 還沒有 Python 執行環境。>> "%LOG%"
  echo 還沒有 Python 執行環境，請先雙擊「OAS 典藏館」捷徑開啟一次（會自動建立），再執行這個檔案。
  pause
  exit /b 1
)
echo ===== 1. 打包網頁 >> "%LOG%"
pushd frontend
call npm ci --no-audit --no-fund >> "..\%LOG%" 2>&1 && call npm run build >> "..\%LOG%" 2>&1
popd
echo ===== 2. 自動化測試 >> "%LOG%"
backend\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r backend\requirements-dev.txt >> "%LOG%" 2>&1
pushd backend
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider >> "..\%LOG%" 2>&1
popd
echo ===== 完成 >> "%LOG%"
echo 完成。結果已寫入 %LOG%；典藏館開著的話，關掉視窗再重新開啟就會用到新版本。
pause
