"""SQLAlchemy ORM models for the TechLog domain.

Models
------
- **Aircraft** — fleet registry, one row per unique registration (PK).
- **TechLogEntry** — an airline-style tech-log page created once per
  flight/session.  Belongs to an Aircraft via ``aircraft_registration`` FK.
- **Defect** — an open defect reported against a TechLogEntry.  Tracks
  lifecycle status through OPEN → UNDER_REVIEW → DEFERRED / MEL_APPLIED →
  RECTIFIED → CLOSED.  Belongs to a TechLogEntry via ``techlog_entry_id`` FK.
- **MaintenanceAction** — a maintenance action performed on an aircraft
  (rectification, inspection, component swap, or general work).  Carries a
  deterministic, human-readable code as its primary key.  May rectify a
  specific ``Defect`` (``defect_id`` FK) or be general maintenance with no
  linked defect.
"""

from __future__ import annotations


from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all TechLog models."""


# ---------------------------------------------------------------------------
# Aircraft
# ---------------------------------------------------------------------------


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
    # A newly registered airframe starts at zero recorded hours/cycles —
    # never NULL. The alembic baseline (7c3e1a9d5b02) already declares both
    # columns NOT NULL with server_default '0'; the model now matches it so
    # ORM-created rows (and the API response) agree with the schema.
    flight_hours = Column(Float, nullable=False, default=0.0, server_default="0")
    flight_cycles = Column(Integer, nullable=False, default=0, server_default="0")
    current_technical_status = Column(
        String, default="SERVICEABLE", server_default="SERVICEABLE"
    )
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    # relationships
    techlog_entries = relationship(
        "TechLogEntry", back_populates="aircraft", cascade="all, delete-orphan"
    )

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


# ---------------------------------------------------------------------------
# TechLogEntry
# ---------------------------------------------------------------------------

# Valid status transitions for a TechLogEntry.
TECHLOG_STATUS_VALUES = ("OPEN", "UNDER_REVIEW", "CLOSED")


class TechLogEntry(Base):
    """One tech-log page — created per flight session.

    ``airline_style_log_line_id`` is the airline's own reference (optional,
    free text).  ``source`` indicates the originator (e.g. ``PILOT_REPORT``,
    ``MAINTENANCE``).
    """

    __tablename__ = "techlog_entry"

    id = Column(Integer, primary_key=True, autoincrement=True)
    aircraft_registration = Column(
        String,
        ForeignKey("aircraft.registration"),
        nullable=False,
        index=True,
    )
    airline_style_log_line_id = Column(String, nullable=True)
    phase = Column(String, nullable=True)  # e.g. "PRE_FLIGHT", "POST_FLIGHT"
    status = Column(String, nullable=False, default="OPEN", server_default="OPEN")
    pilot_report = Column(Text, nullable=True)
    chapter = Column(String, nullable=True)   # ATA chapter
    system_component = Column(String, nullable=True)
    source = Column(String, nullable=True, default="PILOT_REPORT")
    severity = Column(String, nullable=True)  # e.g. "MINOR", "MAJOR"
    closure_timestamp = Column(DateTime, nullable=True)
    mel_reference = Column(String, nullable=True)
    maintenance_action = Column(Text, nullable=True)
    free_text = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    # relationships
    aircraft = relationship("Aircraft", back_populates="techlog_entries")
    defects = relationship(
        "Defect", back_populates="techlog_entry", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "aircraft_registration": self.aircraft_registration,
            "airline_style_log_line_id": self.airline_style_log_line_id,
            "phase": self.phase,
            "status": self.status,
            "pilot_report": self.pilot_report,
            "chapter": self.chapter,
            "system_component": self.system_component,
            "source": self.source,
            "severity": self.severity,
            "closure_timestamp": (
                self.closure_timestamp.isoformat()
                if self.closure_timestamp
                else None
            ),
            "mel_reference": self.mel_reference,
            "maintenance_action": self.maintenance_action,
            "free_text": self.free_text,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Defect
# ---------------------------------------------------------------------------

# Valid status values and allowed transitions.
DEFECT_STATUS_VALUES = (
    "OPEN",
    "UNDER_REVIEW",
    "DEFERRED",
    "MEL_APPLIED",
    "RECTIFIED",
    "CLOSED",
)

DEFECT_STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "OPEN": ("UNDER_REVIEW", "DEFERRED", "MEL_APPLIED", "RECTIFIED", "CLOSED"),
    "UNDER_REVIEW": ("DEFERRED", "MEL_APPLIED", "RECTIFIED", "CLOSED"),
    "DEFERRED": ("MEL_APPLIED", "RECTIFIED", "CLOSED"),
    "MEL_APPLIED": ("RECTIFIED", "CLOSED"),
    "RECTIFIED": ("CLOSED",),
    "CLOSED": (),  # terminal — no further transitions
}


class Defect(Base):
    """A defect raised against a TechLogEntry.

    Tracks lifecycle from OPEN through to CLOSED.  ``closed`` is set to True
    (and ``closure_timestamp`` populated) when status reaches a terminal state.
    """

    __tablename__ = "defect"

    id = Column(Integer, primary_key=True, autoincrement=True)
    techlog_entry_id = Column(
        Integer,
        ForeignKey("techlog_entry.id"),
        nullable=False,
        index=True,
    )
    aircraft_registration = Column(
        String,
        ForeignKey("aircraft.registration"),
        nullable=False,
        index=True,
    )
    description = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="OPEN", server_default="OPEN")
    severity = Column(String, nullable=True)
    chapter = Column(String, nullable=True)
    system_component = Column(String, nullable=True)
    pilot_report = Column(Text, nullable=True)
    source = Column(String, nullable=True, default="PILOT_REPORT")
    mel_reference = Column(String, nullable=True)
    maintenance_action = Column(Text, nullable=True)
    closed = Column(Integer, nullable=False, default=0, server_default="0")
    closure_timestamp = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, onupdate=func.now())

    # relationships
    techlog_entry = relationship("TechLogEntry", back_populates="defects")
    maintenance_actions = relationship(
        "MaintenanceAction",
        back_populates="defect",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "techlog_entry_id": self.techlog_entry_id,
            "aircraft_registration": self.aircraft_registration,
            "description": self.description,
            "status": self.status,
            "severity": self.severity,
            "chapter": self.chapter,
            "system_component": self.system_component,
            "pilot_report": self.pilot_report,
            "source": self.source,
            "mel_reference": self.mel_reference,
            "maintenance_action": self.maintenance_action,
            "closed": bool(self.closed),
            "closure_timestamp": (
                self.closure_timestamp.isoformat()
                if self.closure_timestamp
                else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# MaintenanceAction
# ---------------------------------------------------------------------------

# Valid maintenance action types.
MAINTENANCE_ACTION_TYPES = (
    "RECTIFICATION",
    "INSPECTION",
    "COMPONENT_SWAP",
    "GENERAL",
)

# Defect statuses considered terminal for rectification purposes: a defect
# that is already RECTIFIED or CLOSED cannot be rectified again.
DEFECT_TERMINAL_STATUSES = ("RECTIFIED", "CLOSED")


class MaintenanceAction(Base):
    """A maintenance action performed on an aircraft.

    ``id`` is a deterministic, human-readable code (e.g. ``MA-D-ABYA-0001``)
    used as the primary key.  ``defect_id`` links the action to the specific
    defect it rectifies; it is nullable because an action may be general
    maintenance not tied to any defect.  ``performed_by`` is a display name
    only — never a token or secret.
    """

    __tablename__ = "maintenance_action"

    id = Column(String, primary_key=True)
    aircraft_registration = Column(
        String,
        ForeignKey("aircraft.registration"),
        nullable=False,
        index=True,
    )
    defect_id = Column(
        Integer,
        ForeignKey("defect.id"),
        nullable=True,
        index=True,
    )
    action_type = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    performed_by = Column(String, nullable=False)
    performed_at = Column(DateTime, nullable=False, server_default=func.now())
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    # relationships
    defect = relationship("Defect", back_populates="maintenance_actions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "aircraft_registration": self.aircraft_registration,
            "defect_id": self.defect_id,
            "action_type": self.action_type,
            "description": self.description,
            "performed_by": self.performed_by,
            "performed_at": (
                self.performed_at.isoformat() if self.performed_at else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
