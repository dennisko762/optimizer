"""EUMETSAT lightning adapter: MTG-LI Level 2 Lightning Flashes (LFL).

Official collection: ``EO:EUM:DAT:0691`` (MTG-LI L2 LFL) via the EUMETSAT
Data Store API (``https://api.eumetsat.int``). This is Level 2 — NOT Level 1
raw/radiance data, per the task requirement. Credentials (consumer key/
secret) come ONLY from env (``EUMETSAT_CONSUMER_KEY`` /
``EUMETSAT_CONSUMER_SECRET``); the live adapter is feature-gated off
(:func:`fetch_lightning` returns ``disabled``) until
``EUMETSAT_LIGHTNING_ENABLED=1`` is set AND credentials are present —
access/redistribution for this collection must be confirmed before that
flag is flipped in any real deployment.

Never consumes Lufthansa Virtual private endpoints, and never assumes any
positional tuple value is "intensity" — MTG-LI LFL records expose documented
named fields (flash time, lat/lon, radiance) and only those are surfaced.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .cache import CACHE
from .models import Product, ProductState, to_utc_iso, utc_now_iso

COLLECTION_ID = "EO:EUM:DAT:0691"
PRODUCT_LABEL = "MTG-LI Level 2 Lightning Flashes (LFL)"
SOURCE = "EUMETSAT Data Store"
TOKEN_URL = "https://api.eumetsat.int/token"
SEARCH_URL = "https://api.eumetsat.int/data/search-products/os"


@dataclass(frozen=True)
class EumetsatConfig:
    consumer_key: str = field(default_factory=lambda: os.environ.get("EUMETSAT_CONSUMER_KEY", "").strip())
    consumer_secret: str = field(default_factory=lambda: os.environ.get("EUMETSAT_CONSUMER_SECRET", "").strip())
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("EUMETSAT_TIMEOUT_S", "20")))
    ttl_s: float = field(default_factory=lambda: float(os.environ.get("EUMETSAT_LIGHTNING_TTL_S", "300")))
    #: explicit live-adapter feature gate — OFF until access/redistribution
    #: for EO:EUM:DAT:0691 is confirmed for this deployment.
    enabled: bool = field(default_factory=lambda: os.environ.get("EUMETSAT_LIGHTNING_ENABLED", "0").strip() == "1")

    @classmethod
    def from_env(cls) -> "EumetsatConfig":
        return cls()

    @property
    def has_credentials(self) -> bool:
        return bool(self.consumer_key and self.consumer_secret)


class EumetsatError(RuntimeError):
    pass


def _get_token(config: EumetsatConfig) -> str:
    import httpx

    key = ("eumetsat_token", config.consumer_key)
    cached = CACHE.get(key)
    if cached:
        return cached
    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.post(
                TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(config.consumer_key, config.consumer_secret),
            )
    except httpx.HTTPError as exc:
        raise EumetsatError(f"EUMETSAT token request failed: {exc}") from exc
    if resp.status_code != 200:
        raise EumetsatError(f"EUMETSAT token HTTP {resp.status_code}")
    body = resp.json()
    token = body.get("access_token")
    if not token:
        raise EumetsatError("EUMETSAT token response missing access_token")
    ttl = max(60.0, float(body.get("expires_in", 3600)) - 60.0)
    CACHE.set(key, token, ttl)
    return token


def _search(config: EumetsatConfig, box: tuple[float, float, float, float], start_iso: str, end_iso: str) -> dict[str, Any]:
    import httpx

    token = _get_token(config)
    leftlon, rightlon, bottomlat, toplat = box
    geo = f"POLYGON(({leftlon} {bottomlat},{rightlon} {bottomlat},{rightlon} {toplat},{leftlon} {toplat},{leftlon} {bottomlat}))"
    params = {
        "pi": COLLECTION_ID,
        "geo": geo,
        "dtstart": start_iso,
        "dtend": end_iso,
        "format": "json",
    }
    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.get(SEARCH_URL, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise EumetsatError(f"EUMETSAT search request failed: {exc}") from exc
    if resp.status_code != 200:
        raise EumetsatError(f"EUMETSAT search HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise EumetsatError("EUMETSAT search returned non-JSON") from exc


def fetch_lightning(
    box: tuple[float, float, float, float],
    *,
    lookback_minutes: float = 30.0,
    config: Optional[EumetsatConfig] = None,
) -> Product:
    """Lightning flash activity for a bounded region/time window.

    ``box`` is (leftlon, rightlon, bottomlat, toplat) — same convention as
    the existing GFS route box. Always bounded (region + short lookback),
    never an unbounded global pull.
    """
    config = config or EumetsatConfig.from_env()
    if not config.enabled:
        return Product.disabled(
            "lightning", SOURCE, PRODUCT_LABEL, PRODUCT_LABEL,
            f"EUMETSAT {COLLECTION_ID} live adapter is disabled by default "
            "(set EUMETSAT_LIGHTNING_ENABLED=1 once access/redistribution is confirmed).",
        )
    if not config.has_credentials:
        return Product.unavailable(
            "lightning", SOURCE, PRODUCT_LABEL, PRODUCT_LABEL,
            "EUMETSAT_CONSUMER_KEY/EUMETSAT_CONSUMER_SECRET not configured.",
        )
    now = time.time()
    start_iso = to_utc_iso(now - lookback_minutes * 60.0)
    end_iso = to_utc_iso(now)
    assert start_iso is not None and end_iso is not None, (
        "to_utc_iso(time.time()) must always succeed for a numeric input"
    )
    key = ("eumetsat_lightning", box, round(now / 60.0))  # 1-min cache granularity
    try:
        body = CACHE.get_or_fetch(key, config.ttl_s, lambda: _search(config, box, start_iso, end_iso))
    except EumetsatError as exc:
        stale = CACHE.get_stale(key)
        if stale is not None:
            body = stale
            return _to_product(body, box, start_iso, end_iso, stale=True, detail=str(exc))
        return Product.provider_error("lightning", SOURCE, PRODUCT_LABEL, PRODUCT_LABEL, str(exc))
    return _to_product(body, box, start_iso, end_iso, stale=False, detail=None)


def _to_product(body: dict[str, Any], box, start_iso, end_iso, *, stale: bool, detail: Optional[str]) -> Product:
    features = body.get("features") or []
    leftlon, rightlon, bottomlat, toplat = box
    geometry = {
        "type": "Polygon",
        "coordinates": [[[leftlon, bottomlat], [rightlon, bottomlat], [rightlon, toplat], [leftlon, toplat], [leftlon, bottomlat]]],
    }
    return Product(
        kind="lightning", source=SOURCE, product=PRODUCT_LABEL, label=PRODUCT_LABEL,
        state=ProductState.STALE if stale else ProductState.OK,
        issued_utc=end_iso, valid_from_utc=start_iso, valid_until_utc=end_iso,
        retrieved_utc=utc_now_iso(), stale=stale, geometry=geometry,
        detail=detail,
        extra={"collection": COLLECTION_ID, "flash_count": len(features)},
    )
