"""Alembic env.py for TechLog migrations.

Reads the DB URL from CREW_TECHLOG_DB_PATH (or falls back to alembic.ini).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from crew_platform.technical.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    explicit = os.environ.get("CREW_TECHLOG_DB_PATH")
    if explicit:
        return f"sqlite:///{explicit}"
    return config.get_main_option("sqlalchemy.url", "sqlite:///data/techlog.db")


def run_migrations_offline() -> None:
    context.configure(url=_get_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_get_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
