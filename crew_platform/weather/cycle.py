"""NOAA NOMADS g2sub client for GFS 0.25 deg cycles.

The legacy ``noaa-gfs-bdp-pds`` S3 bucket was retired; GFS 0.25 deg GRIB2 is
now served by the NCEP NOMADS *g2sub* CGI (``filter_gfs_0p25.pl``,
NOMADS 1.2 / g2sub 1.1). This module wraps that endpoint with:

* cycle discovery (browse ``/gfs.YYYYMMDD`` -> ``HH`` -> ``atmos``),
* forecast-file discovery (the ``atmos`` page lists ``f000``..``f384``),
* bounded region-subset downloads (``subregion=on`` + lat/lon box),
* size/timeout bounds, no credentials (public endpoint).

Endpoint behaviour verified 2026-10-06 against cycle 20261005/18:
* the ``file`` form control is a multi-select but the CGI processes only a
  single forecast file per request (a second ``file=`` is ignored), so each
  forecast hour is its own bounded subset;
* a region subset of 25E-75E x 10N-65N at 6 levels x 5 vars is ~1.9 MB and
  returns in ~2 s.
"""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Sequence

# Public endpoint; env-overridable for tests / regional mirrors.
DEFAULT_GFS_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

#: GFS 0.25 deg standard pressure levels we ingest (hPa, thin -> thick).
#: Spans FL050 (925 hPa) .. FL450 (50 hPa) for full flight-level coverage.
INGEST_LEVELS_MB = (50, 100, 150, 200, 250, 300, 400, 500, 700, 850, 925)

#: GRIB2 parameter names -> g2sub ``var_*`` checkbox stem.
INGEST_VARS = {
    "u": "UGRD",    # U-component of wind, m/s
    "v": "VGRD",    # V-component of wind, m/s
    "t": "TMP",     # temperature, K
    "r": "RH",      # relative humidity, %
    "w": "VVEL",    # vertical velocity, m/s
    "cape": "CAPE", # surface-based CAPE, J/kg (surface level)
    "gh": "HGT",    # geopotential height, m (terrain/tropopause proxy)
}


class GfsError(RuntimeError):
    """Base error for GFS ingest."""


class GfsCycleNotFound(GfsError):
    """The requested cycle/file is not (yet) available."""


class GfsDownloadError(GfsError):
    """A bounded download failed or exceeded its limits."""


@dataclass(frozen=True)
class GfsConfig:
    """Endpoint + safety bounds. Everything is env-overridable."""

    base_url: str = field(
        default_factory=lambda: os.environ.get("GFS_G2SUB_URL", DEFAULT_GFS_BASE)
    )
    #: hard cap on a single subset transfer (bytes).
    max_download_bytes: int = field(
        default_factory=lambda: int(os.environ.get("GFS_MAX_DOWNLOAD_BYTES", str(60 * 1024 * 1024)))
    )
    #: per-request timeout (seconds).
    timeout_s: float = field(
        default_factory=lambda: float(os.environ.get("GFS_TIMEOUT_S", "300"))
    )
    #: region padding in degrees (added around the route box).
    region_pad_deg: float = field(
        default_factory=lambda: float(os.environ.get("GFS_REGION_PAD_DEG", "3"))
    )
    #: user-agent identifying the client (polite; public endpoint).
    user_agent: str = "crew-efb-gfs/1.0"

    @classmethod
    def from_env(cls) -> "GfsConfig":
        return cls()


def _get(config: GfsConfig, url: str) -> bytes:
    import httpx

    with httpx.Client(timeout=config.timeout_s, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": config.user_agent})
    return resp.status_code, resp.content if resp.status_code == 200 else None


def _parse_listing(html: str) -> list[str]:
    """All ``href=...dir=<q>`` targets in a g2sub listing page (decoded)."""
    found = re.findall(r'href="[^"]*?dir=([^"]+)"', html)
    out = []
    for q in found:
        out.append(urllib.parse.unquote(q).rstrip("/"))
    return out


def list_cycles(config: GfsConfig) -> list[dict[str, str]]:
    """Available GFS cycle dirs, most recent first.

    Returns ``[{"dir": "/gfs.20261005", "date": "20261005"}, ...]``.
    """
    code, body = _get(config, config.base_url)
    if code != 200 or body is None:
        raise GfsCycleNotFound(f"GFS index returned HTTP {code}")
    out = []
    for d in _parse_listing(body.decode("utf-8", "replace")):
        m = re.match(r"^/gfs\.(\d{8})$", d)
        if m:
            out.append({"dir": d, "date": m.group(1)})
    out.sort(key=lambda c: c["date"], reverse=True)
    return out


def list_cycle_hours(config: GfsConfig, date: str) -> list[str]:
    """Available run hours (``00/06/12/18``) for a cycle date, newest first."""
    url = config.base_url + "?" + urllib.parse.urlencode({"dir": f"/gfs.{date}"})
    code, body = _get(config, url)
    if code != 200 or body is None:
        raise GfsCycleNotFound(f"GFS cycle dir returned HTTP {code}")
    hours = []
    for d in _parse_listing(body.decode("utf-8", "replace")):
        m = re.match(rf"^/gfs\.{re.escape(date)}/(\d{{2}})$", d)
        if m:
            hours.append(m.group(1))
    hours.sort(reverse=True)
    return hours


def list_forecast_files(
    config: GfsConfig, date: str, hour: str, horizon_h: int = 36
) -> list[int]:
    """Forecast-hour offsets (0..horizon_h) available for a run.

    The ``atmos`` page lists every ``f###`` file (0..384); we return the
    integer offsets up to ``horizon_h`` that are present, ascending.
    """
    url = config.base_url + "?" + urllib.parse.urlencode(
        {"dir": f"/gfs.{date}/{hour}/atmos"}
    )
    code, body = _get(config, url)
    if code != 200 or body is None:
        raise GfsCycleNotFound(f"GFS atmos dir returned HTTP {code}")
    html = body.decode("utf-8", "replace")
    hh = f"{int(hour):02d}"
    offsets = set()
    for m in re.finditer(
        rf'gfs\.t{hh}z\.pgrb2\.0p25\.f(\d{{3}})', html
    ):
        off = int(m.group(1))
        if 0 <= off <= horizon_h:
            offsets.add(off)
    return sorted(offsets)


def _atmos_dir(date: str, hour: str) -> str:
    return f"/gfs.{date}/{int(hour):02d}/atmos"


def _forecast_file(date: str, hour: str, offset: int) -> str:
    hh = f"{int(hour):02d}"
    return f"gfs.t{hh}z.pgrb2.0p25.f{offset:03d}"


def build_region_box(
    lats: Sequence[float], lons: Sequence[float], pad_deg: float
) -> tuple[float, float, float, float]:
    """(leftlon, rightlon, toplat, bottomlat) around a lat/lon cloud + pad.

    Longitude is left *unwrapped*: the caller must have already split a route
    that crosses the antimeridian into one box per sub-route (the g2sub box
    cannot wrap). Latitudes are clamped to [-90, 90].
    """
    if not lats or not lons:
        raise ValueError("empty route coordinates")
    lat_min = max(-90.0, min(float(x) for x in lats) - pad_deg)
    lat_max = min(90.0, max(float(x) for x in lats) + pad_deg)
    lon_min = min(float(x) for x in lons) - pad_deg
    lon_max = max(float(x) for x in lons) + pad_deg
    if lon_min < -180.0 or lon_max > 180.0:
        raise ValueError("region box crosses the antimeridian — split the route first")
    return (lon_min, lon_max, lat_max, lat_min)


def download_subset(
    config: GfsConfig,
    date: str,
    hour: str,
    offset: int,
    *,
    leftlon: float,
    rightlon: float,
    toplat: float,
    bottomlat: float,
    levels_mb: Sequence[int] = INGEST_LEVELS_MB,
    vars_: Sequence[str] = tuple(INGEST_VARS.values()),
) -> bytes:
    """Download one bounded forecast-hour subset as concatenated GRIB2 bytes.

    Enforces :attr:`GfsConfig.max_download_bytes` and the request timeout.
    Raises :class:`GfsCycleNotFound` / :class:`GfsDownloadError`.
    """
    import httpx

    params: list[tuple[str, str]] = [
        ("dir", _atmos_dir(date, hour)),
        ("file", _forecast_file(date, hour, offset)),
        ("subregion", "on"),
        ("leftlon", f"{leftlon:.3f}"),
        ("rightlon", f"{rightlon:.3f}"),
        ("toplat", f"{toplat:.3f}"),
        ("bottomlat", f"{bottomlat:.3f}"),
    ]
    for mb in levels_mb:
        params.append((f"lev_{mb}_mb", "on"))
    for stem in vars_:
        params.append((f"var_{stem}", "on"))
    if "CAPE" in vars_:
        params.append(("lev_surface", "on"))
    url = config.base_url + "?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": config.user_agent,
        "Range": f"bytes=0-{config.max_download_bytes}",
    }
    try:
        with httpx.Client(timeout=config.timeout_s, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code in (400, 404):
                    raise GfsCycleNotFound(
                        f"GFS subset HTTP {resp.status_code} (cycle/file not available)"
                    )
                if resp.status_code >= 400:
                    raise GfsDownloadError(f"GFS subset HTTP {resp.status_code}")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    total += len(chunk)
                    if total > config.max_download_bytes:
                        raise GfsDownloadError(
                            f"subset exceeds {config.max_download_bytes} bytes — "
                            "shrink the region or horizon"
                        )
                    chunks.append(chunk)
    except GfsError:
        raise
    except httpx.HTTPError as exc:
        raise GfsDownloadError(f"GFS subset transfer failed: {exc}") from exc
    data = b"".join(chunks)
    if not data:
        raise GfsDownloadError("empty subset (cycle still being produced?)")
    return data
