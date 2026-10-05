"""REST endpoints for the TechLog Aircraft entity.

Mounted as ``/api/crew/technical`` via the main crew-platform routes
module.  All endpoints use the shared process-wide Database wrapper
from :mod:`crew_platform.technical.database`.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from crew_platform.technical.database import get_database
from crew_platform.technical.models import Aircraft

router = APIRouter(prefix="/api/crew/technical", tags=["crew-technical"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AircraftCreateIn(BaseModel):
    """Request body for creating a new aircraft."""

    registration: str = Field(..., max_length=20)
    type: str = Field(..., max_length=64)
    manufacturer: str = Field(..., max_length=64)
    operator: str | None = Field(default=None, max_length=128)
    flight_hours: float = Field(default=0)
    flight_cycles: int = Field(default=0)
    current_technical_status: str = Field(default="SERVICEABLE", max_length=32)


class AircraftOut(BaseModel):
    """Response body for an aircraft."""

    registration: str
    type: str
    manufacturer: str
    operator: str | None
    flight_hours: float
    flight_cycles: int
    current_technical_status: str
    created_at: str | None = None
    updated_at: str | None = None


class FlightCompleteIn(BaseModel):
    """Incremental flight hours/cycles to add after a completed flight."""

    flight_hours: float = Field(default=0, ge=0)
    flight_cycles: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aircraft_out(ac: Aircraft) -> AircraftOut:
    return AircraftOut(
        registration=ac.registration,
        type=ac.type,
        manufacturer=ac.manufacturer,
        operator=ac.operator,
        flight_hours=ac.flight_hours,
        flight_cycles=ac.flight_cycles,
        current_technical_status=ac.current_technical_status,
        created_at=str(ac.created_at) if ac.created_at else None,
        updated_at=str(ac.updated_at) if ac.updated_at else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/aircraft", response_model=AircraftOut, status_code=201)
async def create_aircraft(body: AircraftCreateIn):
    """Create a new aircraft. Returns 409 if the registration already exists."""
    db = get_database()
    with db.session() as session:
        ac = Aircraft(
            registration=body.registration,
            type=body.type,
            manufacturer=body.manufacturer,
            operator=body.operator,
            flight_hours=body.flight_hours,
            flight_cycles=body.flight_cycles,
            current_technical_status=body.current_technical_status,
        )
        session.add(ac)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Aircraft with registration '{body.registration}' already exists",
            )
        session.refresh(ac)
        return _aircraft_out(ac)


@router.get("/aircraft", response_model=list[AircraftOut])
async def list_aircraft():
    """List all registered aircraft."""
    db = get_database()
    with db.session() as session:
        rows = session.query(Aircraft).order_by(Aircraft.registration).all()
        return [_aircraft_out(ac) for ac in rows]


@router.get("/aircraft/{registration}", response_model=AircraftOut)
async def get_aircraft(registration: str):
    """Get a single aircraft by registration. Returns 404 if unknown."""
    db = get_database()
    with db.session() as session:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(
                status_code=404,
                detail=f"Aircraft '{registration}' not found",
            )
        return _aircraft_out(ac)


@router.post(
    "/aircraft/{registration}/flights/complete",
    response_model=AircraftOut,
)
async def complete_flight(registration: str, body: FlightCompleteIn):
    """Increment flight hours and cycles for a completed flight.

    Minimal endpoint — no flight linkage yet. The hours and cycles on
    the request body are *added* to the existing totals.
    """
    db = get_database()
    with db.session() as session:
        ac = session.get(Aircraft, registration)
        if ac is None:
            raise HTTPException(
                status_code=404,
                detail=f"Aircraft '{registration}' not found",
            )
        ac.flight_hours += body.flight_hours
        ac.flight_cycles += body.flight_cycles
        session.commit()
        session.refresh(ac)
        return _aircraft_out(ac)
