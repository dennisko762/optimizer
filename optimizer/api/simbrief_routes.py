from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from optimizer.configs.aircraft.aircraft_catalog import AircraftCatalogEntry, get_catalog_entry, normalize_aircraft_code
from optimizer.number_utils import parse_number
from optimizer.route_profile_models import RemainingRouteProfile


router = APIRouter(prefix="/api/simbrief", tags=["simbrief"])


class SimBriefFlightStatePatch(BaseModel):
    aircraft: str | None = None
    aircraft_registration: str | None = Field(default=None, alias="aircraftRegistration")

    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    gross_weight_kg: float | None = Field(default=None, alias="grossWeightKg")
    mach: float | None = None
    current_cost_index: int | None = Field(default=None, alias="currentCostIndex")

    remaining_distance_nm: float | None = Field(default=None, alias="remainingDistanceNm")
    route_distance_nm: float | None = Field(default=None, alias="routeDistanceNm")

    # Destination airport coordinates — used by SimConnect polling for
    # live remaining distance calculation (Haversine).
    destination_lat: float | None = Field(default=None, alias="destinationLat")
    destination_lon: float | None = Field(default=None, alias="destinationLon")
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    fuel_remaining_kg: float | None = Field(default=None, alias="fuelRemainingKg")
    ground_speed_kt: float | None = Field(default=None, alias="groundSpeedKt")

    pax_count: int | None = Field(default=None, alias="paxCount")

    model_config = {
        "populate_by_name": True,
    }


class SimBriefFlightContextPatch(BaseModel):
    origin: str | None = None
    destination: str | None = None
    planned_block_time_min: float | None = Field(default=None, alias="plannedBlockTimeMin")
    flight_number: str | None = Field(default=None, alias="flightNumber")
    airline: str | None = None

    sibt_utc: str | None = Field(default=None, alias="sibtUtc")
    sobt_utc: str | None = Field(default=None, alias="sobtUtc")

    model_config = {
        "populate_by_name": True,
    }


class SimBriefAircraftInfo(BaseModel):
    simbrief_aircraft_code: str | None = Field(default=None, alias="simbriefAircraftCode")
    aircraft_icao: str | None = Field(default=None, alias="aircraftIcao")
    aircraft_display_name: str | None = Field(default=None, alias="aircraftDisplayName")
    aircraft_config: str | None = Field(default=None, alias="aircraftConfig")

    has_catalog_entry: bool = Field(default=False, alias="hasCatalogEntry")
    has_local_config_file: bool = Field(default=False, alias="hasLocalConfigFile")

    model_config = {
        "populate_by_name": True,
    }


class SimBriefSyncResponse(BaseModel):
    source: str = "SimBrief"
    username: str

    aircraft_info: SimBriefAircraftInfo = Field(alias="aircraftInfo")

    flight_state_patch: SimBriefFlightStatePatch = Field(alias="flightStatePatch")
    flight_context_patch: SimBriefFlightContextPatch = Field(alias="flightContextPatch")
    remaining_route_profile: RemainingRouteProfile | None = Field(
        default=None,
        alias="remainingRouteProfile",
    )

    raw_summary: dict[str, Any] = Field(default_factory=dict, alias="rawSummary")
    warnings: list[str] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


@router.get("/sync", response_model=SimBriefSyncResponse)
async def sync_simbrief(
    username: str = Query(..., min_length=1),
) -> SimBriefSyncResponse:
    """
    Pulls current SimBrief OFP/performance seed.

    Important:
    - This endpoint does NOT run automatically.
    - The UI calls it only when the user presses "Sync SimBrief".
    - It returns patches that the UI can merge into its current state.

    SimBrief is OFP/planned data, not live simulator state.
    So altitude/mach/gross weight are planned/OFP-derived values unless later
    overwritten by SimConnect/live aircraft state.
    """

    from data_fetcher.simbrief.simbrief_service import SimBriefService

    service = SimBriefService()

    try:
        seed = await service.get_performance_seed(username=username)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to load SimBrief data: {exc}",
        ) from exc

    seed_dict = _model_to_dict(seed)

    aircraft_raw = _first_deep_present(
        seed,
        seed_dict,
        [
            "aircraft",
            "aircraft_type",
            "aircraft_icao",
            "type",
            "type_code",
            "icao",
            "aircraft_code",
            "base_type",
            "basetype",
            "aircraft.icao_code",
            "aircraft.icaocode",
            "aircraft.name",
        ],
    )

    aircraft_info = _resolve_aircraft_info(aircraft_raw)

    origin = _first_deep_present(
        seed,
        seed_dict,
        [
            "origin",
            "origin_icao",
            "departure",
            "departure_icao",
            "dep",
            "origin.icao_code",
            "origin.icao",
            "origin.icao_code",
            "general.origin",
        ],
    )

    destination = _first_deep_present(
        seed,
        seed_dict,
        [
            "destination",
            "destination_icao",
            "arrival",
            "arrival_icao",
            "dest",
            "destination.icao_code",
            "destination.icao",
            "general.destination",
        ],
    )

    # Destination airport coordinates for live Haversine distance calculation.
    # SimBrief v2 JSON: destination.pos_lat / destination.pos_long
    destination_lat = _first_deep_float(
        seed,
        seed_dict,
        [
            "destination.pos_lat",
            "destination.lat",
            "destination.latitude",
        ],
    )
    destination_lon = _first_deep_float(
        seed,
        seed_dict,
        [
            "destination.pos_long",
            "destination.pos_lon",
            "destination.lon",
            "destination.longitude",
        ],
    )
    if (destination_lat is None or destination_lon is None) and destination is not None:
        from data_fetcher.sim.airport_lookup import lookup_airport_coordinates

        airport_coordinates = lookup_airport_coordinates(str(destination))
        if airport_coordinates is not None:
            destination_lat, destination_lon = airport_coordinates

    flight_number = _resolve_flight_number(seed, seed_dict)

    planned_block_time_min = _resolve_planned_block_time_min(seed, seed_dict)

    remaining_distance_nm = _first_float_deep_present(
        seed,
        seed_dict,
        [
            "remaining_distance_nm",
            "route_distance_nm",
            "distance_nm",
            "planned_distance_nm",
            "general.route_distance",
            "general.route_distance_nm",
            "general.distance",
            "params.route_distance",
        ],
    )

    altitude_ft = _resolve_cruise_altitude_ft(seed, seed_dict)
    mach = _resolve_cruise_mach(seed, seed_dict)
    current_cost_index = _resolve_cost_index(seed, seed_dict)

    gross_weight_kg = _resolve_gross_weight_kg(seed, seed_dict)
    fuel_remaining_kg = _resolve_fuel_kg(seed, seed_dict)
    aircraft_registration = _first_deep_present(
    seed,
    seed_dict,
    [
        "aircraft_registration",
        "registration",
        "reg",
        "aircraft.reg",
        "aircraft.registration",
        "general.aircraft_registration",
        "general.registration",
        "general.reg",
    ],
)
    pax_count = _resolve_pax_count(seed, seed_dict)
    sibt_utc = _resolve_scheduled_time(seed, seed_dict, block="in")
    sobt_utc = _resolve_scheduled_time(seed, seed_dict, block="out")
    wind_component_kt = _resolve_wind_component_kt(seed, seed_dict)
    isa_deviation_c = _resolve_isa_deviation_c(seed, seed_dict)
    avg_wind_dir, avg_wind_spd = _resolve_avg_wind(seed, seed_dict)

    simbrief_taxi_out_min = _resolve_taxi_time_min(seed, seed_dict, "out")
    simbrief_taxi_in_min = _resolve_taxi_time_min(seed, seed_dict, "in")

    from optimizer.airport_loader import get_taxi_out_min, get_taxi_in_min, get_mct_min, load_airport
    dest_icao = _normalize_airport(destination) if destination else None
    origin_icao = _normalize_airport(origin) if origin else None
    dest_airport = load_airport(dest_icao) if dest_icao else None
    origin_airport = load_airport(origin_icao) if origin_icao else None

    resolved_taxi_out_min = get_taxi_out_min(origin_icao or "", simbrief_taxi_out_min=simbrief_taxi_out_min) if origin_icao else simbrief_taxi_out_min
    resolved_taxi_in_min = get_taxi_in_min(dest_icao or "", simbrief_taxi_in_min=simbrief_taxi_in_min) if dest_icao else simbrief_taxi_in_min
    dest_mct_ii_min = get_mct_min(dest_icao or "") if dest_icao else None

    warnings: list[str] = []

    if origin is None:
        warnings.append("Origin not found in SimBrief seed.")

    if destination is None:
        warnings.append("Destination not found in SimBrief seed.")

    if aircraft_info.aircraft_icao is None:
        warnings.append("Aircraft type not found in SimBrief seed.")

    if aircraft_info.aircraft_icao is not None and not aircraft_info.has_catalog_entry:
        warnings.append(
            f"Aircraft type {aircraft_info.aircraft_icao} is not in the local aircraft catalog. "
            "Aircraft is displayed, but aircraftConfig was not changed."
        )

    if aircraft_info.aircraft_config is not None and not aircraft_info.has_local_config_file:
        warnings.append(
            f"Aircraft config mapping exists for {aircraft_info.aircraft_icao} "
            f"→ {aircraft_info.aircraft_config}, but the YAML file is missing. "
            f"Expected optimizer/configs/aircraft/{aircraft_info.aircraft_config}.yaml."
        )

    if gross_weight_kg is None:
        warnings.append("Gross weight / estimated takeoff weight not found in SimBrief seed.")

    if altitude_ft is None:
        warnings.append("Cruise altitude not found in SimBrief seed.")

    if mach is None:
        warnings.append("Cruise Mach not found in SimBrief seed.")

    if destination_lat is None or destination_lon is None:
        warnings.append("Destination coordinates not found in SimBrief seed or airport fallback.")

    from data_fetcher.simbrief.route_profile import build_remaining_route_profile_from_waypoints

    route_waypoints = getattr(seed, "route_waypoints", []) or []
    remaining_route_profile = build_remaining_route_profile_from_waypoints(route_waypoints)

    if route_waypoints and not remaining_route_profile.segments:
        warnings.append("SimBrief route waypoints were found, but no usable cruise segment distances were parsed.")

    return SimBriefSyncResponse(
        username=username,
        aircraftInfo=aircraft_info,
        flightStatePatch=SimBriefFlightStatePatch(
        aircraft=aircraft_info.aircraft_icao,
        aircraftRegistration=_normalize_registration(aircraft_registration),
        altitudeFt=altitude_ft,
        grossWeightKg=gross_weight_kg,
        mach=mach,
        currentCostIndex=current_cost_index,
        remainingDistanceNm=remaining_distance_nm,
        routeDistanceNm=remaining_distance_nm,
        destinationLat=destination_lat,
        destinationLon=destination_lon,
        fuelRemainingKg=fuel_remaining_kg,
        paxCount=pax_count,
        windComponentKt=wind_component_kt,
        isaDeviationC=isa_deviation_c,
),
        flightContextPatch=SimBriefFlightContextPatch(
            origin=_normalize_airport(origin),
            destination=_normalize_airport(destination),
            plannedBlockTimeMin=planned_block_time_min,
            flightNumber=flight_number,
            airline="SimBrief",
            sibtUtc=sibt_utc,
            sobtUtc=sobt_utc,
        ),
        remainingRouteProfile=remaining_route_profile,
        rawSummary={
            "aircraft_raw": aircraft_raw,
            "aircraft_icao": aircraft_info.aircraft_icao,
            "aircraft_display_name": aircraft_info.aircraft_display_name,
            "aircraft_config": aircraft_info.aircraft_config,
            "has_catalog_entry": aircraft_info.has_catalog_entry,
            "has_local_config_file": aircraft_info.has_local_config_file,
            "origin": origin,
            "destination": destination,
            "flight_number": flight_number,
            "planned_block_time_min": planned_block_time_min,
            "remaining_distance_nm": remaining_distance_nm,
            "altitude_ft": altitude_ft,
            "mach": mach,
            "current_cost_index": current_cost_index,
            "gross_weight_kg": gross_weight_kg,
            "fuel_remaining_kg": fuel_remaining_kg,
            "pax_count": pax_count,
            "wind_component_kt": wind_component_kt,
            "isa_deviation_c": isa_deviation_c,
            "avg_wind": f"{avg_wind_dir:.0f}/{avg_wind_spd:.0f}" if avg_wind_dir is not None else None,
            "_debug_simbrief_wind_raw": _first_deep_present(
                seed, seed_dict,
                ["avg_wind_comp", "wind_component", "wind_comp", "general.avg_wind_comp"],
            ),
            "sibt_utc": sibt_utc,
            "sobt_utc": sobt_utc,
            "destination_lat": destination_lat,
            "destination_lon": destination_lon,
            "route_waypoint_count": len(route_waypoints),
            "cruise_segment_count": remaining_route_profile.segment_count,
            "cruise_segment_distance_nm": remaining_route_profile.total_distance_nm,
            "simbrief_taxi_out_min": simbrief_taxi_out_min,
            "simbrief_taxi_in_min": simbrief_taxi_in_min,
            "resolved_taxi_out_min": resolved_taxi_out_min,
            "resolved_taxi_in_min": resolved_taxi_in_min,
            "dest_mct_ii_min": dest_mct_ii_min,
            "dest_airport_category": dest_airport.category if dest_airport else None,
            "dest_night_curfew": dest_airport.night_curfew.restricted if dest_airport else None,
            "dest_airport_is_fallback": dest_airport.is_fallback if dest_airport else None,
            "_debug_simbrief_isa_raw": _first_deep_present(
                seed, seed_dict,
                ["isa_dev", "isa_deviation", "planned_isa_deviation_c", "general.isa_dev"],
            ),
        },
        warnings=warnings,
    )


@router.get("/flightplan/live")
async def get_live_flightplan(
    refresh: bool = Query(default=False, description="Bypass the fetch cache"),
) -> dict[str, Any]:
    """Real SimBrief OFP for the configured pilot, as the EFB flightplan view.

    Credentials come from the environment only (SIMBRIEF_USER). Responses
    never include credential material. Fetches are cached for a few minutes
    so repeated UI loads do not hammer the SimBrief API.
    """
    from optimizer.api.flightplan_service import (
        CredentialsMissingError,
        FlightplanError,
        fetch_flightplan_view,
    )

    if not os.environ.get("SIMBRIEF_USER", "").strip():
        raise HTTPException(
            status_code=503,
            detail="SimBrief is not configured on this bridge (SIMBRIEF_USER is not set).",
        )
    try:
        return await _to_thread(fetch_flightplan_view, force=refresh)
    except CredentialsMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FlightplanError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/flightplan/import")
async def import_flightplan() -> dict[str, Any]:
    """'Import New Plan' — pull the current SimBrief OFP fresh and save it.

    The freshly fetched plan is persisted to the plan store so it appears in
    My Flights (saved plans) and is loaded on the next app start.
    """
    from optimizer.api.flightplan_service import (
        CredentialsMissingError,
        FlightplanError,
        fetch_flightplan_view,
        save_plan,
    )

    if not os.environ.get("SIMBRIEF_USER", "").strip():
        raise HTTPException(
            status_code=503,
            detail="SimBrief is not configured on this bridge (SIMBRIEF_USER is not set).",
        )
    try:
        view = await _to_thread(fetch_flightplan_view, force=True)
        envelope = await _to_thread(save_plan, view)
    except CredentialsMissingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except FlightplanError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"key": envelope["key"], "saved_at": envelope["saved_at"], "flightplan": view}


@router.get("/flightplans")
async def list_saved_flightplans() -> dict[str, Any]:
    """Saved flightplans (My Flights), most recent first, + the last plan key."""
    from optimizer.api.flightplan_service import list_plans

    return await _to_thread(list_plans)


@router.get("/flightplans/{plan_key:path}")
async def get_saved_flightplan(plan_key: str) -> dict[str, Any]:
    """One saved flightplan by key (origin+dest-flight, e.g. DOHLHR-QR815)."""
    from optimizer.api.flightplan_service import get_plan

    plan = await _to_thread(get_plan, plan_key)
    if plan is None:
        raise HTTPException(status_code=404, detail="Unknown saved flightplan.")
    return plan


@router.delete("/flightplans/{plan_key:path}")
async def delete_saved_flightplan(plan_key: str) -> dict[str, str]:
    """Remove one saved flightplan from the store."""
    from optimizer.api.flightplan_service import delete_plan

    def _delete() -> bool:
        return delete_plan(plan_key)

    if not await _to_thread(_delete):
        raise HTTPException(status_code=404, detail="Unknown saved flightplan.")
    return {"deleted": plan_key}


@router.get("/config/readiness")
async def simbrief_readiness() -> dict[str, Any]:
    """Whether SimBrief is configured — never returns credential values."""
    return {"configured": bool(os.environ.get("SIMBRIEF_USER", "").strip())}


async def _to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking (file/network) helper in the thread pool."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _resolve_aircraft_info(value: Any) -> SimBriefAircraftInfo:
    code = normalize_aircraft_code(str(value)) if value is not None else None

    if code is None:
        return SimBriefAircraftInfo(
            simbriefAircraftCode=None,
            aircraftIcao=None,
            aircraftDisplayName=None,
            aircraftConfig=None,
            hasCatalogEntry=False,
            hasLocalConfigFile=False,
        )

    entry = get_catalog_entry(code)

    if entry is None:
        return SimBriefAircraftInfo(
            simbriefAircraftCode=str(value).strip().upper(),
            aircraftIcao=code,
            aircraftDisplayName=code,
            aircraftConfig=None,
            hasCatalogEntry=False,
            hasLocalConfigFile=False,
        )

    config_key = entry.config_key
    config_exists = _aircraft_config_file_exists(config_key)

    return SimBriefAircraftInfo(
        simbriefAircraftCode=str(value).strip().upper(),
        aircraftIcao=entry.simbrief_code,
        aircraftDisplayName=_display_name_for_entry(entry),
        aircraftConfig=config_key,
        hasCatalogEntry=True,
        hasLocalConfigFile=config_exists,
    )


def _display_name_for_entry(entry: AircraftCatalogEntry) -> str:
    aircraft_type = entry.aircraft_type

    if aircraft_type.startswith("Airbus "):
        return aircraft_type

    if aircraft_type.startswith("Boeing "):
        return aircraft_type

    if entry.simbrief_code.startswith("A"):
        return f"Airbus {aircraft_type}"

    if entry.simbrief_code.startswith("B"):
        return f"Boeing {aircraft_type}"

    return aircraft_type


def _aircraft_config_file_exists(config_key: str) -> bool:
    import sys
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS) / "optimizer"
    else:
        base = Path(__file__).resolve().parents[1]
    return (base / "configs" / "aircraft" / f"{config_key}.yaml").exists()


def _resolve_flight_number(model: Any, model_dict: dict[str, Any]) -> str | None:
    callsign = _first_deep_present(
        model,
        model_dict,
        [
            "callsign",
            "general.callsign",
        ],
    )

    if callsign not in {None, ""}:
        return str(callsign).strip().upper()

    flight_number = _first_deep_present(
        model,
        model_dict,
        [
            "flight_number",
            "flightNumber",
            "fltno",
            "flight_no",
            "general.flight_number",
            "general.flightNumber",
            "general.fltno",
            "general.flight_no",
        ],
    )

    airline_icao = _first_deep_present(
        model,
        model_dict,
        [
            "airline_icao",
            "icao_airline",
            "general.airline_icao",
            "general.icao_airline",
        ],
    )

    airline_iata = _first_deep_present(
        model,
        model_dict,
        [
            "airline_iata",
            "iata_airline",
            "general.airline_iata",
            "general.iata_airline",
        ],
    )

    if flight_number in {None, ""}:
        return None

    flight_text = str(flight_number).strip().upper()

    if any(char.isalpha() for char in flight_text):
        return flight_text

    if airline_icao not in {None, ""}:
        return f"{str(airline_icao).strip().upper()}{flight_text}"

    if airline_iata not in {None, ""}:
        return f"{str(airline_iata).strip().upper()}{flight_text}"

    return flight_text

def _resolve_taxi_time_min(model: Any, model_dict: dict[str, Any], direction: str) -> float | None:
    """
    Extract planned taxi-out or taxi-in time from the SimBrief seed.
    direction: "out" or "in"
    SimBrief returns values in minutes (integer string, e.g. "18").
    """
    if direction == "out":
        keys = ["times.taxi_out", "taxi_out", "taxi_out_min", "params.taxi_out"]
    else:
        keys = ["times.taxi_in", "taxi_in", "taxi_in_min", "params.taxi_in"]

    value = _first_float_deep_present(model, model_dict, keys)
    if value is None:
        return None
    # SimBrief may give seconds (>60) or minutes; normalise to minutes
    if value > 60:
        return round(value / 60.0, 1)
    return float(value) if value > 0 else None


def _resolve_planned_block_time_min(model: Any, model_dict: dict[str, Any]) -> float | None:
    value = _first_float_deep_present(
        model,
        model_dict,
        [
            "planned_block_time_min",
            "block_time_min",
            "ete_min",
            "scheduled_block_time_min",
            "times.est_block",
            "times.sched_block",
            "times.block_time",
            "general.est_block",
            "general.block_time",
        ],
    )

    if value is not None:
        return _time_value_to_minutes(value)

    ete = _first_deep_present(
        model,
        model_dict,
        [
            "ete",
            "ete_hours",
            "ete_h",
            "times.est_time_enroute",
            "general.ete",
        ],
    )

    return _parse_time_to_minutes(ete)


def _resolve_cruise_altitude_ft(model: Any, model_dict: dict[str, Any]) -> float | None:
    value = _first_deep_present(
        model,
        model_dict,
        [
            # Your normalized SimBriefPerformanceSeed fields
            "planned_cruise_altitude_ft",
            "planned_cruise_fl",

            # Common direct names
            "altitude_ft",
            "cruise_altitude_ft",
            "cruise_altitude",
            "initial_altitude",

            # Common raw SimBrief-ish nested names
            "general.initial_altitude",
            "general.cruise_altitude",
            "params.initial_altitude",
            "params.cruise_altitude",
        ],
    )

    if value is None:
        return None

    text = str(value).strip().upper().replace(" ", "")

    try:
        if text.startswith("FL"):
            return float(text.replace("FL", "")) * 100.0

        numeric = float(text)

        # Your model planned_cruise_fl is likely 330, 350, 380 etc.
        if 100 <= numeric <= 600:
            return numeric * 100.0

        return numeric
    except ValueError:
        return None


def _resolve_cruise_mach(model: Any, model_dict: dict[str, Any]) -> float | None:
    value = _first_deep_present(
        model,
        model_dict,
        [
            # Your normalized SimBriefPerformanceSeed field
            "planned_mach",

            # Common direct names
            "mach",
            "cruise_mach",
            "cost_index_mach",

            # Common raw SimBrief-ish nested names
            "general.cruise_mach",
            "params.cruise_mach",
            "params.mach",
            "params.cruise_profile",
            "general.cruise_profile",
        ],
    )

    if value is None:
        return None

    text = str(value).strip().upper()

    # If profile is CI30/LRC/AUTO, this is not a Mach value.
    if text.startswith("CI") or text in {"LRC", "AUTO", "ECON"}:
        return None

    # Examples:
    # "M084"
    # "M0.84"
    # ".84"
    # "0.84"
    # "84"
    if text.startswith("M"):
        text = text[1:]

    if text.startswith("."):
        text = "0" + text

    try:
        numeric = float(text)

        # M084 / 084 / 84 -> 0.84
        if numeric > 10:
            return numeric / 100.0

        if 1.0 < numeric <= 10.0:
            return numeric / 10.0

        return numeric
    except ValueError:
        return None


def _resolve_cost_index(model: Any, model_dict: dict[str, Any]) -> int | None:
    value = _first_deep_present(
        model,
        model_dict,
        [
            # Your normalized SimBriefPerformanceSeed field
            "cost_index",

            # Common aliases
            "costIndex",
            "ci",
            "general.cost_index",
            "general.costindex",
            "params.cost_index",
            "params.costindex",
            "params.ci",
            "general.cruise_profile",
            "params.cruise_profile",
        ],
    )

    if value is None:
        return None

    text = str(value).strip().upper()

    if text.startswith("CI"):
        text = text[2:]

    try:
        return int(float(text))
    except ValueError:
        return None


def _resolve_gross_weight_kg(model: Any, model_dict: dict[str, Any]) -> float | None:
    """
    For in-flight optimizer we need current gross weight.

    SimBrief usually gives planned weights, not live aircraft weight.
    Best available planned proxy:
      estimated takeoff weight / TOW

    Later SimConnect should overwrite this with live gross weight.
    """

    value = _first_float_deep_present(
        model,
        model_dict,
        [
            "gross_weight_kg",
            "takeoff_weight_kg",
            "est_tow_kg",
            "tow_kg",
            "weights.est_tow_kg",
            "weights.takeoff_weight_kg",
            "weights.tow_kg",
            "weights.est_tow",
            "weights.takeoff_weight",
            "weights.tow",
            "weights.gross_weight",
            "general.est_tow",
        ],
    )

    return _mass_to_kg(value, aircraft_hint="gross")


def _resolve_fuel_kg(model: Any, model_dict: dict[str, Any]) -> float | None:
    """
    SimBrief fuel is OFP fuel, not live remaining fuel.

    For preflight or test state this is acceptable.
    Later SimConnect should overwrite this with live fuel remaining.
    """

    value = _first_float_deep_present(
        model,
        model_dict,
        [
            "fuel_remaining_kg",
            "block_fuel_kg",
            "fuel_kg",
            "fuel.plan_ramp_kg",
            "fuel.plan_block_kg",
            "fuel.block_kg",
            "fuel.enroute_burn_kg",
            "fuel.plan_ramp",
            "fuel.plan_block",
            "fuel.block",
            "fuel.enroute_burn",
        ],
    )

    return _mass_to_kg(value, aircraft_hint="fuel")


def _mass_to_kg(value: float | None, *, aircraft_hint: str) -> float | None:
    if value is None:
        return None

    # SimBrief often returns lbs depending on settings/API output.
    # Heuristic:
    # - A380 TOW 560000 lb -> 254000 kg after conversion.
    # - A380 fuel 170000 kg is plausible as kg for longhaul.
    #
    # Gross weight:
    #   if > 450000, likely lb for most aircraft except absurd kg values.
    # Fuel:
    #   if > 250000, likely lb.
    #
    # This is not perfect but avoids turning 170t A380 fuel into 77t.
    if aircraft_hint == "gross":
        if value > 450_000:
            return round(value * 0.45359237, 1)

    if aircraft_hint == "fuel":
        if value > 250_000:
            return round(value * 0.45359237, 1)

    return round(value, 1)


def _time_value_to_minutes(value: float) -> float:
    # If less than 24, assume hours.
    # If greater, assume already minutes.
    if value < 24:
        return round(value * 60.0, 1)

    return round(value, 1)


def _parse_time_to_minutes(value: Any) -> float | None:
    if value is None or value == "":
        return None

    if isinstance(value, (int, float)):
        return _time_value_to_minutes(float(value))

    text = str(value).strip()

    # HH:MM
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            try:
                hours = int(parts[0])
                minutes = int(parts[1])
                return float(hours * 60 + minutes)
            except ValueError:
                return None

    try:
        return _time_value_to_minutes(float(text))
    except ValueError:
        return None


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()

    if hasattr(model, "dict"):
        return model.dict()

    if isinstance(model, dict):
        return model

    return {}


def _first_deep_float(
    model: Any, model_dict: dict[str, Any], paths: list[str]
) -> float | None:
    """Like _first_deep_present but returns a float or None."""
    raw = _first_deep_present(model, model_dict, paths)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _first_deep_present(
    model: Any,
    model_dict: dict[str, Any],
    paths: list[str],
) -> Any:
    for path in paths:
        value = _get_path(model_dict, path)

        if value not in {None, ""}:
            return value

        if "." not in path and hasattr(model, path):
            attr = getattr(model, path)
            if attr not in {None, ""}:
                return attr

    return None


def _first_float_deep_present(
    model: Any,
    model_dict: dict[str, Any],
    paths: list[str],
) -> float | None:
    value = _first_deep_present(model, model_dict, paths)

    if value is None or value == "":
        return None

    return parse_number(value)


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data

    for key in path.split("."):
        if not isinstance(current, dict):
            return None

        if key not in current:
            return None

        current = current[key]

    return current


def _resolve_wind_component_kt(model: Any, model_dict: dict[str, Any]) -> float | None:
    """
    Parses OFP wind component in SimBrief P/M notation.
    "P017" → +17.0 kt (tailwind)
    "M025" → -25.0 kt (headwind)

    SimBrief v2 JSON key: general.avg_wind_comp
    """
    raw = _first_deep_present(
        model,
        model_dict,
        [
            "planned_wind_component_kt",   # from SimBriefPerformanceSeed (already parsed)
            "avg_wind_comp",               # SimBrief v2 JSON — general section
            "wind_component_kt",
            "wind_component",
            "wind_comp",
            "windcomp",
            "general.avg_wind_comp",
            "general.wind_component",
            "general.wind_comp",
        ],
    )
    return _parse_pm_value(raw)


def _resolve_isa_deviation_c(model: Any, model_dict: dict[str, Any]) -> float | None:
    """
    Parses OFP ISA deviation in SimBrief P/M notation.
    "M02" → -2.0 °C (cold atmosphere)
    "P03" → +3.0 °C (warm atmosphere)

    SimBrief v2 JSON key: general.isa_dev
    """
    raw = _first_deep_present(
        model,
        model_dict,
        [
            "planned_isa_deviation_c",     # from SimBriefPerformanceSeed (already parsed)
            "isa_dev",                     # SimBrief v2 JSON — general section
            "isa_deviation_c",
            "isa_deviation",
            "isadev",
            "isa",
            "general.isa_dev",
            "general.isa_deviation",
            "general.isadev",
        ],
    )
    return _parse_pm_value(raw)


def _resolve_avg_wind(
    model: Any, model_dict: dict[str, Any]
) -> tuple[float | None, float | None]:
    """
    Parses average wind "231 / 15" → (231.0, 15.0).
    Returns (None, None) if not available.
    """
    raw = _first_deep_present(
        model,
        model_dict,
        [
            "planned_avg_wind_direction_deg",
            "avg_wind_comp",
            "average_wind",
            "avg_wind",
            "avgwind",
            "general.avg_wind_comp",
            "general.average_wind",
            "general.avg_wind",
        ],
    )
    if raw is None:
        return None, None

    text = str(raw).strip()
    if "/" in text:
        parts = text.split("/")
        if len(parts) == 2:
            try:
                return float(parts[0].strip()), float(parts[1].strip())
            except (ValueError, TypeError):
                pass
    return None, None


def _parse_pm_value(value: Any) -> float | None:
    """
    Parses SimBrief P/M notation into a signed float.
    "P017" → +17.0,  "M025" → -25.0,  "M02" → -2.0
    """
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    if text.startswith("P"):
        sign, text = 1.0, text[1:]
    elif text.startswith("M"):
        sign, text = -1.0, text[1:]
    elif text.startswith("+"):
        sign, text = 1.0, text[1:]
    elif text.startswith("-"):
        sign, text = -1.0, text[1:]
    else:
        sign = 1.0
    try:
        return sign * float(text)
    except (ValueError, TypeError):
        return None


def _resolve_scheduled_time(
    model: Any, model_dict: dict[str, Any], block: str = "in"
) -> str | None:
    """
    Reads SIBT/SOBT from the normalised SimBriefPerformanceSeed.

    The normaliser already extracted these from times.sched_in /
    times.sched_out (Unix timestamps) and converted them to HH:MM UTC.
    This function just reads the pre-parsed field.
    """
    key = "sibt_utc" if block == "in" else "sobt_utc"
    return _first_deep_present(model, model_dict, [key]) or None


def _resolve_pax_count(model: Any, model_dict: dict[str, Any]) -> int | None:
    """
    Extracts total passenger count from SimBrief OFP data.

    SimBrief returns pax_count in the weights section. The exact key
    varies slightly between API versions, so we try several paths.
    """
    value = _first_deep_present(
        model,
        model_dict,
        [
            "pax_count",
            "passenger_count",
            "weights.pax_count",
            "weights.passenger_count",
            "weights.pax",
            "general.pax_count",
            "general.passenger_count",
        ],
    )

    if value is None:
        return None

    try:
        return int(float(str(value).strip()))
    except (ValueError, TypeError):
        return None


def _normalize_airport(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip().upper()

    if text == "":
        return None

    return text

def _normalize_registration(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip().upper()

    if text == "":
        return None

    return text
