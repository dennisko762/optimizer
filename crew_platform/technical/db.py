"""Session factory for the TechLog SQLite database (compatibility shim).

Historically this module owned its *own* engine/session cache while
:mod:`crew_platform.technical.database` owned another one for Alembic. Two
caches over the same file meant the process could hold two engines pointed
at two different paths, and `reset_engine()` on one of them left the other
alive — which is why `tests/test_techlog_aircraft_api.py` leaked rows between
temp databases and the second `POST /aircraft` in a run answered 409.

There is now exactly one engine and one path contract, owned by
:mod:`crew_platform.technical.database` (the module Alembic already uses, so
migrations and the application finally resolve the same file). This module
keeps its public surface — `get_engine`, `get_session`, `reset_engine` — so
the routes and the existing tests that import it are unchanged.

Path resolution (see ``database.get_db_path``):
  1. ``CREW_TECHLOG_DB_PATH`` (or the legacy ``TECHLOG_DB_PATH``) when set,
  2. otherwise ``<EFB_DATA_DIR>/techlog.db``, defaulting EFB_DATA_DIR to
     ``<project root>/data`` — the same convention as the weather and
     flightplan stores.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from crew_platform.technical import database as _database

_SessionLocal: Optional[sessionmaker] = None  # type: ignore[type-arg]
_bound_engine: Optional[Engine] = None


def get_engine() -> Engine:
    """Return the single process-wide SQLAlchemy engine."""
    return _database.get_engine()


def get_session() -> Session:
    """Return a new session bound to the single process-wide engine."""
    global _SessionLocal, _bound_engine
    engine = get_engine()
    # Rebind whenever the engine was replaced (tests re-point the DB path).
    if _SessionLocal is None or _bound_engine is not engine:
        _SessionLocal = sessionmaker(bind=engine)
        _bound_engine = engine
    return _SessionLocal()


def reset_engine() -> None:
    """Dispose the engine and drop the cached session factory (tests)."""
    global _SessionLocal, _bound_engine
    _SessionLocal = None
    _bound_engine = None
    _database.reset_engine()
