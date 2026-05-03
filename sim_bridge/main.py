"""
EFB Sim Bridge — runs on the Windows sim PC alongside MSFS.

Exposes SimConnect telemetry over HTTP so the EFB backend and UI can
run on any device that can reach this PC (tablet, laptop, remote server).

Usage (from project root on the sim PC):
    python sim_bridge/main.py

Configuration via environment variables:
    SIM_BRIDGE_HOST   Bind address (default: 0.0.0.0 — all interfaces)
    SIM_BRIDGE_PORT   Port          (default: 7070)

Remote access options:
    Local network  — connect using this PC's LAN IP (e.g. 192.168.1.x)
    From anywhere  — use Tailscale, ZeroTier, or ngrok as a secure tunnel

On the device running the EFB backend, set:
    SIM_SOURCE=remote
    SIM_BRIDGE_URL=http://<sim-pc-ip>:7070
"""
from __future__ import annotations

import argparse
import os

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.simconnect_client import SimConnectClient
from data_fetcher.sim.telemetry_hub import TelemetryHub, TelemetrySnapshot

_DEFAULT_HOST = os.environ.get("SIM_BRIDGE_HOST", "0.0.0.0")
_DEFAULT_PORT = int(os.environ.get("SIM_BRIDGE_PORT", "7070"))

app = FastAPI(title="EFB Sim Bridge", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_hub: TelemetryHub | None = None


@app.on_event("startup")
async def _startup() -> None:
    global _hub
    _hub = TelemetryHub(
        poll_interval_s=1.0,
        client_factory=lambda: SimConnectClient(cache_ms=200),
    )
    await _hub.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    if _hub is not None:
        await _hub.stop()


async def _get_snapshot() -> TelemetrySnapshot:
    if _hub is None:
        return TelemetrySnapshot()
    snap = await _hub.get_snapshot()
    if snap.live_state is None and snap.last_error is None:
        snap = await _hub.refresh_now()
    return snap


@app.get("/health")
async def health() -> dict:
    snap = await _get_snapshot()
    return {
        "status": "ok",
        "sim": "connected" if snap.connected else "disconnected",
    }


@app.get("/sim/status")
async def sim_status() -> dict:
    snap = await _get_snapshot()
    return {
        "connected": snap.connected,
        "sampleCount": snap.sample_count,
        "dataAgeMs": snap.data_age_ms,
        "lastError": snap.last_error,
        "lastSampleUtc": (
            snap.last_sample_utc.isoformat() if snap.last_sample_utc else None
        ),
    }


@app.get("/sim/telemetry", response_model=LiveSimState)
async def sim_telemetry() -> LiveSimState:
    snap = await _get_snapshot()
    if not snap.connected or snap.live_state is None:
        raise HTTPException(
            status_code=503,
            detail=snap.last_error or "SimConnect not connected. Make sure MSFS is running.",
        )
    return snap.live_state


def main() -> None:
    parser = argparse.ArgumentParser(description="EFB Sim Bridge")
    parser.add_argument(
        "--host", default=_DEFAULT_HOST,
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help="Port (default: 7070)",
    )
    args = parser.parse_args()

    print(f"\n  EFB Sim Bridge — starting on {args.host}:{args.port}")
    print(f"  Waiting for MSFS / SimConnect...")
    print(f"\n  Connect the EFB backend from another device:")
    print(f"    SIM_SOURCE=remote")
    print(f"    SIM_BRIDGE_URL=http://<this-pc-ip>:{args.port}\n")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
