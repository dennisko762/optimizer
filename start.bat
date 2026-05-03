@echo off
setlocal

echo.
echo  EFB Backend
echo  ===========
echo.

:: ── Sim connection ───────────────────────────────────────────────────────────
:: local  = SimConnect on this PC (MSFS must be running here)
:: remote = connect to a sim_bridge.exe running on the sim PC
set SIM_SOURCE=remote
set SIM_BRIDGE_URL=http://192.168.178.51:7070

:: ── CORS ─────────────────────────────────────────────────────────────────────
:: * = allow all origins (needed when browser runs on a different device)
set CORS_ORIGINS=*

:: ── Start ────────────────────────────────────────────────────────────────────
echo  SIM_SOURCE    = %SIM_SOURCE%
echo  SIM_BRIDGE_URL = %SIM_BRIDGE_URL%
echo.
echo  Open in browser:  http://localhost:8000
echo  From other device: http://<this-pc-ip>:8000
echo.

uvicorn optimizer.api.app:app --host 0.0.0.0 --port 8000

pause
