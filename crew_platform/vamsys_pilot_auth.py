"""vAMSYS Pilot OAuth 2.0 Authorization Code + PKCE flow.

This module implements the PKCE boundary for the vAMSYS Pilot API.
It does NOT collect usernames or passwords — authentication happens
entirely through the vAMSYS OAuth consent page in the user's browser.

The bridge holds tokens; the browser client receives only an opaque
local session id.

References:
- https://vamsys.io/docs/pilot
- https://vamsys.io/api-docs/pilot.json
"""

from __future__ import annotations

import hashlib
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

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
    """Derive a S256 code_challenge from a code_verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    import base64
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


class VamsysPilotAuth:
    """Manages the vAMSYS Pilot OAuth Authorization Code + PKCE flow.

    Usage:
        auth = VamsysPilotAuth(client_id="...", redirect_uri="...")
        url, flow = auth.build_authorize_url()
        # User visits url, consents, gets redirected with ?code=...&state=...
        tokens = await auth.exchange_code(code="...", flow=flow)
    """

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
        self._scopes = scopes or ["pilot:profile", "pilot:flights"]
        self._timeout = timeout_seconds

    def build_authorize_url(self) -> tuple[str, PKCEFlowState]:
        """Build the authorization URL and return (url, flow_state).

        The caller must store flow_state to validate the callback.
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
        """Exchange the authorization code for tokens using PKCE."""
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

        data = resp.json()
        return PilotTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            scopes=data.get("scope", "").split(),
        )

    async def refresh_tokens(self, refresh_token: str) -> PilotTokens:
        """Refresh an expired access token."""
        payload = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.TOKEN_URL, data=payload)

        if resp.status_code != 200:
            raise VamsysPilotAuthError(
                f"Token refresh failed HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )

        data = resp.json()
        return PilotTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", refresh_token),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
            scopes=data.get("scope", "").split(),
        )

    async def revoke_token(self, token: str) -> bool:
        """Attempt to revoke a token. Returns True on success."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await http.post(
                    f"{self.TOKEN_URL.rsplit('/', 1)[0]}/revoke",
                    data={
                        "client_id": self._client_id,
                        "token": token,
                    },
                )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False


# ---------------------------------------------------------------------------
# Pilot API client (read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PilotIdentity:
    """Pilot identity from the Pilot API."""

    pilot_id: str
    crew_id: Optional[str] = None
    callsign: Optional[str] = None
    rank: Optional[str] = None
    airline_icao: Optional[str] = None
    hours_total: Optional[float] = None
    display_name: Optional[str] = None


@dataclass(frozen=True)
class PilotFlight:
    """A flight from the pilot's roster/booking."""

    flight_id: str
    flight_number: Optional[str] = None
    departure_icao: Optional[str] = None
    arrival_icao: Optional[str] = None
    aircraft_icao: Optional[str] = None
    callsign: Optional[str] = None
    scheduled_departure_utc: Optional[str] = None
    route: Optional[str] = None
    status: Optional[str] = None


class VamsysPilotClient:
    """Read-only vAMSYS Pilot API client.

    Uses tokens obtained through the PKCE flow.
    No passwords or client secrets touch this class.
    """

    BASE_URL = "https://vamsys.io/api/v1"

    def __init__(self, tokens: PilotTokens, timeout_seconds: float = 15.0) -> None:
        self._tokens = tokens
        self._timeout = timeout_seconds

    @property
    def tokens(self) -> PilotTokens:
        return self._tokens

    def update_tokens(self, tokens: PilotTokens) -> None:
        self._tokens = tokens

    async def _get(self, path: str) -> dict[str, Any]:
        if self._tokens.expired:
            raise VamsysPilotAuthError("Token expired — refresh required")

        headers = {"Authorization": f"Bearer {self._tokens.access_token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self.BASE_URL}{path}", headers=headers)

        if resp.status_code == 401:
            raise VamsysPilotAuthError("Unauthorized — token may be revoked")
        if resp.status_code != 200:
            raise PKCEError(
                f"Pilot API {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    async def get_pilot_identity(self) -> PilotIdentity:
        """Fetch the authenticated pilot's identity."""
        data = await self._get("/pilot/profile")
        p = data.get("data") or data
        return PilotIdentity(
            pilot_id=str(p.get("id", "")),
            crew_id=p.get("crew_id") or p.get("pilot_id") or str(p.get("id", "")),
            callsign=p.get("callsign"),
            rank=p.get("rank"),
            airline_icao=p.get("airline_icao"),
            hours_total=_safe_float(p.get("hours")),
            display_name=p.get("name") or p.get("display_name"),
        )

    async def get_flights(self) -> list[PilotFlight]:
        """Fetch the pilot's current/next flights."""
        data = await self._get("/pilot/flights")
        flights_raw: list[dict[str, Any]] = data.get("data") or []
        return [
            PilotFlight(
                flight_id=str(f.get("id", "")),
                flight_number=f.get("flight_number"),
                departure_icao=f.get("departure_icao"),
                arrival_icao=f.get("arrival_icao"),
                aircraft_icao=f.get("aircraft_icao"),
                callsign=f.get("callsign"),
                scheduled_departure_utc=f.get("scheduled_departure"),
                route=f.get("route"),
                status=f.get("status"),
            )
            for f in flights_raw
        ]


def _safe_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
