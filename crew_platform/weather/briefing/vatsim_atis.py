"""VATSIM live simulated ATIS adapter — clearly labelled ``VATSIM ATIS``.

VATSIM's data feed (``https://data.vatsim.net/v3/vatsim-data.json``) is free
and requires no API key, but it is SIMULATED network data (a controller's
roleplay ATIS text, not a real-world aerodrome ATIS). Task requirement:
production redistribution to end users stays DISABLED by default behind an
explicit env feature gate until written permission / licence compatibility
is documented — this module always fetches+normalizes (cheap, same-process,
useful for internal/dev testing) but :func:`fetch_atis` returns a disabled
Product unless ``VATSIM_ATIS_ENABLED=1`` is set.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .cache import CACHE
from .models import Product, ProductState, to_utc_iso, utc_now_iso

DEFAULT_BASE = "https://data.vatsim.net/v3/vatsim-data.json"
SOURCE = "VATSIM"
LABEL = "VATSIM ATIS"


@dataclass(frozen=True)
class VatsimConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("VATSIM_DATA_URL", DEFAULT_BASE))
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("VATSIM_TIMEOUT_S", "15")))
    ttl_s: float = field(default_factory=lambda: float(os.environ.get("VATSIM_TTL_S", "120")))
    #: explicit production feature gate — OFF by default (task requirement).
    enabled: bool = field(default_factory=lambda: os.environ.get("VATSIM_ATIS_ENABLED", "0").strip() == "1")

    @classmethod
    def from_env(cls) -> "VatsimConfig":
        return cls()


class VatsimError(RuntimeError):
    pass


def _fetch_feed(config: VatsimConfig) -> dict[str, Any]:
    import httpx

    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.get(config.base_url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise VatsimError(f"VATSIM data feed request failed: {exc}") from exc
    if resp.status_code != 200:
        raise VatsimError(f"VATSIM data feed HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise VatsimError("VATSIM data feed returned non-JSON") from exc


def _atis_to_product(rec: dict[str, Any]) -> Product:
    station = rec.get("callsign")
    text = rec.get("text_atis")
    raw = "\n".join(text) if isinstance(text, list) else (text or None)
    issued = to_utc_iso(rec.get("last_updated"))
    lat, lon = rec.get("latitude"), rec.get("longitude")
    geometry = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
    return Product(
        kind="vatsim_atis", source=SOURCE, product="ATIS", label=LABEL,
        state=ProductState.OK if raw else ProductState.PROVIDER_ERROR,
        issued_utc=issued, retrieved_utc=utc_now_iso(),
        station=station, geometry=geometry, raw=raw,
        detail=None if raw else "VATSIM controller session has no ATIS text set",
        extra={"atis_code": rec.get("atis_code"), "frequency": rec.get("frequency")},
    )


def fetch_atis(icao: str, *, config: Optional[VatsimConfig] = None) -> Product:
    """VATSIM ATIS for one aerodrome (e.g. a controller logged on as OTHH_ATIS).

    Returns a DISABLED product (never live data) unless the production
    feature gate ``VATSIM_ATIS_ENABLED=1`` is set.
    """
    config = config or VatsimConfig.from_env()
    icao = (icao or "").strip().upper()
    if not config.enabled:
        return Product.disabled(
            "vatsim_atis", SOURCE, "ATIS", LABEL,
            "VATSIM ATIS redistribution is disabled by default "
            "(set VATSIM_ATIS_ENABLED=1 once licence/permission is documented).",
        )
    if not icao:
        return Product.unavailable("vatsim_atis", SOURCE, "ATIS", LABEL, "No ICAO station requested")
    key = ("vatsim_feed", config.base_url)
    try:
        feed = CACHE.get_or_fetch(key, config.ttl_s, lambda: _fetch_feed(config))
    except VatsimError as exc:
        stale = CACHE.get_stale(key)
        if stale:
            feed = stale
        else:
            return Product.provider_error("vatsim_atis", SOURCE, "ATIS", LABEL, str(exc))
    atis_list = feed.get("atis") or []
    matches = [a for a in atis_list if str(a.get("callsign", "")).upper().startswith(icao)]
    if not matches:
        return Product.unavailable(
            "vatsim_atis", SOURCE, "ATIS", LABEL,
            f"No VATSIM ATIS controller currently online for {icao}",
        )
    return _atis_to_product(matches[0])
