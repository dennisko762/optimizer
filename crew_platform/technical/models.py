"""SQLAlchemy ORM model for the Aircraft entity.

The ``aircraft`` table stores the fleet registry — one row per unique
aircraft registration.  Columns are intentionally nullable where the crew
may not have data yet; ``registration`` is the natural primary key and is
``UNIQUE``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all TechLog models."""


class Aircraft(Base):
    """Persistent aircraft identity.

    ``registration`` is the ICAO registration (e.g. ``"D-ABYA"``), used as the
    primary key.  ``type`` is the ICAO type designator (e.g. ``"B77W"``).
    """

    __tablename__ = "aircraft"

    registration = Column(String, primary_key=True)
    type = Column(String, nullable=True)
    manufacturer = Column(String, nullable=True)
    operator = Column(String, nullable=True)
    flight_hours = Column(Float, nullable=True)
    flight_cycles = Column(Integer, nullable=True)
    current_technical_status = Column(
        String, default="SERVICEABLE", server_default="SERVICEABLE"
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    def to_dict(self) -> dict:
        """Serialise to a plain dict suitable for JSON responses."""
        return {
            "registration": self.registration,
            "type": self.type,
            "manufacturer": self.manufacturer,
            "operator": self.operator,
            "flight_hours": self.flight_hours,
            "flight_cycles": self.flight_cycles,
            "current_technical_status": self.current_technical_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
