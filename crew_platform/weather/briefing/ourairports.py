"""OurAirports metadata — airport identity/coordinates ONLY.

Task requirement: OurAirports may supply airport metadata only (name,
ICAO/IATA, lat/lon, elevation, type) and the non-authoritative nature of
the dataset must stay visible wherever it is shown. It is NEVER used as a
weather or NOTAM source. Data is the free CSV
(https://davidmegginson.github.io/ourairports-data/airports.csv), fetched
once and cached in-process; this is reference/static data, not a live feed.
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from .cache import CACHE

DEFAULT_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
ATTRIBUTION = (
    "Airport metadata: OurAirports (https://ourairports.com/data/) — "
    "community-maintained, NON-AUTHORITATIVE; verify against official AIP/NOTAM sources."
)


@dataclass(frozen=True)
class OurAirportsConfig:
    url: str = field(default_factory=lambda: os.environ.get("OURAIRPORTS_CSV_URL", DEFAULT_URL))
    timeout_s: float = field(default_factory=lambda: float(os.environ.get("OURAIRPORTS_TIMEOUT_S", "30")))
    ttl_s: float = field(default_factory=lambda: float(os.environ.get("OURAIRPORTS_TTL_S", str(24 * 3600))))

    @classmethod
    def from_env(cls) -> "OurAirportsConfig":
        return cls()


class OurAirportsError(RuntimeError):
    pass


def _fetch_csv(config: OurAirportsConfig) -> dict[str, dict[str, Any]]:
    import httpx

    try:
        with httpx.Client(timeout=config.timeout_s, follow_redirects=True) as client:
            resp = client.get(config.url)
    except httpx.HTTPError as exc:
        raise OurAirportsError(f"OurAirports request failed: {exc}") from exc
    if resp.status_code != 200:
        raise OurAirportsError(f"OurAirports HTTP {resp.status_code}")
    reader = csv.DictReader(io.StringIO(resp.text))
    by_ident: dict[str, dict[str, Any]] = {}
    for row in reader:
        ident = (row.get("ident") or "").strip().upper()
        if ident:
            by_ident[ident] = row
    return by_ident


def lookup(icao: str, *, config: Optional[OurAirportsConfig] = None) -> Optional[dict[str, Any]]:
    """Airport metadata for one ICAO ident, or ``None`` if not found/unreachable.

    Every non-empty result carries ``attribution`` + ``authoritative: False``
    so the UI cannot drop the non-authoritative disclosure.
    """
    config = config or OurAirportsConfig.from_env()
    icao = (icao or "").strip().upper()
    if not icao:
        return None
    key = ("ourairports", config.url)
    try:
        table = CACHE.get_or_fetch(key, config.ttl_s, lambda: _fetch_csv(config))
    except OurAirportsError:
        table = CACHE.get_stale(key) or {}
    row = table.get(icao)
    if not row:
        return None
    lat: Optional[float]
    lon: Optional[float]
    try:
        lat = float(row.get("latitude_deg") or "nan")
        lon = float(row.get("longitude_deg") or "nan")
    except ValueError:
        lat = lon = None
    return {
        "icao": icao,
        "iata": row.get("iata_code") or None,
        "name": row.get("name"),
        "type": row.get("type"),
        "lat": lat,
        "lon": lon,
        "elevation_ft": row.get("elevation_ft") or None,
        "municipality": row.get("municipality") or None,
        "country": row.get("iso_country") or None,
        "authoritative": False,
        "attribution": ATTRIBUTION,
    }
