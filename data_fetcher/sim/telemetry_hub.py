from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from data_fetcher.sim.fmc_bridge import get_fmc_bridge_manager
from data_fetcher.sim.sim_client import SimClient, SimClientError
from data_fetcher.sim.sim_config import create_sim_client
from data_fetcher.sim.sim_models import LiveSimState


DEFAULT_POLL_INTERVAL_S = 1.0


@dataclass(slots=True)
class TelemetrySnapshot:
    connected: bool = False
    live_state: LiveSimState | None = None
    last_sample_utc: datetime | None = None
    last_error: str | None = None
    poll_interval_s: float = DEFAULT_POLL_INTERVAL_S
    sample_count: int = 0

    @property
    def data_age_ms(self) -> int | None:
        if self.last_sample_utc is None:
            return None

        delta = datetime.now(timezone.utc) - self.last_sample_utc
        return max(int(delta.total_seconds() * 1000.0), 0)


class TelemetryHub:
    """
    Lightweight local telemetry collector for SimConnect.

    The hub polls SimConnect on a fixed interval in the background and keeps
    the last snapshot in memory. API routes then read the cached snapshot
    instead of performing their own live SimConnect request.
    """

    def __init__(
        self,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        client_factory: Callable[[], SimClient] | None = None,
    ) -> None:
        self.poll_interval_s = poll_interval_s
        self._client_factory = client_factory or create_sim_client
        self._fmc_bridge = get_fmc_bridge_manager()
        self._client: SimClient | None = None
        self._snapshot = TelemetrySnapshot(poll_interval_s=poll_interval_s)
        self._snapshot_lock = asyncio.Lock()
        self._poll_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_logged_error: str | None = None
        self._last_logged_connected = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(
            self._run(),
            name="simconnect-telemetry-hub",
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None

        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        await asyncio.to_thread(self._safe_close_client)
        await asyncio.to_thread(self._fmc_bridge.close)

    async def get_snapshot(self) -> TelemetrySnapshot:
        async with self._snapshot_lock:
            return TelemetrySnapshot(
                connected=self._snapshot.connected,
                live_state=(
                    self._snapshot.live_state.model_copy(deep=True)
                    if self._snapshot.live_state is not None
                    else None
                ),
                last_sample_utc=self._snapshot.last_sample_utc,
                last_error=self._snapshot.last_error,
                poll_interval_s=self._snapshot.poll_interval_s,
                sample_count=self._snapshot.sample_count,
            )

    async def refresh_now(self) -> TelemetrySnapshot:
        await self._poll_once()
        return await self.get_snapshot()

    async def set_target_state(
        self,
        *,
        flight_level: int | None = None,
        mach: float | None = None,
    ) -> dict:
        """
        Push a target flight level / Mach into the running sim.

        Runs in a thread (SimConnect calls are blocking). If no client
        exists yet (sim never connected) the call still creates one and
        fails cleanly — the returned dict carries the error text.
        """
        try:
            client = await asyncio.to_thread(self._get_client)
            return await asyncio.to_thread(
                client.set_target_state,
                flight_level=flight_level,
                mach=mach,
            )
        except SimClientError as exc:
            return {"applied": False, "supported": True, "errors": [str(exc)]}
        except Exception as exc:
            return {
                "applied": False,
                "supported": True,
                "errors": [f"Unexpected SimConnect command error: {exc}"],
            }

    async def _run(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> None:
        async with self._poll_lock:
            try:
                live = await self._get_client().get_live_state()
            except SimClientError as exc:
                await asyncio.to_thread(self._safe_close_client)
                await self._store_disconnected(str(exc))
            except Exception as exc:
                await self._store_disconnected(
                    f"Unexpected SimConnect telemetry error: {exc}"
                )
            else:
                live = await asyncio.to_thread(self._enrich_with_fmc_snapshot, live)
                await self._store_live(live)

    def _get_client(self) -> SimClient:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    async def _store_live(self, live: LiveSimState) -> None:
        if not self._last_logged_connected:
            print("SimConnect telemetry connected.")
        self._last_logged_connected = True
        self._last_logged_error = None

        async with self._snapshot_lock:
            self._snapshot = TelemetrySnapshot(
                connected=True,
                live_state=live,
                last_sample_utc=datetime.now(timezone.utc),
                last_error=None,
                poll_interval_s=self.poll_interval_s,
                sample_count=self._snapshot.sample_count + 1,
            )

    async def _store_disconnected(self, error_message: str) -> None:
        if self._last_logged_connected or error_message != self._last_logged_error:
            print(f"SimConnect telemetry offline: {error_message}")
        self._last_logged_connected = False
        self._last_logged_error = error_message

        async with self._snapshot_lock:
            self._snapshot = TelemetrySnapshot(
                connected=False,
                live_state=self._snapshot.live_state,
                last_sample_utc=self._snapshot.last_sample_utc,
                last_error=error_message,
                poll_interval_s=self.poll_interval_s,
                sample_count=self._snapshot.sample_count,
            )

    def _safe_close_client(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = None

    def _enrich_with_fmc_snapshot(self, live: LiveSimState) -> LiveSimState:
        self._fmc_bridge.update_aircraft_context(live.aircraft_title)
        snapshot, status, error = self._fmc_bridge.get_latest_snapshot()
        return live.model_copy(
            update={
                "fmc_snapshot": snapshot,
                "fmc_adapter_status": status,
                "fmc_adapter_error": error,
            }
        )


_telemetry_hub = TelemetryHub()


def get_telemetry_hub() -> TelemetryHub:
    return _telemetry_hub


async def start_telemetry_hub() -> None:
    await _telemetry_hub.start()


async def stop_telemetry_hub() -> None:
    await _telemetry_hub.stop()
