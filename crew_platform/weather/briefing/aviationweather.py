"""AviationWeather.gov adapter: METAR, TAF, SIGMET (free, no API key).

Uses the public Aviation Weather Center Data API
(``https://aviationweather.gov/api/data/...``), JSON format. This is the
only METAR/TAF/SIGMET upstream in this layer — NOAA GFS winds/temperature
aloft are reused from the existing :mod:`crew_platform.weather.service`
module (task: "do not reimplement the NOAA GFS route-weather engine").

Bounded requests only: METAR/TAF are requested per up-to-N station idents
(never an unbounded bbox scrape); international SIGMET is fetched for the
whole FIR set returned by the API and filtered to the route box client-side
because the upstream has no bbox parameter for isigmet.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .cache import CACHE
from .models import Product, ProductState, to_utc_iso, utc_now_iso

DEFAULT_BASE = "https://aviationweather.gov/api/data"
SOURCE = "AviationWeather.gov"


@dataclass(frozen=True)
class AwcConfig:
    base_url: str = field(default_factory=lambda: os.environ.get("AWC_API_BASE", DEFAULT_BASE))
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("AWC_TIMEOUT_S", "15")))
    metar_taf_ttl_s: float = field(default_factory=lambda: float(os.environ.get("AWC_METAR_TTL_S", "300")))
    sigmet_ttl_s: float = field(default_factory=lambda: float(os.environ.get("AWC_SIGMET_TTL_S", "300")))
    user_agent: str = "crew-efb-briefing/1.0"

    @classmethod
    def from_env(cls) -> "AwcConfig":
        return cls()


class AwcError(RuntimeError):
    """Upstream reachable but response errored/malformed."""


def _get_json(config: AwcConfig, path: str, params: dict[str, Any]) -> Any:
    import httpx

    url = f"{config.base_url.rstrip('/')}/{path}"
    try:
        with httpx.Client(timeout=config.timeout_s) as client:
            resp = client.get(url, params=params, headers={"User-Agent": config.user_agent, "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise AwcError(f"AviationWeather.gov request failed: {exc}") from exc
    if resp.status_code != 200:
        raise AwcError(f"AviationWeather.gov HTTP {resp.status_code} for {path}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AwcError(f"AviationWeather.gov returned non-JSON for {path}") from exc


def _metar_to_product(rec: dict[str, Any]) -> Product:
    station = rec.get("icaoId") or rec.get("id") or rec.get("station_id")
    raw = rec.get("rawOb") or rec.get("raw_text")
    issued = to_utc_iso(rec.get("reportTime") or rec.get("obsTime") or rec.get("receiptTime"))
    lat, lon = rec.get("lat"), rec.get("lon")
    geometry = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
    return Product(
        kind="metar", source=SOURCE, product="METAR", label="METAR",
        state=ProductState.OK if raw else ProductState.PROVIDER_ERROR,
        issued_utc=issued, retrieved_utc=utc_now_iso(),
        station=station, geometry=geometry, raw=raw,
        detail=None if raw else "AviationWeather.gov returned a record with no raw text",
    )


def fetch_metars(idents: list[str], *, config: Optional[AwcConfig] = None) -> list[Product]:
    """Current METAR for each ICAO ident (bounded list, cached per call)."""
    config = config or AwcConfig.from_env()
    idents = sorted({i.strip().upper() for i in idents if i and i.strip()})
    if not idents:
        return []
    key = ("metar", config.base_url, tuple(idents))

    def _fetch() -> list[dict[str, Any]]:
        data = _get_json(config, "metar", {"ids": ",".join(idents), "format": "json"})
        return data if isinstance(data, list) else []

    try:
        raw_recs = CACHE.get_or_fetch(key, config.metar_taf_ttl_s, _fetch)
    except AwcError as exc:
        stale = CACHE.get_stale(key)
        if stale:
            return [_stale_fallback(r, str(exc)) for r in stale]
        return [Product.provider_error("metar", SOURCE, "METAR", "METAR", str(exc))]
    by_station = {str(r.get("icaoId") or r.get("id") or "").upper(): r for r in raw_recs}
    out: list[Product] = []
    for ident in idents:
        rec = by_station.get(ident)
        if rec is None:
            out.append(Product.unavailable(
                "metar", SOURCE, "METAR", "METAR",
                f"No current METAR returned by AviationWeather.gov for {ident}",
            ))
            continue
        p = _metar_to_product(rec)
        p.station = p.station or ident
        out.append(p)
    return out


def _stale_fallback(rec: dict[str, Any], detail: str) -> Product:
    p = _metar_to_product(rec) if "rawOb" in rec or "raw_text" in rec else _taf_to_product(rec)
    p.state = ProductState.STALE
    p.stale = True
    p.detail = detail
    return p


def _taf_to_product(rec: dict[str, Any]) -> Product:
    station = rec.get("icaoId") or rec.get("id") or rec.get("station_id")
    raw = rec.get("rawTAF") or rec.get("raw_text")
    issued = to_utc_iso(rec.get("issueTime"))
    valid_from = to_utc_iso(rec.get("validTimeFrom"))
    valid_until = to_utc_iso(rec.get("validTimeTo"))
    lat, lon = rec.get("lat"), rec.get("lon")
    geometry = {"type": "Point", "coordinates": [lon, lat]} if lat is not None and lon is not None else None
    return Product(
        kind="taf", source=SOURCE, product="TAF", label="TAF",
        state=ProductState.OK if raw else ProductState.PROVIDER_ERROR,
        issued_utc=issued, valid_from_utc=valid_from, valid_until_utc=valid_until,
        retrieved_utc=utc_now_iso(), station=station, geometry=geometry, raw=raw,
        detail=None if raw else "AviationWeather.gov returned a record with no raw text",
    )


def fetch_tafs(idents: list[str], *, config: Optional[AwcConfig] = None) -> list[Product]:
    config = config or AwcConfig.from_env()
    idents = sorted({i.strip().upper() for i in idents if i and i.strip()})
    if not idents:
        return []
    key = ("taf", config.base_url, tuple(idents))

    def _fetch() -> list[dict[str, Any]]:
        data = _get_json(config, "taf", {"ids": ",".join(idents), "format": "json"})
        return data if isinstance(data, list) else []

    try:
        raw_recs = CACHE.get_or_fetch(key, config.metar_taf_ttl_s, _fetch)
    except AwcError as exc:
        stale = CACHE.get_stale(key)
        if stale:
            return [_stale_fallback(r, str(exc)) for r in stale]
        return [Product.provider_error("taf", SOURCE, "TAF", "TAF", str(exc))]
    by_station = {str(r.get("icaoId") or r.get("id") or "").upper(): r for r in raw_recs}
    out: list[Product] = []
    for ident in idents:
        rec = by_station.get(ident)
        if rec is None:
            out.append(Product.unavailable("taf", SOURCE, "TAF", "TAF", f"No current TAF returned for {ident}"))
            continue
        p = _taf_to_product(rec)
        p.station = p.station or ident
        out.append(p)
    return out


def _sigmet_to_product(rec: dict[str, Any], *, intl: bool) -> Product:
    raw = rec.get("rawAirSigmet") or rec.get("rawSigmet") or rec.get("raw_text") or rec.get("hazard")
    issued = to_utc_iso(rec.get("issueTime") or rec.get("validTimeFrom"))
    valid_from = to_utc_iso(rec.get("validTimeFrom"))
    valid_until = to_utc_iso(rec.get("validTimeTo"))
    geometry = None
    coords = rec.get("coords") or rec.get("geometry")
    if isinstance(coords, list) and coords and isinstance(coords[0], dict) and "lat" in coords[0]:
        ring = [[c.get("lon"), c.get("lat")] for c in coords]
        geometry = {"type": "Polygon", "coordinates": [ring]}
    elif isinstance(coords, dict):
        geometry = coords
    label = "SIGMET (international)" if intl else "SIGMET/AIRSIGMET (US domestic)"
    return Product(
        kind="sigmet", source=SOURCE, product=label, label=label,
        state=ProductState.OK if raw else ProductState.PROVIDER_ERROR,
        issued_utc=issued, valid_from_utc=valid_from, valid_until_utc=valid_until,
        retrieved_utc=utc_now_iso(), geometry=geometry, raw=raw,
        station=rec.get("firId") or rec.get("fir"),
        detail=None if raw else "AviationWeather.gov returned a SIGMET record with no raw text",
        extra={"hazard": rec.get("hazard")},
    )


def fetch_sigmets(*, intl: bool = True, config: Optional[AwcConfig] = None) -> list[Product]:
    """All currently active SIGMETs (bounded: whole current product set only,
    no polling loop, filtering to the route box is the caller's job).

    DECISION (follow-up from PR #16 review, kept a faithful 1:1 proxy):
    aviationweather.gov's ``/api/data/isigmet`` sometimes republishes the same
    advisory more than once (e.g. an amended SIGMET alongside its original).
    This adapter intentionally does NOT dedupe — identical-looking records can
    still be genuinely distinct advisories (same FIR/hazard reissued with a
    different validity window or amendment), and silently dropping one is a
    safety regression risk worse than an occasional visual duplicate. If
    dedup is ever wanted, it belongs in the UI/mapper layer
    (``efb-ui/src/crew/qatar/briefingMappers.js``) keyed on the full
    (firId, hazard, validTimeFrom, validTimeTo, rawSigmet) tuple — never here.
    """
    config = config or AwcConfig.from_env()
    path = "isigmet" if intl else "airsigmet"
    key = (path, config.base_url)

    def _fetch() -> list[dict[str, Any]]:
        data = _get_json(config, path, {"format": "json"})
        return data if isinstance(data, list) else []

    try:
        recs = CACHE.get_or_fetch(key, config.sigmet_ttl_s, _fetch)
    except AwcError as exc:
        stale = CACHE.get_stale(key)
        if stale:
            out: list[Product] = []
            for r in stale:
                p = _sigmet_to_product(r, intl=intl)
                p.state = ProductState.STALE
                p.stale = True
                p.detail = str(exc)
                out.append(p)
            return out
        return [Product.provider_error("sigmet", SOURCE, "SIGMET", "SIGMET", str(exc))]
    return [_sigmet_to_product(r, intl=intl) for r in recs]
