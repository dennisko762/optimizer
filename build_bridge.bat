@echo off
setlocal

echo.
echo  EFB Sim Bridge -- Build
echo  =======================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found in PATH.
    echo  Install Python 3.11+ and make sure it is on the PATH.
    pause
    exit /b 1
)

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo  PyInstaller not found. Installing...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo  ERROR: Could not install PyInstaller.
        pause
        exit /b 1
    )
)

echo  Installing / verifying dependencies...
python -m pip install httpx fastapi uvicorn[standard] SimConnect pydantic --quiet
if errorlevel 1 (
    echo  ERROR: Dependency install failed.
    pause
    exit /b 1
)

echo.
echo  Building sim_bridge.exe...
echo.

pyinstaller sim_bridge.spec --noconfirm
if errorlevel 1 (
    echo.
    echo  ERROR: Build failed. See output above.
    pause
    exit /b 1
)

echo.
echo  Done!
echo  Output: dist\sim_bridge.exe
echo.
echo  Copy dist\sim_bridge.exe to any Windows PC running MSFS.
echo  Double-click to start. No Python required on that PC.
echo.
pause
