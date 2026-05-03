"""
EFB Server — single executable for the sim PC.

Runs SimConnect, optimization API, SimBrief sync, and serves the
frontend — all in one process. No separate backend or cloud needed.

Open in browser from any device on the same network:
    http://<sim-pc-ip>:7070

Tailscale is installed automatically on first run if not present,
so the EFB is reachable from anywhere (not just the local network).
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import subprocess
import sys
import urllib.request

# Exe always runs alongside MSFS — use SimConnect directly.
os.environ.setdefault("SIM_SOURCE", "local")
os.environ.setdefault("CORS_ORIGINS", "*")

_DEFAULT_PORT = int(os.environ.get("EFB_PORT", "7070"))

def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "localhost"


_TAILSCALE_INSTALLER_URL = (
    "https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe"
)


def _tailscale_installed() -> bool:
    try:
        result = subprocess.run(
            ["tailscale", "version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _install_tailscale() -> None:
    print("  Tailscale not found — downloading installer...")
    installer_path = os.path.join(os.environ.get("TEMP", "."), "tailscale-setup.exe")
    try:
        urllib.request.urlretrieve(_TAILSCALE_INSTALLER_URL, installer_path)
        print("  Running Tailscale installer (follow the prompts)...")
        subprocess.run([installer_path, "/silent", "/norestart"], check=True)
        print("  Tailscale installed.")
        print("  Open Tailscale in the system tray and log in to get your remote IP.")
    except Exception as exc:
        print(f"  Could not install Tailscale automatically: {exc}")
        print(f"  Install manually from https://tailscale.com/download")


def _ensure_tailscale() -> None:
    if _tailscale_installed():
        try:
            result = subprocess.run(
                ["tailscale", "ip", "--4"],
                capture_output=True, text=True, timeout=5,
            )
            ts_ip = result.stdout.strip()
            if ts_ip:
                print(f"  Tailscale:     http://{ts_ip}:{_DEFAULT_PORT}  (from anywhere)")
        except Exception:
            pass
        return

    print()
    print("  Tailscale not detected.")
    answer = input("  Install Tailscale for remote access from anywhere? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes", "j", "ja"):
        _install_tailscale()
    else:
        print("  Skipped. Install later from https://tailscale.com/download")


def main() -> None:
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="EFB Server")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-tailscale", action="store_true",
                        help="Skip Tailscale check")
    args = parser.parse_args()

    local_ip = _local_ip()

    print()
    print("  ╔══════════════════════════════╗")
    print("  ║       EFB Server             ║")
    print("  ╚══════════════════════════════╝")
    print()
    print(f"  This PC:       http://localhost:{args.port}")
    print(f"  Local network: http://{local_ip}:{args.port}")

    if not args.no_tailscale:
        _ensure_tailscale()

    print()

    from optimizer.api.app import create_app
    import uvicorn

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
