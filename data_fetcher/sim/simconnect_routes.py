from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from data_fetcher.sim.remaining_distance import estimate_route_remaining_distance
from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.telemetry_hub import TelemetrySnapshot, get_telemetry_hub
from delay_module.eta_calculator import DelayTriggerConfig, compute_eta_if_possible
from optimizer.route_profile_models import (
    RemainingRouteProfile,
    finalize_remaining_route_profile,
)


router = APIRouter(prefix="/api/simconnect", tags=["simconnect"])

_destination_lat: float | None = None
_destination_lon: float | None = None
_remaining_route_profile: RemainingRouteProfile | None = None


class DestinationPayload(BaseModel):
    lat: float | None = None
    lon: float | None = None
    remaining_route_profile: RemainingRouteProfile | None = Field(
        default=None,
        alias="remainingRouteProfile",
    )

    model_config = {
        "populate_by_name": True,
    }


class ApplyPayload(BaseModel):
    """Optimizer recommendation to push into the running sim."""

    flight_level: int | None = Field(default=None, alias="flightLevel")
    mach: float | None = None
    reason: str | None = None

    model_config = {
        "populate_by_name": True,
    }


class ApplyResponse(BaseModel):
    applied: bool
    supported: bool = True
    flight_level_applied: bool | None = Field(default=None, alias="flightLevelApplied")
    mach_applied: bool | None = Field(default=None, alias="machApplied")
    flight_level: int | None = Field(default=None, alias="flightLevel")
    mach: float | None = None
    errors: list[str] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


@router.post("/destination", status_code=204)
def set_destination(body: DestinationPayload) -> None:
    global _destination_lat, _destination_lon, _remaining_route_profile
    _destination_lat = body.lat
    _destination_lon = body.lon
    _remaining_route_profile = finalize_remaining_route_profile(
        body.remaining_route_profile
    )


@router.post("/apply", response_model=ApplyResponse)
async def apply_recommendation(body: ApplyPayload) -> ApplyResponse:
    """
    Push an optimizer recommendation into the running sim (SimConnect SET
    simvars): target flight level and/or Mach.

    The EFB calls this from the optimizer panel's Apply button. The
    response always carries per-target applied flags plus human-readable
    errors — the UI renders them verbatim and never invents success.
    """
    hub = get_telemetry_hub()
    raw = await hub.set_target_state(
        flight_level=body.flight_level,
        mach=body.mach,
    )
    print(
        "SimConnect apply:"
        f" fl={body.flight_level} mach={body.mach}"
        f" applied={bool(raw.get('applied'))}"
        + (f" reason={body.reason}" if body.reason else "")
    )
    return ApplyResponse(
        applied=bool(raw.get("applied", False)),
        supported=bool(raw.get("supported", False)),
        flightLevelApplied=raw.get("flightLevelApplied"),
        machApplied=raw.get("machApplied"),
        flightLevel=raw.get("flightLevel", body.flight_level),
        mach=raw.get("mach", body.mach),
        errors=list(raw.get("errors") or []),
    )


class SimConnectFlightStatePatch(BaseModel):
    aircraft: str | None = None
    aircraft_config: str | None = Field(default=None, alias="aircraftConfig")
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    gross_weight_kg: float | None = Field(default=None, alias="grossWeightKg")
    mach: float | None = None
    current_cost_index: int | None = Field(default=None, alias="currentCostIndex")
    fmc_source: str | None = Field(default=None, alias="fmcSource")
    fmc_cruise_flight_level: int | None = Field(
        default=None,
        alias="fmcCruiseFlightLevel",
    )
    fmc_step_climb_distance_nm: float | None = Field(
        default=None,
        alias="fmcStepClimbDistanceNm",
    )

    # Wind component along track (positive = tailwind, negative = headwind).
    # Populated from AIRCRAFT_WIND_Z when available.
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    fuel_remaining_kg: float | None = Field(default=None, alias="fuelRemainingKg")
    fuel_flow_kg_h: float | None = Field(default=None, alias="fuelFlowKgH")
    fuel_flow_source: str | None = Field(default=None, alias="fuelFlowSource")
    ground_speed_kt: float | None = Field(default=None, alias="groundSpeedKt")

    # Computed from live lat/lon + destination coordinates (Haversine).
    # Replaces the static SimBrief OFP distance during flight.
    remaining_distance_nm: float | None = Field(default=None, alias="remainingDistanceNm")

    # Optional GPS flightplan timing. The UI keeps the same layout but prefers
    # this for Live ETA when available.
    gps_ete_seconds: float | None = Field(default=None, alias="gpsEteSeconds")
    gps_eta_seconds: float | None = Field(default=None, alias="gpsEtaSeconds")

    model_config = {
        "populate_by_name": True,
    }


class SimConnectTelemetryResponse(BaseModel):
    source: str = "SimConnect"
    connected: bool
    collector_status: str = Field(default="idle", alias="collectorStatus")
    sample_interval_s: float = Field(default=1.0, alias="sampleIntervalS")
    data_age_ms: int | None = Field(default=None, alias="dataAgeMs")
    last_sample_utc: str | None = Field(default=None, alias="lastSampleUtc")
    last_error: str | None = Field(default=None, alias="lastError")

    flight_state_patch: SimConnectFlightStatePatch = Field(alias="flightStatePatch")

    raw_summary: dict[str, Any] = Field(default_factory=dict, alias="rawSummary")
    warnings: list[str] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


class EtaResponse(BaseModel):
    remaining_distance_nm: float = Field(alias="remainingDistanceNm")
    ground_speed_kt: float = Field(alias="groundSpeedKt")
    remaining_time_min: float = Field(alias="remainingTimeMin")

    eta_utc: str = Field(alias="etaUtc")
    sibt_utc: str = Field(alias="sibtUtc")

    delay_min: float = Field(alias="delayMin")
    delay_status: str = Field(alias="delayStatus")
    delay_label: str = Field(alias="delayLabel")

    should_recalculate: bool = Field(alias="shouldRecalculate")
    recalculate_reason: str | None = Field(default=None, alias="recalculateReason")

    current_delay_min: float = Field(alias="currentDelayMin")
    target_delay_min: float = Field(alias="targetDelayMin")

    connected: bool = True
    warnings: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


@router.get("/eta", response_model=EtaResponse)
async def simconnect_eta(
    sibt: str = Query(..., description="Scheduled in-block time HH:MM (from SimBrief)"),
    sobt: str | None = Query(default=None, description="Scheduled off-block time HH:MM"),
    target_delay_min: float = Query(default=0.0, alias="targetDelayMin"),
    last_delay_min: float | None = Query(default=None, alias="lastDelayMin"),
    destination_lat: float | None = Query(default=None, alias="destinationLat"),
    destination_lon: float | None = Query(default=None, alias="destinationLon"),
    destination: str | None = Query(default=None, alias="destination"),
) -> EtaResponse:
    """
    Computes current ETA and delay from live SimConnect data + SimBrief schedule.

    This is the core proactive-trigger endpoint. The frontend polls this
    every 60 seconds. When should_recalculate is True, the EFB shows a
    prompt and auto-populates current_delay_min in the scenario payload.

    Parameters
    ----------
    sibt:
        Scheduled in-block time from SimBrief OFP (HH:MM UTC).
    target_delay_min:
        Acceptable delay (default 0 = on time). Used for recovery target.
    last_delay_min:
        Delay from the previous calculation. Used to detect significant change.

    Returns
    -------
    EtaResponse with delay_min, delay_status, should_recalculate, and
    pre-filled current_delay_min/target_delay_min for direct use in payloads.
    """

    snapshot = await _get_telemetry_snapshot()
    warnings: list[str] = []
    live = snapshot.live_state
    if not snapshot.connected or live is None:
        return EtaResponse(
            remainingDistanceNm=0,
            groundSpeedKt=0,
            remainingTimeMin=0,
            etaUtc="--:--",
            sibtUtc=sibt,
            delayMin=0,
            delayStatus="UNKNOWN",
            delayLabel="no sim",
            shouldRecalculate=False,
            recalculateReason=None,
            currentDelayMin=0,
            targetDelayMin=target_delay_min,
            connected=False,
            warnings=_collector_warnings(snapshot, fallback="SimConnect telemetry collector is warming up."),
        )

    default_destination_lat = (
        destination_lat if destination_lat is not None else _destination_lat
    )
    default_destination_lon = (
        destination_lon if destination_lon is not None else _destination_lon
    )
    effective_destination_lat, effective_destination_lon, destination_lookup_warning = _resolve_destination_coordinates(
        destination_lat=default_destination_lat,
        destination_lon=default_destination_lon,
        destination=destination,
    )
    if destination_lookup_warning is not None:
        warnings.append(destination_lookup_warning)

    remaining_nm, _, _ = _resolve_remaining_distance(
        live,
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
    )

    if remaining_nm is None:
        warnings.append(
            _missing_remaining_distance_warning(
                destination_lat=effective_destination_lat,
                destination_lon=effective_destination_lon,
            )
        )

    config = DelayTriggerConfig(
        target_delay_min=target_delay_min,
        last_known_delay_min=last_delay_min,
    )

    eta = compute_eta_if_possible(
        remaining_distance_nm=remaining_nm,
        ground_speed_kt=live.ground_speed_kt,
        sibt_utc=sibt,
        sobt_utc=sobt,
        config=config,
    )

    if eta is None:
        warnings.append(
            "Cannot compute ETA: remaining distance or ground speed unavailable."
        )
        return EtaResponse(
            remainingDistanceNm=remaining_nm or 0,
            groundSpeedKt=live.ground_speed_kt or 0,
            remainingTimeMin=0,
            etaUtc="--:--",
            sibtUtc=sibt,
            delayMin=0,
            delayStatus="UNKNOWN",
            delayLabel="no data",
            shouldRecalculate=False,
            recalculateReason=None,
            currentDelayMin=0,
            targetDelayMin=target_delay_min,
            connected=True,
            warnings=warnings,
        )

    return EtaResponse(
        remainingDistanceNm=eta.remaining_distance_nm,
        groundSpeedKt=eta.ground_speed_kt,
        remainingTimeMin=eta.remaining_time_min,
        etaUtc=eta.eta_utc,
        sibtUtc=eta.sibt_utc,
        delayMin=eta.delay_min,
        delayStatus=eta.delay_status,
        delayLabel=eta.delay_label,
        shouldRecalculate=eta.should_recalculate,
        recalculateReason=eta.recalculate_reason,
        currentDelayMin=eta.current_delay_min,
        targetDelayMin=eta.target_delay_min,
        connected=True,
        warnings=warnings,
    )


@router.get("/status")
async def simconnect_status() -> dict[str, Any]:
    snapshot = await _get_telemetry_snapshot(refresh_if_empty=False)
    return {
        "source": "SimConnect",
        "available": True,
        "client": "data_fetcher.sim.telemetry_hub.TelemetryHub",
        "connected": snapshot.connected,
        "collectorStatus": _collector_status(snapshot),
        "sampleIntervalS": snapshot.poll_interval_s,
        "dataAgeMs": snapshot.data_age_ms,
        "lastSampleUtc": _format_snapshot_timestamp(snapshot.last_sample_utc),
        "lastError": snapshot.last_error,
        "sampleCount": snapshot.sample_count,
    }


@router.get("/telemetry", response_model=SimConnectTelemetryResponse)
async def simconnect_telemetry(
    destination_lat: float | None = Query(default=None, alias="destinationLat"),
    destination_lon: float | None = Query(default=None, alias="destinationLon"),
    destination: str | None = Query(default=None, alias="destination"),
) -> SimConnectTelemetryResponse:
    """
    Reads live aircraft telemetry from MSFS via the existing SimConnectClient.

    Pass destinationLat / destinationLon (from SimBrief sync) to support live
    remaining-distance fallback when no SimBrief route profile is available.

    Important:
    - This endpoint returns only real SimConnect telemetry.
    - It does not generate fallback/test/default values.
    - It does not overwrite OFP-only fields like route distance or flight number.
    """

    snapshot = await _get_telemetry_snapshot()
    live = snapshot.live_state
    if not snapshot.connected or live is None:
        return SimConnectTelemetryResponse(
            connected=False,
            collectorStatus=_collector_status(snapshot),
            sampleIntervalS=snapshot.poll_interval_s,
            dataAgeMs=snapshot.data_age_ms,
            lastSampleUtc=_format_snapshot_timestamp(snapshot.last_sample_utc),
            lastError=snapshot.last_error,
            flightStatePatch=SimConnectFlightStatePatch(),
            rawSummary={},
            warnings=_collector_warnings(snapshot, fallback="SimConnect telemetry collector is warming up."),
        )

    default_destination_lat = (
        destination_lat if destination_lat is not None else _destination_lat
    )
    default_destination_lon = (
        destination_lon if destination_lon is not None else _destination_lon
    )
    effective_destination_lat, effective_destination_lon, destination_lookup_warning = _resolve_destination_coordinates(
        destination_lat=default_destination_lat,
        destination_lon=default_destination_lon,
        destination=destination,
    )

    patch, remaining_distance_source, remaining_distance_details = _live_state_to_patch(
        live,
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
    )
    warnings = _build_warnings(
        live,
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
        remaining_distance_nm=patch.remaining_distance_nm,
        remaining_distance_source=remaining_distance_source,
    )
    if destination_lookup_warning is not None:
        warnings.append(destination_lookup_warning)

    res = SimConnectTelemetryResponse(
        source=_navigation_source(live),
        connected=True,
        collectorStatus=_collector_status(snapshot),
        sampleIntervalS=snapshot.poll_interval_s,
        dataAgeMs=snapshot.data_age_ms,
        lastSampleUtc=_format_snapshot_timestamp(snapshot.last_sample_utc),
        lastError=snapshot.last_error,
        flightStatePatch=patch,
        rawSummary=_raw_summary(
            live,
            remaining_distance_source=remaining_distance_source,
            remaining_distance_details=remaining_distance_details,
        ),
        warnings=warnings,
    )
    # Compact trace only: the EFB polls this endpoint continuously (M3), so
    # dumping the whole response per sample would flood the server log.
    print(
        "SimConnect telemetry:"
        f" connected={res.connected} fl={patch.altitude_ft}"
        f" mach={patch.mach} ff={patch.fuel_flow_kg_h}"
        f" ({patch.fuel_flow_source or 'no fuel-flow source'})"
    )
    return res


def _live_state_to_patch(
    live: LiveSimState,
    *,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
) -> tuple[SimConnectFlightStatePatch, str | None, dict[str, Any] | None]:
    # Remaining distance: prefer the synced SimBrief route profile, then fall back.
    remaining_nm, remaining_distance_source, remaining_distance_details = (
        _resolve_remaining_distance(
            live,
            destination_lat=destination_lat,
            destination_lon=destination_lon,
        )
    )

    # Wind component: use AIRCRAFT_WIND_Z directly (longitudinal axis).
    # This is the most reliable source — no track calculation needed.
    wind_component_kt = live.wind_component_along_track()

    return (
        SimConnectFlightStatePatch(
            aircraft=_live_aircraft_code(live),
            aircraftConfig=_live_aircraft_config(live),
            altitudeFt=live.altitude_ft,
            grossWeightKg=live.gross_weight_kg,
            mach=live.mach,
            currentCostIndex=(
                live.fmc_snapshot.cost_index
                if live.fmc_snapshot is not None
                else None
            ),
            fmcSource=(
                live.fmc_snapshot.source
                if live.fmc_snapshot is not None
                else None
            ),
            fmcCruiseFlightLevel=(
                live.fmc_snapshot.cruise_flight_level
                if live.fmc_snapshot is not None
                else None
            ),
            fmcStepClimbDistanceNm=(
                live.fmc_snapshot.step_climb.distance_nm
                if live.fmc_snapshot is not None and live.fmc_snapshot.step_climb is not None
                else None
            ),
            fuelRemainingKg=live.fuel_remaining_kg,
            fuelFlowKgH=live.fuel_flow_kg_h,
            fuelFlowSource=live.fuel_flow_source,
            groundSpeedKt=live.ground_speed_kt,
            isaDeviationC=live.isa_deviation_c,
            windComponentKt=wind_component_kt,
            remainingDistanceNm=remaining_nm,
            gpsEteSeconds=live.gps_ete_seconds if live.gps_is_active_flight_plan else None,
            gpsEtaSeconds=live.gps_eta_seconds if live.gps_is_active_flight_plan else None,
        ),
        remaining_distance_source,
        remaining_distance_details,
    )


def _live_aircraft_entry(live: LiveSimState):
    from optimizer.configs.aircraft.aircraft_catalog import resolve_aircraft_from_title

    return resolve_aircraft_from_title(live.aircraft_title)


def _live_aircraft_code(live: LiveSimState) -> str | None:
    entry = _live_aircraft_entry(live)
    return entry.simbrief_code if entry is not None else None


def _live_aircraft_config(live: LiveSimState) -> str | None:
    entry = _live_aircraft_entry(live)
    return entry.config_key if entry is not None else None

def _resolve_remaining_distance(
    live: LiveSimState,
    *,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
) -> tuple[float | None, str | None, dict[str, Any] | None]:
    fmc_snapshot = live.fmc_snapshot
    fmc_remaining_nm = (
        fmc_snapshot.destination_distance_nm()
        if fmc_snapshot is not None
        else None
    )
    if fmc_remaining_nm is not None:
        return round(float(fmc_remaining_nm), 2), "FMC_ADAPTER", {
            "destinationIdent": (
                fmc_snapshot.destination.ident
                if fmc_snapshot is not None and fmc_snapshot.destination is not None
                else None
            ),
            "destinationEtaZulu": (
                fmc_snapshot.destination.eta_zulu
                if fmc_snapshot is not None and fmc_snapshot.destination is not None
                else None
            ),
            "destinationFuel": (
                fmc_snapshot.destination.fuel
                if fmc_snapshot is not None and fmc_snapshot.destination is not None
                else None
            ),
            "page": fmc_snapshot.page if fmc_snapshot is not None else None,
            "adapterKey": (
                fmc_snapshot.adapter_key if fmc_snapshot is not None else None
            ),
        }

    route_estimate = estimate_route_remaining_distance(
        current_lat=live.latitude,
        current_lon=live.longitude,
        route_profile=_remaining_route_profile,
    )
    if route_estimate is not None:
        return (
            float(route_estimate["remainingDistanceNm"]),
            "SIMBRIEF_ROUTE",
            route_estimate,
        )

    gps_remaining_nm = (
        live.gps_remaining_distance_nm
        if live.gps_is_active_flight_plan and _positive(live.gps_remaining_distance_nm)
        else None
    )
    if gps_remaining_nm is not None:
        return round(float(gps_remaining_nm), 2), "GPS_FLIGHT_PLAN", None

    direct_remaining_nm = live.get_remaining_distance_nm(
        destination_lat=destination_lat,
        destination_lon=destination_lon,
    )
    if direct_remaining_nm is not None:
        return round(float(direct_remaining_nm), 2), "DESTINATION_GC", None

    return None, None, None


def _build_warnings(
    live: LiveSimState,
    *,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
    remaining_distance_nm: float | None = None,
    remaining_distance_source: str | None = None,
) -> list[str]:
    warnings: list[str] = []

    if live.altitude_ft is None:
        warnings.append("SimConnect did not return indicated or pressure altitude.")

    if live.gross_weight_kg is None:
        warnings.append("SimConnect did not return gross weight.")

    if live.mach is None:
        warnings.append("SimConnect did not return Mach.")

    if live.fuel_remaining_kg is None:
        warnings.append("SimConnect did not return fuel remaining.")

    if live.fuel_flow_kg_h is None:
        warnings.append(
            "SimConnect did not return usable fuel flow; optimization will not use a static fuel-flow fallback."
        )

    if (
        live.fmc_adapter_status not in {None, "inactive", "connected"}
        and live.fmc_adapter_error
        and live.aircraft_title
    ):
        warnings.append(live.fmc_adapter_error)
    elif live.fmc_adapter_status == "starting" and live.aircraft_title:
        warnings.append(
            "Aircraft-specific FMC bridge is active but no supported FMC page has been parsed yet. "
            "Open the relevant progress page to enable FMC-derived telemetry."
        )

    if live.ground_speed_kt is None:
        warnings.append("SimConnect did not return ground speed.")

    if live.isa_deviation_c is None:
        warnings.append("SimConnect did not return enough data to calculate ISA deviation.")

    if live.wind_z_kt is None and live.wind_velocity_kt is None:
        warnings.append("SimConnect did not return wind data.")

    if remaining_distance_source in {"FMC_ADAPTER", "SIMBRIEF_ROUTE"}:
        pass
    elif _remaining_route_profile is not None and remaining_distance_nm is not None:
        warnings.append(
            "Remaining distance fell back from the synced SimBrief route profile to a less accurate source."
        )
    elif remaining_distance_source in {"GPS_FLIGHT_PLAN", "DESTINATION_GC"}:
        pass
    elif remaining_distance_nm is None:
        warnings.append(
            _missing_remaining_distance_warning(
                destination_lat=destination_lat,
                destination_lon=destination_lon,
            )
        )

    return warnings


def _resolve_destination_coordinates(
    *,
    destination_lat: float | None,
    destination_lon: float | None,
    destination: str | None,
) -> tuple[float | None, float | None, str | None]:
    if destination_lat is not None and destination_lon is not None:
        return destination_lat, destination_lon, None

    from data_fetcher.sim.airport_lookup import lookup_airport_coordinates

    coordinates = lookup_airport_coordinates(destination)
    if coordinates is None:
        return destination_lat, destination_lon, None

    lat, lon = coordinates
    return (
        lat,
        lon,
        f"Destination coordinates were resolved from airport database for {str(destination).strip().upper()}.",
    )


def _raw_summary(
    live: LiveSimState,
    *,
    remaining_distance_source: str | None = None,
    remaining_distance_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "aircraft_title": live.aircraft_title,
        "altitude_ft": live.altitude_ft,
        "pressure_altitude_ft": live.pressure_altitude_ft,
        "true_altitude_ft": live.true_altitude_ft,
        "flight_level": live.flight_level,
        "mach": live.mach,
        "true_airspeed_kt": live.true_airspeed_kt,
        "ground_speed_kt": live.ground_speed_kt,
        "vertical_speed_fpm": live.vertical_speed_fpm,
        "gross_weight_kg": live.gross_weight_kg,
        "fuel_remaining_kg": live.fuel_remaining_kg,
        "fuel_flow_kg_h": live.fuel_flow_kg_h,
        "fuel_flow_source": live.fuel_flow_source,
        "latitude": live.latitude,
        "longitude": live.longitude,
        "wind_velocity_kt": live.wind_velocity_kt,
        "wind_direction_deg": live.wind_direction_deg,
        "wind_x_kt": live.wind_x_kt,
        "wind_z_kt": live.wind_z_kt,
        "ambient_temperature_c": live.ambient_temperature_c,
        "isa_deviation_c": live.isa_deviation_c,
        "on_ground": live.on_ground,
        "navigation_source": _navigation_source(live).lower(),
        "gps_is_active_flight_plan": live.gps_is_active_flight_plan,
        "gps_ete_seconds": live.gps_ete_seconds,
        "gps_eta_seconds": live.gps_eta_seconds,
        "gps_remaining_distance_nm": live.gps_remaining_distance_nm,
        "gps_waypoint_distance_nm": live.gps_waypoint_distance_nm,
        "gps_ground_speed_kt": live.gps_ground_speed_kt,
        "fmc_adapter_status": live.fmc_adapter_status,
        "fmc_adapter_error": live.fmc_adapter_error,
        "remaining_distance_source": (
            remaining_distance_source.lower()
            if remaining_distance_source is not None
            else None
        ),
        "route_profile_segment_count": (
            _remaining_route_profile.segment_count
            if _remaining_route_profile is not None
            else None
        ),
        "route_profile_total_distance_nm": (
            _remaining_route_profile.total_distance_nm
            if _remaining_route_profile is not None
            else None
        ),
    }

    if remaining_distance_details is not None:
        summary.update(
            {
                "fmc_destination_ident": remaining_distance_details.get(
                    "destinationIdent"
                ),
                "fmc_destination_eta_zulu": remaining_distance_details.get(
                    "destinationEtaZulu"
                ),
                "fmc_destination_fuel": remaining_distance_details.get(
                    "destinationFuel"
                ),
                "fmc_page": remaining_distance_details.get("page"),
                "fmc_adapter_key": remaining_distance_details.get("adapterKey"),
                "route_profile_active_segment_index": remaining_distance_details.get(
                    "activeSegmentIndex"
                ),
                "route_profile_active_waypoint": remaining_distance_details.get(
                    "activeWaypointIdent"
                ),
                "route_profile_segment_deviation_nm": remaining_distance_details.get(
                    "segmentDeviationNm"
                ),
                "route_profile_distance_to_next_waypoint_nm": remaining_distance_details.get(
                    "distanceToNextWaypointNm"
                ),
            }
        )

    if live.fmc_snapshot is not None:
        summary.update(
            {
                "fmc_aircraft": live.fmc_snapshot.aircraft,
                "fmc_source": live.fmc_snapshot.source,
                "fmc_cdu_index": live.fmc_snapshot.cdu_index,
                "fmc_flight_number": live.fmc_snapshot.flight_number,
                "fmc_cost_index": live.fmc_snapshot.cost_index,
                "fmc_cruise_flight_level": live.fmc_snapshot.cruise_flight_level,
                "fmc_econ_speed_mach": live.fmc_snapshot.econ_speed_mach,
                "fmc_destination_ident": (
                    live.fmc_snapshot.destination.ident
                    if live.fmc_snapshot.destination is not None
                    else None
                ),
                "fmc_destination_eta_zulu": (
                    live.fmc_snapshot.destination.eta_zulu
                    if live.fmc_snapshot.destination is not None
                    else None
                ),
                "fmc_destination_fuel": (
                    live.fmc_snapshot.destination.fuel
                    if live.fmc_snapshot.destination is not None
                    else None
                ),
                "fmc_to_waypoint_ident": (
                    live.fmc_snapshot.to_waypoint.ident
                    if live.fmc_snapshot.to_waypoint is not None
                    else None
                ),
                "fmc_to_waypoint_distance_nm": (
                    live.fmc_snapshot.to_waypoint.dtg_nm
                    if live.fmc_snapshot.to_waypoint is not None
                    else None
                ),
                "fmc_next_waypoint_ident": (
                    live.fmc_snapshot.next_waypoint.ident
                    if live.fmc_snapshot.next_waypoint is not None
                    else None
                ),
                "fmc_next_waypoint_distance_nm": (
                    live.fmc_snapshot.next_waypoint.dtg_nm
                    if live.fmc_snapshot.next_waypoint is not None
                    else None
                ),
                "fmc_step_climb_time_zulu": (
                    live.fmc_snapshot.step_climb.time_zulu
                    if live.fmc_snapshot.step_climb is not None
                    else None
                ),
                "fmc_step_climb_distance_nm": (
                    live.fmc_snapshot.step_climb.distance_nm
                    if live.fmc_snapshot.step_climb is not None
                    else None
                ),
            }
        )

    return summary


def _missing_remaining_distance_warning(
    *,
    destination_lat: float | None,
    destination_lon: float | None,
) -> str:
    if _remaining_route_profile is not None:
        return (
            "Remaining distance could not be calculated from live position and the "
            "synced SimBrief route profile."
        )
    if destination_lat is None or destination_lon is None:
        return (
            "No synced SimBrief route profile or destination coordinates are available "
            "for live remaining-distance calculation."
        )
    return "Remaining distance could not be calculated from live position and destination."


async def _get_telemetry_snapshot(
    *,
    refresh_if_empty: bool = True,
) -> TelemetrySnapshot:
    hub = get_telemetry_hub()
    snapshot = await hub.get_snapshot()
    if refresh_if_empty and snapshot.live_state is None and snapshot.last_error is None:
        snapshot = await hub.refresh_now()
    return snapshot


def _collector_status(snapshot: TelemetrySnapshot) -> str:
    if snapshot.connected:
        return "connected"
    if snapshot.last_error is not None:
        return "disconnected"
    return "warming_up"


def _collector_warnings(
    snapshot: TelemetrySnapshot,
    *,
    fallback: str,
) -> list[str]:
    if snapshot.last_error is not None:
        return [snapshot.last_error]
    return [fallback]


def _format_snapshot_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _navigation_source(live: LiveSimState) -> str:
    if live.gps_is_active_flight_plan and (
        _positive(live.gps_remaining_distance_nm)
        or _positive(live.gps_ete_seconds)
    ):
        return "GPS"
    return "SimConnect"
