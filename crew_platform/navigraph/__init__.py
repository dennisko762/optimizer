"""Navigraph integration — subscription-gated charts, airspace and tiles.

The whole package is designed around one hard rule: **the EFB must stay
usable without a Navigraph subscription.** Every entry point degrades to a
declared `not_subscribed` / `not_configured` state instead of raising, and
every cached payload survives an upstream outage (stale-while-error).

Credentials live ONLY in the environment (see `config.py`); nothing in this
package reads or writes a credential file inside the repository.
"""

from __future__ import annotations

from crew_platform.navigraph.cache import CacheEntry, TtlCache
from crew_platform.navigraph.config import (
    DATA_TTLS,
    NavigraphConfig,
    load_config,
)
from crew_platform.navigraph.ratelimit import RateLimiter, RateLimitExceeded
from crew_platform.navigraph.auth import (
    NavigraphAuth,
    NavigraphAuthError,
    NavigraphTokens,
    decode_subscriptions,
)
from crew_platform.navigraph.client import (
    NavigraphClient,
    NavigraphUnavailable,
    SubscriptionGate,
    gate_for,
)
from crew_platform.navigraph.aero import (
    NatTrack,
    Notam,
    OperationalRisk,
    parse_nat_track_message,
    parse_notam_records,
    parse_risk_records,
)

__all__ = [
    "CacheEntry",
    "TtlCache",
    "DATA_TTLS",
    "NavigraphConfig",
    "load_config",
    "RateLimiter",
    "RateLimitExceeded",
    "NavigraphAuth",
    "NavigraphAuthError",
    "NavigraphTokens",
    "decode_subscriptions",
    "NavigraphClient",
    "NavigraphUnavailable",
    "SubscriptionGate",
    "gate_for",
    "NatTrack",
    "Notam",
    "OperationalRisk",
    "parse_nat_track_message",
    "parse_notam_records",
    "parse_risk_records",
]
