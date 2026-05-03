@echo off
:: Dev shortcut — starts the bridge without building an exe.
:: Requires Python + dependencies installed in this environment.
cd /d "%~dp0"
python sim_bridge/main.py %*
pause
