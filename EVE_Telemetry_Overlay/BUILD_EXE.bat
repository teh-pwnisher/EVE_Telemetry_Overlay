@echo off
setlocal
cd /d "%~dp0"
title Build EVE Telemetry Overlay

echo ============================================================
echo   EVE TELEMETRY OVERLAY - ONE CLICK EXE BUILDER
echo ============================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    set PY=py
    goto :havepython
)

where python >nul 2>nul
if %errorlevel%==0 (
    set PY=python
    goto :havepython
)

echo Python was not found.
where winget >nul 2>nul
if not %errorlevel%==0 (
    echo ERROR: Python and winget are both unavailable.
    echo Install Python 3, then double-click this file again.
    pause
    exit /b 1
)

echo Installing Python...
winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
if not %errorlevel%==0 (
    echo Python installation failed.
    pause
    exit /b 1
)
set PY=py

:havepython
echo Installing build dependencies...
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade pyinstaller psutil
if not %errorlevel%==0 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Building windowed executable...
%PY% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "EVE-Telemetry-Overlay" ^
  --collect-all psutil ^
  "EVE_Telemetry_Overlay.pyw"

if not %errorlevel%==0 (
    echo BUILD FAILED.
    pause
    exit /b 1
)

copy /y "config.json" "dist\config.json" >nul

echo.
echo BUILD COMPLETE
echo EXE: %CD%\dist\EVE-Telemetry-Overlay.exe
echo.
echo Ctrl+Alt+T = lock/unlock click-through
echo Ctrl+Alt+Q = quit
echo.
start "" "%CD%\dist"
pause
