Build GP Assistant on Windows (do not use macOS)
================================================

1. Install Python 3.10+ and check "Add python.exe to PATH"
   https://www.python.org/downloads/windows/

2. Unzip this folder COMPLETELY (do not double-click files inside the .zip window).
   Then double-click build-windows.bat in the unzipped root.
   If Python is missing, the script installs Python 3.12 automatically (needs internet).
   On ARM Windows the helper uses x64 Python (emulation) so pip can install
   wheels AND so the frozen GPAssistant.exe is win-amd64 -- the same artifact
   GitHub Release ships, which runs on ordinary Intel/AMD PCs. Detect the
   interpreter with sysconfig.get_platform()==win-amd64, not platform.machine()
   (that reports the CPU, which is ARM64 even for x64 Python).

3. After PyInstaller the script auto-verifies streamlit metadata, OpenSSL DLLs,
   stock_analysis/akshare in the bundle, then runs GPAssistant.exe --smoke-frozen-imports.
   If any step fails you get BUILD FAILED — do not ship that dist folder.
   Success: dist\GPAssistant\GPAssistant.exe (keep the whole GPAssistant folder).

This tree includes the v0.3.8 capital-gate and in-app updater.
Do not test startup bugs with the GitHub v0.3.6 / v0.3.7 Windows zip.
