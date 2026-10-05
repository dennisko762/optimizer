"""SQLAlchemy models for the TechLog domain.

The single, stable import point for TechLog ORM classes: Alembic's
``env.py`` imports this module, so every model mapped onto :data:`Base`
here is discoverable by ``alembic revision --autogenerate``.

Domain models added in this ticket:

- :class:`Aircraft` — the persistent aircraft record, keyed by
  registration. Technical state belongs to the aircraft (never to a
  flight): hours/cycles accumulate on this row as flights are completed.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase

# Placeholder technical status used until real status derivation arrives
# (later ticket). Free-form string for now — no enum constraint in the DB.
SERVICEABLE = "SERVICEABLE"

# Consistent naming for all TechLog tables.
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for all TechLog domain models."""

    metadata = MetaData(naming_convention=convention)


class Aircraft(Base):
    """A persistent aircraft registered in the TechLog system.

    The registration (tail number) is the primary identity: it is unique
    and is what the rest of the platform keys aircraft technical state by.
    Hours and cycles accumulate on this row as completed flights are
    recorded; ``current_technical_status`` is a placeholder derived state
    until real status logic lands in a later ticket.
    """

    __tablename__ = "aircraft"

    registration = Column(
        String(20),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        autoincrement=False,
    )
    type = Column(String(64), nullable=False)
    manufacturer = Column(String(64), nullable=False)
    operator = Column(String(128), nullable=True)
    flight_hours = Column(
        Float, nullable=False, default=0, server_default="0"
    )
    flight_cycles = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    current_technical_status = Column(
        String(32),
        nullable=False,
        default=SERVICEABLE,
        server_default=SERVICEABLE,
    )
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
