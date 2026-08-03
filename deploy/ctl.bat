@echo off
REM Windows CMD 入口
REM 用法: deploy\ctl.bat start-all
REM       deploy\ctl.bat start-all --with-scheduler
REM       deploy\ctl.bat scheduler start
setlocal
set ROOT=%~dp0..
if defined PYTHON_BIN (
  set PY=%PYTHON_BIN%
) else (
  set PY=python
)
cd /d "%ROOT%"
"%PY%" "%~dp0ctl.py" %*
exit /b %ERRORLEVEL%
