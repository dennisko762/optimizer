"""Tests for the /api/crew/technical/aircraft REST endpoints.

Coverage:
- POST create → 201 with correct data returned
- POST create duplicate → 409
- GET list → returns created aircraft
- GET one → returns data; unknown registration → 404
- POST flights/complete → increments persist across a second GET
- Data survives a process restart (engine dispose + re-create, GET still works)
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_platform.technical import database as db_mod
from crew_platform.technical.models import Base
from crew_platform.technical.routes import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    """Point the TechLog DB at a temp file and migrate (create_all)."""
    db_file = tmp_path / "test_techlog.db"
    os.environ["CREW_TECHLOG_DB_PATH"] = str(db_file)
    db_mod.reset_engine()
    engine = db_mod.get_engine()
    Base.metadata.create_all(engine)
    yield db_file
    db_mod.reset_engine()
    os.environ.pop("CREW_TECHLOG_DB_PATH", None)


@pytest.fixture()
def client(tmp_db):
    """TestClient wired to the technical router."""
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


SAMPLE_AIRCRAFT = {
    "registration": "D-ABCD",
    "type": "A320",
    "manufacturer": "Airbus",
    "operator": "Test Air",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_aircraft(client):
    """POST /aircraft returns 201 with correct payload."""
    resp = client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    assert resp.status_code == 201
    data = resp.json()
    assert data["registration"] == "D-ABCD"
    assert data["type"] == "A320"
    assert data["manufacturer"] == "Airbus"
    assert data["operator"] == "Test Air"
    assert data["flight_hours"] == 0
    assert data["flight_cycles"] == 0
    assert data["current_technical_status"] == "SERVICEABLE"


def test_create_duplicate_409(client):
    """Double-create with the same registration returns 409."""
    resp1 = client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    assert resp1.status_code == 201
    resp2 = client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    assert resp2.status_code == 409


def test_get_after_create(client):
    """GET /aircraft/{reg} returns data after creation."""
    client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    resp = client.get("/api/crew/technical/aircraft/D-ABCD")
    assert resp.status_code == 200
    data = resp.json()
    assert data["registration"] == "D-ABCD"
    assert data["manufacturer"] == "Airbus"


def test_get_unknown_404(client):
    """GET for an unknown registration returns 404."""
    resp = client.get("/api/crew/technical/aircraft/ZZ-NONE")
    assert resp.status_code == 404


def test_list_aircraft(client):
    """GET /aircraft returns all created aircraft."""
    client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    client.post(
        "/api/crew/technical/aircraft",
        json={**SAMPLE_AIRCRAFT, "registration": "G-WXYZ"},
    )
    resp = client.get("/api/crew/technical/aircraft")
    assert resp.status_code == 200
    regs = [a["registration"] for a in resp.json()]
    assert "D-ABCD" in regs
    assert "G-WXYZ" in regs


def test_flights_complete_increments_persist(client):
    """POST flights/complete increments persist across a subsequent GET."""
    client.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)

    # First increment
    resp = client.post(
        "/api/crew/technical/aircraft/D-ABCD/flights/complete",
        json={"flight_hours": 2.5, "flight_cycles": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["flight_hours"] == 2.5
    assert data["flight_cycles"] == 1

    # Second increment
    resp = client.post(
        "/api/crew/technical/aircraft/D-ABCD/flights/complete",
        json={"flight_hours": 1.5, "flight_cycles": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["flight_hours"] == 4.0
    assert data["flight_cycles"] == 2

    # GET confirms persisted
    resp = client.get("/api/crew/technical/aircraft/D-ABCD")
    assert resp.status_code == 200
    data = resp.json()
    assert data["flight_hours"] == 4.0
    assert data["flight_cycles"] == 2


def test_flights_complete_unknown_404(client):
    """POST flights/complete for unknown registration returns 404."""
    resp = client.post(
        "/api/crew/technical/aircraft/ZZ-NONE/flights/complete",
        json={"flight_hours": 1.0, "flight_cycles": 1},
    )
    assert resp.status_code == 404


def test_data_survives_restart(tmp_db):
    """Data survives an engine dispose + re-create (simulated process restart).

    This simulates what happens when uvicorn is killed and restarted:
    the in-memory engine is gone, a new one is created, and the SQLite
    file still holds the data.
    """
    # Phase 1: create aircraft via API
    app = FastAPI()
    app.include_router(router)
    c1 = TestClient(app)
    resp = c1.post("/api/crew/technical/aircraft", json=SAMPLE_AIRCRAFT)
    assert resp.status_code == 201

    resp = c1.post(
        "/api/crew/technical/aircraft/D-ABCD/flights/complete",
        json={"flight_hours": 10.0, "flight_cycles": 5},
    )
    assert resp.status_code == 200

    # Phase 2: "restart" — dispose engine, clear singletons
    db_mod.reset_engine()

    # Phase 3: new app, new client — data must still be there
    app2 = FastAPI()
    app2.include_router(router)
    c2 = TestClient(app2)

    resp = c2.get("/api/crew/technical/aircraft/D-ABCD")
    assert resp.status_code == 200
    data = resp.json()
    assert data["registration"] == "D-ABCD"
    assert data["flight_hours"] == 10.0
    assert data["flight_cycles"] == 5
