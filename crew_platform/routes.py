"""FastAPI routes for the crew platform.

Provides endpoints for:
- Provider listing and selection
- vAMSYS PKCE authentication flow
- Pilot identity and flights
- eDesk check-in validation
- Session management
- Configuration readiness (without exposing credential values)
- Operations notifications (weather-change events)
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from crew_platform.boarding import BoardingState, build_boarding_view_model
from crew_platform.edesk import validate_checkin, create_checkin
from crew_platform.providers import (
    get_provider,
    list_providers,
    provider_theme_css_vars,
)
from crew_platform.sessions import CrewSession, SessionStore
from crew_platform.vamsys_pilot_auth import (
    PKCEFlowState,
    VamsysPilotAuth,
    VamsysPilotClient,
    VamsysPilotAuthError,
    PKCEError,
    PilotFlight,
    PilotIdentity,
    PilotTokens,
)

from crew_platform.technical.routes import router as technical_router  # noqa: E402

router = APIRouter(prefix="/api/crew", tags=["crew-platform"])

# Browser-facing OAuth callback (no /api prefix — this is the URL the pilot
# registers at vAMSYS, e.g. https://<host>/crew/auth/callback).
callback_router = APIRouter(tags=["crew-platform"])

# In-memory stores (bridge-scoped, not persistent)
_session_store = SessionStore()
# state -> (flow, session_id) — the session id is recovered from the
# authorize redirect, which carries no session_id of its own.
_pending_flows: dict[str, tuple[PKCEFlowState, str]] = {}


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
    local: bool = False
    pilot_id: Optional[str] = None
    crew_id: Optional[str] = None
    callsign: Optional[str] = None
    rank: Optional[str] = None
    display_name: Optional[str] = None


class LocalSessionIn(BaseModel):
    provider_id: str
    display_name: Optional[str] = None
    pilot_id: Optional[str] = None


class AuthStartOut(BaseModel):
    authorize_url: str
    session_id: str
    redirect_uri: str  # the registered callback URL (for reference/tests)


class AuthCallbackIn(BaseModel):
    code: str
    state: str
    session_id: str


class CheckInRequest(BaseModel):
    session_id: str
    flight_id: str
    flight_number: Optional[str] = None
    simbrief_departure: Optional[str] = None
    simbrief_arrival: Optional[str] = None
    simbrief_callsign: Optional[str] = None
    simbrief_aircraft: Optional[str] = None
    simbrief_flight_date: Optional[str] = None


class DispatchRequest(BaseModel):
    route_id: Optional[int] = None


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
    vamsys_redirect_uri_https: bool
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
    """Report configuration readiness without exposing credential values.

    The v3 spec mandates an HTTPS redirect URI (except loopback), so a
    set-but-not-https redirect URI is reported as not ready.
    """
    client_id = bool(os.environ.get("VAMSYS_PILOT_CLIENT_ID", ""))
    redirect_uri = os.environ.get("VAMSYS_REDIRECT_URI", "")
    redirect_https = not redirect_uri or redirect_uri.startswith("https://")
    session_secret = bool(os.environ.get("CREW_PLATFORM_SESSION_SECRET", ""))
    return ConfigReadinessOut(
        vamsys_client_id_set=client_id,
        vamsys_redirect_uri_set=bool(redirect_uri),
        vamsys_redirect_uri_https=redirect_https,
        session_secret_set=session_secret,
        ready=client_id and bool(redirect_uri) and redirect_https and session_secret,
    )


@router.post("/auth/start", response_model=AuthStartOut)
async def auth_start(provider_id: str = Query(...)):
    """Start the vAMSYS PKCE authorization flow.

    Returns the authorize URL the pilot's browser must visit. The browser
    is redirected back to VAMSYS_REDIRECT_URI with ?code=&state=, where the
    GET /crew/auth/callback completes the exchange.
    """
    try:
        get_provider(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    client_id = os.environ.get("VAMSYS_PILOT_CLIENT_ID", "")
    redirect_uri = os.environ.get("VAMSYS_REDIRECT_URI", "")
    if not client_id or not redirect_uri:
        raise HTTPException(
            status_code=503,
            detail="vAMSYS client_id or redirect_uri not configured "
                   "(VAMSYS_PILOT_CLIENT_ID / VAMSYS_REDIRECT_URI)",
        )

    auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)
    url, flow = auth.build_authorize_url()

    session = _session_store.create(provider_id)
    _pending_flows[flow.state] = (flow, session.session_id)

    return AuthStartOut(
        authorize_url=url,
        session_id=session.session_id,
        redirect_uri=redirect_uri,
    )


async def _finish_oauth(session: CrewSession, code: str, flow: PKCEFlowState):
    """Exchange code for tokens, fetch identity, store on the session.

    Shared by the browser GET callback and the programmatic POST callback.
    Returns (session, error) where error is None on success.
    """
    client_id = _vamsys_client_id()
    redirect_uri = _vamsys_redirect_uri()
    auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)
    try:
        auth.validate_callback(received_state=flow.state, expected_flow=flow)
        tokens = await auth.exchange_code(code=code, flow=flow)
    except PKCEError as e:
        return session, str(e)

    session.tokens = tokens

    # Fetch pilot identity (GET /user in v3), auto-refreshing the token
    # on 401 per the spec's revoked-token handling.
    try:
        def _refresh():
            return _refresh_session_tokens(session, client_id, redirect_uri)

        client = VamsysPilotClient(tokens, refresh_fn=_refresh)
        pilot = await client.get_pilot_identity()
        session.pilot = pilot
        session.tokens = client.tokens  # may have been refreshed
    except Exception:
        # Authentication succeeded but profile fetch failed — session still valid
        pass

    return session, None


@router.post("/auth/callback", response_model=SessionOut)
async def auth_callback(body: AuthCallbackIn):
    """Programmatic (same-tab) OAuth callback: exchange code, return session.

    For the standard browser redirect flow use GET /crew/auth/callback.
    """
    session = _session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    stored = _pending_flows.pop(body.state, None)
    if not stored:
        raise HTTPException(status_code=400, detail="Unknown or expired auth state")
    flow, _session_id = stored

    session, err = await _finish_oauth(session, body.code, flow)
    if err:
        raise HTTPException(status_code=400, detail=err)
    return _session_out(session)


@callback_router.get("/crew/auth/callback")
async def browser_auth_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """Browser-facing OAuth redirect target (registered at vAMSYS).

    Exchanges the code server-side, then redirects the pilot back into the
    app at /?crew_session=<id>&provider=<id> so the SPA can adopt the session
    and re-apply the airline theme.
    """
    stored = _pending_flows.pop(state, None)
    if not stored:
        return _callback_error("Unknown or expired auth state. Please sign in again.")
    flow, session_id = stored

    session = _session_store.get(session_id)
    if not session:
        return _callback_error("Session no longer exists. Please sign in again.")

    session, err = await _finish_oauth(session, code, flow)
    if err:
        return _callback_error(err)

    return RedirectResponse(
        url=f"/?crew_session={session.session_id}&provider={session.provider_id}",
        status_code=302,
    )


def _callback_error(detail: str):
    """Minimal self-contained error page for a failed OAuth redirect."""
    safe = detail.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>vAMSYS sign-in</title>
<style>body{{font-family:system-ui,sans-serif;background:#0a0e14;color:#e8ecf0;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{max-width:420px;padding:2rem;border:1px solid #263041;border-radius:12px}}
h1{{font-size:1.1rem;color:#f87171;margin-top:0}} a{{color:#60a5fa}}</style>
</head><body><div class="card"><h1>Sign-in problem</h1>
<p>{safe}</p>
<p><a href="/">Return to crew app</a></p></div></body></html>"""
    return HTMLResponse(content=html, status_code=400)


async def _refresh_session_tokens(
    session: CrewSession, client_id: str, redirect_uri: str
) -> PilotTokens:
    """Refresh the session's tokens in place and return the new pair."""
    auth = VamsysPilotAuth(client_id=client_id, redirect_uri=redirect_uri)
    if not session.tokens or not session.tokens.refresh_token:
        raise VamsysPilotAuthError("No refresh token available")
    tokens = await auth.refresh_tokens(session.tokens.refresh_token)
    session.tokens = tokens
    return tokens


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
async def get_flights(session_id: str, status: str = Query(default="current")):
    """List the pilot's bookings from vAMSYS (GET /bookings, v3).

    Bookings carry airport IDs, not ICAOs — each booking's linked SimBrief
    OFP (GET /bookings/{id}/simbrief) is fetched to resolve ICAO + aircraft.
    """
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if session.local:
        raise HTTPException(
            status_code=400,
            detail="Local sessions have no vAMSYS flight list. "
                   "Use the check-in form to enter flight data manually.",
        )
    if not session.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    def _refresh():
        return _refresh_session_tokens(session, _vamsys_client_id(), _vamsys_redirect_uri())

    try:
        client = VamsysPilotClient(session.tokens, refresh_fn=_refresh)
        bookings = await client.get_bookings(status=status)
        # Resolve ICAOs from the linked OFP for each booking (bounded: the
        # current roster is small). A missing OFP (404) is fine — ICAOs stay
        # null and the OFP tile shows the linked-OFP state.
        resolved_by_id: dict[str, PilotFlight] = {}
        for b in bookings:
            ofp = await client.get_booking_simbrief(b.flight_id)
            if ofp is not None:
                resolved_by_id[b.flight_id] = _replace_flight_icaos(b, ofp)
        session.tokens = client.tokens  # persist any refresh
        return [
            _flight_dict(resolved_by_id.get(b.flight_id, b)) for b in bookings
        ]
    except VamsysPilotAuthError:
        raise HTTPException(status_code=401, detail="Token expired or revoked")


def _vamsys_client_id() -> str:
    return os.environ.get("VAMSYS_PILOT_CLIENT_ID", "")


def _vamsys_redirect_uri() -> str:
    return os.environ.get("VAMSYS_REDIRECT_URI", "")


def _flight_dict(f: PilotFlight) -> dict:
    return {
        "flight_id": f.flight_id,
        "flight_number": f.flight_number,
        "departure_icao": f.departure_icao,
        "arrival_icao": f.arrival_icao,
        "aircraft_icao": f.aircraft_icao,
        "callsign": f.callsign,
        "scheduled_departure_utc": f.scheduled_departure_utc,
        "route": f.route,
        "status": f.status,
        "route_id": f.route_id,
        "network": f.network,
        "passengers": f.passengers,
        "cargo": f.cargo,
        "cost_index": f.cost_index,
        "altitude": f.altitude,
        "has_ofp": f.has_ofp,
    }


@router.get("/session/{session_id}/flights/{flight_id}/ofp")
async def get_flight_ofp(session_id: str, flight_id: str):
    """Fetch the linked SimBrief OFP for a booking (Flight/OFP tile).

    Returns the OFP payload + pdf_url, or 404 with has_ofp=false when no
    OFP has been linked to the booking yet.
    """
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if not session.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    def _refresh():
        return _refresh_session_tokens(session, _vamsys_client_id(), _vamsys_redirect_uri())

    try:
        client = VamsysPilotClient(session.tokens, refresh_fn=_refresh)
        ofp = await client.get_booking_simbrief(flight_id)
        session.tokens = client.tokens
        if ofp is None:
            raise HTTPException(
                status_code=404,
                detail="No SimBrief OFP linked to this booking yet.",
            )
        return {
            "flight_id": flight_id,
            "has_ofp": True,
            "ofp_data": (ofp or {}).get("ofp_data"),
            "pdf_url": (ofp or {}).get("pdf_url"),
            "created_at": (ofp or {}).get("created_at"),
        }
    except VamsysPilotAuthError:
        raise HTTPException(status_code=401, detail="Token expired or revoked")


@router.post("/session/{session_id}/flights/{flight_id}/dispatch")
async def get_flight_dispatch(session_id: str, flight_id: str, body: DispatchRequest = DispatchRequest()):
    """Create a Phoenix dispatch session for a booking (remote check-in).

    Uses the documented v3 write path POST /dispatch-url, which returns a
    URL the pilot opens to complete the dispatch form. The endpoint takes a
    *route id*; if not supplied in the body, it is resolved from the booking
    (GET /bookings/{id}). Requires the flights:write scope on the registered
    client; without it vAMSYS returns an error which we surface as 403.
    """
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    if not session.is_authenticated:
        raise HTTPException(status_code=401, detail="Not authenticated")

    def _refresh():
        return _refresh_session_tokens(session, _vamsys_client_id(), _vamsys_redirect_uri())

    try:
        client = VamsysPilotClient(session.tokens, refresh_fn=_refresh)
        route_id = body.route_id
        if route_id is None:
            booking = await client.get_booking(flight_id, resolve_icao=False)
            route_id = booking.route_id
        if not route_id:
            raise HTTPException(
                status_code=422,
                detail="This booking has no route_id; dispatch-url cannot be created.",
            )
        url = await client.get_dispatch_url(int(route_id))
        session.tokens = client.tokens
        return {"flight_id": flight_id, "dispatch_url": url}
    except HTTPException:
        raise
    except PKCEError as e:
        msg = str(e)
        if "HTTP 403" in msg:
            raise HTTPException(
                status_code=403,
                detail="Client is missing the flights:write scope. "
                       "Re-register the vAMSYS Pilot client with that scope.",
            )
        raise HTTPException(status_code=502, detail=msg[:300])
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
        flight_number=body.flight_number or body.simbrief_callsign,
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
        remote_checkin_status=record.remote_checkin_status if checked_in else "dispatch_url",
        remote_checkin_reason=(
            "Remote check-in uses the documented vAMSYS Pilot API v3 write "
            "path (POST /dispatch-url, Phoenix dispatch). The pilot opens "
            "the returned URL to complete the dispatch form. Use the "
            "'Remote Check-In (Phoenix)' button in eDesk."
        ),
    )


# ---------------------------------------------------------------------------
# Notifications endpoints
# ---------------------------------------------------------------------------


@router.get("/notifications")
async def get_notifications(session_id: str = Query(...)):
    """Get the notification feed for a session (newest first)."""
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")

    from crew_platform.notifications import get_or_create_feed

    feed = get_or_create_feed(session_id)
    events = feed.get_events()
    return [
        {
            "id": e.id,
            "type": e.type,
            "icao": e.icao,
            "summary": e.summary,
            "provenance": e.provenance,
            "timestamp": e.timestamp,
            "observed": e.observed,
        }
        for e in events
    ]


@router.post("/notifications/clear")
async def clear_notifications(session_id: str = Query(...)):
    """Clear the notification feed for a session."""
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")

    from crew_platform.notifications import get_or_create_feed

    feed = get_or_create_feed(session_id)
    feed.clear()
    return {"ok": True}


def _session_out(session) -> SessionOut:
    return SessionOut(
        session_id=session.session_id,
        provider_id=session.provider_id,
        authenticated=session.is_authenticated,
        local=session.local,
        pilot_id=session.pilot.pilot_id if session.pilot else None,
        crew_id=session.pilot.crew_id if session.pilot else None,
        callsign=session.pilot.callsign if session.pilot else None,
        rank=session.pilot.rank if session.pilot else None,
        display_name=session.pilot.display_name if session.pilot else None,
    )


@router.post("/session/local", response_model=SessionOut)
async def create_local_session(body: LocalSessionIn):
    """Create an offline local session without vAMSYS OAuth.

    Used when no vAMSYS pilot client is configured: the crew works with
    manually entered / SimBrief flight data. Check-in is local-only;
    the flight list endpoint is unavailable.
    """
    try:
        get_provider(body.provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))

    session = _session_store.create(body.provider_id)
    session.local = True
    session.pilot = PilotIdentity(
        pilot_id=body.pilot_id or f"local-{body.provider_id}",
        display_name=body.display_name or "Local Pilot",
    )
    return _session_out(session)


# ---------------------------------------------------------------------------
# Boarding
# ---------------------------------------------------------------------------


class BoardingUpdateIn(BaseModel):
    session_id: str
    flight_id: str
    pax_ate: Optional[int] = None
    bags_loaded: Optional[int] = None
    pax_planned: Optional[int] = None
    bags_expected: Optional[int] = None
    contacts: Optional[list[dict]] = None
    groups: Optional[list[dict]] = None
    oew_kg: Optional[float] = None
    pax_kg_each: Optional[float] = None
    bag_kg_each: Optional[float] = None
    cargo_kg: Optional[float] = None
    fuel_kg: Optional[float] = None


@router.get("/boarding")
async def get_boarding(
    session_id: str = Query(...),
    flight_id: str = Query(...),
):
    """Get the boarding view model for a flight."""
    session = _session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")

    state = session.boarding if session.boarding and session.boarding.flight_id == flight_id else BoardingState(flight_id=flight_id)

    # Seed the header from the session's check-in record when it matches
    # the requested flight. OFP data (pax/bag/fuel) requires the live
    # SimBrief integration and is left to crew entry for now — see
    # crew_platform/boarding.py (no fabricated OFP values).
    flight_data = None
    if session.checkin and session.checkin.flight_id == flight_id:
        ci = session.checkin
        flight_data = {
            "flight_number": ci.flight_number or ci.callsign or "",
            "departure_icao": ci.departure_icao or "",
            "arrival_icao": ci.arrival_icao or "",
            "aircraft_icao": ci.aircraft_icao or "",
            "callsign": ci.callsign or "",
            "sibt": "",
            "sobt": "",
            "block_time": "",
        }
    ofp_data = None

    vm = build_boarding_view_model(flight_data, ofp_data, state)
    return vm


@router.post("/boarding/update")
async def update_boarding(body: BoardingUpdateIn):
    """Update boarding state and return the refreshed view model."""
    session = _session_store.get(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Unknown session")

    # Validate non-negative counts
    for field_name, value in [
        ("pax_ate", body.pax_ate),
        ("bags_loaded", body.bags_loaded),
        ("pax_planned", body.pax_planned),
        ("bags_expected", body.bags_expected),
    ]:
        if value is not None and value < 0:
            raise HTTPException(
                status_code=422,
                detail=f"{field_name} must not be negative",
            )

    # Get or create boarding state
    state = session.boarding
    if state is None or state.flight_id != body.flight_id:
        state = BoardingState(flight_id=body.flight_id)

    # Apply updates
    if body.pax_ate is not None:
        state.pax_ate = body.pax_ate
    if body.bags_loaded is not None:
        state.bags_loaded = body.bags_loaded
    if body.pax_planned is not None:
        state.pax_planned = body.pax_planned
    if body.bags_expected is not None:
        state.bags_expected = body.bags_expected
    if body.contacts is not None:
        state.contacts = body.contacts
    if body.groups is not None:
        state.groups = body.groups
    if body.oew_kg is not None:
        state.oew_kg = body.oew_kg
    if body.pax_kg_each is not None:
        state.pax_kg_each = body.pax_kg_each
    if body.bag_kg_each is not None:
        state.bag_kg_each = body.bag_kg_each
    if body.cargo_kg is not None:
        state.cargo_kg = body.cargo_kg
    if body.fuel_kg is not None:
        state.fuel_kg = body.fuel_kg

    session.boarding = state

    vm = build_boarding_view_model(None, None, state)
    return vm
