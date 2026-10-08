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

# Per-datatype cache TTL in seconds. Charts metadata and chart images are
# revised on the AIRAC cycle, so they are cached aggressively; anything
# time-critical (NOTAM, risk, NAT) gets a short TTL.
DATA_TTLS: dict[str, int] = {
    "userinfo": 15 * 60,
    "airport": 24 * 60 * 60,
    "charts_index": 6 * 60 * 60,
    "chart_image": 7 * 24 * 60 * 60,
    "tile": 24 * 60 * 60,
    "airspace": 6 * 60 * 60,
    "notam": 10 * 60,
    "risk": 15 * 60,
    "nat": 30 * 60,
}

#: Datatype -> Navigraph subscription scope that unlocks it. Datatypes
#: mapped to ``None`` are not part of the Navigraph product at all and are
#: served from an operator-supplied feed instead (see aero.py).
DATA_SUBSCRIPTION: dict[str, Optional[str]] = {
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
    ttls: dict[str, int] = field(default_factory=lambda: dict(DATA_TTLS))

    @property
    def configured(self) -> bool:
        """True when a client credential pair OR an injected token exists."""
        return bool((self.client_id and self.client_secret) or self.access_token)

    @property
    def scope_list(self) -> list[str]:
        return [s for s in self.scopes.split() if s]

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
    )
