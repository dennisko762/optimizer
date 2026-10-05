"""REST endpoints for the TechLog aircraft registry.

Mounted under ``/api/crew/technical`` by the main app router.

Endpoints
---------
GET  /aircraft/                  — list all aircraft
GET  /aircraft/{registration}    — get one (404 if missing)
POST /aircraft/                  — create (409 on duplicate registration)
POST /aircraft/{registration}/flights/complete
                                 — record a completed flight (increments
                                   flight_hours / flight_cycles on request)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from crew_platform.technical.db import get_session
from crew_platform.technical.models import Aircraft

router = APIRouter(prefix="/api/crew/technical", tags=["crew-technical"])


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
    flight_hours: Optional[float] = None   # hours to ADD
    flight_cycles: Optional[int] = None    # cycles to ADD


# ---- routes ---------------------------------------------------------------

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
            current_technical_status=body.current_technical_status or "SERVICEABLE",
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
