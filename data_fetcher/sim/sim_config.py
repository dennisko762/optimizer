from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_fetcher.sim.sim_client import SimClient

SIM_SOURCE_LOCAL = "local"
SIM_SOURCE_REMOTE = "remote"

_DEFAULT_BRIDGE_URL = "http://localhost:7070"
_DEFAULT_SIMCONNECT_CACHE_MS = 200


def get_sim_source() -> str:
    return os.environ.get("SIM_SOURCE", SIM_SOURCE_LOCAL).lower()


def get_sim_bridge_url() -> str:
    return os.environ.get("SIM_BRIDGE_URL", _DEFAULT_BRIDGE_URL)


def create_sim_client() -> "SimClient":
    if get_sim_source() == SIM_SOURCE_REMOTE:
        from data_fetcher.sim.remote_sim_client import RemoteSimClient
        return RemoteSimClient(bridge_url=get_sim_bridge_url())

    from data_fetcher.sim.simconnect_client import SimConnectClient
    return SimConnectClient(cache_ms=_DEFAULT_SIMCONNECT_CACHE_MS)
