@echo off
REM ASCII only. Helper version stamped so screenshots show which zip you ran.
setlocal EnableExtensions
echo.
echo === GP Assistant Windows build ===
echo helper: auto-python-20260903a
echo Script: %~f0
echo Frozen app: win-amd64 GPAssistant.exe (same as GitHub Release; runs on normal PCs)
echo.

set "ERR=0"
set "CHECK_PY=%~dp0check_python.py"

cd /d "%~dp0"
if not exist "..\pyproject.toml" (
  echo ERROR: pyproject.toml not found.
  echo Unzip the whole archive first. Do not run this from inside the zip window.
  echo Current dir: %CD%
  set "ERR=1"
  goto :done
)
cd /d "%~dp0\.."
echo Repo root: %CD%
if not exist "dashboard\home.py" (
  echo ERROR: dashboard\home.py missing.
  echo The zip was extracted with broken filenames, or this is an old folder.
  echo Use GP-Assistant-build-on-windows-20260903a.zip and extract to a NEW folder.
  set "ERR=1"
  goto :done
)
if not exist "dashboard\pages\0_opportunity.py" (
  echo ERROR: dashboard\pages\0_opportunity.py missing.
  set "ERR=1"
  goto :done
)
echo.

set "PY="
call :pick_python
if defined PY goto :have_python

echo No working Python found. Auto-installing 3.12 ^(needs internet^) ...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure-python.ps1"
if errorlevel 1 (
  echo ERROR: auto-install failed.
  set "ERR=1"
  goto :done
)
call :prepend_python_dirs
set "PY="
if exist "%CD%\.python-for-build.txt" (
  for /f "usebackq delims=" %%i in ("%CD%\.python-for-build.txt") do set "PY=%%i"
)
if defined PY echo Read python path file: %PY%
if not defined PY call :pick_python
if not defined PY (
  echo.
  echo ERROR: Python still not usable after auto-install.
  echo Close this window, open a NEW cmd, and run build-windows.bat again
  echo so PATH can refresh. If it still fails, install from:
  echo   https://www.python.org/downloads/windows/
  echo and CHECK "Add python.exe to PATH".
  set "ERR=1"
  goto :done
)

:have_python

echo Using: %PY%
"%PY%" "%CHECK_PY%"
if errorlevel 1 (
  echo ERROR: need win-amd64 Python so pip wheels and the frozen exe match GitHub Release.
  set "ERR=1"
  goto :done
)
echo.

echo [1/5] venv .venv-win
if exist ".venv-win\Scripts\python.exe" (
  ".venv-win\Scripts\python.exe" "%CHECK_PY%" >nul 2>&1
  if errorlevel 1 (
    echo Existing .venv-win is not win-amd64, recreating ...
    rmdir /s /q ".venv-win"
  )
)
if not exist ".venv-win\Scripts\python.exe" (
  if /I "%PY%"=="py -3-amd64" (
    py -3-amd64 -m venv .venv-win
  ) else if /I "%PY%"=="py -3" (
    py -3 -m venv .venv-win
  ) else (
    "%PY%" -m venv .venv-win
  )
  if errorlevel 1 (
    echo ERROR: venv failed
    set "ERR=1"
    goto :done
  )
)
call ".venv-win\Scripts\activate.bat"
if not exist ".venv-win\Scripts\python.exe" (
  echo ERROR: activate failed
  set "ERR=1"
  goto :done
)

echo [2/5] pip install ^(several minutes, non-editable for PyInstaller^)
python -m pip install -U pip
if errorlevel 1 (
  echo ERROR: pip upgrade failed
  set "ERR=1"
  goto :done
)
pip uninstall -y quant-trading-system >nul 2>&1
pip install --prefer-binary --force-reinstall ".[data,dashboard,gui]" pyinstaller
if errorlevel 1 (
  echo ERROR: pip install failed
  set "ERR=1"
  goto :done
)
python -c "import quant_trading_system.stock_analysis.scheduler as s; print('preflight OK', s.__file__)"
if errorlevel 1 (
  echo ERROR: quant_trading_system not importable. Do NOT use pip install -e.
  set "ERR=1"
  goto :done
)

echo [3/5] PyInstaller
pyinstaller app\packaging\gp_assistant.spec --noconfirm
if errorlevel 1 (
  echo ERROR: PyInstaller failed
  set "ERR=1"
  goto :done
)
if not exist "dist\GPAssistant\GPAssistant.exe" (
  echo ERROR: dist\GPAssistant\GPAssistant.exe not created
  dir dist
  set "ERR=1"
  goto :done
)

echo [4/5] verify bundle ^(metadata, SSL, stock_analysis, akshare^)
python app\packaging\verify_frozen_bundle.py dist\GPAssistant
if errorlevel 1 (
  echo ERROR: frozen bundle verification failed. Do not ship this build.
  set "ERR=1"
  goto :done
)
echo Running frozen import smoke test ...
dist\GPAssistant\GPAssistant.exe --smoke-frozen-imports
if errorlevel 1 (
  echo ERROR: --smoke-frozen-imports failed. Do not ship this build.
  if exist "dist\GPAssistant\results\smoke.log" (
    echo ---- smoke.log ----
    type "dist\GPAssistant\results\smoke.log"
  ) else (
    echo smoke.log not found under dist\GPAssistant\results\
  )
  set "ERR=1"
  goto :done
)
if exist "dist\GPAssistant\results\smoke.log" type "dist\GPAssistant\results\smoke.log"

echo [5/5] zip
copy /Y packaging\desktop-readme.txt dist\readme.txt >nul
python app\packaging\make_zip.py dist\GP-Assistant-Windows.zip dist\GPAssistant dist\readme.txt
if errorlevel 1 (
  echo ERROR: zip failed
  set "ERR=1"
  goto :done
)

echo.
echo BUILD OK
echo Run:  %CD%\dist\GPAssistant\GPAssistant.exe
echo Zip:  %CD%\dist\GP-Assistant-Windows.zip
echo Keep the whole GPAssistant folder, not just the exe.
goto :done

:done
echo.
if "%ERR%"=="1" (
  echo BUILD FAILED. Screenshot this window if you need help.
) else (
  echo Done. This window will wait until you press a key.
)
pause
endlocal
exit /b %ERR%

:try_exe
"%~1" "%CHECK_PY%" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%~1"
exit /b 0

:pick_python
set "PY="
set "_CAND="

if exist "%LOCALAPPDATA%\Programs\Python\Python312-amd64\python.exe" call :try_exe "%LOCALAPPDATA%\Programs\Python\Python312-amd64\python.exe"
if defined PY exit /b 0

call :try_exe python
if defined PY exit /b 0

py -3-amd64 "%CHECK_PY%" >nul 2>&1
if not errorlevel 1 set "PY=py -3-amd64" & exit /b 0

py -3 "%CHECK_PY%" >nul 2>&1
if not errorlevel 1 set "PY=py -3" & exit /b 0

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
  if not defined _CAND if exist "%%D\python.exe" set "_CAND=%%D\python.exe"
)
if defined _CAND call :try_exe "%_CAND%"
if defined PY exit /b 0

if exist "%ProgramFiles%\Python312\python.exe" call :try_exe "%ProgramFiles%\Python312\python.exe"
if defined PY exit /b 0

if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
  "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3-amd64 "%CHECK_PY%" >nul 2>&1
  if not errorlevel 1 set "PY=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" & exit /b 0
)
exit /b 1

:prepend_python_dirs
if exist "%LOCALAPPDATA%\Programs\Python\Python312-amd64\python.exe" (
  set "PATH=%LOCALAPPDATA%\Programs\Python\Python312-amd64;%LOCALAPPDATA%\Programs\Python\Python312-amd64\Scripts;%PATH%"
  exit /b 0
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
  echo %%~nxD | find /I "-arm64" >nul
  if errorlevel 1 if exist "%%D\python.exe" (
    set "PATH=%%D;%%D\Scripts;%PATH%"
  )
)
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" (
  set "PATH=%LOCALAPPDATA%\Programs\Python\Launcher;%PATH%"
)
exit /b 0
