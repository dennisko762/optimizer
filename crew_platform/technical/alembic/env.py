"""Alembic environment for the TechLog SQLite database.

The database URL is resolved at migration time from the environment via
``crew_platform.technical.database`` — consistent with how the application
resolves it. Nothing in this file (or alembic.ini) contains a connection
string, credential, or token.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the project root importable so ``crew_platform`` resolves when Alembic
# is run from anywhere (prepend_sys_path=. in alembic.ini covers the usual
# repo-root invocation; this keeps the module self-sufficient).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from crew_platform.technical import database as techlog_database  # noqa: E402
from crew_platform.technical import models  # noqa: E402

# Alembic Config object (values from alembic.ini).
config = context.config

# Set the URL from the environment-based resolver. No plaintext connection
# details exist here; the resolved URL is a local file path only.
config.set_main_option("sqlalchemy.url", techlog_database.build_database_url())

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support.
target_metadata = models.Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL to stdout)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with a live SQLite connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
