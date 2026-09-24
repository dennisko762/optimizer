"""FastAPI routes for the crew platform.

Provides endpoints for:
- Provider listing and selection
- vAMSYS PKCE authentication flow
- Pilot identity and flights
- eDesk check-in validation
- Session management
- Configuration readiness (without exposing credential values)
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from crew_platform.edesk import validate_checkin, create_checkin
from crew_platform.providers import (
    get_provider,
    list_providers,
    provider_theme_css_vars,
)
from crew_platform.sessions import SessionStore
from crew_platform.vamsys_pilot_auth import (
    PKCEFlowState,
    VamsysPilotAuth,
    VamsysPilotClient,
    VamsysPilotAuthError,
    PKCEError,
    PilotFlight,
    PilotIdentity,
)

router = APIRouter(prefix="/api/crew", tags=["crew-platform"])

# In-memory stores (bridge-scoped, not persistent)
_session_store = SessionStore()
_pending_flows: dict[str, PKCEFlowState] = {}  # state -> flow


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ProviderOut(BaseModel):
    id: str
    display_name: str
    short_code: str
    icao: str
    supports_vamsys: bool
    supports_simbrief: bool
    supports_live_optimizer: bool
    supports_remote_checkin: bool
    theme: dict[str, str]


class SessionOut(BaseModel):
    session_id: str
    provider_id: str
    authenticated: bool
    pilot_id: Optional[str] = None
    crew_id: Optional[str] = None
    callsign: Optional[str] = None
    rank: Optional[str] = None
    display_name: Optional[str] = None


class AuthStartOut(BaseModel):
    authorize_url: str
    session_id: str


class AuthCallbackIn(BaseModel):
    code: str
    state: str
    session_id: str


class CheckInRequest(BaseModel):
    session_id: str
    flight_id: str
    simbrief_departure: Optional[str] = None
    simbrief_arrival: Optional[str] = None
    simbrief_callsign: Optional[str] = None
    simbrief_aircraft: Optional[str] = None
    simbrief_flight_date: Optional[str] = None


class CheckInOut(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str]
    checked_in: bool
    remote_checkin_status: str
    remote_checkin_reason: str


class ConfigReadinessOut(BaseModel):
    vamsys_client_id_set: bool
    vamsys_redirect_uri_set: bool
    session_secret_set: bool
    ready: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/providers", response_model=list[ProviderOut])
async def get_providers():
    """List all available airline provider profiles."""
    return [
        ProviderOut(
            id=p.id,
            display_name=p.display_name,
            short_code=p.short_code,
            icao=p.icao,
            supports_vamsys=p.supports_vamsys,
            supports_simbrief=p.supports_simbrief,
            supports_live_optimizer=p.supports_live_optimizer,
            supports_remote_checkin=p.supports_remote_checkin,
            theme=provider_theme_css_vars(p),
        )
        for p in list_providers()
    ]


@router.get("/providers/{provider_id}/theme")
async def get_provider_theme(provider_id: str):
    """Get theme CSS variables for a provider."""
    try:
        provider = get_provider(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return provider_theme_css_vars(provider)


@router.get("/config/readiness", response_model=ConfigReadinessOut)
async def config_readiness():
    """Report configuration readiness without exposing credential values."""
    client_id = bool(os.environ.get("VAMSYS_PILOT_CLIENT_ID", ""))
    redirect_uri = bool(os.environ.get("VAMSYS_REDIRECT_URI", ""))
    session_secret = bool(os.environ.get("CREW_PLATFORM_SESSION_SECRET", ""))
    return ConfigReadinessOut(
        vamsys_client_id_set=client_id,
        vamsys_redirect_uri_set=redirect_uri,
        session_secret_set=session_secret,
        ready=client_id and redirect_uri and session_secret,
    )


@router.post("/auth/start", response_model=AuthStartOut)
async def auth_start(provider_id: str = Query(...)):
    """Start the vAMSYS PKCE authorization flow."""
    try:
        get_provider(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    client_id = os.environ.get("VAMSYS_PILOT_CLIENT_ID", "")
    redirect_uri = os.environ.get("VAMSYS_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="vAMSYS client_id or redirect_uri not configured",
        )

    auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)
    url, flow = auth.build_authorize_url()

    session = _session_store.create(provider_id)
    _pending_flows[flow.state] = flow

    return AuthStartOut(authorize_url=url, session_id=session.session_id)


@router.post("/auth/callback", response_model=SessionOut)
async def auth_callback(body: AuthCallbackIn):
    """Handle the OAuth callback and exchange the code for tokens."""
    session = _session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    flow = _pending_flows.pop(body.state, None)
    if not flow:
        raise HTTPException(status_code=400, detail="Unknown or expired auth state")

    client_id = os.environ.get("VAMSYS_PILOT_CLIENT_ID", "")
    redirect_uri = os.environ.get("VAMSYS_REDIRECT_URI", "")
    auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)

    try:
        auth.validate_callback(received_state=body.state, expected_flow=flow)
        tokens = await auth.exchange_code(code=body.code, flow=flow)
    except PKCEError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session.tokens = tokens

    # Fetch pilot identity
    try:
        client = VamsysPilotClient(tokens)
        pilot = await client.get_pilot_identity()
        session.pilot = pilot
    except Exception:
        # Authentication succeeded but profile fetch failed — session still valid
        pass

    return _session_out(session)


@router.get("/session/{session_id}", response_model=SessionOut)
async def get_session(session_id: str):
    """Get session info. Returns only opaque data, no tokens."""
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return _session_out(session)


@router.post("/session/{session_id}/logout")
async def logout(session_id: str):
    """Log out and destroy the session."""
    session = _session_store.get(session_id)
    if session and session.tokens:
        client_id = os.environ.get("VAMSYS_PILOT_CLIENT_ID", "")
        redirect_uri = os.environ.get("VAMSYS_REDIRECT_URI", "")
        auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)
        await auth.revoke_token(session.tokens.access_token)
    _session_store.remove(session_id)
    return {"ok": True}


@router.get("/session/{session_id}/flights")
async def get_flights(session_id: str):
    """List the pilot's flights from vAMSYS."""
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if not session.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        client = VamsysPilotClient(session.tokens)
        flights = await client.get_flights()
        return [
            {
                "flight_id": f.flight_id,
                "flight_number": f.flight_number,
                "departure_icao": f.departure_icao,
                "arrival_icao": f.arrival_icao,
                "aircraft_icao": f.aircraft_icao,
                "callsign": f.callsign,
                "scheduled_departure_utc": f.scheduled_departure_utc,
                "route": f.route,
                "status": f.status,
            }
            for f in flights
        ]
    except VamsysPilotAuthError:
        raise HTTPException(status_code=401, detail="Token expired or revoked")


@router.post("/checkin", response_model=CheckInOut)
async def checkin(body: CheckInRequest):
    """Validate and create a local check-in."""
    session = _session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if not session.is_authenticated or not session.pilot:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        provider = get_provider(session.provider_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # We need the flight from the session's last flight list
    # For MVP, construct a PilotFlight from the request
    flight = PilotFlight(
        flight_id=body.flight_id,
        flight_number=None,
        departure_icao=body.simbrief_departure,
        arrival_icao=body.simbrief_arrival,
        aircraft_icao=body.simbrief_aircraft,
        callsign=body.simbrief_callsign,
    )

    validation = validate_checkin(
        flight=flight,
        pilot=session.pilot,
        provider=provider,
        simbrief_departure=body.simbrief_departure,
        simbrief_arrival=body.simbrief_arrival,
        simbrief_callsign=body.simbrief_callsign,
        simbrief_aircraft=body.simbrief_aircraft,
        simbrief_flight_date=body.simbrief_flight_date,
    )

    checked_in = False
    if validation.valid:
        record = create_checkin(
            flight=flight,
            pilot=session.pilot,
            provider=provider,
            validation=validation,
        )
        session.checkin = record
        session.selected_flight_id = body.flight_id
        checked_in = True

    return CheckInOut(
        valid=validation.valid,
        errors=validation.errors,
        warnings=validation.warnings,
        checked_in=checked_in,
        remote_checkin_status="unsupported",
        remote_checkin_reason=(
            "Remote check-in is disabled. No explicitly documented "
            "vAMSYS Pilot API write endpoint exists for flight check-in."
        ),
    )


def _session_out(session) -> SessionOut:
    return SessionOut(
        session_id=session.session_id,
        provider_id=session.provider_id,
        authenticated=session.is_authenticated,
        pilot_id=session.pilot.pilot_id if session.pilot else None,
        crew_id=session.pilot.crew_id if session.pilot else None,
        callsign=session.pilot.callsign if session.pilot else None,
        rank=session.pilot.rank if session.pilot else None,
        display_name=session.pilot.display_name if session.pilot else None,
    )
