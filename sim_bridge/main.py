"""
EFB Server — single executable for the sim PC.

Runs SimConnect, optimization API, SimBrief sync, and serves the
frontend — all in one process. No separate backend or cloud needed.

Open in browser from any device on the same network:
    http://<sim-pc-ip>:7070

Tailscale can be set up explicitly with --setup-tailscale.
"""
from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import subprocess
import sys
import traceback

if not getattr(sys, "frozen", False):
    _PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

# Must be set before any project imports so telemetry_hub picks it up.
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


def _tailscale_running() -> bool:
    try:
        r = subprocess.run(["tailscale", "version"], capture_output=True, timeout=1.5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _tailscale_ip() -> str | None:
    try:
        r = subprocess.run(
            ["tailscale", "ip", "--4"],
            capture_output=True, text=True, timeout=1.5,
        )
        ip = r.stdout.strip()
        return ip if ip else None
    except Exception:
        return None


def _install_tailscale_winget() -> bool:
    """Try installing via winget (available on Windows 10 1709+ / Windows 11)."""
    try:
        r = subprocess.run(
            [
                "winget", "install", "tailscale.tailscale",
                "-e", "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            timeout=120,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pause_before_exit() -> None:
    try:
        input("  Press Enter to exit...")
    except EOFError:
        pass


def _show_tailscale_status(port: int, *, setup: bool) -> None:
    if _tailscale_running():
        ts_ip = _tailscale_ip()
        if ts_ip:
            print(f"  Tailscale:     http://{ts_ip}:{port}  (from anywhere)")
        return

    if not setup:
        print("  Tailscale:     not running  (use --setup-tailscale to enable)")
        return

    print()
    print("  Tailscale not found.")
    print("  With Tailscale you can reach this EFB from anywhere,")
    print("  not just your local network.")
    print()
    answer = input("  Install Tailscale now? [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes", "j", "ja"):
        print("  Skipped. Install later from https://tailscale.com/download/windows")
        return

    print("  Installing via winget...")
    if _install_tailscale_winget():
        print("  Tailscale installed.")
        print("  Open Tailscale in the system tray and log in.")
        ts_ip = _tailscale_ip()
        if ts_ip:
            print(f"  Tailscale: http://{ts_ip}:{port}")
    else:
        print("  winget install failed or not available.")
        print("  Opening download page...")
        try:
            import webbrowser
            webbrowser.open("https://tailscale.com/download/windows")
        except Exception:
            print("  Download manually: https://tailscale.com/download/windows")


def main() -> None:
    multiprocessing.freeze_support()

    parser = argparse.ArgumentParser(description="EFB Server")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--no-tailscale", action="store_true")
    parser.add_argument("--setup-tailscale", action="store_true")
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
        _show_tailscale_status(args.port, setup=args.setup_tailscale)

    print()

    try:
        from optimizer.api.app import create_app
        import uvicorn
    except Exception:
        print("  ERROR: Failed to load application modules.")
        print()
        traceback.print_exc()
        print()
        _pause_before_exit()
        sys.exit(1)

    try:
        app = create_app()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except Exception:
        print()
        print("  ERROR: Server crashed.")
        traceback.print_exc()
        print()
        _pause_before_exit()
        sys.exit(1)


if __name__ == "__main__":
    main()
