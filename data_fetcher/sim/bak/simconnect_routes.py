from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.simconnect_client import SimConnectClient, SimConnectClientError
from delay_module.eta_calculator import DelayTriggerConfig, compute_eta_if_possible


router = APIRouter(prefix="/api/simconnect", tags=["simconnect"])

_client: SimConnectClient | None = None


class SimConnectFlightStatePatch(BaseModel):
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    gross_weight_kg: float | None = Field(default=None, alias="grossWeightKg")
    mach: float | None = None

    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    fuel_remaining_kg: float | None = Field(default=None, alias="fuelRemainingKg")
    ground_speed_kt: float | None = Field(default=None, alias="groundSpeedKt")

    model_config = {
        "populate_by_name": True,
    }


class SimConnectTelemetryResponse(BaseModel):
    source: str = "SimConnect"
    connected: bool

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

    client = _get_client()
    warnings: list[str] = []

    try:
        live = await client.get_live_state()
    except SimConnectClientError as exc:
        _reset_client()
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
            warnings=[str(exc)],
        )

    remaining_nm = live.get_remaining_distance_nm(
        destination_lat=destination_lat,
        destination_lon=destination_lon,
    ) if hasattr(live, "get_remaining_distance_nm") else None

    if remaining_nm is None:
        warnings.append(
            "Remaining distance not available from SimConnect. "
            "Provide destinationLat/destinationLon for live calculation."
        )

    config = DelayTriggerConfig(
        target_delay_min=target_delay_min,
        last_known_delay_min=last_delay_min,
    )

    eta = compute_eta_if_possible(
        remaining_distance_nm=remaining_nm or live.ground_speed_kt,
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
            remainingDistanceNm=live.ground_speed_kt or 0,
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
def simconnect_status() -> dict[str, Any]:
    return {
        "source": "SimConnect",
        "available": True,
        "client": "data_fetcher.sim.simconnect_client.SimConnectClient",
    }


@router.get("/telemetry", response_model=SimConnectTelemetryResponse)
async def simconnect_telemetry() -> SimConnectTelemetryResponse:
    """
    Reads live aircraft telemetry from MSFS via the existing SimConnectClient.

    Important:
    - This endpoint returns only real SimConnect telemetry.
    - It does not generate fallback/test/default values.
    - It does not overwrite OFP-only fields like route distance or flight number.
    """

    client = _get_client()

    try:
        live = await client.get_live_state()
    except SimConnectClientError as exc:
        _reset_client()
        return SimConnectTelemetryResponse(
            connected=False,
            flightStatePatch=SimConnectFlightStatePatch(),
            rawSummary={},
            warnings=[str(exc)],
        )
    except Exception as exc:
        _reset_client()
        return SimConnectTelemetryResponse(
            connected=False,
            flightStatePatch=SimConnectFlightStatePatch(),
            rawSummary={},
            warnings=[f"Unexpected SimConnect telemetry error: {exc}"],
        )

    patch = _live_state_to_patch(live)
    warnings = _build_warnings(live)

    return SimConnectTelemetryResponse(
        connected=True,
        flightStatePatch=patch,
        rawSummary=_raw_summary(live),
        warnings=warnings,
    )


def _get_client() -> SimConnectClient:
    global _client

    if _client is None:
        _client = SimConnectClient(cache_ms=200)

    return _client


def _reset_client() -> None:
    global _client

    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass

    _client = None


def _live_state_to_patch(live: LiveSimState) -> SimConnectFlightStatePatch:
    return SimConnectFlightStatePatch(
        altitudeFt=live.altitude_ft,
        grossWeightKg=live.gross_weight_kg,
        mach=live.mach,
        fuelRemainingKg=live.fuel_remaining_kg,
        groundSpeedKt=live.ground_speed_kt,
        isaDeviationC=live.isa_deviation_c,

        # Important:
        # LiveSimState has wind velocity/direction, not wind component.
        # windComponentKt requires track/course-to-destination.
        # Until we calculate that from route/position, do not fake it.
        windComponentKt=None,
    )


def _build_warnings(live: LiveSimState) -> list[str]:
    warnings: list[str] = []

    if live.altitude_ft is None:
        warnings.append("SimConnect did not return altitude.")

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

    if live.wind_velocity_kt is not None and live.wind_direction_deg is not None:
        warnings.append(
            "SimConnect returned wind velocity/direction, but wind component was not calculated yet."
        )

    return warnings


def _raw_summary(live: LiveSimState) -> dict[str, Any]:
    return {
        "altitude_ft": live.altitude_ft,
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
        "ambient_temperature_c": live.ambient_temperature_c,
        "isa_deviation_c": live.isa_deviation_c,
        "on_ground": live.on_ground,
    }