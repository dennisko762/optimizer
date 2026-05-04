from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.telemetry_hub import TelemetrySnapshot, get_telemetry_hub
from delay_module.eta_calculator import DelayTriggerConfig, compute_eta_if_possible


router = APIRouter(prefix="/api/simconnect", tags=["simconnect"])

_destination_lat: float | None = None
_destination_lon: float | None = None


class DestinationPayload(BaseModel):
    lat: float
    lon: float


@router.post("/destination", status_code=204)
def set_destination(body: DestinationPayload) -> None:
    global _destination_lat, _destination_lon
    _destination_lat = body.lat
    _destination_lon = body.lon


class SimConnectFlightStatePatch(BaseModel):
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    gross_weight_kg: float | None = Field(default=None, alias="grossWeightKg")
    mach: float | None = None

    # Wind component along track (positive = tailwind, negative = headwind).
    # Populated from AIRCRAFT_WIND_X when available.
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    fuel_remaining_kg: float | None = Field(default=None, alias="fuelRemainingKg")
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

    effective_destination_lat, effective_destination_lon, destination_lookup_warning = _resolve_destination_coordinates(
        destination_lat=destination_lat,
        destination_lon=destination_lon,
        destination=destination,
    )
    if destination_lookup_warning is not None:
        warnings.append(destination_lookup_warning)

    remaining_nm = live.get_remaining_distance_nm(
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
    ) if hasattr(live, "get_remaining_distance_nm") else None

    if remaining_nm is None:
        warnings.append(
            "Remaining distance not available from SimConnect. "
            "Provide destinationLat/destinationLon or a destination ICAO code for live calculation."
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

    Pass destinationLat / destinationLon (from SimBrief sync) to enable live
    remaining distance calculation via Haversine.

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

    patch = _live_state_to_patch(
        live,
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
    )
    warnings = _build_warnings(
        live,
        destination_lat=effective_destination_lat,
        destination_lon=effective_destination_lon,
        remaining_distance_nm=patch.remaining_distance_nm,
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
        rawSummary=_raw_summary(live),
        warnings=warnings,
    )
    print("SimConnect telemetry response:", res.json())
    return res


def _live_state_to_patch(
    live: LiveSimState,
    *,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
) -> SimConnectFlightStatePatch:
    # Remaining distance: live Haversine when destination coords provided.
    gps_remaining_nm = (
        live.gps_remaining_distance_nm
        if live.gps_is_active_flight_plan and _positive(live.gps_remaining_distance_nm)
        else None
    )
    remaining_nm = gps_remaining_nm or live.get_remaining_distance_nm(
        destination_lat=destination_lat,
        destination_lon=destination_lon,
    )

    # Wind component: use AIRCRAFT_WIND_X directly (longitudinal axis).
    # This is the most reliable source — no track calculation needed.
    wind_component_kt = live.wind_component_along_track()

    return SimConnectFlightStatePatch(
        altitudeFt=live.altitude_ft,
        grossWeightKg=live.gross_weight_kg,
        mach=live.mach,
        fuelRemainingKg=live.fuel_remaining_kg,
        groundSpeedKt=live.ground_speed_kt,
        isaDeviationC=live.isa_deviation_c,
        windComponentKt=wind_component_kt,
        remainingDistanceNm=remaining_nm,
        gpsEteSeconds=live.gps_ete_seconds if live.gps_is_active_flight_plan else None,
        gpsEtaSeconds=live.gps_eta_seconds if live.gps_is_active_flight_plan else None,
    )


def _build_warnings(
    live: LiveSimState,
    *,
    destination_lat: float | None = None,
    destination_lon: float | None = None,
    remaining_distance_nm: float | None = None,
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

    if live.ground_speed_kt is None:
        warnings.append("SimConnect did not return ground speed.")

    if live.isa_deviation_c is None:
        warnings.append("SimConnect did not return enough data to calculate ISA deviation.")

    if live.wind_x_kt is None and live.wind_velocity_kt is None:
        warnings.append("SimConnect did not return wind data.")

    if live.gps_is_active_flight_plan and _positive(live.gps_remaining_distance_nm):
        pass
    elif destination_lat is None or destination_lon is None:
        warnings.append(
            "No destination coordinates available for live remaining-distance calculation."
        )
    elif remaining_distance_nm is None:
        warnings.append(
            "Remaining distance could not be calculated from live position and destination."
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


def _raw_summary(live: LiveSimState) -> dict[str, Any]:
    return {
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
        "latitude": live.latitude,
        "longitude": live.longitude,
        "wind_velocity_kt": live.wind_velocity_kt,
        "wind_direction_deg": live.wind_direction_deg,
        "wind_x_kt": live.wind_x_kt,
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
    }


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
