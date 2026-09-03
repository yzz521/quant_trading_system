@echo off
REM Double-click this in the unzipped repo root if packaging\build-windows.bat flashes.
cd /d "%~dp0"
cmd /k packaging\build-windows.bat
