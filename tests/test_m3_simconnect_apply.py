"""
M3 SimConnect: backend tests for the optimizer "Apply" path and telemetry hub.

Covers the acceptance criterion "Tests: Client/Hub gegen Mocked-Simvars":
- SimConnectClient.set_target_state  (FL / Mach -> autopilot reference events)
- TelemetryHub.set_target_state      (thread hop + clean failure without a sim)
- TelemetryHub polling               (live snapshot vs offline stays disconnected)
- POST /api/simconnect/apply         (route layer over a stubbed hub)

Test-harness notes (learned the hard way):
- data_fetcher/sim/simconnect_client.py does ``from SimConnect import SimConnect,
  AircraftRequests, AircraftEvents`` at MODULE IMPORT time. Swapping
  sys.modules["SimConnect"] per fixture does NOT help — the module keeps the
  first factory it bound. These tests therefore monkeypatch the module
  ATTRIBUTES (simconnect_client.SimConnect / .AircraftRequests / .AircraftEvents).
- The "apply" path uses autopilot EVENTS (AP_ALT_VAR_SET_ENGLISH /
  AP_MACH_VAR_SET) and verifies them by reading the selected-value SimVars
  back, so the mock sim has to update AUTOPILOT_ALTITUDE_LOCK_VAR /
  AUTOPILOT_MACH_HOLD_VAR when an event fires — exactly like the real sim.
- pytest-asyncio is not installed in the efb venv, so the async tests run
  through plain asyncio.run() instead of @pytest.mark.asyncio.
- TelemetryHub pulls the PROCESS-WIDE FMC bridge singleton in __init__, so the
  poll tests swap in a dedicated manager instance to stay isolated.
- Run with the efb venv (it has SimConnect + fastapi + pytest):
  .venv-efb/Scripts/python.exe -m pytest tests/test_m3_simconnect_apply.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# ── Make the repo importable ─────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_fetcher.sim import simconnect_client as simconnect_client_module


class _MockAircraftRequests:
    """Mocked AircraftRequests: reads from the mock sim's value dict."""

    def __init__(self, sim):
        self._sim = sim

    def get(self, name):
        return self._sim.values.get(name)

    def set(self, name, value):
        self._sim.sets.append((name, value))
        return name in self._sim.acceptable


class _MockEvent:
    def __init__(self, sim, name):
        self._sim = sim
        self._name = name

    def __call__(self, value=0):
        self._sim.events.append((self._name, value))
        if self._name not in self._sim.accepted_events:
            return
        # Mirror the real sim: the event updates the selected-value SimVar.
        if self._name == "AP_ALT_VAR_SET_ENGLISH":
            self._sim.values["AUTOPILOT_ALTITUDE_LOCK_VAR"] = float(value)
        elif self._name == "AP_MACH_VAR_SET":
            self._sim.values["AUTOPILOT_MACH_HOLD_VAR"] = float(value) / 100.0


class _MockEventHelper:
    def __init__(self, sim):
        self._sim = sim

    def get(self, name):
        if name in self._sim.known_events:
            return _MockEvent(self._sim, name)
        return None


class _MockAircraftEvents:
    def __init__(self, sim):
        self.Autopilot = _MockEventHelper(sim)


class _MockSimConnect:
    DEFAULT_EVENTS = ("AP_ALT_VAR_SET_ENGLISH", "AP_MACH_VAR_SET")

    def __init__(self, values=None, acceptable=(), broken=False):
        self.values = dict(values or {})
        self.acceptable = set(acceptable)
        self.broken = broken
        self.sets: list[tuple[str, object]] = []
        self.events: list[tuple[str, object]] = []
        self.known_events = set(self.DEFAULT_EVENTS)
        # Events the sim actually honours (an aircraft may ignore one).
        self.accepted_events = set(self.DEFAULT_EVENTS)
        self.exited = False

    def exit(self):
        self.exited = True

    def connect(self):
        if self.broken:
            raise RuntimeError("MSFS not reachable (mock)")
        return self


def _install_stub(monkeypatch, factory, *, broken=False):
    """Point simconnect_client's bound names at mock classes for this test.

    When ``broken`` is set, the failure is raised from the
    ``AircraftRequests`` constructor — mirroring the real binding, whose
    request channel creation is what fails when MSFS is not reachable.
    (Raising from ``SimConnect()`` itself would be swallowed by the
    client's connect() while leaving the previous state half-set.)
    """

    class SimConnectClass:
        def __new__(cls, *a, **k):
            return factory()

    class AircraftRequests:
        def __new__(cls, sim, *a, **k):
            if broken:
                raise RuntimeError("MSFS not reachable (mock)")
            return _MockAircraftRequests(sim)

    class AircraftEvents:
        def __new__(cls, sim, *a, **k):
            return _MockAircraftEvents(sim)

    monkeypatch.setattr(simconnect_client_module, "SimConnect", SimConnectClass)
    monkeypatch.setattr(
        simconnect_client_module, "AircraftRequests", AircraftRequests
    )
    monkeypatch.setattr(simconnect_client_module, "AircraftEvents", AircraftEvents)


@pytest.fixture
def mock_sim(monkeypatch):
    """A connected sim with default cruise Simvars; returns the state holder."""
    sim_values = {
        "TITLE": "BOEING 777-300ER",
        "INDICATED_ALTITUDE": 37000.0,
        "PLANE_ALTITUDE": 37010.0,
        "AIRSPEED_MACH": 0.84,
        "GROUND_VELOCITY": 490.0,
        "FUEL_TOTAL_QUANTITY_WEIGHT": 20000.0,
        "PLANE_LATITUDE": 25.2,
        "PLANE_LONGITUDE": 51.7,
        "AUTOPILOT_ALTITUDE_LOCK_VAR": 37000.0,
        "AUTOPILOT_MACH_HOLD_VAR": 0.84,
    }
    state = {"sim": None}

    def factory():
        state["sim"] = _MockSimConnect(values=sim_values)
        return state["sim"]

    _install_stub(monkeypatch, factory)
    return state


@pytest.fixture
def offline_sim(monkeypatch):
    """A sim whose SimConnect() constructor fails (MSFS not running)."""
    state = {"sim": None}

    def factory():
        return _MockSimConnect(broken=True)

    _install_stub(monkeypatch, factory, broken=True)
    return state


# ── SimConnectClient.set_target_state ────────────────────────────────────


class TestClientSetTargetState:
    def test_flight_level_fires_ap_alt_var_set_in_feet(self, mock_sim):
        client = simconnect_client_module.SimConnectClient()
        result = client.set_target_state(flight_level=380)

        assert result["applied"] is True
        assert result["flightLevelApplied"] is True
        assert result["flightLevel"] == 380
        assert result["altitudeFt"] == 38000.0
        assert result["selectedAltitudeFt"] == 38000.0
        assert result["errors"] == []
        assert mock_sim["sim"].events == [("AP_ALT_VAR_SET_ENGLISH", 38000)]
        # The aircraft is never teleported: no position SimVar is written.
        assert mock_sim["sim"].sets == []

    def test_mach_fires_ap_mach_var_set_times_100(self, mock_sim):
        client = simconnect_client_module.SimConnectClient()
        result = client.set_target_state(mach=0.86)

        assert result["applied"] is True
        assert result["machApplied"] is True
        assert result["mach"] == 0.86
        assert result["selectedMach"] == pytest.approx(0.86)
        assert mock_sim["sim"].events == [("AP_MACH_VAR_SET", 86)]
        assert mock_sim["sim"].sets == []

    def test_both_targets_applied(self, mock_sim):
        client = simconnect_client_module.SimConnectClient()
        result = client.set_target_state(flight_level=390, mach=0.82)

        assert result["applied"] is True
        assert result["flightLevelApplied"] is True
        assert result["machApplied"] is True
        assert ("AP_ALT_VAR_SET_ENGLISH", 39000) in mock_sim["sim"].events
        assert ("AP_MACH_VAR_SET", 82) in mock_sim["sim"].events

    def test_no_targets_returns_not_applied(self, mock_sim):
        client = simconnect_client_module.SimConnectClient()
        result = client.set_target_state()

        assert result["applied"] is False
        assert result["supported"] is True
        assert any("Nothing to set" in e for e in result["errors"])
        assert mock_sim["sim"] is None  # never connected

    def test_connection_failure_returns_error_not_exception(self, offline_sim):
        client = simconnect_client_module.SimConnectClient()
        result = client.set_target_state(flight_level=380)

        assert result["applied"] is False
        assert result["supported"] is True
        assert result["errors"], "expected an error message, not an exception"
        assert "MSFS" in result["errors"][0]

    def test_ignored_event_reported_per_target(self, mock_sim):
        """An aircraft that swallows the event must NOT report success."""
        client = simconnect_client_module.SimConnectClient()
        client.connect()
        mock_sim["sim"].accepted_events = set()  # events fire but change nothing

        result = client.set_target_state(flight_level=400, mach=0.80)

        assert result["applied"] is False
        assert result["flightLevelApplied"] is False
        assert result["machApplied"] is False
        assert len(result["errors"]) == 2
        assert all("did not accept" in e for e in result["errors"])

    def test_unknown_event_in_binding_is_reported(self, mock_sim):
        client = simconnect_client_module.SimConnectClient()
        client.connect()
        mock_sim["sim"].known_events = set()  # binding exposes no such event

        result = client.set_target_state(flight_level=380)

        assert result["applied"] is False
        assert result["flightLevelApplied"] is False
        assert any("AP_ALT_VAR_SET_ENGLISH" in e for e in result["errors"])


# ── TelemetryHub.set_target_state ────────────────────────────────────────


def _make_hub(client_factory):
    """A TelemetryHub whose FMC-bridge reference is a dedicated instance
    (the real one is a process-wide singleton that would leak state)."""
    from data_fetcher.sim.telemetry_hub import TelemetryHub

    hub = TelemetryHub(client_factory=client_factory)
    from data_fetcher.sim.fmc_bridge import FmcBridgeManager

    hub._fmc_bridge = FmcBridgeManager()
    return hub


class TestHubSetTargetState:
    def test_hub_forwards_to_client(self, mock_sim):
        from data_fetcher.sim.simconnect_client import SimConnectClient

        async def scenario():
            hub = _make_hub(SimConnectClient)
            try:
                return await hub.set_target_state(flight_level=385, mach=0.83)
            finally:
                await hub.stop()

        result = asyncio.run(scenario())

        assert result["applied"] is True
        assert result["flightLevel"] == 385
        assert result["mach"] == 0.83
        assert ("AP_ALT_VAR_SET_ENGLISH", 38500) in mock_sim["sim"].events

    def test_hub_without_sim_fails_cleanly(self, offline_sim):
        from data_fetcher.sim.simconnect_client import SimConnectClient

        async def scenario():
            hub = _make_hub(SimConnectClient)
            try:
                return await hub.set_target_state(flight_level=380)
            finally:
                await hub.stop()

        result = asyncio.run(scenario())

        assert result["applied"] is False
        assert result["supported"] is True
        assert result["errors"]

    def test_hub_survives_unexpected_client_error(self):
        from data_fetcher.sim.sim_client import SimClient

        class BoomClient(SimClient):
            async def get_live_state(self):
                raise RuntimeError("no telemetry in this test")

            def set_target_state(self, *, flight_level=None, mach=None):
                raise RuntimeError("sim exploded")

        async def scenario():
            hub = _make_hub(BoomClient)
            try:
                return await hub.set_target_state(flight_level=380)
            finally:
                await hub.stop()

        result = asyncio.run(scenario())

        assert result["applied"] is False
        assert any("sim exploded" in e for e in result["errors"])

    def test_poll_stores_live_state(self, mock_sim):
        from data_fetcher.sim.simconnect_client import SimConnectClient

        async def scenario():
            hub = _make_hub(SimConnectClient)
            try:
                return await hub.refresh_now()
            finally:
                await hub.stop()

        snapshot = asyncio.run(scenario())

        assert snapshot.connected is True
        assert snapshot.live_state is not None
        assert snapshot.live_state.mach == 0.84
        assert snapshot.live_state.fuel_remaining_kg is not None
        assert snapshot.sample_count == 1
        assert snapshot.last_error is None

    def test_poll_offline_stays_disconnected(self, offline_sim):
        from data_fetcher.sim.simconnect_client import SimConnectClient

        async def scenario():
            hub = _make_hub(SimConnectClient)
            try:
                return await hub.refresh_now()
            finally:
                await hub.stop()

        snapshot = asyncio.run(scenario())

        assert snapshot.connected is False
        assert snapshot.live_state is None
        assert snapshot.last_error is not None


    def test_apply_is_serialized_against_the_poll_loop(self):
        """Apply must not touch the client while a poll is in flight.

        python-simconnect is not thread-safe; overlapping an Apply with the
        1 Hz poll can fail the poll, which closes the client and blanks the
        crew's live strip mid-flight.
        """
        from data_fetcher.sim.sim_client import SimClient
        from data_fetcher.sim.sim_models import LiveSimState

        trace: list[str] = []

        class SlowClient(SimClient):
            async def get_live_state(self):
                trace.append("poll-start")
                await asyncio.sleep(0.05)
                trace.append("poll-end")
                return LiveSimState(aircraft_title="A350-900 (Default Cabin)")

            def set_target_state(self, *, flight_level=None, mach=None):
                trace.append("apply")
                return {"applied": True, "supported": True, "errors": []}

        async def scenario():
            hub = _make_hub(SlowClient)
            try:
                poll = asyncio.create_task(hub._poll_once())
                await asyncio.sleep(0.01)  # let the poll get inside the lock
                apply_result = await hub.set_target_state(flight_level=380)
                await poll
                return apply_result
            finally:
                await hub.stop()

        result = asyncio.run(scenario())

        assert result["applied"] is True
        # The apply waited for the poll to finish rather than interleaving.
        assert trace == ["poll-start", "poll-end", "apply"], trace


# ── POST /api/simconnect/apply (route layer) ─────────────────────────────


def _apply_client(monkeypatch, raw_result):
    """A TestClient over just the SimConnect router with a stubbed hub."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from data_fetcher.sim import simconnect_routes

    captured: dict = {}

    class _StubHub:
        async def set_target_state(self, *, flight_level=None, mach=None):
            captured["flight_level"] = flight_level
            captured["mach"] = mach
            return raw_result

    monkeypatch.setattr(simconnect_routes, "get_telemetry_hub", lambda: _StubHub())

    app = FastAPI()
    app.include_router(simconnect_routes.router)
    return TestClient(app), captured


class TestApplyRoute:
    def test_apply_forwards_camelcase_payload_and_reports_success(self, monkeypatch):
        client, captured = _apply_client(
            monkeypatch,
            {
                "applied": True,
                "supported": True,
                "flightLevelApplied": True,
                "machApplied": True,
                "flightLevel": 380,
                "mach": 0.84,
                "errors": [],
            },
        )

        resp = client.post(
            "/api/simconnect/apply",
            json={"flightLevel": 380, "mach": 0.84, "reason": "Step climb to FL380"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] is True
        assert body["flightLevelApplied"] is True
        assert body["machApplied"] is True
        assert body["errors"] == []
        assert captured == {"flight_level": 380, "mach": 0.84}

    def test_apply_surfaces_sim_errors_verbatim_without_failing(self, monkeypatch):
        client, _ = _apply_client(
            monkeypatch,
            {
                "applied": False,
                "supported": True,
                "errors": ["SimConnect is not connected. Start MSFS and load a flight."],
            },
        )

        resp = client.post("/api/simconnect/apply", json={"flightLevel": 380})

        assert resp.status_code == 200
        body = resp.json()
        assert body["applied"] is False
        assert body["errors"] == [
            "SimConnect is not connected. Start MSFS and load a flight."
        ]

    def test_apply_reports_unsupported_client(self, monkeypatch):
        client, _ = _apply_client(
            monkeypatch,
            {
                "applied": False,
                "supported": False,
                "errors": ["This sim client does not support target-state commands."],
            },
        )

        resp = client.post("/api/simconnect/apply", json={"mach": 0.82})

        assert resp.status_code == 200
        assert resp.json()["supported"] is False
