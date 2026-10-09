from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from optimizer.route_profile_models import RemainingRouteProfile


class EfbAction(str, Enum):
    NORMAL_RECALC = "NORMAL_RECALC"
    CONNEX_UPLINK = "CONNEX_UPLINK"
    TARGET_ON_BLOCK = "TARGET_ON_BLOCK"
    REROUTE = "REROUTE"
    WEATHER_REFRESH = "WEATHER_REFRESH"
    FIXED_SPEED_FL = "FIXED_SPEED_FL"
    HOLDING_OR_METERING = "HOLDING_OR_METERING"
    ATC_SPEED_CONSTRAINT = "ATC_SPEED_CONSTRAINT"
    ATC_LEVEL_CONSTRAINT = "ATC_LEVEL_CONSTRAINT"
    VATSIM_EVENT_FLOW = "VATSIM_EVENT_FLOW"
    VA_SCORING = "VA_SCORING"
    OFP_DRIFT_CHECK = "OFP_DRIFT_CHECK"


class UiCruiseSegment(BaseModel):
    distance_nm: float = Field(alias="distanceNm")
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    model_config = {
        "populate_by_name": True,
    }


class UiFlightState(BaseModel):
    aircraft: str | None = None
    """ICAO/SimBrief type code or raw SimConnect TITLE.

    Nullable on purpose: the live EFB sends whatever the sim gave it, which
    may be nothing. The aircraft performance profile is then resolved (and
    the request refused with a readable reason if it cannot be) by
    ``optimizer.api.aircraft_config_resolver`` — a non-nullable ``str`` here
    would 422 on an explicit ``null`` before that logic ever ran.
    """
    engine_variant: str | None = Field(default=None, alias="engineVariant")
    altitude_ft: float = Field(alias="altitudeFt")
    gross_weight_kg: float = Field(alias="grossWeightKg")
    mach: float
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

    remaining_distance_nm: float = Field(alias="remainingDistanceNm")
    route_distance_nm: float | None = Field(default=None, alias="routeDistanceNm")
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")
    fuel_remaining_kg: float | None = Field(default=None, alias="fuelRemainingKg")
    fuel_flow_kg_h: float | None = Field(default=None, alias="fuelFlowKgH")
    fuel_flow_source: str | None = Field(default=None, alias="fuelFlowSource")
    ground_speed_kt: float | None = Field(default=None, alias="groundSpeedKt")
    cruise_segments: list[UiCruiseSegment] = Field(default_factory=list, alias="cruiseSegments")

    pax_count: int | None = Field(default=None, alias="paxCount")
    """
    Total passengers on board.
    Populated by the SimBrief sync endpoint (weights.pax_count).
    Passed through to DynamicCostFactors.total_pax for IROPs soft cost.
    """

    model_config = {
        "populate_by_name": True,
    }


class UiFlightContext(BaseModel):
    origin: str | None = None
    destination: str | None = None

    planned_block_time_min: float | None = Field(default=None, alias="plannedBlockTimeMin")
    elapsed_flight_time_min: float | None = Field(default=None, alias="elapsedFlightTimeMin")
    remaining_flight_time_min: float | None = Field(default=None, alias="remainingFlightTimeMin")

    flight_number: str | None = Field(default=None, alias="flightNumber")
    airline: str | None = None
    sibt_utc: str | None = Field(default=None, alias="sibtUtc")
    sobt_utc: str | None = Field(default=None, alias="sobtUtc")

    model_config = {
        "populate_by_name": True,
    }


class OptimizeRequest(BaseModel):
    action: EfbAction

    aircraft_config: str | None = Field(default=None, alias="aircraftConfig")
    """Aircraft performance profile key (e.g. "a359", "b77w").

    OPTIONAL and explicitly nullable: both an absent key and ``null`` mean
    "derive it from flightState.aircraft". A bare ``str`` field would 422 on
    an explicit ``null`` (a Pydantic default only applies to an ABSENT key),
    which is exactly what broke the live optimizer when SimConnect supplied
    no aircraft config. When neither this field nor the aircraft identifier
    resolves to an existing YAML profile the request is refused — no default
    airframe is substituted (AGENTS.md).
    """

    flight_state: UiFlightState = Field(alias="flightState")
    flight_context: UiFlightContext = Field(alias="flightContext")
    remaining_route_profile: RemainingRouteProfile | None = Field(
        default=None,
        alias="remainingRouteProfile",
    )

    # Trigger-specific data from UI.
    # Examples:
    # CONNEX_UPLINK: {"currentEtaUtc": "18:50"}
    # TARGET_ON_BLOCK: {"currentDelayMin": 12, "targetDelayMin": 0}
    # REROUTE: {"distanceDeltaNm": 45}
    # WEATHER_REFRESH: {"newWindComponentKt": -35, "newIsaDeviationC": 2}
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {
        "populate_by_name": True,
    }


class StrategyResponse(BaseModel):
    cost_index: int | None = Field(alias="costIndex")
    mach: float
    speed_mode: str | None = Field(default=None, alias="speedMode")
    target_cas_kt: float | None = Field(default=None, alias="targetCasKt")
    cost_index_source: str | None = Field(default=None, alias="costIndexSource")
    cost_index_label: str | None = Field(default=None, alias="costIndexLabel")
    performance_ci_kg_per_min: float | None = Field(default=None, alias="performanceCiKgPerMin")
    flight_level: int | None = Field(default=None, alias="flightLevel")
    label: str | None = None

    fuel_kg: float = Field(alias="fuelKg")
    time_min: float = Field(alias="timeMin")
    total_cost_eur: float = Field(alias="totalCostEur")

    delta_fuel_kg: float = Field(alias="deltaFuelKg")
    delta_cruise_time_min: float = Field(alias="deltaCruiseTimeMin")
    gate_time_saved_min: float = Field(alias="gateTimeSavedMin")
    delta_cost_eur: float = Field(alias="deltaCostEur")

    allowed: bool
    rejection_reason: str | None = Field(default=None, alias="rejectionReason")

    model_config = {
        "populate_by_name": True,
    }


class AppliedStateResponse(BaseModel):
    wind_component_kt: float = Field(alias="windComponentKt")
    isa_deviation_c: float = Field(alias="isaDeviationC")
    remaining_distance_nm: float = Field(alias="remainingDistanceNm")

    model_config = {
        "populate_by_name": True,
    }


class InterpretedScenarioResponse(BaseModel):
    trigger: str
    scenario_type: str = Field(alias="scenarioType")
    objective: str
    priority: str
    reasons: list[str]
    warnings: list[str]

    model_config = {
        "populate_by_name": True,
    }


class ConnexConnectionResponse(BaseModel):
    outbound_flight: str = Field(alias="outboundFlight")
    destination: str
    gate: str | None
    etd_utc: str = Field(alias="etdUtc")
    ltop_utc: str = Field(alias="ltopUtc")

    affected_pax: int = Field(alias="affectedPax")
    fuel_budget_kg: float = Field(alias="fuelBudgetKg")

    margin_to_ltop_min: float = Field(alias="marginToLtopMin")
    required_recovery_min: float = Field(alias="requiredRecoveryMin")

    protected: bool
    at_risk: bool = Field(alias="atRisk")

    model_config = {
        "populate_by_name": True,
    }


class OperationalDataResponse(BaseModel):
    title: str
    status: str
    rows: list[tuple[str, str]]
    note: str | None = None

    connex_connections: list[ConnexConnectionResponse] = Field(
        default_factory=list,
        alias="connexConnections",
    )

    model_config = {
        "populate_by_name": True,
    }


class OptimizeResponse(BaseModel):
    recommendation: str
    optimizer_mode: str | None = Field(default=None, alias="optimizerMode")

    aircraft_config: str | None = Field(default=None, alias="aircraftConfig")
    """The performance profile actually used for this computation."""
    aircraft_config_source: str | None = Field(
        default=None,
        alias="aircraftConfigSource",
    )
    """Where that key came from — "request" or "aircraft:<identifier>"."""

    current_strategy: StrategyResponse = Field(alias="currentStrategy")
    best_strategy: StrategyResponse = Field(alias="bestStrategy")
    strategies: list[StrategyResponse]

    applied_state: AppliedStateResponse = Field(alias="appliedState")
    interpreted: InterpretedScenarioResponse
    operational_data: OperationalDataResponse = Field(alias="operationalData")

    model_config = {
        "populate_by_name": True,
    }
