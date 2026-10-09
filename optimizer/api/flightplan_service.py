"""Flightplan service for the EFB — real SimBrief OFPs behind one API surface.

Consolidates the SimBrief flightplan pipeline for the crew EFB:

- Credentials come from the environment ONLY (``SIMBRIEF_USER`` /
  ``SIMBRIEF_PASS``). Nothing in this module or its routes hardcodes
  credentials, and none are ever logged or returned.
- Live OFP fetch goes through the existing ``SimBriefClient`` (JSON v2 API)
  with a short in-memory TTL cache so the UI can poll without hammering
  SimBrief.
- Imported plans are persisted to a small JSON store (one file, in-memory
  safe) so multiple saved plans are browsable and the last plan is restored
  on app start.

The navlog of the real v2 payload is a *list* of per-waypoint rows
(``ident`` / ``pos_lat`` / ``pos_long`` / ``altitude_feet`` / ``distance`` /
``time_leg`` / ``time_total`` / ``fuel_flow`` / ``fuel_leg`` /
``fuel_plan_onboard`` / ``wind_dir`` / ``wind_spd`` / ``fir`` /
``via_airway``). ``build_flightplan`` turns that into the rows the Qatar
flightplan table renders (WPT/AWY/FIR/LEG NM/REM NM/ETE/LEG ETE/ALT/WIND/
BURN/PLN FUEL) plus the hero and fuel-block facts.
"""

from __future__ import annotations

import json
import math
import os
import time
<<<<<<< HEAD
from dataclasses import dataclass, field
=======
from dataclasses import dataclass
>>>>>>> origin/main
from pathlib import Path
from threading import Lock
from typing import Any, Optional


class FlightplanError(RuntimeError):
    """SimBrief or plan-store failure that can be surfaced to the UI."""


class CredentialsMissingError(FlightplanError):
    """SIMBRIEF_USER is not configured in the environment."""


# ---------------------------------------------------------------------------
# Credentials — ENV VARS ONLY. Never logged, never returned in responses.
# ---------------------------------------------------------------------------

def simbrief_username() -> Optional[str]:
    """The configured SimBrief pilot username (``SIMBRIEF_USER``)."""
    return os.environ.get("SIMBRIEF_USER", "").strip() or None


def has_credentials() -> bool:
    return simbrief_username() is not None


def ensure_credentials() -> str:
    user = simbrief_username()
    if not user:
        raise CredentialsMissingError(
            "SimBrief is not configured on this bridge. "
            "Set SIMBRIEF_USER in the server environment."
        )
    return user


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _fl(altitude_ft: Optional[float]) -> Optional[int]:
    if altitude_ft is None:
        return None
    if altitude_ft > 1000:
        return int(round(altitude_ft / 100.0))
    return int(round(altitude_ft))


def _kg_factor(units: Optional[str]) -> float:
    """SimBrief returns weights in the pilot's configured units (``params.units``)."""
    if units and str(units).strip().upper().startswith("LB"):
        return 0.45359237
    return 1.0


def _hms_to_minutes(value: Any) -> Optional[float]:
    """'HH:MM:SS' / 'HH:MM' / bare minutes → minutes.

    Real v2 values are zero-padded ("13:20:00", "00:11:00") — split on the
    colon, never parse per-character.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if ":" in text:
        nums: list[float] = []
        for part in text.split(":"):
            n = _num(part)
            if n is None:
                return None
            nums.append(n)
        seconds = 0.0
        for n in nums:
            seconds = seconds * 60.0 + n
        return seconds / 60.0
    n = _num(text)
    return n


def _minutes_to_hhmm(minutes: Optional[float]) -> Optional[str]:
    if minutes is None:
        return None
    total = int(round(minutes))
    h, m = divmod(total, 60)
    return f"{h}:{m:02d}"


def _wind_cell(wind_dir: Any, wind_spd: Any) -> Optional[str]:
    d = _num(wind_dir)
    s = _num(wind_spd)
    if d is None and s is None:
        return None
    if d is not None and s is not None:
        return f"{int(round(d)):03d}/{int(round(s))}"
    return None


# ---------------------------------------------------------------------------
# Navlog → waypoint table rows
# ---------------------------------------------------------------------------

EARTH_RADIUS_NM = 3440.065


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r1, r2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(r1) * math.cos(r2) * math.sin(dlon / 2.0) ** 2
    )
    return EARTH_RADIUS_NM * 2.0 * math.asin(math.sqrt(min(1.0, h)))


def _navlog_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """The v2 payload carries ``navlog`` as a list; tolerate dict wrappers."""
    nav = raw.get("navlog")
    if isinstance(nav, list):
        return [r for r in nav if isinstance(r, dict)]
    if isinstance(nav, dict):
        for key in ("fix", "fixes", "waypoints"):
            value = nav.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    for key in ("fix", "fixes", "waypoints"):
        value = raw.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def _name_or_none(row: dict[str, Any]) -> Optional[str]:
    ident = str(row.get("ident") or "").strip().upper()
    name = str(row.get("name") or "").strip().upper()
    if name and name != ident:
        return name
    return None


def _build_rows(
    raw: dict[str, Any],
    *,
    origin: Optional[str],
    dest: Optional[str],
    origin_name: Optional[str],
    dest_name: Optional[str],
    kg: float,
    origin_lat: Optional[float] = None,
    origin_lon: Optional[float] = None,
) -> list[dict[str, Any]]:
    rows = [
        r for r in _navlog_rows(raw)
        if str(r.get("type") or "").lower() not in {"ltlg", ""}
        and str(r.get("ident") or "").strip()
    ]
    # Ensure the arrival airport row exists (v2 ends with the dest ``apt``).
    last_is_dest = bool(
        dest
        and rows
        and str(rows[-1].get("ident") or "").upper() == str(dest).upper()
    )
    if not last_is_dest and dest:
        rows.append({"ident": dest, "name": dest_name, "type": "apt"})

    out: list[dict[str, Any]] = []
    if origin:
        out.append({
            "ident": origin,
            "name": origin_name,
            "airway": None,
            "fir": None,
            "leg_nm": None,
            "rem_nm": None,
            "ete": None,
            "leg_ete": None,
            "alt": None,
            "wind": None,
            "burn": None,
            "plan_fuel_t": None,
            "lat": origin_lat,
            "lon": origin_lon,
            "stage": "DEP",
        })
    for row in rows:
        out.append({
            "ident": str(row.get("ident") or "").upper(),
            "name": _name_or_none(row),
            "airway": str(row.get("via_airway") or "").strip() or None,
            "fir": str(row.get("fir") or "").strip().upper() or None,
            "leg_nm": _num(row.get("distance")),
            "rem_nm": None,  # filled below
            "ete": _minutes_to_hhmm(_hms_to_minutes(row.get("time_total"))),
            "leg_ete": _minutes_to_hhmm(_hms_to_minutes(row.get("time_leg"))),
            "alt": _fl(_num(row.get("altitude_feet"))),
            "wind": _wind_cell(row.get("wind_dir"), row.get("wind_spd")),
            "burn": _num(row.get("fuel_leg")),
            "plan_fuel_t": _num(row.get("fuel_plan_onboard")),
            "lat": _num(row.get("pos_lat")),
            "lon": _num(row.get("pos_long")),
            "stage": str(row.get("stage") or "").upper() or None,
        })

    if not out:
        return out

    # REM NM: cumulative leg distance from the end.
    remaining = 0.0
    for row in reversed(out):
        if row["ident"] is not None and str(row["ident"]) == str(dest or "").upper():
            row["rem_nm"] = 0.0
            continue
        row["rem_nm"] = round(remaining, 0) if remaining > 0 else None
        leg = row["leg_nm"]
        if leg:
            remaining += leg
    # The departure row carries the full route distance.
    if len(out) > 1:
        out[0]["rem_nm"] = round(remaining, 0) if remaining > 0 else None

    # BURN / PLN FUEL → tonnes when the payload is not already kg-scaled.
    for row in out:
        if row["burn"] is not None:
            row["burn"] = round(row["burn"] * kg, 0)
        if row["plan_fuel_t"] is not None:
            row["plan_fuel_t"] = round(row["plan_fuel_t"] * kg / 1000.0, 1)
    return out


# ---------------------------------------------------------------------------
# Raw OFP → flightplan view dict (pure — unit-testable, no I/O)
# ---------------------------------------------------------------------------

def build_flightplan(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw SimBrief JSON v2 payload into the EFB flightplan view."""
    raw = raw or {}
    general = raw.get("general") if isinstance(raw.get("general"), dict) else {}
    times = raw.get("times") if isinstance(raw.get("times"), dict) else {}
    aircraft = raw.get("aircraft") if isinstance(raw.get("aircraft"), dict) else {}
    origin_sec = raw.get("origin") if isinstance(raw.get("origin"), dict) else {}
    dest_sec = raw.get("destination") if isinstance(raw.get("destination"), dict) else {}
    alt_sec = raw.get("alternate")
    if isinstance(alt_sec, dict):
        alts = [alt_sec]
    elif isinstance(alt_sec, list):
        alts = [a for a in alt_sec if isinstance(a, dict)]
    else:
        alts = []
    fuel = raw.get("fuel") if isinstance(raw.get("fuel"), dict) else {}
    weights = raw.get("weights") if isinstance(raw.get("weights"), dict) else {}
    params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
    files = raw.get("files") if isinstance(raw.get("files"), dict) else {}

    kg = _kg_factor(params.get("units"))

    origin = str(origin_sec.get("icao_code") or "").upper() or None
    dest = str(dest_sec.get("icao_code") or "").upper() or None
    alternate = str(alts[0].get("icao_code") or "").upper() if alts else None

    flight_number = str(general.get("flight_number") or "").strip() or None
    airline_icao = str(general.get("icao_airline") or "").strip().upper() or None
    callsign = f"{airline_icao}{flight_number}" if airline_icao and flight_number else None

    aircraft_type = str(aircraft.get("name") or "").strip() or None
    aircraft_icao = str(aircraft.get("icaocode") or aircraft.get("icao_code") or "").strip().upper() or None
    reg = str(aircraft.get("reg") or "").strip().upper() or None

    fuel_block = _num(fuel.get("plan_ramp"))
    fuel_takeoff = _num(fuel.get("plan_takeoff"))
    fuel_trip = _num(fuel.get("enroute_burn"))
    fuel_landing = _num(fuel.get("plan_landing"))
    fuel_taxi = _num(fuel.get("taxi"))
    fuel_reserve = _num(fuel.get("reserve"))
    fuel_alt_burn = _num(fuel.get("alternate_burn"))
    fuel_extra = _num(fuel.get("extra"))
    fuel_contingency = _num(fuel.get("contingency"))

    tow = _num(weights.get("est_tow"))
    zfw = _num(weights.get("est_zfw"))
    ldw = _num(weights.get("est_ldw"))
    oew = _num(weights.get("oew"))

    def _t(v: Optional[float]) -> Optional[float]:
        return round(v * kg / 1000.0, 1) if v is not None else None

    reserve_alt = None
    if fuel_reserve is not None:
        reserve_alt = fuel_reserve
        if fuel_alt_burn is not None:
            reserve_alt += fuel_alt_burn

    files_pdf = files.get("pdf") if isinstance(files.get("pdf"), dict) else {}
    files_dir = str(files.get("directory") or "").strip()
    pdf_url = None
    if files_dir and files_pdf.get("link"):
        pdf_url = files_dir.rstrip("/") + "/" + str(files_pdf["link"])

    xml_file = str(params.get("xml_file") or "").strip() or None
    airac = str(params.get("airac") or "").strip() or None
    time_generated = str(params.get("time_generated") or "").strip() or None
    static_id = str(params.get("static_id") or "").strip() or None

    sched_block_min = _hms_to_minutes(times.get("sched_block")) or _hms_to_minutes(times.get("est_block"))

    return {
        "source": "SimBrief",
        "static_id": static_id,
        "generated_at": time_generated,
        "airac": airac,
        "flight_number": flight_number,
        "callsign": callsign,
        "airline_icao": airline_icao,
        "aircraft": aircraft_type,
        "aircraft_icao": aircraft_icao,
        "registration": reg,
        "origin": origin,
        "origin_name": str(origin_sec.get("name") or "").strip() or None,
        "origin_lat": _num(origin_sec.get("pos_lat")),
        "origin_lon": _num(origin_sec.get("pos_long")),
        "destination": dest,
        "destination_name": str(dest_sec.get("name") or "").strip() or None,
        "destination_lat": _num(dest_sec.get("pos_lat")),
        "destination_lon": _num(dest_sec.get("pos_long")),
        "alternate": alternate,
        "route": str(general.get("route") or "").strip() or None,
        "route_distance_nm": _num(general.get("route_distance")),
        "gc_distance_nm": _num(general.get("gc_distance")),
        "std_utc": str(times.get("sched_out") or "").strip() or None,
        "sta_utc": str(times.get("sched_in") or "").strip() or None,
        "ete_min": sched_block_min,
        "cruise_fl": _fl(_num(general.get("initial_altitude"))),
        "cruise_mach": _num(general.get("cruise_mach")),
        "cost_index": _num(general.get("costindex")),
        "fuel": {
            "block": _t(fuel_block),
            "takeoff": _t(fuel_takeoff),
            "trip": _t(fuel_trip),
            "landing": _t(fuel_landing),
            "taxi": _t(fuel_taxi),
            "reserve": _t(fuel_reserve),
            "reserve_alt": _t(reserve_alt),
            "extra": _t(fuel_extra),
            "contingency": _t(fuel_contingency),
        },
        "weights": {
            "tow": _t(tow),
            "zfw": _t(zfw),
            "ldw": _t(ldw),
            "oew": _t(oew),
        },
        "pax_count": _num(weights.get("pax_count")),
        "cargo_kg": round(_num(weights.get("cargo")) * kg, 0) if _num(weights.get("cargo")) is not None else None,
        "pdf_url": pdf_url,
        "xml_file": xml_file,
        "waypoints": _build_rows(
            raw,
            origin=origin,
            dest=dest,
            origin_name=str(origin_sec.get("name") or "").strip() or None,
            dest_name=str(dest_sec.get("name") or "").strip() or None,
            kg=kg,
            origin_lat=_num(origin_sec.get("pos_lat")),
            origin_lon=_num(origin_sec.get("pos_long")),
        ),
    }


# ---------------------------------------------------------------------------
# Live fetch with TTL cache
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    fetched_at: float
    raw: dict[str, Any]
    view: dict[str, Any]


_cache: _CacheEntry | None = None
_cache_lock = Lock()
DEFAULT_TTL_SECONDS = 600.0


def fetch_flightplan_view(
    *,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    force: bool = False,
    username: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch (or return cached) live SimBrief OFP as a flightplan view dict.

    Synchronous by contract — call it from a worker thread (``asyncio.to_thread``)
    in async routes. ``username`` is only used as an explicit override
    (tests); production paths pass nothing and the service uses
    ``SIMBRIEF_USER`` from the environment.
    """
    global _cache
    now = time.time()
    if not force and _cache is not None and (now - _cache.fetched_at) < ttl_seconds:
        return _cache.view

    user = (username or "").strip() or ensure_credentials()

    import asyncio

    from data_fetcher.simbrief.simbrief_client import SimBriefClient

    client = SimBriefClient(timeout_seconds=30.0)
    try:
        raw = asyncio.run(client.fetch_latest_ofp(username=user))
    except Exception as exc:  # SimBriefClientError / httpx errors
        raise FlightplanError(f"SimBrief fetch failed: {exc}") from exc

    view = build_flightplan(raw)
    view["fetched_at"] = now
    with _cache_lock:
        _cache = _CacheEntry(fetched_at=now, raw=raw, view=view)
    return view


def clear_fetch_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


# ---------------------------------------------------------------------------
# Plan store — multiple saved plans, JSON file, thread-safe
# ---------------------------------------------------------------------------

def plan_key(plan: dict[str, Any]) -> str:
    origin = str(plan.get("origin") or "?").upper()
    dest = str(plan.get("destination") or "?").upper()
    flight = str(plan.get("flight_number") or "LOC").upper()
    return f"{origin}{dest}-{flight}"


def _store_path() -> Path:
    base = os.environ.get("EFB_DATA_DIR", "").strip()
    if not base:
        base = str(Path(__file__).resolve().parents[1] / "data")
    path = Path(base) / "efb" / "flightplans.json"
    return path


_store_lock = Lock()


def _load_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return {"last_plan": None, "plans": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"last_plan": None, "plans": {}}
        data.setdefault("plans", {})
        data.setdefault("last_plan", None)
        return data
    except (OSError, ValueError):
        return {"last_plan": None, "plans": {}}


def _save_store(data: dict[str, Any]) -> None:
    path = _store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise FlightplanError(f"Could not persist flightplan store: {exc}") from exc


def save_plan(view: dict[str, Any]) -> dict[str, Any]:
    """Persist one flightplan view; returns the stored plan envelope."""
    key = plan_key(view)
    with _store_lock:
        data = _load_store()
        envelope = {
            "key": key,
            "saved_at": time.time(),
            "flightplan": view,
        }
        data["plans"][key] = envelope
        data["last_plan"] = key
        _save_store(data)
    return envelope


def list_plans() -> dict[str, Any]:
    data = _load_store()
    plans = data["plans"]
    items = sorted(
        (p for p in plans.values() if isinstance(p, dict)),
        key=lambda p: p.get("saved_at") or 0.0,
        reverse=True,
    )
    return {"last_plan": data.get("last_plan"), "plans": items}


def get_plan(key: str) -> dict[str, Any] | None:
    data = _load_store()
    return data["plans"].get(key)


def delete_plan(key: str) -> bool:
    """Remove one saved plan; adjusts last_plan. Returns False when unknown."""
    with _store_lock:
        data = _load_store()
        if key not in data["plans"]:
            return False
        data["plans"].pop(key, None)
        if data.get("last_plan") == key:
            remaining = sorted(
                (p for p in data["plans"].values() if isinstance(p, dict)),
                key=lambda p: p.get("saved_at") or 0.0,
                reverse=True,
            )
            data["last_plan"] = remaining[0]["key"] if remaining else None
        _save_store(data)
    return True
