"""vAMSYS Pilot API v3 — OAuth 2.0 Authorization Code + PKCE client.

Built against the live OpenAPI spec (Pilot API v3.0.0,
https://vamsys.io/api-docs/pilot.json):

- Server:        https://vamsys.io/api/v3/pilot
- Authorize:     GET  https://vamsys.io/oauth/authorize
- Token:         POST https://vamsys.io/oauth/token  (form-encoded)
- Identity:      GET  /user          (scopes: identity:basic [+ pilot:read])
- Bookings:      GET  /bookings      (scope: flights:read)
- Booking OFP:   GET  /bookings/{id}/simbrief   (404 when no OFP linked)
- Dispatch:      POST /dispatch-url  (scope: flights:write) — creates a
                 Phoenix dispatch session; documented remote check-in path.

PKCE is mandatory (public client, no secret). Access tokens live 1 hour;
refresh tokens are returned with the exchange. On 401 the client may
auto-refresh once via an injected refresh callback.

This module does NOT collect usernames or passwords — authentication
happens entirely through the vAMSYS OAuth consent page in the pilot's
browser. Client IDs are numeric (integer) in the vAMSYS spec.

Assumptions / calibration notes:
- The spec does not document an "I am flying this flight" check-in write.
  The documented remote path is POST /dispatch-url (Phoenix dispatch).
- BookingData carries airport *IDs*, not ICAOs; ICAOs are resolved from
  the linked SimBrief OFP when available (see routes._ofp_icao).
"""

from __future__ import annotations

import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import httpx


class PKCEError(RuntimeError):
    """Raised on PKCE flow errors."""


class VamsysPilotAuthError(PKCEError):
    """Raised when Pilot API authentication fails."""


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


def generate_code_verifier(length: int = 64) -> str:
    """Generate a cryptographic code_verifier (43-128 chars, URL-safe)."""
    if not (43 <= length <= 128):
        raise ValueError("code_verifier length must be 43-128")
    return secrets.token_urlsafe(length)[:length]


def generate_code_challenge(verifier: str) -> str:
    """Derive the S256 code_challenge from a code_verifier."""
    import base64

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    """Generate an opaque, unpredictable state parameter."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclass
class PilotTokens:
    """Holds an OAuth2 access / refresh token pair for the Pilot API."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 30  # 30s grace


# ---------------------------------------------------------------------------
# PKCE Auth Flow
# ---------------------------------------------------------------------------


@dataclass
class PKCEFlowState:
    """Tracks an in-progress PKCE authorization flow."""

    state: str
    code_verifier: str
    redirect_uri: str
    created_at: float = field(default_factory=time.time)


# Scopes documented in the v3 spec (securitySchemes.oauth2.flows.authorizationCode.scopes).
DEFAULT_SCOPES = ["identity:basic", "pilot:read", "flights:read"]

# Optional scopes the client can request at registration time.
OPTIONAL_SCOPES = [
    "identity:networks",
    "identity:discord",
    "identity:social",
    "pilot:write",
    "flights:write",
    "activities:read",
    "activities:write",
]


class VamsysPilotAuth:
    """Manages the vAMSYS Pilot OAuth Authorization Code + PKCE flow (v3)."""

    AUTHORIZE_URL = "https://vamsys.io/oauth/authorize"
    TOKEN_URL = "https://vamsys.io/oauth/token"

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: Optional[list[str]] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._scopes = scopes or list(DEFAULT_SCOPES)
        self._timeout = timeout_seconds

    def build_authorize_url(self) -> tuple[str, PKCEFlowState]:
        """Build the authorization URL and return (url, flow_state).

        v3 authorize params (all required): client_id, redirect_uri,
        response_type=code, scope, state, code_challenge,
        code_challenge_method=S256.
        """
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        state = generate_state()

        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(self._scopes),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }

        url = f"{self.AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        flow = PKCEFlowState(
            state=state,
            code_verifier=verifier,
            redirect_uri=self._redirect_uri,
        )
        return url, flow

    def validate_callback(
        self,
        *,
        received_state: str,
        expected_flow: PKCEFlowState,
    ) -> None:
        """Validate the callback state parameter. Raises PKCEError on mismatch."""
        if not secrets.compare_digest(received_state, expected_flow.state):
            raise PKCEError("State mismatch — possible CSRF attack")

    async def exchange_code(
        self,
        *,
        code: str,
        flow: PKCEFlowState,
    ) -> PilotTokens:
        """Exchange the authorization code for tokens (form-encoded)."""
        payload = {
            "grant_type": "authorization_code",
            "client_id": self._client_id,
            "redirect_uri": flow.redirect_uri,
            "code": code,
            "code_verifier": flow.code_verifier,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.TOKEN_URL, data=payload)

        if resp.status_code != 200:
            raise VamsysPilotAuthError(
                f"Token exchange failed HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )

        return _tokens_from_response(resp.json())

    async def refresh_tokens(self, refresh_token: str) -> PilotTokens:
        """Refresh an expired access token.

        Per the v3 spec, a 400 with error=invalid_grant means the pilot
        revoked consent — the caller should discard stored tokens.
        """
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.TOKEN_URL, data=payload)

        if resp.status_code != 200:
            body = resp.text[:300]
            if resp.status_code == 400 and "invalid_grant" in body:
                raise VamsysPilotAuthError(
                    "Refresh rejected (invalid_grant) — consent was revoked. "
                    "Re-authorize via the consent page."
                )
            raise VamsysPilotAuthError(
                f"Token refresh failed HTTP {resp.status_code}: {body}"
            )

        data = resp.json()
        tokens = _tokens_from_response(data)
        if not tokens.refresh_token:
            tokens.refresh_token = refresh_token
        return tokens

    async def revoke_token(self, token: str) -> bool:
        """Best-effort token revocation. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await http.post(
                    "https://vamsys.io/oauth/revoke",
                    data={
                        "client_id": self._client_id,
                        "token": token,
                    },
                )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


def _tokens_from_response(data: dict[str, Any]) -> PilotTokens:
    """Map a v3 token endpoint response to PilotTokens."""
    return PilotTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=time.time() + float(data.get("expires_in", 3600)),
        scopes=data.get("scope", "").split() if data.get("scope") else [],
    )


# ---------------------------------------------------------------------------
# Pilot API client (v3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PilotIdentity:
    """Pilot identity assembled from GET /user."""

    pilot_id: str  # user id (spec: user.id)
    crew_id: Optional[str] = None  # pilot account id (spec: user.pilot.id)
    username: Optional[str] = None  # pilot callsign/username (e.g. "JS001")
    email: Optional[str] = None
    callsign: Optional[str] = None  # == username in vAMSYS
    rank: Optional[str] = None
    airline_icao: Optional[str] = None  # not exposed by /user; left None
    airline_id: Optional[int] = None
    hours_total: Optional[float] = None
    display_name: Optional[str] = None
    frozen: bool = False
    banned: bool = False


@dataclass(frozen=True)
class PilotFlight:
    """A booking from the pilot's roster (GET /bookings)."""

    flight_id: str  # booking id as string
    flight_number: Optional[str] = None
    departure_icao: Optional[str] = None  # resolved from linked OFP, if any
    arrival_icao: Optional[str] = None  # resolved from linked OFP, if any
    aircraft_icao: Optional[str] = None  # not in BookingData; OFP may carry it
    callsign: Optional[str] = None
    scheduled_departure_utc: Optional[str] = None
    route: Optional[str] = None  # pilot's flight-plan route string
    status: Optional[str] = None
    # v3 extras
    route_id: Optional[int] = None
    network: Optional[str] = None
    passengers: Optional[int] = None
    cargo: Optional[int] = None
    cost_index: Optional[str] = None
    altitude: Optional[int] = None
    has_ofp: bool = False


# ---------------------------------------------------------------------------
# OFP helpers
# ---------------------------------------------------------------------------


def _ofp_field(ofp: dict[str, Any], *paths: str) -> Any:
    """Walk nested OFP dicts defensively."""
    node: Any = ofp
    for key in paths:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _first_ofp_present(ofp: dict[str, Any], candidates: list[str]) -> Any:
    """Return the first non-empty value among candidate dotted paths.

    Mirrors the calibrated SimBrief extraction in optimizer.api.simbrief_routes
    (_first_deep_present): SimBrief OFP fields appear under several possible
    keys and may be a bare ICAO string or a nested dict with an icao field.
    """
    for cand in candidates:
        val = _ofp_field(ofp, *cand.split("."))
        if val is None:
            continue
        if isinstance(val, str):
            if val.strip():
                return val.strip()
            continue
        if isinstance(val, dict):
            inner = val.get("icao") or val.get("icao_code") or val.get("icaocode")
            if inner:
                return str(inner).strip()
    return None


# Calibrated candidate paths (order matters: most-specific SimBrief v2 first).
_DEP_CANDIDATES = [
    "origin",
    "departure",
    "origin.icao",
    "origin.icao_code",
    "departure.icao",
    "general.origin",
]
_ARR_CANDIDATES = [
    "destination",
    "arrival",
    "destination.icao",
    "destination.icao_code",
    "arrival.icao",
    "general.destination",
]
_ACFT_CANDIDATES = [
    "aircraft",
    "aircraft.icao_code",
    "aircraft.icaocode",
    "aircraft.name",
    "general.aircraft",
]


def _replace_flight_icaos(flight: PilotFlight, ofp: dict[str, Any]) -> PilotFlight:
    """Resolve airport ICAOs from a linked SimBrief OFP payload.

    ofp is the raw SimBrief OFP (the value of SimbriefOfpData.ofp_data). A
    SimBrief OFP always has at least the departure/arrival ICAOs; aircraft
    may be absent in which case the booking's value (if any) is kept.
    """
    ofp_data = (ofp or {}).get("ofp_data") or {}
    dep = _first_ofp_present(ofp_data, _DEP_CANDIDATES)
    arr = _first_ofp_present(ofp_data, _ARR_CANDIDATES)
    acft = _first_ofp_present(ofp_data, _ACFT_CANDIDATES) or flight.aircraft_icao
    return PilotFlight(
        flight_id=flight.flight_id,
        flight_number=flight.flight_number,
        departure_icao=dep,
        arrival_icao=arr,
        aircraft_icao=acft,
        callsign=flight.callsign,
        scheduled_departure_utc=flight.scheduled_departure_utc,
        route=flight.route,
        status=flight.status,
        route_id=flight.route_id,
        network=flight.network,
        passengers=flight.passengers,
        cargo=flight.cargo,
        cost_index=flight.cost_index,
        altitude=flight.altitude,
        has_ofp=True,
    )


class VamsysPilotClient:
    """vAMSYS Pilot API v3 client with one-shot auto token refresh.

    Uses tokens obtained through the PKCE flow. No passwords or client
    secrets touch this class.
    """

    BASE_URL = "https://vamsys.io/api/v3/pilot"

    def __init__(
        self,
        tokens: PilotTokens,
        timeout_seconds: float = 15.0,
        refresh_fn: Optional[Callable[[], Awaitable[PilotTokens]]] = None,
    ) -> None:
        self._tokens = tokens
        self._timeout = timeout_seconds
        self._refresh_fn = refresh_fn
        self._refreshed_once = False

    @property
    def tokens(self) -> PilotTokens:
        return self._tokens

    def update_tokens(self, tokens: PilotTokens) -> None:
        self._tokens = tokens
        self._refreshed_once = False

    async def _get(self, path: str) -> dict[str, Any]:
        if self._tokens.expired:
            raise VamsysPilotAuthError("Token expired — refresh required")

        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self.BASE_URL}{path}", headers=headers)

        if resp.status_code == 401 and not self._refreshed_once:
            # Spec: on 401 try refreshing once; invalid_grant => revoked.
            if self._refresh_fn is None:
                raise VamsysPilotAuthError("Unauthorized — token may be revoked")
            try:
                self._refreshed_once = True
                self._tokens = await self._refresh_fn()
                headers = {
                    "Authorization": f"Bearer {self._tokens.access_token}"
                }
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    resp = await http.get(f"{self.BASE_URL}{path}", headers=headers)
            except VamsysPilotAuthError:
                raise

        if resp.status_code == 401:
            raise VamsysPilotAuthError("Unauthorized — token may be revoked")
        if resp.status_code != 200:
            raise PKCEError(
                f"Pilot API {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._tokens.expired:
            raise VamsysPilotAuthError("Token expired — refresh required")

        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(f"{self.BASE_URL}{path}", json=payload, headers=headers)

        if resp.status_code == 401 and not self._refreshed_once and self._refresh_fn is not None:
            self._refreshed_once = True
            self._tokens = await self._refresh_fn()
            headers = {
                "Authorization": f"Bearer {self._tokens.access_token}"
            }
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await http.post(f"{self.BASE_URL}{path}", json=payload, headers=headers)

        if resp.status_code == 401:
            raise VamsysPilotAuthError("Unauthorized — token may be revoked")
        if resp.status_code != 200:
            raise PKCEError(
                f"Pilot API {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # -- User / identity ----------------------------------------------------

    async def get_user(self) -> tuple[dict[str, Any], PilotIdentity]:
        """GET /user → (raw UserData, PilotIdentity)."""
        data = await self._get("/user")
        u = data.get("data") or data
        pilot_raw = u.get("pilot") or {}
        name = " ".join(
            part for part in (u.get("first_name"), u.get("last_name")) if part
        ).strip() or None
        identity = PilotIdentity(
            pilot_id=str(u.get("id", "")),
            crew_id=str(pilot_raw.get("id", "")) if pilot_raw and pilot_raw.get("id") else None,
            username=pilot_raw.get("username") if pilot_raw else None,
            email=u.get("email"),
            callsign=pilot_raw.get("username") if pilot_raw else None,
            airline_icao=None,  # /user exposes airline_id, not ICAO
            airline_id=pilot_raw.get("airline_id") if pilot_raw else None,
            hours_total=None,
            display_name=name,
            frozen=bool(pilot_raw.get("frozen", False)) if pilot_raw else False,
            banned=bool(pilot_raw.get("banned", False)) if pilot_raw else False,
        )
        return u, identity

    # Backwards-compatible name (old v1 client used /pilot/profile).
    async def get_pilot_identity(self) -> PilotIdentity:
        _, identity = await self.get_user()
        return identity

    # -- Bookings ------------------------------------------------------------

    async def get_bookings(self, status: str = "current") -> list[PilotFlight]:
        """GET /bookings?filter[status]=... → list of PilotFlight.

        Airport ICAOs are NOT in BookingData (IDs only). Call
        get_booking_simbrief() per booking to resolve ICAOs from the
        linked SimBrief OFP when available.
        """
        params = urllib.parse.urlencode({"filter[status]": status})
        data = await self._get(f"/bookings?{params}")
        raw: list[dict[str, Any]] = data.get("data") or []
        flights: list[PilotFlight] = []
        for b in raw:
            flights.append(
                PilotFlight(
                    flight_id=str(b.get("id", "")),
                    flight_number=b.get("flight_number"),
                    departure_icao=None,
                    arrival_icao=None,
                    aircraft_icao=None,
                    callsign=b.get("callsign"),
                    scheduled_departure_utc=b.get("departure_time"),
                    route=b.get("user_route"),
                    status=b.get("type") or "booked",
                    route_id=b.get("route_id"),
                    network=b.get("network"),
                    passengers=b.get("passengers"),
                    cargo=b.get("cargo"),
                    cost_index=b.get("cost_index"),
                    altitude=b.get("altitude"),
                )
            )
        return flights

    async def get_booking(
        self,
        booking_id: str | int,
        *,
        resolve_icao: bool = True,
    ) -> PilotFlight:
        """GET /bookings/{id}; optionally resolve ICAOs from the linked OFP."""
        data = await self._get(f"/bookings/{booking_id}")
        b = data.get("data") or data
        flight = PilotFlight(
            flight_id=str(b.get("id", "")),
            flight_number=b.get("flight_number"),
            departure_icao=None,
            arrival_icao=None,
            aircraft_icao=None,
            callsign=b.get("callsign"),
            scheduled_departure_utc=b.get("departure_time"),
            route=b.get("user_route"),
            status=b.get("type") or "booked",
            route_id=b.get("route_id"),
            network=b.get("network"),
            passengers=b.get("passengers"),
            cargo=b.get("cargo"),
            cost_index=b.get("cost_index"),
            altitude=b.get("altitude"),
        )
        if resolve_icao:
            ofp = await self.get_booking_simbrief(booking_id)
            if ofp is not None:
                flight = _replace_flight_icaos(flight, ofp)
        return flight

    async def get_booking_simbrief(
        self, booking_id: str | int
    ) -> Optional[dict[str, Any]]:
        """GET /bookings/{id}/simbrief → SimbriefOfpData, or None if 404.

        Returns the raw OFP payload:
        {"ofp_data": {...raw SimBrief OFP...}, "pdf_url": ..., "created_at": ...}
        """
        try:
            data = await self._get(f"/bookings/{booking_id}/simbrief")
        except PKCEError as e:
            if "HTTP 404" in str(e):
                return None
            raise
        return data.get("data")

    # -- Remote dispatch (documented write path) ------------------------------

    async def get_dispatch_url(self, route_id: int) -> str:
        """POST /dispatch-url → Phoenix dispatch session URL.

        The pilot opens the URL to complete the dispatch form (SimBrief,
        cargo, containers); Phoenix creates the booking on submit.
        Requires the flights:write scope on the registered client.
        """
        data = await self._post("/dispatch-url", {"route_id": route_id})
        payload = data.get("data") or data
        url = payload.get("url")
        if not url:
            raise PKCEError(f"Dispatch URL response missing 'url': {data!r}"[:300])
        return url
