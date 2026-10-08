"""Navigraph API client — cached, rate-limited, subscription-gated.

Request path for every call:

    subscription gate -> fresh cache -> rate limiter -> HTTP
                      -> on error/429/401: stale cache (labelled)
                      -> else: a declared status, never an exception

So the three failure modes that matter in a cockpit all degrade
gracefully: no key (``not_configured``), no subscription
(``not_subscribed``), no network (``stale`` with an age).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from crew_platform.navigraph.auth import (
    DEMO_AIRPORTS,
    NavigraphAuth,
    NavigraphAuthError,
    NavigraphTokens,
)
from crew_platform.navigraph.cache import CacheEntry, TtlCache
from crew_platform.navigraph.config import (
    API_BASE,
    DATA_SUBSCRIPTION,
    ENROUTE_TILE_BASE,
    NavigraphConfig,
    load_config,
)
from crew_platform.navigraph.ratelimit import RateLimiter, RateLimitExceeded


class NavigraphUnavailable(RuntimeError):
    """Navigraph data could not be produced and no cache exists.

    Carries a machine-readable ``status`` so the API layer can answer
    with a declared state instead of a 500.
    """

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class SubscriptionGate:
    """Whether a datatype may be fetched, and why not if it may not."""

    datatype: str
    allowed: bool
    status: str  # available | not_configured | not_subscribed | not_authenticated
    required_subscription: Optional[str] = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "datatype": self.datatype,
            "allowed": self.allowed,
            "status": self.status,
            "required_subscription": self.required_subscription,
            "detail": self.detail,
        }


def gate_for(
    datatype: str,
    config: NavigraphConfig,
    tokens: Optional[NavigraphTokens],
) -> SubscriptionGate:
    """Evaluate the subscription gate for one datatype."""
    required = DATA_SUBSCRIPTION.get(datatype, "charts")

    # Datatypes served from an operator feed, not Navigraph.
    if required is None and datatype in ("notam", "risk", "nat"):
        url = {
            "notam": config.notam_url,
            "risk": config.risk_url,
            "nat": config.risk_url,
        }[datatype]
        if not url:
            return SubscriptionGate(
                datatype,
                False,
                "not_configured",
                None,
                f"No {datatype.upper()} feed configured "
                f"(set NAVIGRAPH_{'NOTAM' if datatype == 'notam' else 'RISK'}_URL)",
            )
        return SubscriptionGate(datatype, True, "available", None, "operator feed")

    if not config.configured:
        return SubscriptionGate(
            datatype,
            False,
            "not_configured",
            required,
            "Navigraph credentials are not configured on this installation",
        )
    if tokens is None or not tokens.access_token:
        return SubscriptionGate(
            datatype,
            False,
            "not_authenticated",
            required,
            "Pilot has not signed in to Navigraph on this device",
        )
    if required and not tokens.has_subscription(required):
        return SubscriptionGate(
            datatype,
            False,
            "not_subscribed",
            required,
            f"Navigraph account has no '{required}' subscription "
            f"(demo airports only: {', '.join(DEMO_AIRPORTS)})",
        )
    return SubscriptionGate(datatype, True, "available", required, "")


def _result(
    entry: CacheEntry,
    *,
    fresh: bool,
    gate: SubscriptionGate,
    note: str = "",
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Wrap a payload with the provenance the UI must display."""
    return {
        "status": "ok" if fresh else "stale",
        "datatype": entry.datatype,
        "data": entry.value,
        "cached": True,
        "fresh": fresh,
        "age_seconds": int(entry.age(now)),
        "ttl_seconds": entry.ttl,
        "subscription": gate.as_dict(),
        "note": note,
        "source": "navigraph",
    }


class NavigraphClient:
    """High-level Navigraph accessor used by the FastAPI routes."""

    def __init__(
        self,
        config: Optional[NavigraphConfig] = None,
        cache: Optional[TtlCache] = None,
        limiter: Optional[RateLimiter] = None,
        auth: Optional[NavigraphAuth] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: float = 20.0,
    ):
        self.config = config or load_config()
        self.cache = cache or TtlCache(self.config.cache_dir)
        self.limiter = limiter or RateLimiter(
            self.config.rate_limit_rpm, self.config.rate_limit_burst
        )
        self.auth = auth or NavigraphAuth(self.config)
        self._transport = transport
        self._timeout = timeout
        self._tokens: Optional[NavigraphTokens] = None
        if self.config.access_token:
            from crew_platform.navigraph.auth import decode_subscriptions, token_expiry

            self._tokens = NavigraphTokens(
                access_token=self.config.access_token,
                refresh_token=self.config.refresh_token,
                expires_at=token_expiry(self.config.access_token) or 0.0,
                scopes=self.config.scope_list,
                subscriptions=decode_subscriptions(self.config.access_token),
            )

    # -- token lifecycle --------------------------------------------------

    @property
    def tokens(self) -> Optional[NavigraphTokens]:
        return self._tokens

    def set_tokens(self, tokens: Optional[NavigraphTokens]) -> None:
        self._tokens = tokens

    async def ensure_tokens(self) -> Optional[NavigraphTokens]:
        """Return usable tokens, refreshing once if they have expired."""
        tokens = self._tokens
        if tokens and not tokens.expired:
            return tokens
        refresh = (tokens.refresh_token if tokens else None) or self.auth.load_refresh_token()
        if not refresh:
            return tokens
        try:
            self._tokens = await self.auth.refresh(refresh)
        except (NavigraphAuthError, httpx.HTTPError):
            # Keep the (expired) tokens: the gate will report
            # not_authenticated and cached data still serves.
            return tokens
        return self._tokens

    # -- status -----------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Subscription/configuration status — no secrets, safe for the UI."""
        tokens = self._tokens
        gates = {
            datatype: gate_for(datatype, self.config, tokens).as_dict()
            for datatype in DATA_SUBSCRIPTION
        }
        return {
            "configured": self.config.configured,
            "authenticated": bool(tokens and tokens.access_token and not tokens.expired),
            "subscriptions": list(tokens.subscriptions) if tokens else [],
            "demo_airports": list(DEMO_AIRPORTS),
            "config": self.config.redacted(),
            "tokens": tokens.redacted() if tokens else None,
            "rate_limit": self.limiter.status(),
            "cache": self.cache.stats(),
            "datatypes": gates,
        }

    # -- generic fetch ----------------------------------------------------

    async def _http_json(
        self, url: str, headers: dict[str, str], params: Optional[dict[str, Any]] = None
    ) -> Any:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as http:
            resp = await http.get(url, headers=headers, params=params)
        if resp.status_code == 429:
            delay = self.limiter.penalise(resp.headers.get("Retry-After"))
            raise RateLimitExceeded(delay, scope="upstream")
        if resp.status_code == 401:
            raise NavigraphAuthError("Navigraph rejected the access token (401)")
        if resp.status_code >= 400:
            raise NavigraphUnavailable(
                "upstream_error", f"Navigraph returned HTTP {resp.status_code}"
            )
        return resp.json()

    async def _http_bytes(
        self, url: str, headers: dict[str, str], cookies: Optional[dict[str, str]] = None
    ) -> bytes:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as http:
            resp = await http.get(url, headers=headers, cookies=cookies or {})
        if resp.status_code == 429:
            delay = self.limiter.penalise(resp.headers.get("Retry-After"))
            raise RateLimitExceeded(delay, scope="upstream")
        if resp.status_code == 401:
            raise NavigraphAuthError("Navigraph rejected the access token (401)")
        if resp.status_code >= 400:
            raise NavigraphUnavailable(
                "upstream_error", f"Navigraph returned HTTP {resp.status_code}"
            )
        return resp.content

    async def fetch(
        self,
        datatype: str,
        cache_key: str,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        binary: bool = False,
        use_tile_cookies: bool = False,
        authenticated: bool = True,
    ) -> dict[str, Any]:
        """Cached, gated, rate-limited GET returning a wrapped result."""
        tokens = await self.ensure_tokens() if authenticated else None
        gate = gate_for(datatype, self.config, tokens)

        cached = self.cache.get(cache_key)
        if cached is not None:
            return _result(cached, fresh=True, gate=gate)

        if not gate.allowed:
            stale = self.cache.get_stale(cache_key)
            if stale is not None:
                return _result(
                    stale, fresh=False, gate=gate, note=f"{gate.status}: {gate.detail}"
                )
            raise NavigraphUnavailable(gate.status, gate.detail)

        headers: dict[str, str] = {"Accept": "*/*" if binary else "application/json"}
        if authenticated and tokens:
            headers["Authorization"] = f"Bearer {tokens.access_token}"
        cookies = tokens.tile_cookies if (use_tile_cookies and tokens) else None

        try:
            self.limiter.acquire()
            payload: Any = (
                await self._http_bytes(url, headers, cookies)
                if binary
                else await self._http_json(url, headers, params)
            )
        except (RateLimitExceeded, NavigraphAuthError, NavigraphUnavailable, httpx.HTTPError) as exc:
            stale = self.cache.get_stale(cache_key)
            if stale is not None:
                return _result(stale, fresh=False, gate=gate, note=f"offline: {exc}")
            if isinstance(exc, RateLimitExceeded):
                raise NavigraphUnavailable("rate_limited", str(exc)) from exc
            if isinstance(exc, NavigraphAuthError):
                raise NavigraphUnavailable("not_authenticated", str(exc)) from exc
            if isinstance(exc, NavigraphUnavailable):
                raise
            raise NavigraphUnavailable("offline", f"Navigraph unreachable: {exc}") from exc

        entry = self.cache.put(cache_key, datatype, payload, self.config.ttl(datatype))
        return _result(entry, fresh=True, gate=gate)

    # -- Navigraph endpoints ---------------------------------------------

    async def airport(self, icao: str) -> dict[str, Any]:
        code = icao.strip().upper()
        return await self.fetch(
            "airport", f"airport:{code}", f"{API_BASE}/v2/airport/{code}"
        )

    async def charts_index(
        self, icao: str, version: str = "STD", rules: str = "IFR"
    ) -> dict[str, Any]:
        code = icao.strip().upper()
        version = version.upper() if version.upper() in ("STD", "CAO") else "STD"
        rules = rules.upper() if rules.upper() in ("IFR", "VFR", "ANY") else "IFR"
        return await self.fetch(
            "charts_index",
            f"charts:{code}:{version}:{rules}",
            f"{API_BASE}/v2/charts/{code}",
            params={"version": version, "rules": rules},
        )

    async def chart_image(self, icao: str, filename: str) -> dict[str, Any]:
        code = icao.strip().upper()
        safe = filename.strip().lstrip("/")
        if "/" in safe or ".." in safe:
            raise NavigraphUnavailable("bad_request", "invalid chart filename")
        return await self.fetch(
            "chart_image",
            f"chart:{code}:{safe}",
            f"{API_BASE}/v2/charts/{code}/{safe}",
            binary=True,
        )

    async def enroute_tile(
        self, layer: str, z: int, x: int, y: int, retina: bool = False
    ) -> dict[str, Any]:
        allowed = {
            "ifr.hi.day", "ifr.hi.night", "ifr.lo.day", "ifr.lo.night",
            "vfr.day", "vfr.night", "world.day", "world.night",
        }
        if layer not in allowed:
            raise NavigraphUnavailable("bad_request", f"unknown tile layer '{layer}'")
        if not (0 <= z <= 18):
            raise NavigraphUnavailable("bad_request", "zoom must be 0-18")
        span = 2 ** z
        if not (0 <= x < span and 0 <= y < span):
            raise NavigraphUnavailable("bad_request", "tile x/y out of range for zoom")
        suffix = "@2x.png" if retina else ".png"
        return await self.fetch(
            "tile",
            f"tile:{layer}:{z}:{x}:{y}:{int(retina)}",
            f"{ENROUTE_TILE_BASE}/styles/{layer}/{z}/{x}/{y}{suffix}",
            binary=True,
            use_tile_cookies=True,
        )

    # -- navigation data (airspace / airways entitlement) -----------------

    async def navdata_packages(
        self, package_status: Optional[str] = "current"
    ) -> dict[str, Any]:
        """List the FMS-data packages this pilot's subscription unlocks.

        Navigraph does not expose per-airspace or per-airway REST queries:
        navdata is delivered as AIRAC packages (a signed-URL zip per
        cycle) from ``/v1/navdata/packages``. The endpoint itself encodes
        the entitlement — an unsubscribed user gets an ``outdated``
        default package rather than an error — so the route layer reports
        the cycle/status and does NOT download the archive. Bulk download
        on every page view would violate Navigraph's ToS and blow the
        rate budget.
        """
        params: dict[str, Any] = {"format": "fmsdata_api_package"}
        status = (package_status or "").strip().lower()
        if status in ("outdated", "current", "future"):
            params["package_status"] = status
        return await self.fetch(
            "airspace",
            f"navdata:{params.get('package_status', 'any')}",
            f"{API_BASE}/v1/navdata/packages",
            params=params,
        )

    # -- operator feeds (NOTAM / risk / NAT) ------------------------------

    async def notams(self, icaos: list[str]) -> dict[str, Any]:
        """NOTAMs for a set of stations from the operator feed."""
        codes = sorted({c.strip().upper() for c in icaos if c and c.strip()})
        if not codes:
            raise NavigraphUnavailable("bad_request", "no ICAO codes requested")
        if not self.config.notam_url:
            cached = self.cache.get_stale(f"notam:{','.join(codes)}")
            gate = gate_for("notam", self.config, self._tokens)
            if cached is not None:
                return _result(cached, fresh=False, gate=gate, note=gate.detail)
            raise NavigraphUnavailable(gate.status, gate.detail)
        return await self.fetch(
            "notam",
            f"notam:{','.join(codes)}",
            self.config.notam_url,
            params={"icao": ",".join(codes)},
            authenticated=False,
        )

    async def risk_bulletin(self) -> dict[str, Any]:
        """Operational risk + NAT track bulletin from the operator feed."""
        if not self.config.risk_url:
            gate = gate_for("risk", self.config, self._tokens)
            cached = self.cache.get_stale("risk:bulletin")
            if cached is not None:
                return _result(cached, fresh=False, gate=gate, note=gate.detail)
            raise NavigraphUnavailable(gate.status, gate.detail)
        return await self.fetch(
            "risk", "risk:bulletin", self.config.risk_url, authenticated=False
        )


# ---------------------------------------------------------------------------
# Process-wide singleton (the cache and rate budget must be shared)
# ---------------------------------------------------------------------------

_client: Optional[NavigraphClient] = None
_client_created_at: float = 0.0


def get_client(reload: bool = False) -> NavigraphClient:
    global _client, _client_created_at
    if _client is None or reload:
        _client = NavigraphClient()
        _client_created_at = time.time()
    return _client


def reset_client() -> None:
    """Drop the singleton — used by tests and after a config change."""
    global _client
    _client = None
