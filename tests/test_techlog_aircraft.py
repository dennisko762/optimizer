"""Tests for TechLog aircraft entity — CRUD + migration + integration.

Uses an in-memory SQLite database so no on-disk file is needed.
"""

from __future__ import annotations

import importlib
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from crew_platform.technical.models import Aircraft, Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_session():
    """Return a fresh in-memory SQLAlchemy Session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def test_db(tmp_path):
    """Return a temporary file path for a SQLite DB and set the env var."""
    path = tmp_path / "techlog_test.db"
    os.environ["CREW_TECHLOG_DB_PATH"] = str(path)
    yield path
    os.environ.pop("CREW_TECHLOG_DB_PATH", None)
    from crew_platform.technical.db import reset_engine

    reset_engine()


@pytest.fixture()
def api_client(test_db):
    """Return a TestClient with the techlog DB pointed at a temp file."""
    from crew_platform.technical.db import get_engine, reset_engine
    from crew_platform.technical.models import Base

    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)

    from optimizer.api import app as app_module

    importlib.reload(app_module)
    yield TestClient(app_module.app)


# ===========================================================================
# ORM model tests
# ===========================================================================


class TestAircraftModel:
    def test_create_minimal(self, db_session):
        ac = Aircraft(registration="D-ABYA")
        db_session.add(ac)
        db_session.commit()
        fetched = db_session.get(Aircraft, "D-ABYA")
        assert fetched is not None
        assert fetched.registration == "D-ABYA"

    def test_create_full(self, db_session):
        ac = Aircraft(
            registration="A6-EGA",
            type="B77W",
            manufacturer="Boeing",
            flight_hours=1234.5,
            flight_cycles=567,
        )
        db_session.add(ac)
        db_session.commit()
        fetched = db_session.get(Aircraft, "A6-EGA")
        assert fetched.type == "B77W"
        assert fetched.manufacturer == "Boeing"
        assert fetched.flight_hours == 1234.5
        assert fetched.flight_cycles == 567

    def test_to_dict(self, db_session):
        ac = Aircraft(registration="D-ABYA", type="B77W")
        db_session.add(ac)
        db_session.commit()
        db_session.refresh(ac)
        d = ac.to_dict()
        assert d["registration"] == "D-ABYA"
        assert d["type"] == "B77W"
        assert d["current_technical_status"] == "SERVICEABLE"

    def test_duplicate_registration_rejected(self, db_session):
        db_session.add(Aircraft(registration="D-ABYA"))
        db_session.commit()
        db_session.add(Aircraft(registration="D-ABYA"))
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_table_has_all_columns(self, db_session):
        insp = inspect(db_session.bind)
        cols = {c["name"] for c in insp.get_columns("aircraft")}
        assert cols >= {
            "registration",
            "type",
            "manufacturer",
            "operator",
            "flight_hours",
            "flight_cycles",
            "current_technical_status",
            "created_at",
            "updated_at",
        }

    def test_server_default(self, db_session):
        ac = Aircraft(registration="D-DEFAULT")
        db_session.add(ac)
        db_session.commit()
        assert ac.current_technical_status == "SERVICEABLE"


# ===========================================================================
# Alembic migration test
# ===========================================================================


class TestAlembicMigration:
    def test_alembic_upgrade_head(self, tmp_path):
        """Run alembic upgrade head programmatically and confirm the tables exist."""
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "alembic_test.db"
        os.environ["CREW_TECHLOG_DB_PATH"] = str(db_path)
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
        command.upgrade(cfg, "head")
        engine = create_engine(f"sqlite:///{db_path}")
        insp = inspect(engine)
        tables = insp.get_table_names()
        assert "aircraft" in tables
        assert "techlog_entry" in tables
        assert "defect" in tables
        engine.dispose()
        os.environ.pop("CREW_TECHLOG_DB_PATH", None)


# ===========================================================================
# REST endpoint tests — Aircraft
# ===========================================================================


class TestAircraftEndpoints:
    def test_create_and_get(self, api_client):
        payload = {"registration": "D-ABYA", "type": "B77W"}
        resp = api_client.post("/api/crew/technical/aircraft/", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["registration"] == "D-ABYA"
        assert body["type"] == "B77W"
        assert body["current_technical_status"] == "SERVICEABLE"

        resp2 = api_client.get("/api/crew/technical/aircraft/D-ABYA")
        assert resp2.status_code == 200
        assert resp2.json()["registration"] == "D-ABYA"

    def test_list(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.get("/api/crew/technical/aircraft/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_duplicate_409(self, api_client):
        payload = {"registration": "D-ABYA"}
        api_client.post("/api/crew/technical/aircraft/", json=payload)
        resp = api_client.post("/api/crew/technical/aircraft/", json=payload)
        assert resp.status_code == 409

    def test_get_404(self, api_client):
        resp = api_client.get("/api/crew/technical/aircraft/UNKNOWN")
        assert resp.status_code == 404

    def test_flights_complete(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/flights/complete",
            json={"flight_hours": 1.0, "flight_cycles": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["flight_hours"] == 1.0
        assert resp.json()["flight_cycles"] == 1

    def test_flights_complete_unknown_404(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/aircraft/UNKNOWN/flights/complete",
            json={"flight_hours": 1.0},
        )
        assert resp.status_code == 404

    def test_flights_complete_descriptive_message(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/aircraft/UNKNOWN/flights/complete",
            json={"flight_hours": 1.0},
        )
        assert "not found" in resp.json()["detail"].lower()


# ===========================================================================
# REST endpoint tests — TechLog Entries
# ===========================================================================


class TestTechLogEntryEndpoints:
    def _seed_aircraft(self, api_client, reg="A7-TEST"):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": reg, "type": "B77W"},
        )

    def test_create_techlog_entry(self, api_client):
        self._seed_aircraft(api_client)
        resp = api_client.post(
            "/api/crew/technical/aircraft/A7-TEST/techlog",
            json={
                "pilot_report": "Fuel leak observed on engine 2",
                "phase": "POST_FLIGHT",
                "chapter": "28",
                "severity": "MAJOR",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["aircraft_registration"] == "A7-TEST"
        assert body["pilot_report"] == "Fuel leak observed on engine 2"
        assert body["status"] == "OPEN"
        assert body["id"] is not None

    def test_create_techlog_entry_aircraft_not_found(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/aircraft/MISSING/techlog",
            json={"pilot_report": "test"},
        )
        assert resp.status_code == 404

    def test_list_techlog_entries(self, api_client):
        self._seed_aircraft(api_client)
        api_client.post(
            "/api/crew/technical/aircraft/A7-TEST/techlog",
            json={"pilot_report": "Entry 1"},
        )
        api_client.post(
            "/api/crew/technical/aircraft/A7-TEST/techlog",
            json={"pilot_report": "Entry 2"},
        )
        resp = api_client.get("/api/crew/technical/aircraft/A7-TEST/techlog")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_techlog_entries_aircraft_not_found(self, api_client):
        resp = api_client.get("/api/crew/technical/aircraft/MISSING/techlog")
        assert resp.status_code == 404

    def test_get_single_techlog_entry(self, api_client):
        self._seed_aircraft(api_client)
        create_resp = api_client.post(
            "/api/crew/technical/aircraft/A7-TEST/techlog",
            json={"pilot_report": "Test entry"},
        )
        entry_id = create_resp.json()["id"]
        resp = api_client.get(f"/api/crew/technical/techlog/{entry_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == entry_id

    def test_get_techlog_entry_not_found(self, api_client):
        resp = api_client.get("/api/crew/technical/techlog/99999")
        assert resp.status_code == 404


# ===========================================================================
# REST endpoint tests — Defects
# ===========================================================================


class TestDefectEndpoints:
    def _seed(self, api_client, reg="A7-TEST"):
        """Create an aircraft and a techlog entry, return entry_id."""
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": reg, "type": "B77W"},
        )
        resp = api_client.post(
            f"/api/crew/technical/aircraft/{reg}/techlog",
            json={"pilot_report": "Seed entry"},
        )
        return resp.json()["id"]

    def test_create_defect(self, api_client):
        entry_id = self._seed(api_client)
        resp = api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={
                "description": "Hydraulic pressure low",
                "severity": "MAJOR",
                "chapter": "29",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["description"] == "Hydraulic pressure low"
        assert body["status"] == "OPEN"
        assert body["closed"] is False
        assert body["aircraft_registration"] == "A7-TEST"

    def test_create_defect_entry_not_found(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/techlog/99999/defects",
            json={"description": "test"},
        )
        assert resp.status_code == 404

    def test_list_defects(self, api_client):
        entry_id = self._seed(api_client)
        api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={"description": "Defect 1"},
        )
        api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={"description": "Defect 2"},
        )
        resp = api_client.get(
            f"/api/crew/technical/techlog/{entry_id}/defects"
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_get_single_defect(self, api_client):
        entry_id = self._seed(api_client)
        create_resp = api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={"description": "Test defect"},
        )
        defect_id = create_resp.json()["id"]
        resp = api_client.get(f"/api/crew/technical/defects/{defect_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == defect_id

    def test_get_defect_not_found(self, api_client):
        resp = api_client.get("/api/crew/technical/defects/99999")
        assert resp.status_code == 404


# ===========================================================================
# Defect status lifecycle tests
# ===========================================================================


class TestDefectStatusLifecycle:
    def _seed_defect(self, api_client, reg="A7-TEST"):
        """Create aircraft + entry + defect, return defect_id."""
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": reg, "type": "B77W"},
        )
        entry_resp = api_client.post(
            f"/api/crew/technical/aircraft/{reg}/techlog",
            json={"pilot_report": "Seed"},
        )
        entry_id = entry_resp.json()["id"]
        defect_resp = api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={"description": "Test defect"},
        )
        return defect_resp.json()["id"]

    def test_open_to_under_review(self, api_client):
        defect_id = self._seed_defect(api_client)
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "UNDER_REVIEW"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "UNDER_REVIEW"
        assert resp.json()["closed"] is False

    def test_open_to_deferred(self, api_client):
        defect_id = self._seed_defect(api_client)
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "DEFERRED"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "DEFERRED"

    def test_open_to_rectified_closes(self, api_client):
        defect_id = self._seed_defect(api_client)
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "RECTIFIED"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "RECTIFIED"
        assert body["closed"] is True
        assert body["closure_timestamp"] is not None

    def test_open_to_closed(self, api_client):
        defect_id = self._seed_defect(api_client)
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "CLOSED"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "CLOSED"
        assert resp.json()["closed"] is True

    def test_full_lifecycle(self, api_client):
        """OPEN → UNDER_REVIEW → DEFERRED → MEL_APPLIED → RECTIFIED → CLOSED."""
        defect_id = self._seed_defect(api_client)
        for status in [
            "UNDER_REVIEW",
            "DEFERRED",
            "MEL_APPLIED",
            "RECTIFIED",
            "CLOSED",
        ]:
            resp = api_client.post(
                f"/api/crew/technical/defects/{defect_id}/status",
                json={"status": status},
            )
            assert resp.status_code == 200, (
                f"Failed transitioning to {status}: {resp.json()}"
            )
            assert resp.json()["status"] == status

        # Final state
        final = api_client.get(
            f"/api/crew/technical/defects/{defect_id}"
        ).json()
        assert final["closed"] is True
        assert final["closure_timestamp"] is not None

    def test_closed_is_terminal(self, api_client):
        """Once CLOSED, no further transitions allowed."""
        defect_id = self._seed_defect(api_client)
        api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "CLOSED"},
        )
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "OPEN"},
        )
        assert resp.status_code == 409

    def test_invalid_status_400(self, api_client):
        defect_id = self._seed_defect(api_client)
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "BOGUS"},
        )
        assert resp.status_code == 400

    def test_invalid_transition_409(self, api_client):
        """RECTIFIED → DEFERRED is not allowed (backward)."""
        defect_id = self._seed_defect(api_client)
        api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "RECTIFIED"},
        )
        resp = api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "DEFERRED"},
        )
        assert resp.status_code == 409

    def test_status_not_found(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/defects/99999/status",
            json={"status": "CLOSED"},
        )
        assert resp.status_code == 404


# ===========================================================================
# Round-trip integration test
# ===========================================================================


class TestRoundTrip:
    def test_full_lifecycle_create_to_close(self, api_client):
        """Full lifecycle: create aircraft → techlog → defect → close."""
        # 1. Create aircraft
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-TEST", "type": "A320"},
        )

        # 2. Record a flight
        api_client.post(
            "/api/crew/technical/aircraft/D-TEST/flights/complete",
            json={"flight_hours": 2.5, "flight_cycles": 1},
        )

        # 3. Create techlog entry
        entry_resp = api_client.post(
            "/api/crew/technical/aircraft/D-TEST/techlog",
            json={
                "pilot_report": "Engine vibration noticed on approach",
                "phase": "POST_FLIGHT",
                "chapter": "72",
                "severity": "MINOR",
            },
        )
        assert entry_resp.status_code == 201
        entry_id = entry_resp.json()["id"]

        # 4. Create defect
        defect_resp = api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={
                "description": "Engine 1 vibration above normal limits",
                "severity": "MINOR",
                "chapter": "72",
            },
        )
        assert defect_resp.status_code == 201
        defect_id = defect_resp.json()["id"]

        # 5. Progress through lifecycle
        for status in ["UNDER_REVIEW", "DEFERRED", "RECTIFIED"]:
            resp = api_client.post(
                f"/api/crew/technical/defects/{defect_id}/status",
                json={"status": status},
            )
            assert resp.status_code == 200

        # 6. Verify final state
        defect = api_client.get(
            f"/api/crew/technical/defects/{defect_id}"
        ).json()
        assert defect["status"] == "RECTIFIED"
        assert defect["closed"] is True

        aircraft = api_client.get(
            "/api/crew/technical/aircraft/D-TEST"
        ).json()
        assert aircraft["flight_hours"] == 2.5
        assert aircraft["flight_cycles"] == 1

        entries = api_client.get(
            "/api/crew/technical/aircraft/D-TEST/techlog"
        ).json()
        assert len(entries) == 1
        assert entries[0]["id"] == entry_id
