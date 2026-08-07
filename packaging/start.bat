@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PORT=8502
set PY=%~dp0runtime\python.exe

if not exist "%PY%" (
  echo [ERROR] 未找到运行时: %PY%
  echo 请确认解压完整（runtime 目录存在）。
  pause
  exit /b 1
)

rem 用 python 解析入口文件路径，避免 bat 内嵌中文文件名编码问题
for /f "delims=" %%F in ('"%PY%" -c "import glob,os;cwd=os.getcwd();print(next(p for p in glob.glob(os.path.join(cwd,'quant_trading_system','dashboard','*.py')) if os.path.basename(p)=='首页.py'))"') do set ENTRY=%%F

echo 启动 GP助手看板 -^> http://127.0.0.1:%PORT% （首次启动约需 10-30 秒）
start "" powershell -NoProfile -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:%PORT%'"

"%PY%" -m streamlit run "%ENTRY%" --server.port %PORT% --server.headless true

echo 看板已停止。
pause
