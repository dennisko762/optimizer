"""SQLite + SQLAlchemy persistence for the crew platform TechLog domain.

Design rules (mirrors the rest of the EFB backend):
- Connection details come from environment variables ONLY — never hardcoded.
  The database is local, unauthenticated SQLite: there are no credentials in
  the connection string by design, and none may ever be added.
- The database file lives in the project data path, which matches the repo
  convention that runtime data sits at the project root (see .gitignore:
  ``db.sqlite3``). The file itself is gitignored and is never committed.
- Schema is owned by Alembic. Application code must not create or alter
  tables directly; run ``alembic upgrade head`` (script dir:
  ``crew_platform/technical/alembic``) to apply migrations.

Environment variables:
- ``TECHLOG_DB_PATH`` — full path to the SQLite file. Wins when set.
- ``EFB_DATA_DIR``    — directory in which the SQLite file is created
                        (default: project root, i.e. where ``.env`` lives).
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

ENV_DB_PATH = "TECHLOG_DB_PATH"
ENV_DATA_DIR = "EFB_DATA_DIR"
DB_FILENAME = "techlog.db"
DB_ENGINE_URL_PREFIX = "sqlite:///"


def _project_root() -> Path:
    """Project root — two levels above this file (crew_platform/technical/).

    In a PyInstaller build (frozen), the source tree is unpacked under
    ``sys._MEIPASS``; the *project root* for data purposes stays the
    directory the executable was started from (cwd), because a file
    inside the temp extraction would vanish on exit.
    """
    import sys

    if getattr(sys, "frozen", False):
        return Path.cwd()
    return Path(__file__).resolve().parents[2]


def get_db_path() -> Path:
    """Resolve the SQLite file path from the environment.

    ``TECHLOG_DB_PATH`` (absolute or cwd-relative) wins; otherwise the file
    goes into ``EFB_DATA_DIR`` (default: project root) as ``techlog.db``.
    No credentials, no fixed secrets — a bare file location only.
    """
    raw = os.environ.get(ENV_DB_PATH, "").strip()
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path
    data_dir = Path(os.environ.get(ENV_DATA_DIR, "") or _project_root())
    return data_dir / DB_FILENAME


def build_database_url() -> str:
    """SQLAlchemy URL for the resolved SQLite path (file location only)."""
    return DB_ENGINE_URL_PREFIX + get_db_path().as_posix()


def _configure_sqlite(engine: Engine) -> None:
    """Per-connection pragmas for SQLite robustness."""

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            # Journal mode WAL: readers do not block writers and vice versa.
            cursor.execute("PRAGMA journal_mode=WAL")
            # Foreign keys are off by default in SQLite; the domain schema
            # will rely on them once tables land.
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def create_engine() -> Engine:
    """Create the SQLAlchemy engine for the TechLog SQLite database.

    The directory holding the database file is created on first use.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = _sa_create_engine(build_database_url())
    _configure_sqlite(engine)
    return engine


class Database:
    """Small session factory wrapper for TechLog storage access."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(
            bind=engine, expire_on_commit=False
        )

    def session(self) -> Session:
        """Open a new session. Close it when done (use as a context manager)."""
        return self._session_factory()

    def dispose(self) -> None:
        """Dispose of the underlying connection pool."""
        self.engine.dispose()


_engine: Engine | None = None
_database: Database | None = None


def get_engine() -> Engine:
    """Process-wide engine (created lazily on first use)."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_database() -> Database:
    """Process-wide Database wrapper (created lazily on first use)."""
    global _database
    if _database is None:
        _database = Database(get_engine())
    return _database


def reset_engine() -> None:
    """Dispose the cached engine (used by tests that re-point env vars)."""
    global _engine, _database
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _database = None
