"""SQLAlchemy models for the TechLog domain.

Baseline (empty on purpose): the TechLog/Aircraft domain models are NOT
part of this ticket. This module exists so that Alembic autogenerate and
future migrations have a single, stable import point to discover models
from. When the domain ticket lands, ORM classes go here and are registered
on :data:`Base` (either directly or via explicit imports); ``alembic
revision --autogenerate`` against this module will pick them up.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Consistent naming for all TechLog tables.
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all TechLog domain models.

    No models are mapped yet — the baseline migration therefore creates an
    empty schema (only the Alembic bookkeeping table).
    """

    metadata = MetaData(naming_convention=convention)


# Domain models will be imported here in a later ticket so that
# ``Base.metadata`` reflects the full schema for autogenerate.
