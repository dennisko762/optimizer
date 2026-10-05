"""REST endpoints for the TechLog domain.

Mounted under ``/api/crew/technical`` by the main app router.

Endpoints — Aircraft
---------------------
GET  /aircraft/                           — list all aircraft
GET  /aircraft/{registration}             — get one (404 if missing)
POST /aircraft/                           — create (409 on duplicate)
POST /aircraft/{reg}/flights/complete     — increment hours/cycles

Endpoints — TechLog Entries
----------------------------
POST /aircraft/{reg}/techlog              — create a techlog entry
GET  /aircraft/{reg}/techlog              — list entries for an aircraft
GET  /techlog/{entry_id}                  — get a single entry

Endpoints — Defects
--------------------
POST /techlog/{entry_id}/defects          — create a defect on an entry
GET  /techlog/{entry_id}/defects          — list defects for an entry
GET  /defects/{defect_id}                 — get a single defect
POST /defects/{defect_id}/status          — transition defect status

Endpoints — Maintenance Actions
--------------------------------
POST /aircraft/{reg}/maintenance          — create a maintenance action
GET  /aircraft/{reg}/maintenance          — list actions (newest first)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from crew_platform.technical.db import get_session
from crew_platform.technical.models import (
    Aircraft,
    Defect,
    DEFECT_STATUS_TRANSITIONS,
    DEFECT_STATUS_VALUES,
    DEFECT_TERMINAL_STATUSES,
    MaintenanceAction,
    MAINTENANCE_ACTION_TYPES,
    TechLogEntry,
    TECHLOG_STATUS_VALUES,
)

router = APIRouter(prefix="/api/crew/technical", tags=["crew-technical"])


def _apply_defect_transition(defect: Defect, new_status: str) -> None:
    """Validate and apply a defect status transition in-place.

    Raises 409 if the transition is not allowed from the defect's current
    status.  Terminal statuses (RECTIFIED, CLOSED) set ``closed`` and
    ``closure_timestamp``.  Does not commit — the caller owns the session.
    """
    allowed = DEFECT_STATUS_TRANSITIONS.get(defect.status, ())
    if new_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot transition from '{defect.status}' to "
                f"'{new_status}'. Allowed: {', '.join(allowed) or 'none (terminal state)'}"
            ),
        )
    defect.status = new_status
    if new_status in ("RECTIFIED", "CLOSED"):
        defect.closed = 1
        defect.closure_timestamp = datetime.now(timezone.utc)


# ---- request / response schemas -------------------------------------------


class AircraftIn(BaseModel):
    registration: str
    type: Optional[str] = None
    manufacturer: Optional[str] = None
    operator: Optional[str] = None
    flight_hours: Optional[float] = None
    flight_cycles: Optional[int] = None
    current_technical_status: Optional[str] = "SERVICEABLE"


class FlightCompleteIn(BaseModel):
    """Payload for recording a completed flight."""

    flight_hours: Optional[float] = None  # hours to ADD
    flight_cycles: Optional[int] = None  # cycles to ADD


class TechLogEntryIn(BaseModel):
    """Payload for creating a new tech-log entry."""

    airline_style_log_line_id: Optional[str] = None
    phase: Optional[str] = None
    pilot_report: Optional[str] = None
    chapter: Optional[str] = None
    system_component: Optional[str] = None
    source: Optional[str] = "PILOT_REPORT"
    severity: Optional[str] = None
    mel_reference: Optional[str] = None
    maintenance_action: Optional[str] = None
    free_text: Optional[str] = None


class DefectIn(BaseModel):
    """Payload for creating a new defect."""

    description: str
    severity: Optional[str] = None
    chapter: Optional[str] = None
    system_component: Optional[str] = None
    pilot_report: Optional[str] = None
    source: Optional[str] = "PILOT_REPORT"
    mel_reference: Optional[str] = None
    maintenance_action: Optional[str] = None


class DefectStatusIn(BaseModel):
    """Payload for transitioning a defect's status."""

    status: str


class MaintenanceActionIn(BaseModel):
    """Payload for recording a maintenance action on an aircraft.

    ``defect_id`` is optional: when supplied the action is linked to that
    defect, and for ``action_type=RECTIFICATION`` the defect is transitioned
    to RECTIFIED.  ``performed_by`` is a display name only.
    """

    action_type: str
    description: str
    performed_by: str
    defect_id: Optional[int] = None
    performed_at: Optional[datetime] = None


# ---- Aircraft routes -------------------------------------------------------


@router.get("/aircraft/")
def list_aircraft():
    """Return all registered aircraft."""
    session = get_session()
    try:
        rows = session.query(Aircraft).all()
        return [r.to_dict() for r in rows]
    finally:
        session.close()


@router.get("/aircraft/{registration}")
def get_aircraft(registration: str):
    """Return a single aircraft by registration (404 if not found)."""
    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        return ac.to_dict()
    finally:
        session.close()


@router.post("/aircraft/", status_code=201)
def create_aircraft(body: AircraftIn):
    """Create a new aircraft entry (409 if registration already exists)."""
    session = get_session()
    try:
        ac = Aircraft(
            registration=body.registration,
            type=body.type,
            manufacturer=body.manufacturer,
            operator=body.operator,
            flight_hours=body.flight_hours,
            flight_cycles=body.flight_cycles,
            current_technical_status=body.current_technical_status
            or "SERVICEABLE",
        )
        session.add(ac)
        session.commit()
        session.refresh(ac)
        return ac.to_dict()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Aircraft '{body.registration}' already exists",
        )
    finally:
        session.close()


@router.post("/aircraft/{registration}/flights/complete")
def record_completed_flight(registration: str, body: FlightCompleteIn):
    """Increment flight hours/cycles for an aircraft after a completed flight."""
    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        if body.flight_hours is not None:
            ac.flight_hours = (ac.flight_hours or 0) + body.flight_hours
        if body.flight_cycles is not None:
            ac.flight_cycles = (ac.flight_cycles or 0) + body.flight_cycles
        session.commit()
        session.refresh(ac)
        return ac.to_dict()
    finally:
        session.close()


# ---- TechLog Entry routes --------------------------------------------------


@router.post("/aircraft/{registration}/techlog", status_code=201)
def create_techlog_entry(registration: str, body: TechLogEntryIn):
    """Create a new tech-log entry for the given aircraft."""
    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        entry = TechLogEntry(
            aircraft_registration=registration,
            airline_style_log_line_id=body.airline_style_log_line_id,
            phase=body.phase,
            pilot_report=body.pilot_report,
            chapter=body.chapter,
            system_component=body.system_component,
            source=body.source,
            severity=body.severity,
            mel_reference=body.mel_reference,
            maintenance_action=body.maintenance_action,
            free_text=body.free_text,
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry.to_dict()
    finally:
        session.close()


@router.get("/aircraft/{registration}/techlog")
def list_techlog_entries(registration: str):
    """List all tech-log entries for an aircraft."""
    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        rows = (
            session.query(TechLogEntry)
            .filter(TechLogEntry.aircraft_registration == registration)
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        session.close()


@router.get("/techlog/{entry_id}")
def get_techlog_entry(entry_id: int):
    """Return a single tech-log entry by id."""
    session = get_session()
    try:
        entry = session.get(TechLogEntry, entry_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail="TechLog entry not found"
            )
        return entry.to_dict()
    finally:
        session.close()


# ---- Defect routes ---------------------------------------------------------


@router.post("/techlog/{entry_id}/defects", status_code=201)
def create_defect(entry_id: int, body: DefectIn):
    """Create a new defect against a tech-log entry."""
    session = get_session()
    try:
        entry = session.get(TechLogEntry, entry_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail="TechLog entry not found"
            )
        defect = Defect(
            techlog_entry_id=entry_id,
            aircraft_registration=entry.aircraft_registration,
            description=body.description,
            severity=body.severity,
            chapter=body.chapter,
            system_component=body.system_component,
            pilot_report=body.pilot_report,
            source=body.source,
            mel_reference=body.mel_reference,
            maintenance_action=body.maintenance_action,
        )
        session.add(defect)
        session.commit()
        session.refresh(defect)
        return defect.to_dict()
    finally:
        session.close()


@router.get("/techlog/{entry_id}/defects")
def list_defects(entry_id: int):
    """List all defects for a tech-log entry."""
    session = get_session()
    try:
        entry = session.get(TechLogEntry, entry_id)
        if entry is None:
            raise HTTPException(
                status_code=404, detail="TechLog entry not found"
            )
        rows = (
            session.query(Defect)
            .filter(Defect.techlog_entry_id == entry_id)
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        session.close()


@router.get("/defects/{defect_id}")
def get_defect(defect_id: int):
    """Return a single defect by id."""
    session = get_session()
    try:
        defect = session.get(Defect, defect_id)
        if defect is None:
            raise HTTPException(status_code=404, detail="Defect not found")
        return defect.to_dict()
    finally:
        session.close()


@router.post("/defects/{defect_id}/status")
def transition_defect_status(defect_id: int, body: DefectStatusIn):
    """Transition a defect to a new status.

    Validates against the allowed status transitions.  Terminal statuses
    (RECTIFIED, CLOSED) set ``closed=True`` and record ``closure_timestamp``.
    """
    new_status = body.status.upper()
    if new_status not in DEFECT_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.status}'. "
            f"Valid values: {', '.join(DEFECT_STATUS_VALUES)}",
        )

    session = get_session()
    try:
        defect = session.get(Defect, defect_id)
        if defect is None:
            raise HTTPException(status_code=404, detail="Defect not found")

        _apply_defect_transition(defect, new_status)

        session.commit()
        session.refresh(defect)
        return defect.to_dict()
    finally:
        session.close()


# ---- Maintenance Action routes ---------------------------------------------


def _deterministic_action_code(registration: str) -> str:
    """Build a deterministic, collision-safe maintenance action code.

    Format: ``MA-<REG>-<seq>-<short-uuid>``.  The sequence counts existing
    actions for this aircraft (so codes read in order), and the short-uuid
    suffix guards against two actions created for the same aircraft in
    rapid succession landing on the same number.
    """
    session = get_session()
    try:
        seq = (
            session.query(MaintenanceAction)
            .filter(MaintenanceAction.aircraft_registration == registration)
            .count()
        ) + 1
    finally:
        session.close()
    return f"MA-{registration}-{seq:04d}-{uuid.uuid4().hex[:6]}"


@router.post("/aircraft/{registration}/maintenance", status_code=201)
def create_maintenance_action(registration: str, body: MaintenanceActionIn):
    """Create a maintenance action for an aircraft.

    If ``defect_id`` is supplied the defect must belong to this aircraft
    (404 otherwise).  For ``action_type=RECTIFICATION`` the defect must be in
    a non-terminal state (409 if already RECTIFIED/CLOSED); the rectification
    transitions the defect to RECTIFIED and appends the action text to the
    linked tech-log entry.
    """
    action_type = (body.action_type or "").upper()
    if action_type not in MAINTENANCE_ACTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action_type '{body.action_type}'. "
            f"Valid values: {', '.join(MAINTENANCE_ACTION_TYPES)}",
        )

    performed_at = (
        body.performed_at
        if body.performed_at is not None
        else datetime.now(timezone.utc)
    )

    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")

        # Resolve the linked defect, if any.
        defect = None
        entry = None
        if body.defect_id is not None:
            defect = session.get(Defect, body.defect_id)
            if defect is None:
                raise HTTPException(status_code=404, detail="Defect not found")
            if defect.aircraft_registration != registration:
                raise HTTPException(
                    status_code=404,
                    detail="Defect does not belong to this aircraft",
                )
            entry = session.get(TechLogEntry, defect.techlog_entry_id)

        if action_type == "RECTIFICATION" and defect is not None:
            if defect.status in DEFECT_TERMINAL_STATUSES:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Defect is already {defect.status}; it cannot be "
                        "rectified again."
                    ),
                )
            # Reuse T3 transition logic to move the defect to RECTIFIED.
            _apply_defect_transition(defect, "RECTIFIED")
            # Append the maintenance action text to the tech-log entry so the
            # aircraft history retains both the defect and the rectification.
            if entry is not None:
                existing = entry.maintenance_action
                entry.maintenance_action = (
                    f"{existing}\n{body.description}".strip()
                    if existing
                    else body.description
                )

        code = _deterministic_action_code(registration)
        action = MaintenanceAction(
            id=code,
            aircraft_registration=registration,
            defect_id=body.defect_id,
            action_type=action_type,
            description=body.description,
            performed_by=body.performed_by,
            performed_at=performed_at,
        )
        session.add(action)
        session.commit()
        session.refresh(action)
        return action.to_dict()
    finally:
        session.close()


@router.get("/aircraft/{registration}/maintenance")
def list_maintenance_actions(registration: str):
    """List all maintenance actions for an aircraft, newest first."""
    session = get_session()
    try:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(status_code=404, detail="Aircraft not found")
        rows = (
            session.query(MaintenanceAction)
            .filter(MaintenanceAction.aircraft_registration == registration)
            .order_by(MaintenanceAction.performed_at.desc(),
                      MaintenanceAction.id.desc())
            .all()
        )
        return [r.to_dict() for r in rows]
    finally:
        session.close()
