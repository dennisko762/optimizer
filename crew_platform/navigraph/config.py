"""Navigraph configuration — environment only, never the repository.

Recognised environment variables
--------------------------------
NAVIGRAPH_CLIENT_ID         Client id issued by Navigraph (required)
NAVIGRAPH_CLIENT_SECRET     Client secret issued by Navigraph (required)
NAVIGRAPH_SCOPES            Space-separated scope list. Default:
                            "openid offline_access charts tiles fmsdata"
NAVIGRAPH_ACCESS_TOKEN      Pre-obtained access token (optional; lets an
                            operator inject a token instead of running the
                            device flow in this process)
NAVIGRAPH_REFRESH_TOKEN     Matching refresh token (optional)
NAVIGRAPH_TOKEN_STORE       Absolute path for the refresh-token store. The
                            file is written 0600 and MUST live outside the
                            repository. Unset = tokens stay in memory only.
NAVIGRAPH_RATE_LIMIT_RPM    Client-side request ceiling per minute
                            (default 60 — conservative, Navigraph does not
                            publish a public figure)
NAVIGRAPH_RATE_LIMIT_BURST  Token-bucket burst (default 10)
NAVIGRAPH_CACHE_DIR         Directory for the on-disk offline cache.
                            Unset = memory-only cache.
NAVIGRAPH_NOTAM_URL         Optional NOTAM feed base URL (see aero.py —
                            Navigraph itself does not serve NOTAMs)
NAVIGRAPH_RISK_URL          Optional operational-risk / NAT-track feed URL
NAVIGRAPH_NOTAM_PROVENANCE  Declared provenance of the NOTAM feed:
                            ``live`` | ``fixture`` | ``demo``. Unset =
                            auto-detected (loopback/file URL -> fixture).
NAVIGRAPH_RISK_PROVENANCE   Same, for the risk / NAT-track feed.

No value from this module is ever logged or returned over the API; the
status endpoint reports booleans only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

# Navigraph service endpoints (public, documented).
IDENTITY_BASE = "https://identity.api.navigraph.com"
API_BASE = "https://api.navigraph.com"
ENROUTE_TILE_BASE = "https://enroute-bitmap.charts.api-v2.navigraph.com"

DEFAULT_SCOPES = "openid offline_access charts tiles fmsdata"

# Per-datatype cache TTL in seconds.
#
# IMPORTANT — Navigraph chart data is deliberately ABSENT from this map.
# Navigraph's charts documentation
# (https://developers.navigraph.com/docs/charts/airport-charts) forbids
# caching, storing or serving chart imagery offline, so ``charts_index``,
# ``chart_image`` and ``tile`` are never written to the cache and never
# served from it. See ``NON_CACHEABLE_DATATYPES`` below. Only non-chart
# data (account info, FMS-package metadata, the operator NOTAM/risk/NAT
# feeds) is TTL-cached, and only the time-critical ones get a short TTL.
DATA_TTLS: dict[str, int] = {
    "userinfo": 15 * 60,
    "airport": 24 * 60 * 60,
    "airspace": 6 * 60 * 60,
    "notam": 10 * 60,
    "risk": 15 * 60,
    "nat": 30 * 60,
}

#: Datatypes that must NEVER be cached, mirrored to disk or served from a
#: cache — not even as an offline fallback. These are Navigraph chart
#: products; their licence only permits live, authenticated delivery to an
#: entitled account.
NON_CACHEABLE_DATATYPES: frozenset[str] = frozenset(
    {"charts_index", "chart_image", "tile"}
)


def is_cacheable(datatype: str) -> bool:
    """False for Navigraph chart products (licence forbids storage)."""
    return datatype not in NON_CACHEABLE_DATATYPES

#: Datatype -> Navigraph **subscription** claim that unlocks it. Datatypes
#: mapped to ``None`` are not part of the Navigraph product at all and are
#: served from an operator-supplied feed instead (see aero.py).
#:
#: Note on tiles: ``tiles`` is an OAuth *scope*, not a subscription claim.
#: The entitlement that unlocks enroute bitmap tiles is the same ``charts``
#: subscription as the airport charts; the scope only decides whether the
#: token request also returns the signed CloudFront cookies. Both are
#: required, which is why ``DATA_SCOPES`` exists alongside this map.
DATA_SUBSCRIPTION: dict[str, Optional[str]] = {
    "userinfo": None,
    "airport": "charts",
    "charts_index": "charts",
    "chart_image": "charts",
    "tile": "charts",
    "airspace": "fmsdata",
    "notam": None,
    "risk": None,
    "nat": None,
}

#: Datatype -> OAuth scope that must be present on the access token for
#: the transport to work at all (independent of the subscription claim).
DATA_SCOPES: dict[str, Optional[str]] = {
    "userinfo": None,
    "airport": "charts",
    "charts_index": "charts",
    "chart_image": "charts",
    "tile": "tiles",
    "airspace": "fmsdata",
    "notam": None,
    "risk": None,
    "nat": None,
}

#: Declared provenance values for an operator feed.
PROVENANCE_LIVE = "live"
PROVENANCE_FIXTURE = "fixture"
PROVENANCE_DEMO = "demo"
_PROVENANCE_VALUES = (PROVENANCE_LIVE, PROVENANCE_FIXTURE, PROVENANCE_DEMO)


def detect_provenance(url: Optional[str], declared: Optional[str] = None) -> str:
    """Classify a feed URL as live / fixture / demo data.

    An explicit ``NAVIGRAPH_*_PROVENANCE`` value always wins. Otherwise a
    loopback or ``file://`` URL is treated as FIXTURE data, because that is
    what the repo's own fixture server serves — the UI must never label it
    LIVE (same data-integrity rule as AGENTS.md's fuel-flow rule).
    """
    value = (declared or "").strip().lower()
    if value in _PROVENANCE_VALUES:
        return value
    if not url:
        return PROVENANCE_FIXTURE
    lowered = url.strip().lower()
    if lowered.startswith("file:"):
        return PROVENANCE_FIXTURE
    for marker in ("//127.0.0.1", "//localhost", "//[::1]", "//0.0.0.0"):
        if marker in lowered:
            return PROVENANCE_FIXTURE
    return PROVENANCE_LIVE


@dataclass(frozen=True)
class NavigraphConfig:
    """Resolved Navigraph configuration. Secrets stay in this object."""

    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scopes: str = DEFAULT_SCOPES
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_store: Optional[str] = None
    rate_limit_rpm: int = 60
    rate_limit_burst: int = 10
    cache_dir: Optional[str] = None
    notam_url: Optional[str] = None
    risk_url: Optional[str] = None
    notam_provenance: Optional[str] = None
    risk_provenance: Optional[str] = None
    ttls: dict[str, int] = field(default_factory=lambda: dict(DATA_TTLS))

    @property
    def configured(self) -> bool:
        """True when a client credential pair OR an injected token exists."""
        return bool((self.client_id and self.client_secret) or self.access_token)

    @property
    def scope_list(self) -> list[str]:
        return [s for s in self.scopes.split() if s]

    def feed_provenance(self, datatype: str) -> str:
        """Declared provenance for an operator-feed datatype."""
        if datatype == "notam":
            return detect_provenance(self.notam_url, self.notam_provenance)
        if datatype in ("risk", "nat"):
            return detect_provenance(self.risk_url, self.risk_provenance)
        return PROVENANCE_LIVE

    def ttl(self, datatype: str) -> int:
        return int(self.ttls.get(datatype, 300))

    def redacted(self) -> dict[str, object]:
        """Configuration readiness as booleans — safe to serialise."""
        return {
            "client_id_set": bool(self.client_id),
            "client_secret_set": bool(self.client_secret),
            "access_token_injected": bool(self.access_token),
            "refresh_token_injected": bool(self.refresh_token),
            "token_store_set": bool(self.token_store),
            "cache_dir_set": bool(self.cache_dir),
            "notam_feed_set": bool(self.notam_url),
            "risk_feed_set": bool(self.risk_url),
            "notam_provenance": self.feed_provenance("notam"),
            "risk_provenance": self.feed_provenance("risk"),
            "non_cacheable_datatypes": sorted(NON_CACHEABLE_DATATYPES),
            "scopes": self.scope_list,
            "rate_limit_rpm": self.rate_limit_rpm,
            "configured": self.configured,
        }


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def load_config(env: Optional[dict[str, str]] = None) -> NavigraphConfig:
    """Build a :class:`NavigraphConfig` from the environment.

    Missing credentials are NOT an error — they produce an unconfigured
    config whose consumers report ``not_configured`` to the UI.
    """
    src = os.environ if env is None else env

    def get(name: str) -> Optional[str]:
        value = (src.get(name) or "").strip()
        return value or None

    if env is None:
        rpm = _int_env("NAVIGRAPH_RATE_LIMIT_RPM", 60)
        burst = _int_env("NAVIGRAPH_RATE_LIMIT_BURST", 10)
    else:
        try:
            rpm = int(env.get("NAVIGRAPH_RATE_LIMIT_RPM") or 60) or 60
        except ValueError:
            rpm = 60
        try:
            burst = int(env.get("NAVIGRAPH_RATE_LIMIT_BURST") or 10) or 10
        except ValueError:
            burst = 10

    return NavigraphConfig(
        client_id=get("NAVIGRAPH_CLIENT_ID"),
        client_secret=get("NAVIGRAPH_CLIENT_SECRET"),
        scopes=get("NAVIGRAPH_SCOPES") or DEFAULT_SCOPES,
        access_token=get("NAVIGRAPH_ACCESS_TOKEN"),
        refresh_token=get("NAVIGRAPH_REFRESH_TOKEN"),
        token_store=get("NAVIGRAPH_TOKEN_STORE"),
        rate_limit_rpm=rpm,
        rate_limit_burst=burst,
        cache_dir=get("NAVIGRAPH_CACHE_DIR"),
        notam_url=get("NAVIGRAPH_NOTAM_URL"),
        risk_url=get("NAVIGRAPH_RISK_URL"),
        notam_provenance=get("NAVIGRAPH_NOTAM_PROVENANCE"),
        risk_provenance=get("NAVIGRAPH_RISK_PROVENANCE"),
    )
