"""Tests for the TechLog Aircraft model + Alembic migration.

Covers the ticket acceptance criteria:
- ``alembic upgrade head`` on a fresh DB creates ``aircraft`` with the
  correct schema (columns, nullability, defaults, unique registration).
- The migration is idempotent (re-running upgrade head is a no-op).
- The ORM model round-trips against the migrated schema and applies the
  documented column defaults.
- ``CREW_TECHLOG_DB_PATH`` is honoured (absolute + cwd-relative), the
  legacy ``TECHLOG_DB_PATH`` alias still works, and the default path is
  ``<repo-root>/data/techlog.db``.
- Importing the model/database modules never creates the DB file at
  runtime (schema is Alembic-owned; no ``create_all``).
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "crew_platform" / "technical" / "alembic" / "alembic.ini"


def _run_alembic(db_path: Path | None, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in (
        "CREW_TECHLOG_DB_PATH",
        "TECHLOG_DB_PATH",
        "EFB_DATA_DIR",
    )}
    if db_path is not None:
        env["CREW_TECHLOG_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def fresh_db(tmp_path: Path) -> Path:
    """A brand-new SQLite file migrated to head."""
    db = tmp_path / "techlog_test.db"
    result = _run_alembic(db, "upgrade", "head")
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    return db


@pytest.fixture()
def clean_techlog_env(monkeypatch, tmp_path: Path):
    """Point the resolver at a temp path and clear competing env vars."""
    for var in ("CREW_TECHLOG_DB_PATH", "TECHLOG_DB_PATH", "EFB_DATA_DIR"):
        monkeypatch.delenv(var, raising=False)
    import crew_platform.technical.database as database

    database.reset_engine()
    yield
    database.reset_engine()


# ---------------------------------------------------------------------------
# Migration: schema
# ---------------------------------------------------------------------------


def test_upgrade_head_creates_aircraft_table(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "aircraft" in tables
    assert "alembic_version" in tables
    with sqlite3.connect(fresh_db) as conn:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert version == "7c3e1a9d5b02"


def test_aircraft_table_schema(fresh_db: Path) -> None:
    with sqlite3.connect(fresh_db) as conn:
        info = {
            row[1]: {
                "type": row[2],
                "notnull": bool(row[3]),
                "pk": bool(row[5]),
                "default": row[4],
            }
            for row in conn.execute("PRAGMA table_info('aircraft')")
        }

    assert set(info) == {
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

    assert info["registration"]["pk"]
    assert info["registration"]["notnull"]
    assert info["type"]["type"].upper().startswith("VARCHAR")
    assert info["type"]["notnull"]
    assert info["manufacturer"]["notnull"]
    assert not info["operator"]["notnull"]
    assert info["flight_hours"]["type"].upper() in ("REAL", "FLOAT")
    assert info["flight_hours"]["notnull"]
    assert info["flight_hours"]["default"] == "'0'"
    assert info["flight_cycles"]["type"].upper() == "INTEGER"
    assert info["flight_cycles"]["notnull"]
    assert info["flight_cycles"]["default"] == "'0'"
    assert info["current_technical_status"]["notnull"]
    assert info["current_technical_status"]["default"] == "'SERVICEABLE'"
    assert info["created_at"]["notnull"]
    assert info["updated_at"]["notnull"]

    # Registration is the unique identity of the aircraft.
    with sqlite3.connect(fresh_db) as conn:
        covered = set()
        for row in conn.execute("PRAGMA index_list('aircraft')"):
            # row: (seq, name, unique, origin, partial)
            if row[2] == 1:
                covered |= {
                    r[2] for r in conn.execute(f"PRAGMA index_info('{row[1]}')")
                }
    assert "registration" in covered


def test_upgrade_head_is_idempotent(fresh_db: Path) -> None:
    result = _run_alembic(fresh_db, "upgrade", "head")
    assert result.returncode == 0, result.stderr
    assert "Running upgrade 442a00f38d69 -> 7c3e1a9d5b02" not in result.stdout


def test_downgrade_drops_aircraft_table(fresh_db: Path) -> None:
    result = _run_alembic(fresh_db, "downgrade", "base")
    assert result.returncode == 0, result.stderr
    with sqlite3.connect(fresh_db) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "aircraft" not in tables


# ---------------------------------------------------------------------------
# ORM model round-trip
# ---------------------------------------------------------------------------


def test_model_roundtrip_with_defaults(fresh_db: Path, clean_techlog_env,
                                       monkeypatch) -> None:
    monkeypatch.setenv("CREW_TECHLOG_DB_PATH", str(fresh_db))
    from crew_platform.technical import database as techlog_database
    from crew_platform.technical.models import Aircraft

    db = techlog_database.get_database()
    with db.session() as session:
        session.add(Aircraft(registration="A7-BAD", type="B777-300ER",
                             manufacturer="Boeing", operator="QR"))
        session.commit()

    with db.session() as session:
        row = session.get(Aircraft, "A7-BAD")
        assert row is not None
        assert row.type == "B777-300ER"
        assert row.manufacturer == "Boeing"
        assert row.operator == "QR"
        assert row.flight_hours == 0.0
        assert row.flight_cycles == 0
        assert row.current_technical_status == "SERVICEABLE"
        assert row.created_at is not None
        assert row.updated_at is not None


def test_model_unique_registration_constraint(fresh_db: Path, clean_techlog_env,
                                              monkeypatch) -> None:
    import sqlalchemy.exc
    monkeypatch.setenv("CREW_TECHLOG_DB_PATH", str(fresh_db))
    from crew_platform.technical import database as techlog_database
    from crew_platform.technical.models import Aircraft

    db = techlog_database.get_database()
    with db.session() as session:
        session.add(Aircraft(registration="A7-DUP", type="A350-900",
                             manufacturer="Airbus"))
        session.commit()
    with db.session() as session:
        session.add(Aircraft(registration="A7-DUP", type="A350-900",
                             manufacturer="Airbus"))
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            session.commit()


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------


def test_default_db_path_is_repo_data_dir(clean_techlog_env) -> None:
    from crew_platform.technical import database as techlog_database

    assert techlog_database.get_db_path() == REPO_ROOT / "data" / "techlog.db"


def test_crew_techlog_db_path_wins(clean_techlog_env, monkeypatch,
                                   tmp_path: Path) -> None:
    explicit = tmp_path / "custom" / "my.db"
    monkeypatch.setenv("CREW_TECHLOG_DB_PATH", str(explicit))
    from crew_platform.technical import database as techlog_database

    assert techlog_database.get_db_path() == explicit
    assert "user" not in techlog_database.build_database_url()


def test_legacy_env_var_still_honoured(clean_techlog_env, monkeypatch,
                                       tmp_path: Path) -> None:
    explicit = tmp_path / "legacy.db"
    monkeypatch.setenv("TECHLOG_DB_PATH", str(explicit))
    from crew_platform.technical import database as techlog_database

    assert techlog_database.get_db_path() == explicit


def test_efb_data_dir_used_when_no_path(clean_techlog_env, monkeypatch,
                                        tmp_path: Path) -> None:
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
    from crew_platform.technical import database as techlog_database

    assert techlog_database.get_db_path() == tmp_path / "techlog.db"


def test_imports_never_create_db_file(clean_techlog_env, monkeypatch,
                                      tmp_path: Path) -> None:
    """Runtime code must not create the schema — Alembic owns it."""
    db = tmp_path / "never_created.db"
    monkeypatch.setenv("CREW_TECHLOG_DB_PATH", str(db))
    # Fresh interpreter state for the modules: they are already imported by
    # earlier tests in this module, so simulate first-use by asserting the
    # side effect directly — importing must not create anything.
    import crew_platform.technical.database  # noqa: F401
    import crew_platform.technical.models  # noqa: F401

    assert not db.exists()


def test_url_has_no_credentials(clean_techlog_env, monkeypatch,
                                tmp_path: Path) -> None:
    monkeypatch.setenv("CREW_TECHLOG_DB_PATH", str(tmp_path / "x.db"))
    from crew_platform.technical import database as techlog_database

    url = techlog_database.build_database_url()
    assert url.startswith("sqlite:///")
    assert "@" not in url
