"""Tests for TechLog P1-T5 — aircraft technical status derivation.

Covers:
- Pure ``derive_status`` unit tests: all 4 branches + precedence
  (OPEN beats DEFERRED beats UNDER_REVIEW beats none) and terminal
  statuses being ignored.
- Automatic status updates on every defect state change and after
  maintenance rectification — no explicit "recompute" call from the
  client (stored ``aircraft.current_technical_status`` column checked).
- GET /aircraft/{reg}/status — shape, live-defect listing, 404.
- Acceptance walk: OPEN → OPEN_DEFECTS, DEFERRED → DISPATCHABLE_WITH_MEL,
  RECTIFIED → SERVICEABLE.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient

from crew_platform.technical.status import (
    TECHNICAL_STATUS_VALUES,
    derive_status,
    live_defects,
)


class _DefectLike:
    """Minimal stand-in for a Defect with a status attribute."""

    def __init__(self, status: str):
        self.status = status


def _statuses(*statuses):
    return [_DefectLike(s) for s in statuses]


# ===========================================================================
# Pure derivation unit tests
# ===========================================================================


class TestDeriveStatus:
    def test_no_defects_is_serviceable(self):
        assert derive_status([]) == "SERVICEABLE"

    def test_only_terminal_defects_is_serviceable(self):
        assert derive_status(_statuses("RECTIFIED", "CLOSED")) == "SERVICEABLE"

    def test_open_defect(self):
        assert derive_status(_statuses("OPEN")) == "OPEN_DEFECTS"

    def test_deferred_is_dispatchable_with_mel(self):
        assert derive_status(_statuses("DEFERRED")) == "DISPATCHABLE_WITH_MEL"

    def test_mel_applied_is_dispatchable_with_mel(self):
        assert derive_status(_statuses("MEL_APPLIED")) == "DISPATCHABLE_WITH_MEL"

    def test_under_review(self):
        assert derive_status(_statuses("UNDER_REVIEW")) == "UNDER_REVIEW"

    def test_precedence_open_beats_deferred(self):
        assert (
            derive_status(_statuses("OPEN", "DEFERRED")) == "OPEN_DEFECTS"
        )

    def test_precedence_open_beats_mel_applied_and_under_review(self):
        assert (
            derive_status(_statuses("OPEN", "MEL_APPLIED", "UNDER_REVIEW"))
            == "OPEN_DEFECTS"
        )

    def test_precedence_deferred_beats_under_review(self):
        assert (
            derive_status(_statuses("DEFERRED", "UNDER_REVIEW"))
            == "DISPATCHABLE_WITH_MEL"
        )

    def test_precedence_mel_applied_beats_under_review(self):
        assert (
            derive_status(_statuses("MEL_APPLIED", "UNDER_REVIEW"))
            == "DISPATCHABLE_WITH_MEL"
        )

    def test_full_precedence_chain(self):
        # OPEN beats DEFERRED beats UNDER_REVIEW beats none:
        full = _statuses("OPEN", "DEFERRED", "UNDER_REVIEW")
        assert derive_status(full) == "OPEN_DEFECTS"
        assert derive_status(full[1:]) == "DISPATCHABLE_WITH_MEL"
        assert derive_status(full[2:]) == "UNDER_REVIEW"
        assert derive_status([]) == "SERVICEABLE"

    def test_terminal_defects_do_not_influence_status(self):
        assert (
            derive_status(_statuses("UNDER_REVIEW", "RECTIFIED", "CLOSED"))
            == "UNDER_REVIEW"
        )

    def test_accepts_raw_status_strings(self):
        assert derive_status(["OPEN"]) == "OPEN_DEFECTS"
        assert derive_status(["deferred", "closed"]) == "DISPATCHABLE_WITH_MEL"

    def test_unknown_statuses_ignored(self):
        assert derive_status(_statuses("SOMETHING_ELSE")) == "SERVICEABLE"

    def test_all_values_are_valid(self):
        for value in TECHNICAL_STATUS_VALUES:
            assert value in ("OPEN_DEFECTS", "DISPATCHABLE_WITH_MEL",
                             "UNDER_REVIEW", "SERVICEABLE")

    def test_live_defects_filters_terminals(self):
        defects = _statuses("OPEN", "RECTIFIED", "CLOSED", "DEFERRED")
        live = live_defects(defects)
        assert [d.status for d in live] == ["OPEN", "DEFERRED"]


# ===========================================================================
# API tests
# ===========================================================================


@pytest.fixture()
def api_client(tmp_path):
    """Return a TestClient with the techlog DB pointed at a temp file."""
    path = tmp_path / "techlog_status_test.db"
    os.environ["CREW_TECHLOG_DB_PATH"] = str(path)

    from crew_platform.technical.db import get_engine, reset_engine
    from crew_platform.technical.models import Base

    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)

    from optimizer.api import app as app_module

    importlib.reload(app_module)
    yield TestClient(app_module.app)

    os.environ.pop("CREW_TECHLOG_DB_PATH", None)
    from crew_platform.technical.db import reset_engine as _reset

    _reset()


BASE = "/api/crew/technical"


def _make_aircraft(client, reg="A7-TEST"):
    resp = client.post(
        f"{BASE}/aircraft/",
        json={"registration": reg, "type": "B77W"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_defect(client, reg="A7-TEST", **fields):
    resp = client.post(
        f"{BASE}/aircraft/{reg}/techlog",
        json={"pilot_report": "T5 entry"},
    )
    assert resp.status_code == 201, resp.text
    entry_id = resp.json()["id"]
    body = {"description": "T5 defect", "chapter": "32",
            "system_component": "WHEEL BRAKES",
            "pilot_report": "Brake wear noticed on rollout", **fields}
    resp = client.post(
        f"{BASE}/techlog/{entry_id}/defects",
        json=body,
    )
    assert resp.status_code == 201, resp.text
    return entry_id, resp.json()


class TestStatusEndpoint:
    def test_unknown_aircraft_404(self, api_client):
        resp = api_client.get(f"{BASE}/aircraft/XX-NOPE/status")
        assert resp.status_code == 404

    def test_fresh_aircraft_serviceable(self, api_client):
        _make_aircraft(api_client)
        resp = api_client.get(f"{BASE}/aircraft/A7-TEST/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["registration"] == "A7-TEST"
        assert body["type"] == "B77W"
        assert body["current_technical_status"] == "SERVICEABLE"
        assert body["open_defects"] == 0
        assert body["open_defects_list"] == []

    def test_acceptance_walk_open_deferred_rectified(self, api_client):
        """Ticket acceptance: OPEN → OPEN_DEFECTS → DEFERRED →
        DISPATCHABLE_WITH_MEL → RECTIFIED → SERVICEABLE, with the stored
        aircraft column tracking the derivation automatically."""
        _make_aircraft(api_client)

        # 1. Create an OPEN defect.
        _, defect = _make_defect(api_client)
        resp = api_client.get(f"{BASE}/aircraft/A7-TEST/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["current_technical_status"] == "OPEN_DEFECTS"
        assert body["open_defects"] == 1
        item = body["open_defects_list"][0]
        assert item["id"] == defect["id"]
        assert item["ata"] == "32"
        assert item["system_component"] == "WHEEL BRAKES"
        assert item["status"] == "OPEN"
        assert item["pilot_report"] is not None

        # Stored column updated without any client recompute call.
        ac = api_client.get(f"{BASE}/aircraft/A7-TEST").json()
        assert ac["current_technical_status"] == "OPEN_DEFECTS"

        # 2. Transition to DEFERRED.
        resp = api_client.post(
            f"{BASE}/defects/{defect['id']}/status",
            json={"status": "DEFERRED"},
        )
        assert resp.status_code == 200, resp.text
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "DISPATCHABLE_WITH_MEL"
        assert body["open_defects"] == 1
        assert body["open_defects_list"][0]["status"] == "DEFERRED"
        ac = api_client.get(f"{BASE}/aircraft/A7-TEST").json()
        assert ac["current_technical_status"] == "DISPATCHABLE_WITH_MEL"

        # 3. Rectify via the T4 maintenance path.
        resp = api_client.post(
            f"{BASE}/aircraft/A7-TEST/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Brakes replaced",
                "performed_by": "Tech One",
                "defect_id": defect["id"],
            },
        )
        assert resp.status_code == 201, resp.text
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "SERVICEABLE"
        assert body["open_defects"] == 0
        assert body["open_defects_list"] == []
        ac = api_client.get(f"{BASE}/aircraft/A7-TEST").json()
        assert ac["current_technical_status"] == "SERVICEABLE"

    def test_under_review_status(self, api_client):
        _make_aircraft(api_client)
        _, defect = _make_defect(api_client)
        resp = api_client.post(
            f"{BASE}/defects/{defect['id']}/status",
            json={"status": "UNDER_REVIEW"},
        )
        assert resp.status_code == 200, resp.text
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "UNDER_REVIEW"

    def test_open_beats_deferred_via_api(self, api_client):
        _make_aircraft(api_client)
        _, d1 = _make_defect(api_client)
        _, d2 = _make_defect(api_client)
        assert api_client.post(
            f"{BASE}/defects/{d1['id']}/status", json={"status": "DEFERRED"}
        ).status_code == 200
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "OPEN_DEFECTS"
        assert body["open_defects"] == 2
        assert api_client.post(
            f"{BASE}/defects/{d2['id']}/status", json={"status": "DEFERRED"}
        ).status_code == 200
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "DISPATCHABLE_WITH_MEL"

    def test_rectified_defect_disappears_from_status_list(self, api_client):
        _make_aircraft(api_client)
        _, d1 = _make_defect(api_client)
        _, d2 = _make_defect(api_client)
        resp = api_client.post(
            f"{BASE}/aircraft/A7-TEST/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Fix d1",
                "performed_by": "Tech One",
                "defect_id": d1["id"],
            },
        )
        assert resp.status_code == 201, resp.text
        body = api_client.get(f"{BASE}/aircraft/A7-TEST/status").json()
        assert body["current_technical_status"] == "OPEN_DEFECTS"
        assert body["open_defects"] == 1
        assert body["open_defects_list"][0]["id"] == d2["id"]
