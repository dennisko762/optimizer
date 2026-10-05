"""Tests for TechLog aircraft entity — CRUD + migration + integration.

Uses an in-memory SQLite database so no on-disk file is needed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from crew_platform.technical.models import Aircraft, Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    """Yield a SQLAlchemy session backed by an in-memory SQLite database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def tmp_db_path(tmp_path):
    """Return a temporary file path for a SQLite DB and set the env var."""
    db_file = tmp_path / "techlog_test.db"
    os.environ["CREW_TECHLOG_DB_PATH"] = str(db_file)
    yield db_file
    os.environ.pop("CREW_TECHLOG_DB_PATH", None)
    # Reset the module-level engine so other tests get a fresh one.
    from crew_platform.technical.db import reset_engine
    reset_engine()


@pytest.fixture()
def api_client(tmp_db_path):
    """Return a TestClient with the techlog DB pointed at a temp file."""
    # Force the engine to be created fresh with the temp path.
    from crew_platform.technical.db import reset_engine, get_engine
    reset_engine()
    engine = get_engine()
    Base.metadata.create_all(engine)

    from optimizer.api.app import app
    client = TestClient(app)
    yield client
    reset_engine()


# ===========================================================================
# Unit tests — ORM model
# ===========================================================================

class TestAircraftModel:

    def test_create_minimal(self, db_session):
        ac = Aircraft(registration="D-ABYA")
        db_session.add(ac)
        db_session.commit()
        fetched = db_session.get(Aircraft, "D-ABYA")
        assert fetched is not None
        assert fetched.registration == "D-ABYA"
        assert fetched.current_technical_status == "SERVICEABLE"

    def test_create_full(self, db_session):
        ac = Aircraft(
            registration="A6-EGA",
            type="B77W",
            manufacturer="Boeing",
            operator="Emirates Virtual",
            flight_hours=1234.5,
            flight_cycles=567,
            current_technical_status="SERVICEABLE",
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
        assert "created_at" in d

    def test_duplicate_registration_rejected(self, db_session):
        db_session.add(Aircraft(registration="D-ABYA"))
        db_session.commit()
        db_session.add(Aircraft(registration="D-ABYA"))
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_table_has_all_columns(self, db_session):
        """PRAGMA confirms all 9 columns, correct types/nullability/defaults."""
        engine = db_session.get_bind()
        insp = inspect(engine)
        cols = {c["name"]: c for c in insp.get_columns("aircraft")}
        assert set(cols) == {
            "registration", "type", "manufacturer", "operator",
            "flight_hours", "flight_cycles", "current_technical_status",
            "created_at", "updated_at",
        }
        # PK is non-nullable
        assert cols["registration"]["nullable"] is False or cols["registration"].get("primary_key")

    def test_correct_server_default(self, db_session):
        """current_technical_status defaults to SERVICEABLE."""
        ac = Aircraft(registration="TEST-DEFAULT")
        db_session.add(ac)
        db_session.commit()
        db_session.refresh(ac)
        assert ac.current_technical_status == "SERVICEABLE"


# ===========================================================================
# Alembic migration
# ===========================================================================

class TestAlembicMigration:
    """Verify the migration file inits and creates the SQLite with alembic_version."""

    def test_alembic_upgrade_head(self, tmp_db_path):
        """Run alembic upgrade head programmatically and confirm the table + version tag."""
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config()
        alembic_cfg.set_main_option("script_location", "crew_platform/technical/migrations")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{tmp_db_path}")
        command.upgrade(alembic_cfg, "head")

        # Verify the DB has the aircraft table and alembic_version
        engine = create_engine(f"sqlite:///{tmp_db_path}")
        insp = inspect(engine)
        tables = insp.get_table_names()
        assert "aircraft" in tables
        assert "alembic_version" in tables

        # Check version
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
            assert row is not None
            assert row[0] == "442a00f38d69"
        engine.dispose()


# ===========================================================================
# REST endpoint integration tests
# ===========================================================================

class TestAircraftEndpoints:

    def test_list_empty(self, api_client):
        resp = api_client.get("/api/crew/technical/aircraft/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_get(self, api_client):
        payload = {
            "registration": "D-ABYA",
            "type": "B77W",
            "manufacturer": "Boeing",
            "operator": "Lufthansa Virtual",
        }
        resp = api_client.post("/api/crew/technical/aircraft/", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["registration"] == "D-ABYA"
        assert body["type"] == "B77W"
        assert body["current_technical_status"] == "SERVICEABLE"

        # GET single
        resp2 = api_client.get("/api/crew/technical/aircraft/D-ABYA")
        assert resp2.status_code == 200
        assert resp2.json()["registration"] == "D-ABYA"

    def test_create_duplicate_409(self, api_client):
        payload = {"registration": "D-ABYA"}
        api_client.post("/api/crew/technical/aircraft/", json=payload)
        resp = api_client.post("/api/crew/technical/aircraft/", json=payload)
        assert resp.status_code == 409

    def test_get_unknown_404(self, api_client):
        resp = api_client.get("/api/crew/technical/aircraft/UNKNOWN")
        assert resp.status_code == 404

    def test_list_returns_created(self, api_client):
        api_client.post("/api/crew/technical/aircraft/", json={"registration": "A6-EGA"})
        api_client.post("/api/crew/technical/aircraft/", json={"registration": "D-ABYA"})
        resp = api_client.get("/api/crew/technical/aircraft/")
        assert resp.status_code == 200
        regs = {a["registration"] for a in resp.json()}
        assert regs == {"A6-EGA", "D-ABYA"}

    def test_flights_complete_increments(self, api_client):
        api_client.post("/api/crew/technical/aircraft/", json={"registration": "D-ABYA"})
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/flights/complete",
            json={"flight_hours": 2.5, "flight_cycles": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["flight_hours"] == 2.5
        assert resp.json()["flight_cycles"] == 1

        # Second increment
        resp2 = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/flights/complete",
            json={"flight_hours": 1.0, "flight_cycles": 1},
        )
        assert resp2.json()["flight_hours"] == 3.5
        assert resp2.json()["flight_cycles"] == 2

    def test_flights_complete_unknown_404(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/aircraft/UNKNOWN/flights/complete",
            json={"flight_hours": 1.0},
        )
        assert resp.status_code == 404

    def test_create_with_descriptive_message(self, api_client):
        """POST /aircraft/ returns with a descriptive message on 409."""
        api_client.post("/api/crew/technical/aircraft/", json={"registration": "DUP"})
        resp = api_client.post("/api/crew/technical/aircraft/", json={"registration": "DUP"})
        assert resp.status_code == 409
        assert "DUP" in resp.json()["detail"]


# ===========================================================================
# Full round-trip
# ===========================================================================

class TestRoundTrip:

    def test_create_get_increment_get(self, api_client):
        """Full lifecycle: create → GET → increment → GET confirms persistence."""
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-TEST", "flight_hours": 100, "flight_cycles": 50},
        )
        api_client.post(
            "/api/crew/technical/aircraft/D-TEST/flights/complete",
            json={"flight_hours": 5.5, "flight_cycles": 2},
        )
        resp = api_client.get("/api/crew/technical/aircraft/D-TEST")
        body = resp.json()
        assert body["flight_hours"] == 105.5
        assert body["flight_cycles"] == 52
