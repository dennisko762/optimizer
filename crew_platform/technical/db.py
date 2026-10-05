"""Database session factory for the TechLog SQLite database.

The DB path is resolved from:
  1. ``CREW_TECHLOG_DB_PATH`` environment variable, or
  2. ``<EFB_DATA_DIR>/data/techlog.db``   (``EFB_DATA_DIR`` default = cwd).

On first import the engine is **not** created; call :func:`get_engine` or use
the :func:`get_session` async helper to create it lazily so that test code
can override the path before any SQL is emitted.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

_engine: Optional[Engine] = None
_SessionLocal: Optional[sessionmaker] = None  # type: ignore[type-arg]


def _resolve_db_path() -> Path:
    """Return the absolute path to the TechLog SQLite file."""
    explicit = os.environ.get("CREW_TECHLOG_DB_PATH")
    if explicit:
        return Path(explicit)
    data_root = Path(os.environ.get("EFB_DATA_DIR", "."))
    return data_root / "data" / "techlog.db"


def get_engine() -> Engine:
    """Return (and lazily create) the global SQLAlchemy engine."""
    global _engine
    if _engine is None:
        db_path = _resolve_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        # Enable WAL mode for better concurrent read performance.
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return _engine


def get_session() -> Session:
    """Return a new SQLAlchemy session bound to the global engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def reset_engine() -> None:
    """Tear down the engine — used by tests."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
