"""Tests for TechLog maintenance actions — model + migration + API.

Covers:
- MaintenanceAction ORM model (creation, to_dict, deterministic id).
- Alembic migration (upgrade head creates maintenance_action table).
- POST /aircraft/{reg}/maintenance — general, rectification, 404, 409, 400.
- GET  /aircraft/{reg}/maintenance — list, newest-first.
- History integrity: after rectifying a defect, both GET techlog and
  GET maintenance show the record.
- Persistence across a client/engine restart.
"""

from __future__ import annotations

import importlib
import os
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from crew_platform.technical.models import (
    Aircraft,
    Base,
    Defect,
    MaintenanceAction,
    MAINTENANCE_ACTION_TYPES,
    TechLogEntry,
)


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
    path = tmp_path / "techlog_maintenance_test.db"
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


def _seed_defect(api_client, reg="A7-TEST"):
    """Create aircraft + techlog entry + defect. Returns (entry_id, defect_id)."""
    api_client.post(
        "/api/crew/technical/aircraft/",
        json={"registration": reg, "type": "B77W"},
    )
    entry_resp = api_client.post(
        f"/api/crew/technical/aircraft/{reg}/techlog",
        json={"pilot_report": "Seed entry"},
    )
    entry_id = entry_resp.json()["id"]
    defect_resp = api_client.post(
        f"/api/crew/technical/techlog/{entry_id}/defects",
        json={"description": "Defect to rectify"},
    )
    return entry_id, defect_resp.json()["id"]


# ===========================================================================
# ORM model tests
# ===========================================================================


class TestMaintenanceActionModel:
    def test_create_minimal(self, db_session):
        action = MaintenanceAction(
            id="MA-D-ABYA-0001-abcdef",
            aircraft_registration="D-ABYA",
            action_type="GENERAL",
            description="Oil top-up",
            performed_by="Tech One",
        )
        db_session.add(action)
        db_session.commit()
        fetched = db_session.get(
            MaintenanceAction, "MA-D-ABYA-0001-abcdef"
        )
        assert fetched is not None
        assert fetched.aircraft_registration == "D-ABYA"
        assert fetched.action_type == "GENERAL"

    def test_create_with_defect_fk(self, db_session):
        ac = Aircraft(registration="D-ABYA")
        db_session.add(ac)
        entry = TechLogEntry(aircraft_registration="D-ABYA")
        db_session.add(entry)
        db_session.flush()
        defect = Defect(
            techlog_entry_id=entry.id,
            aircraft_registration="D-ABYA",
            description="d",
        )
        db_session.add(defect)
        db_session.flush()
        action = MaintenanceAction(
            id="MA-D-ABYA-0002-abcdef",
            aircraft_registration="D-ABYA",
            defect_id=defect.id,
            action_type="RECTIFICATION",
            description="Rectified leak",
            performed_by="Tech One",
        )
        db_session.add(action)
        db_session.commit()
        assert action.defect_id == defect.id
        assert defect.maintenance_actions[0].id == action.id

    def test_to_dict(self, db_session):
        when = datetime(2026, 10, 5, 12, 0, 0)
        action = MaintenanceAction(
            id="MA-X-0001-abcdef",
            aircraft_registration="D-ABYA",
            action_type="COMPONENT_SWAP",
            description="Swapped FMC",
            performed_by="Tech Two",
            performed_at=when,
        )
        db_session.add(action)
        db_session.commit()
        db_session.refresh(action)
        d = action.to_dict()
        assert d["id"] == "MA-X-0001-abcdef"
        assert d["aircraft_registration"] == "D-ABYA"
        assert d["defect_id"] is None
        assert d["action_type"] == "COMPONENT_SWAP"
        assert d["description"] == "Swapped FMC"
        assert d["performed_by"] == "Tech Two"
        # SQLite stores naive datetimes, so compare the date value.
        assert d["performed_at"] == when.isoformat()

    def test_action_type_constants(self):
        assert set(MAINTENANCE_ACTION_TYPES) == {
            "RECTIFICATION",
            "INSPECTION",
            "COMPONENT_SWAP",
            "GENERAL",
        }

    def test_table_has_all_columns(self, db_session):
        insp = inspect(db_session.bind)
        cols = {c["name"] for c in insp.get_columns("maintenance_action")}
        assert cols >= {
            "id",
            "aircraft_registration",
            "defect_id",
            "action_type",
            "description",
            "performed_by",
            "performed_at",
            "created_at",
        }


# ===========================================================================
# Alembic migration test
# ===========================================================================


class TestAlembicMigration:
    def test_alembic_upgrade_head_includes_maintenance_action(self, tmp_path):
        from alembic import command
        from alembic.config import Config

        db_path = tmp_path / "alembic_maintenance_test.db"
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
        assert "maintenance_action" in tables
        cols = {c["name"] for c in insp.get_columns("maintenance_action")}
        assert "defect_id" in cols
        engine.dispose()
        os.environ.pop("CREW_TECHLOG_DB_PATH", None)


# ===========================================================================
# REST endpoint tests — create maintenance action
# ===========================================================================


class TestCreateMaintenanceAction:
    def test_create_general_action(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "GENERAL",
                "description": "Daily walk-around inspection",
                "performed_by": "Tech One",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["aircraft_registration"] == "D-ABYA"
        assert body["action_type"] == "GENERAL"
        assert body["defect_id"] is None
        assert body["performed_by"] == "Tech One"
        assert body["performed_at"] is not None
        assert body["id"].startswith("MA-D-ABYA-")

    def test_create_action_aircraft_not_found(self, api_client):
        resp = api_client.post(
            "/api/crew/technical/aircraft/MISSING/maintenance",
            json={
                "action_type": "GENERAL",
                "description": "x",
                "performed_by": "Tech",
            },
        )
        assert resp.status_code == 404

    def test_create_action_invalid_type_400(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "DISASSEMBLE",
                "description": "x",
                "performed_by": "Tech",
            },
        )
        assert resp.status_code == 400

    def test_create_action_with_unknown_defect_404(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Rectify",
                "performed_by": "Tech",
                "defect_id": 99999,
            },
        )
        assert resp.status_code == 404

    def test_create_action_with_foreign_defect_404(self, api_client):
        """A defect from another aircraft cannot be linked (404)."""
        entry_id, defect_id = _seed_defect(api_client, reg="A7-ONE")
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "A7-TWO"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/A7-TWO/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Rectify someone else's defect",
                "performed_by": "Tech",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 404


# ===========================================================================
# REST endpoint tests — rectification
# ===========================================================================


class TestRectification:
    def test_rectification_closes_defect_and_writes_techlog(self, api_client):
        """POST maintenance with defect_id → defect RECTIFIED + techlog entry."""
        entry_id, defect_id = _seed_defect(api_client, reg="D-ABYA")
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Leak rectified per AMM 28-31-00",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["defect_id"] == defect_id
        assert body["action_type"] == "RECTIFICATION"

        # Defect is RECTIFIED with closure fields set.
        defect = api_client.get(
            f"/api/crew/technical/defects/{defect_id}"
        ).json()
        assert defect["status"] == "RECTIFIED"
        assert defect["closed"] is True
        assert defect["closure_timestamp"] is not None

        # Techlog entry carries the maintenance action text.
        entry = api_client.get(
            f"/api/crew/technical/techlog/{entry_id}"
        ).json()
        assert "Leak rectified per AMM 28-31-00" in (
            entry["maintenance_action"] or ""
        )

    def test_rectification_non_rectification_type_allows_terminal(self, api_client):
        """INSPECTION on an already-CLOSED defect is allowed (409 rule is
        specific to RECTIFICATION)."""
        _, defect_id = _seed_defect(api_client, reg="D-ABYA")
        api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "CLOSED"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "INSPECTION",
                "description": "Borescope inspection",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 201

    def test_rectify_already_rectified_409(self, api_client):
        _, defect_id = _seed_defect(api_client, reg="D-ABYA")
        ok = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "First fix",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert ok.status_code == 201
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Second fix attempt",
                "performed_by": "Tech Two",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 409

    def test_rectify_closed_defect_409(self, api_client):
        _, defect_id = _seed_defect(api_client, reg="D-ABYA")
        api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "CLOSED"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Too late",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 409

    def test_rectify_under_review_defect(self, api_client):
        """OPEN → UNDER_REVIEW, then rectification is allowed."""
        _, defect_id = _seed_defect(api_client, reg="D-ABYA")
        api_client.post(
            f"/api/crew/technical/defects/{defect_id}/status",
            json={"status": "UNDER_REVIEW"},
        )
        resp = api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "Fixed during review",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert resp.status_code == 201
        defect = api_client.get(
            f"/api/crew/technical/defects/{defect_id}"
        ).json()
        assert defect["status"] == "RECTIFIED"


# ===========================================================================
# REST endpoint tests — list maintenance actions
# ===========================================================================


class TestListMaintenanceActions:
    def test_list_empty(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        resp = api_client.get(
            "/api/crew/technical/aircraft/D-ABYA/maintenance"
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_newest_first(self, api_client):
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-ABYA"},
        )
        api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "GENERAL",
                "description": "Old action",
                "performed_by": "Tech",
                "performed_at": "2026-10-01T08:00:00Z",
            },
        )
        api_client.post(
            "/api/crew/technical/aircraft/D-ABYA/maintenance",
            json={
                "action_type": "INSPECTION",
                "description": "New action",
                "performed_by": "Tech",
                "performed_at": "2026-10-04T08:00:00Z",
            },
        )
        resp = api_client.get(
            "/api/crew/technical/aircraft/D-ABYA/maintenance"
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 2
        assert rows[0]["description"] == "New action"
        assert rows[1]["description"] == "Old action"

    def test_list_aircraft_not_found(self, api_client):
        resp = api_client.get(
            "/api/crew/technical/aircraft/MISSING/maintenance"
        )
        assert resp.status_code == 404

    def test_list_scoped_per_aircraft(self, api_client):
        for reg in ("A7-ONE", "A7-TWO"):
            api_client.post(
                "/api/crew/technical/aircraft/",
                json={"registration": reg},
            )
            api_client.post(
                f"/api/crew/technical/aircraft/{reg}/maintenance",
                json={
                    "action_type": "GENERAL",
                    "description": f"work on {reg}",
                    "performed_by": "Tech",
                },
            )
        rows = api_client.get(
            "/api/crew/technical/aircraft/A7-ONE/maintenance"
        ).json()
        assert len(rows) == 1
        assert rows[0]["aircraft_registration"] == "A7-ONE"


# ===========================================================================
# History integrity + persistence
# ===========================================================================


class TestHistoryIntegrity:
    def test_techlog_and_maintenance_both_show_record(self, api_client):
        """After rectifying defect QT-x, GET techlog AND GET maintenance
        both show the record (Phase 1 end-to-end acceptance item)."""
        api_client.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "A7-QT", "type": "B77W"},
        )
        entry_resp = api_client.post(
            "/api/crew/technical/aircraft/A7-QT/techlog",
            json={
                "pilot_report": "QT-x reported: fuel leak engine 2",
                "phase": "POST_FLIGHT",
            },
        )
        entry_id = entry_resp.json()["id"]
        defect_resp = api_client.post(
            f"/api/crew/technical/techlog/{entry_id}/defects",
            json={"description": "QT-x fuel leak engine 2"},
        )
        defect_id = defect_resp.json()["id"]

        action_resp = api_client.post(
            "/api/crew/technical/aircraft/A7-QT/maintenance",
            json={
                "action_type": "RECTIFICATION",
                "description": "QT-x rectified: replaced check valve",
                "performed_by": "Tech One",
                "defect_id": defect_id,
            },
        )
        assert action_resp.status_code == 201

        # GET techlog shows the rectification text.
        entries = api_client.get(
            "/api/crew/technical/aircraft/A7-QT/techlog"
        ).json()
        assert len(entries) == 1
        assert "replaced check valve" in entries[0]["maintenance_action"]

        # GET maintenance shows the rectification record.
        actions = api_client.get(
            "/api/crew/technical/aircraft/A7-QT/maintenance"
        ).json()
        assert len(actions) == 1
        assert actions[0]["defect_id"] == defect_id
        assert actions[0]["action_type"] == "RECTIFICATION"

        # The defect itself is retained in history as RECTIFIED.
        defect = api_client.get(
            f"/api/crew/technical/defects/{defect_id}"
        ).json()
        assert defect["status"] == "RECTIFIED"

    def test_persistence_across_restart(self, test_db):
        """Data survives an engine/app restart (fresh engine, same DB file)."""
        from crew_platform.technical.db import get_engine, reset_engine
        from crew_platform.technical.models import Base
        from optimizer.api import app as app_module

        reset_engine()
        Base.metadata.create_all(get_engine())
        importlib.reload(app_module)
        client1 = TestClient(app_module.app)

        client1.post(
            "/api/crew/technical/aircraft/",
            json={"registration": "D-PERSIST"},
        )
        create = client1.post(
            "/api/crew/technical/aircraft/D-PERSIST/maintenance",
            json={
                "action_type": "COMPONENT_SWAP",
                "description": "Swapped APU",
                "performed_by": "Tech One",
            },
        )
        assert create.status_code == 201
        action_id = create.json()["id"]

        # Simulate a process restart: discard engine + reload the app.
        reset_engine()
        importlib.reload(app_module)
        client2 = TestClient(app_module.app)

        rows = client2.get(
            "/api/crew/technical/aircraft/D-PERSIST/maintenance"
        ).json()
        assert len(rows) == 1
        assert rows[0]["id"] == action_id
        assert rows[0]["description"] == "Swapped APU"
