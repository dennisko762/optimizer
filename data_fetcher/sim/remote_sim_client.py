from __future__ import annotations

import httpx

from data_fetcher.sim.sim_client import SimClient, SimClientError
from data_fetcher.sim.sim_models import LiveSimState


class RemoteSimClientError(SimClientError):
    pass


class RemoteSimClient(SimClient):
    """
    Connects to a running sim_bridge instance over HTTP.

    The bridge runs on the sim PC (Windows/MSFS) and exposes LiveSimState
    via GET /sim/telemetry. This client can run on any device that can reach
    the bridge's IP and port.
    """

    def __init__(self, bridge_url: str, timeout_s: float = 3.0) -> None:
        self._bridge_url = bridge_url.rstrip("/")
        self._timeout_s = timeout_s

    async def get_live_state(self) -> LiveSimState:
        url = f"{self._bridge_url}/sim/telemetry"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as client:
                response = await client.get(url)
                response.raise_for_status()
                return LiveSimState.model_validate(response.json())
        except httpx.HTTPStatusError as exc:
            raise RemoteSimClientError(
                f"Sim bridge returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.ConnectError as exc:
            raise RemoteSimClientError(
                f"Cannot reach sim bridge at {self._bridge_url}. "
                "Make sure sim_bridge is running on the sim PC."
            ) from exc
        except httpx.TimeoutException as exc:
            raise RemoteSimClientError(
                f"Sim bridge at {self._bridge_url} timed out after {self._timeout_s}s."
            ) from exc
        except Exception as exc:
            raise RemoteSimClientError(
                f"Unexpected error reading from sim bridge: {exc}"
            ) from exc
