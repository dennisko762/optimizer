#!/bin/sh

# EFB Backend
# Passe SIM_BRIDGE_URL auf die IP des Sim-PCs an.

export SIM_SOURCE=remote
export SIM_BRIDGE_URL=http://192.168.178.51:7070   # ← IP des Sim-PCs hier eintragen
export CORS_ORIGINS="*"

echo ""
echo "  SIM_BRIDGE_URL = $SIM_BRIDGE_URL"
echo "  Browser: http://localhost:8000"
echo ""

uvicorn optimizer.api.app:app --host 0.0.0.0 --port 8000
