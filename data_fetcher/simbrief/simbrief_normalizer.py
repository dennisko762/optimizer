from __future__ import annotations

from datetime import datetime, timezone as tz
from typing import Any

from data_fetcher.simbrief.simbrief_models import SimBriefPerformanceSeed
from optimizer.number_utils import parse_number


def to_performance_seed(raw: dict) -> SimBriefPerformanceSeed:
    general = _section(raw, "general")
    times = _section(raw, "times")
    aircraft = _section(raw, "aircraft")
    origin = _section(raw, "origin")
    destination = _section(raw, "destination")
    alternate = _section(raw, "alternate")
    weights = _section(raw, "weights")
    fuel = _section(raw, "fuel")

    altitude_ft = _to_int(general.get("initial_altitude"))
    planned_fl = _altitude_to_fl(altitude_ft)

    callsign = _clean_str(general.get("callsign"))

    flight_number = _clean_str(
        general.get("flight_number")
        or general.get("fltno")
        or general.get("flight_no")
    )

    airline_icao = _clean_str(
        general.get("airline_icao")
        or general.get("icao_airline")
    )

    airline_iata = _clean_str(
        general.get("airline_iata")
        or general.get("iata_airline")
    )

    return SimBriefPerformanceSeed(
        aircraft=_clean_str(
            aircraft.get("icaocode")
            or aircraft.get("icao_code")
            or aircraft.get("type")
        ),

        aircraft_registration=_clean_str(
            aircraft.get("reg")
            or aircraft.get("registration")
            or general.get("aircraft_registration")
            or general.get("registration")
            or general.get("reg")
        ),

        callsign=callsign,
        flight_number=flight_number,
        airline_icao=airline_icao,
        airline_iata=airline_iata,

        origin=_clean_str(origin.get("icao_code")),
        destination=_clean_str(destination.get("icao_code")),
        alternate=_clean_str(alternate.get("icao_code")),

        destination_lat=_to_float(destination.get("pos_lat")),
        destination_lon=_to_float(destination.get("pos_long")),

        route_distance_nm=_to_float(general.get("route_distance")),
        planned_block_time_min=_duration_to_minutes(
            times.get("est_block")
            or times.get("sched_block")
            or times.get("block_time")
            or general.get("est_block")
            or general.get("block_time")
            or times.get("est_time_enroute")
        ),

        planned_cruise_altitude_ft=altitude_ft,
        planned_cruise_fl=planned_fl,
        planned_mach=_to_float(general.get("cruise_mach")),
        cost_index=_to_float(general.get("costindex")),

        tow_kg=_to_float(weights.get("est_tow")),
        zfw_kg=_to_float(weights.get("est_zfw")),
        landing_weight_kg=_to_float(weights.get("est_ldw")),

        block_fuel_kg=_to_float(fuel.get("plan_ramp")),
        trip_fuel_kg=_to_float(fuel.get("enroute_burn")),
        reserve_fuel_kg=_to_float(fuel.get("reserve")),

        pax_count=_to_int(
            weights.get("pax_count")
            or weights.get("passenger_count")
            or general.get("pax_count")
        ),
        cargo_kg=_to_float(
            weights.get("cargo_weight")
            or weights.get("cargo_kg")
        ),

        sibt_utc=_unix_or_seconds_to_hhmm(
            times.get("est_in")
            or times.get("estin")
            or times.get("sched_in")
            or times.get("schedin")
            or general.get("est_in")
            or general.get("sched_in")
        ),
        sobt_utc=_unix_or_seconds_to_hhmm(
            times.get("est_out")
            or times.get("estout")
            or times.get("sched_out")
            or times.get("schedout")
            or general.get("est_out")
            or general.get("sched_out")
        ),

        planned_wind_component_kt=_parse_pm_value(
            general.get("avg_wind_comp")      # SimBrief v2 primary key
            or general.get("wind_component")
            or general.get("wind_comp")
            or general.get("windcomp")
            or general.get("avg_wind_component")
        ),
        planned_isa_deviation_c=_parse_pm_value(
            general.get("isa_dev")            # SimBrief v2 primary key
            or general.get("isa_deviation")
            or general.get("isadev")
            or general.get("isa")
        ),
        **_unpack_avg_wind(general),
    )


def _unpack_avg_wind(general: dict) -> dict:
    """Helper to unpack average wind into two separate fields."""
    raw = (
        general.get("avg_wind_comp")
        or general.get("average_wind")
        or general.get("avg_wind")
        or general.get("avgwind")
    )
    direction, speed = _parse_avg_wind(raw)
    return {
        "planned_avg_wind_direction_deg": direction,
        "planned_avg_wind_speed_kt": speed,
    }


def _parse_pm_value(value: Any) -> float | None:
    """
    Parses SimBrief P/M notation into a signed float.

    Examples:
        "P017"  → +17.0
        "M025"  → -25.0
        "M02"   → -2.0
        "P003"  → +3.0
        "+17"   → +17.0 (plain numeric also accepted)
    """
    if value is None:
        return None

    text = str(value).strip().upper()

    if not text:
        return None

    if text.startswith("P"):
        sign, text = 1, text[1:]
    elif text.startswith("M"):
        sign, text = -1, text[1:]
    elif text.startswith("+"):
        sign, text = 1, text[1:]
    elif text.startswith("-"):
        sign, text = -1, text[1:]
    else:
        sign = 1

    try:
        return sign * float(text)
    except (ValueError, TypeError):
        return None


def _parse_avg_wind(value: Any) -> tuple[float | None, float | None]:
    """
    Parses SimBrief average wind string "231 / 15" into (direction_deg, speed_kt).
    Also handles "23115" (DDDSS compact form).
    Returns (None, None) on failure.
    """
    if value is None:
        return None, None

    text = str(value).strip()

    if "/" in text:
        parts = text.split("/")
        if len(parts) == 2:
            try:
                return float(parts[0].strip()), float(parts[1].strip())
            except (ValueError, TypeError):
                return None, None

    # Compact DDDSS form: "23115" → 231°, 15 kt
    if text.isdigit() and len(text) == 5:
        try:
            return float(text[:3]), float(text[3:])
        except (ValueError, TypeError):
            return None, None

    return None, None


def _unix_or_seconds_to_hhmm(value: Any) -> str | None:
    """
    Converts SimBrief time values to HH:MM UTC.

    SimBrief returns scheduled times as Unix timestamps (int) in the
    `times` section: times.sched_in, times.sched_out, times.est_in, ...
    """
    if value is None:
        return None
    text = str(value).strip()
    if ":" in text and len(text) <= 5:
        return text  # already HH:MM
    iso_text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz.utc)
        return parsed.astimezone(tz.utc).strftime("%H:%M")
    except ValueError:
        pass
    try:
        ts = int(float(text))
        if ts > 86400:  # Unix timestamp
            return datetime.fromtimestamp(ts, tz=tz.utc).strftime("%H:%M")
        # Seconds since midnight
        return f"{ts // 3600:02d}:{(ts % 3600) // 60:02d}"
    except (ValueError, TypeError, OSError):
        return None


def _duration_to_minutes(value: Any) -> float | None:
    if value is None:
        return None

    text = str(value).strip()
    if text == "":
        return None

    if ":" in text:
        parts = text.split(":")
        try:
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = int(parts[2])
                return round(hours * 60.0 + minutes + seconds / 60.0, 1)
            if len(parts) == 2:
                hours = int(parts[0])
                minutes = int(parts[1])
                return round(hours * 60.0 + minutes, 1)
        except ValueError:
            return None

    number = parse_number(value)
    if number is None:
        return None

    if number < 24:
        return round(number * 60.0, 1)

    return round(number, 1)


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)

    if isinstance(value, dict):
        return value

    return {}


def _get(data: dict[str, Any], *path: str) -> Any:
    current: Any = data

    for key in path:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

        if current is None:
            return None

    return current


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()

    if text == "":
        return None

    return text.upper()


def _to_float(value: Any) -> float | None:
    return parse_number(value)


def _to_int(value: Any) -> int | None:
    number = _to_float(value)

    if number is None:
        return None

    return int(round(number))


def _altitude_to_fl(altitude_ft: int | None) -> int | None:
    if altitude_ft is None:
        return None

    # SimBrief usually gives 37000.
    # If it already gives 370, keep it.
    if altitude_ft > 1000:
        return altitude_ft // 100

    return altitude_ft
