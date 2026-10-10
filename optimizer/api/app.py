from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from data_fetcher.sim.simconnect_routes import router as simconnect_router
from data_fetcher.sim.telemetry_hub import start_telemetry_hub, stop_telemetry_hub

from optimizer.api.optimize_routes import router as optimize_router
from optimizer.api.simbrief_routes import router as simbrief_router
from optimizer.api.trajectory_routes import router as trajectory_router

from crew_platform.routes import router as crew_router, callback_router as crew_callback_router, technical_router as crew_technical_router
from crew_platform.settings_routes import router as crew_settings_router
from crew_platform.weather.routes import router as crew_weather_router
from crew_platform.weather import scheduler as weather_scheduler
from crew_platform.weather.briefing.routes import router as crew_briefing_router
from crew_platform.navigraph.routes import router as navigraph_router


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ (stdlib only).

    Used for VAMSYS_PILOT_CLIENT_ID / VAMSYS_REDIRECT_URI /
    CREW_PLATFORM_SESSION_SECRET. Existing environment variables always
    win; values are never logged. The file is gitignored.
    """
    for candidate in (
        Path(__file__).parent.parent.parent / ".env",
        Path(os.environ.get("EFB_ENV_FILE", "")) if os.environ.get("EFB_ENV_FILE") else None,
    ):
        if not candidate or not candidate.is_file():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            pass


def create_app() -> FastAPI:
    _load_dotenv()

    app = FastAPI(
        title="Dynamic CI Assistant API",
        version="0.1.0",
    )

    _cors_env = os.environ.get("CORS_ORIGINS", "")
    _origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    if not _origins:
        _origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    _allow_all = "*" in _origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if _allow_all else _origins,
        # credentials cannot be used with wildcard origin
        allow_credentials=not _allow_all,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(optimize_router)
    app.include_router(simbrief_router)
    app.include_router(simconnect_router)
    app.include_router(trajectory_router)
    app.include_router(crew_router)
    app.include_router(crew_technical_router)
    app.include_router(crew_settings_router)
    app.include_router(crew_weather_router)
    app.include_router(crew_briefing_router)
    app.include_router(navigraph_router)
    # Browser OAuth callback — must be registered before the catch-all
    # static-file mount below so the redirect URL is not swallowed.
    app.include_router(crew_callback_router)

    @app.on_event("startup")
    async def startup_telemetry() -> None:
        await start_telemetry_hub()
        # background GFS cycle scheduler (no-op until a route is active)
        weather_scheduler.start()

    @app.on_event("shutdown")
    async def shutdown_telemetry() -> None:
        await stop_telemetry_hub()
        weather_scheduler.stop()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # Dist path (built Vite frontend) — shared by /privacy and the catch-all mount.
    if getattr(sys, "frozen", False):
        _dist = Path(sys._MEIPASS) / "efb-ui" / "dist"
    else:
        _dist = Path(__file__).parent.parent.parent / "efb-ui" / "dist"

    # /privacy — public privacy policy required by vAMSYS for Pilot API
    # clients. Served explicitly (StaticFiles would only serve /privacy.html).
    from fastapi.responses import FileResponse

    if _dist.exists():

        @app.get("/privacy", include_in_schema=False)
        async def privacy_page():
            return FileResponse(str(_dist / "privacy.html"))

    # Serve the built Vite frontend if dist/ exists.
    # Must be mounted last — catches all unmatched routes.
    if _dist.exists():
        app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")

    return app


app = create_app()
