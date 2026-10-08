"""vAMSYS API client with OAuth2 authentication.

Provides access to the Lufthansa Virtual vAMSYS endpoints:
  - Pilot profile & standings
  - Booked / active flights
  - PIREP submission status

The client handles token refresh transparently.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx


class VamsysError(RuntimeError):
    """Base error for vAMSYS interactions."""


class VamsysAuthError(VamsysError):
    """Raised when authentication or token refresh fails."""


@dataclass
class VamsysTokens:
    """Holds an OAuth2 access / refresh token pair."""

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: float = 0.0  # epoch seconds

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 30  # 30 s grace


@dataclass(frozen=True)
class PilotProfile:
    pilot_id: str
    callsign: Optional[str] = None
    rank: Optional[str] = None
    hours_total: Optional[float] = None
    airline_icao: Optional[str] = None


@dataclass(frozen=True)
class BookedFlight:
    booking_id: str
    flight_number: Optional[str] = None
    departure_icao: Optional[str] = None
    arrival_icao: Optional[str] = None
    aircraft_icao: Optional[str] = None
    scheduled_departure_utc: Optional[str] = None
    scheduled_arrival_utc: Optional[str] = None
    route: Optional[str] = None
    status: Optional[str] = None


@dataclass(frozen=True)
class PIREPStatus:
    pirep_id: str
    flight_number: Optional[str] = None
    state: Optional[str] = None  # e.g. "pending", "accepted", "rejected"
    filed_at_utc: Optional[str] = None
    flight_time_min: Optional[float] = None
    fuel_used_kg: Optional[float] = None
    landing_rate_fpm: Optional[float] = None


class VamsysClient:
    """Async vAMSYS OAuth2 API client.

    Usage::

        client = VamsysClient(
            base_url="https://vamsys.io/api/v1",
            client_id="...",
            client_secret="...",
        )
        tokens = await client.authenticate(username="...", password="...")
        profile = await client.get_pilot_profile()
    """

    def __init__(
        self,
        *,
        base_url: str = "https://vamsys.io/api/v1",
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout_seconds
        self._tokens: Optional[VamsysTokens] = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def authenticate(
        self,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        refresh_token: Optional[str] = None,
    ) -> VamsysTokens:
        """Obtain tokens via password grant or refresh-token grant."""
        payload: dict[str, str] = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }

        if refresh_token:
            payload["grant_type"] = "refresh_token"
            payload["refresh_token"] = refresh_token
        elif username and password:
            payload["grant_type"] = "password"
            payload["username"] = username
            payload["password"] = password
        else:
            raise ValueError(
                "Provide (username + password) or refresh_token."
            )

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/oauth/token", json=payload
            )

        if resp.status_code != 200:
            raise VamsysAuthError(
                f"vAMSYS auth failed HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )

        data = resp.json()
        self._tokens = VamsysTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            expires_at=time.time() + float(data.get("expires_in", 3600)),
        )
        return self._tokens

    def set_tokens(self, tokens: VamsysTokens) -> None:
        """Inject pre-existing tokens (e.g. loaded from config)."""
        self._tokens = tokens

    async def _ensure_auth(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._tokens is None:
            raise VamsysAuthError("Not authenticated — call authenticate() first.")
        if self._tokens.expired and self._tokens.refresh_token:
            await self.authenticate(refresh_token=self._tokens.refresh_token)
        if self._tokens is None or self._tokens.expired:
            raise VamsysAuthError("Token expired and no refresh_token available.")
        return self._tokens.access_token

    async def _get(self, path: str) -> dict[str, Any]:
        token = await self._ensure_auth()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(f"{self._base_url}{path}", headers=headers)
        if resp.status_code == 401:
            raise VamsysAuthError("Unauthorized — token may be revoked.")
        if resp.status_code != 200:
            raise VamsysError(
                f"vAMSYS API {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # ------------------------------------------------------------------
    # API endpoints
    # ------------------------------------------------------------------

    async def get_pilot_profile(self) -> PilotProfile:
        data = await self._get("/pilot/profile")
        p = data.get("data") or data
        return PilotProfile(
            pilot_id=str(p.get("id", "")),
            callsign=p.get("callsign"),
            rank=p.get("rank"),
            hours_total=_safe_float(p.get("hours")),
            airline_icao=p.get("airline_icao"),
        )

    async def get_booked_flights(self) -> list[BookedFlight]:
        data = await self._get("/pilot/flights")
        flights_raw: list[dict[str, Any]] = data.get("data") or []
        result: list[BookedFlight] = []
        for f in flights_raw:
            result.append(
                BookedFlight(
                    booking_id=str(f.get("id", "")),
                    flight_number=f.get("flight_number"),
                    departure_icao=f.get("departure_icao"),
                    arrival_icao=f.get("arrival_icao"),
                    aircraft_icao=f.get("aircraft_icao"),
                    scheduled_departure_utc=f.get("scheduled_departure"),
                    scheduled_arrival_utc=f.get("scheduled_arrival"),
                    route=f.get("route"),
                    status=f.get("status"),
                )
            )
        return result

    async def get_pirep_status(self, pirep_id: str) -> PIREPStatus:
        data = await self._get(f"/pilot/pireps/{pirep_id}")
        p = data.get("data") or data
        return PIREPStatus(
            pirep_id=str(p.get("id", pirep_id)),
            flight_number=p.get("flight_number"),
            state=p.get("state"),
            filed_at_utc=p.get("filed_at"),
            flight_time_min=_safe_float(p.get("flight_time")),
            fuel_used_kg=_safe_float(p.get("fuel_used")),
            landing_rate_fpm=_safe_float(p.get("landing_rate")),
        )


def _safe_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
