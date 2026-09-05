@echo off
setlocal
cd /d "%~dp0"
if exist "dist\EVE-Telemetry-Overlay.exe" (
    start "" "dist\EVE-Telemetry-Overlay.exe"
    exit /b 0
)
echo The EXE has not been built yet.
echo Double-click BUILD_EXE.bat first.
pause
