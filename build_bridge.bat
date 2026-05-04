@echo off
setlocal

echo.
echo  EFB -- Build
echo  ============
echo.

:: ── Node / npm ───────────────────────────────────────────────────────────────
where npm >nul 2>&1
if errorlevel 1 (
    echo  ERROR: npm not found. Install Node.js from https://nodejs.org
    pause & exit /b 1
)

echo  Building frontend...
cd efb-ui
if exist package-lock.json (
    call npm ci --silent --prefer-offline
) else (
    call npm install --silent --prefer-offline
)
if errorlevel 1 ( echo  ERROR: npm install failed. & cd .. & pause & exit /b 1 )
call npm run build
if errorlevel 1 ( echo  ERROR: npm run build failed. & cd .. & pause & exit /b 1 )
cd ..
echo  Frontend ready.
echo.

:: ── Python / PyInstaller ─────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 ( echo  ERROR: Python not found. & pause & exit /b 1 )

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo  Installing PyInstaller...
    python -m pip install pyinstaller --quiet
)

echo  Installing Python dependencies...
python -m pip install --disable-pip-version-check --upgrade-strategy only-if-needed -r requirements-efb.txt --quiet

echo.
echo  Building efb.exe...
echo.
pyinstaller sim_bridge.spec --noconfirm --clean
if errorlevel 1 ( echo. & echo  ERROR: Build failed. & pause & exit /b 1 )

echo.
echo  Done!  →  dist\efb.exe
echo.
echo  Copy dist\efb.exe to the sim PC and double-click to start.
echo.
pause
