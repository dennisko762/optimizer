from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data_fetcher.sim.simconnect_routes import router as simconnect_router
from data_fetcher.sim.telemetry_hub import start_telemetry_hub, stop_telemetry_hub

from optimizer.api.optimize_routes import router as optimize_router
from optimizer.api.simbrief_routes import router as simbrief_router


def create_app() -> FastAPI:
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

    @app.on_event("startup")
    async def startup_telemetry() -> None:
        await start_telemetry_hub()

    @app.on_event("shutdown")
    async def shutdown_telemetry() -> None:
        await stop_telemetry_hub()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
