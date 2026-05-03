from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.simconnect_client import SimConnectClient, SimConnectClientError


DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_SIMCONNECT_CACHE_MS = 200


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
        client_factory: Callable[[], SimConnectClient] | None = None,
    ) -> None:
        self.poll_interval_s = poll_interval_s
        self._client_factory = client_factory or (
            lambda: SimConnectClient(cache_ms=DEFAULT_SIMCONNECT_CACHE_MS)
        )
        self._client = self._client_factory()
        self._snapshot = TelemetrySnapshot(poll_interval_s=poll_interval_s)
        self._snapshot_lock = asyncio.Lock()
        self._poll_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

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

    async def _run(self) -> None:
        while True:
            await self._poll_once()
            await asyncio.sleep(self.poll_interval_s)

    async def _poll_once(self) -> None:
        async with self._poll_lock:
            try:
                live = await self._client.get_live_state()
            except SimConnectClientError as exc:
                await asyncio.to_thread(self._safe_close_client)
                await self._store_disconnected(str(exc))
            except Exception as exc:
                await self._store_disconnected(
                    f"Unexpected SimConnect telemetry error: {exc}"
                )
            else:
                await self._store_live(live)

    async def _store_live(self, live: LiveSimState) -> None:
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
        try:
            self._client.close()
        except Exception:
            pass
        self._client = self._client_factory()


_telemetry_hub = TelemetryHub()


def get_telemetry_hub() -> TelemetryHub:
    return _telemetry_hub


async def start_telemetry_hub() -> None:
    await _telemetry_hub.start()


async def stop_telemetry_hub() -> None:
    await _telemetry_hub.stop()
