from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field


# ============================================================
# OPERATIONAL TRIGGERS
# ============================================================

class OperationalTrigger(str, Enum):
    """
    OperationalTrigger beschreibt den Anlass für eine neue Berechnung.

    Beispiel:
    - Weather forecast wurde aktualisiert
    - Connex Info kam rein
    - ATC gab neue Speed/FL Restriction
    - Re-route erhalten
    - Pilot möchte manuell neu berechnen

    Der Trigger ist NICHT zwingend gleich dem Optimierungsziel.
    Weather Update bedeutet z.B. meistens:
      neue Forecast-Daten anwenden -> DCI/CI neu berechnen.
    """

    MANUAL_RECALCULATION = "MANUAL_RECALCULATION"

    # Flight progress / time adherence
    TIME_DEVIATION_DETECTED = "TIME_DEVIATION_DETECTED"
    TARGET_ON_BLOCK_UPDATED = "TARGET_ON_BLOCK_UPDATED"

    # Forecast / weather updates
    WEATHER_FORECAST_UPDATED = "WEATHER_FORECAST_UPDATED"
    WIND_PROFILE_UPDATED = "WIND_PROFILE_UPDATED"
    TEMPERATURE_PROFILE_UPDATED = "TEMPERATURE_PROFILE_UPDATED"

    # Operational / ATM
    REROUTE_RECEIVED = "REROUTE_RECEIVED"
    ATC_SPEED_CONSTRAINT = "ATC_SPEED_CONSTRAINT"
    ATC_LEVEL_CONSTRAINT = "ATC_LEVEL_CONSTRAINT"
    HOLDING_OR_METERING_EXPECTED = "HOLDING_OR_METERING_EXPECTED"
    ARRIVAL_RUNWAY_OR_PROCEDURE_CHANGED = "ARRIVAL_RUNWAY_OR_PROCEDURE_CHANGED"

    # Airline / dispatch / ACARS
    CONNEX_INFO_RECEIVED = "CONNEX_INFO_RECEIVED"
    CREW_DUTY_PRESSURE_UPDATED = "CREW_DUTY_PRESSURE_UPDATED"
    CURFEW_RISK_UPDATED = "CURFEW_RISK_UPDATED"
    REACTIONARY_DELAY_RISK_UPDATED = "REACTIONARY_DELAY_RISK_UPDATED"

    # Sim / virtual airline
    VA_SCORING_RISK_UPDATED = "VA_SCORING_RISK_UPDATED"
    VATSIM_EVENT_FLOW_UPDATED = "VATSIM_EVENT_FLOW_UPDATED"


# ============================================================
# SCENARIO TYPES
# ============================================================

class ScenarioType(str, Enum):
    """
    ScenarioType beschreibt die Optimierungsart / operative Situation.

    Wichtig:
    Ein Weather Update ist langfristig eher Trigger als Hauptobjective.
    Trotzdem behalten wir WEATHER_UPDATE als ScenarioType, damit du es
    direkt testen kannst.
    """

    NORMAL_COST_OPTIMIZATION = "NORMAL_COST_OPTIMIZATION"

    # Airline-like scenarios
    FIXED_SPEED_FL = "FIXED_SPEED_FL"
    TARGET_ON_BLOCK = "TARGET_ON_BLOCK"
    CONNEX_RECOVERY = "CONNEX_RECOVERY"
    REROUTE_RECOVERY = "REROUTE_RECOVERY"
    WEATHER_UPDATE = "WEATHER_UPDATE"

    # VATSIM / operational constraints
    HOLDING_EXPECTED = "HOLDING_EXPECTED"
    ATC_SPEED_CONSTRAINT = "ATC_SPEED_CONSTRAINT"
    ATC_LEVEL_CONSTRAINT = "ATC_LEVEL_CONSTRAINT"
    VATSIM_EVENT_FLOW = "VATSIM_EVENT_FLOW"

    # Advanced / later
    VA_SCORING = "VA_SCORING"
    OFP_DRIFT_CHECK = "OFP_DRIFT_CHECK"


class ScenarioPriority(str, Enum):
    """
    Priority beschreibt, wie teuer/riskant es wäre,
    das operative Problem nicht zu lösen.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DelayPhase(str, Enum):
    GATE = "gate"
    TAXI = "taxi"
    ENROUTE = "enroute"
    ARRIVAL = "arrival"


class ObjectiveType(str, Enum):
    """
    ObjectiveType beschreibt, wie der Optimizer Strategien bewerten soll.
    """

    MINIMIZE_TOTAL_COST = "MINIMIZE_TOTAL_COST"

    # Pilot3-like: Expected Total Cost minimieren
    MINIMIZE_EXPECTED_TOTAL_COST = "MINIMIZE_EXPECTED_TOTAL_COST"

    # OTP erreichen, danach Kosten minimieren
    MEET_TARGET_WITH_MIN_FUEL = "MEET_TARGET_WITH_MIN_FUEL"
    MEET_OTP_TARGET_THEN_MINIMIZE_COST = "MEET_OTP_TARGET_THEN_MINIMIZE_COST"

    # Connex / Dispatch budget
    MAX_RECOVERY_WITHIN_FUEL_BUDGET = "MAX_RECOVERY_WITHIN_FUEL_BUDGET"

    # Fixed ATC/FMC constraint
    FIXED_STRATEGY_EVALUATION = "FIXED_STRATEGY_EVALUATION"

    # Holding/Event: Recovery nicht sinnlos verheizen
    MINIMIZE_WASTED_RECOVERY = "MINIMIZE_WASTED_RECOVERY"

    # VA score mode
    MAXIMIZE_VA_SCORE = "MAXIMIZE_VA_SCORE"


# ============================================================
# INPUT GROUPS
# ============================================================

class FlightContextInput(BaseModel):
    """
    Allgemeiner Flugkontext.

    Wichtig für Lufthansa-Pilot-Logik:
    - Nutzung besonders sinnvoll bei Flugzeit > 1h
    - inbound FRA/MUC / Hub-Kontext
    - Route- und Schedule-Kontext
    """

    origin: str | None = None
    destination: str | None = None

    planned_block_time_min: float | None = None
    elapsed_flight_time_min: float | None = None
    remaining_flight_time_min: float | None = None

    is_hub_inbound: bool = False
    hub_airport: str | None = None
    hub_bank_id: str | None = None

    flight_number: str | None = None
    airline: str | None = None
    sibt_utc: str | None = None
    sobt_utc: str | None = None


class CostScenarioInput(BaseModel):
    """
    Dynamische Kostenannahmen.

    Wenn Werte None sind, nutzt die Cost Engine später Config-Fallbacks.
    """

    fuel_price_eur_per_kg: float | None = None
    ets_eur_per_kg: float | None = None
    fuel_surcharge_eur_per_kg: float | None = None

    cockpit_cost_eur_per_h: float | None = None
    cabin_cost_eur_per_h: float | None = None
    maintenance_cost_eur_per_h: float | None = None
    ownership_cost_eur_per_h: float | None = None
    schedule_cost_eur_per_h: float | None = None

    time_cost_override_eur_per_h: float | None = None
    delay_cost_override_eur_per_min: float | None = None

    delay_phase: DelayPhase = DelayPhase.ENROUTE
    reactionary_delay: bool = False


class TimingScenarioInput(BaseModel):
    """
    Zeit-/Delay-Daten.
    """

    current_eta_utc: str | None = None
    planned_arrival_utc: str | None = None
    scheduled_arrival_utc: str | None = None
    target_on_block_utc: str | None = None

    current_delay_min: float | None = None
    target_delay_min: float | None = None

    required_time_recovery_min: float | None = None
    max_acceptable_delay_min: float | None = None


class ConnexGroupInput(BaseModel):
    """
    Eine einzelne Connection-Gruppe.

    Damit kann später abgebildet werden:
    - Gruppe A verpasst Anschluss schon bei +3 min
    - Gruppe B erst bei +12 min
    - Gruppe C hat Hotelrisiko / letzte Verbindung
    """

    affected_pax: int
    destination: str | None = None
    outbound_flight: str | None = None

    scheduled_departure_utc: str | None = None
    latest_transfer_arrival_utc: str | None = None

    minimum_connection_time_min: float | None = None
    connection_buffer_min: float | None = None

    acceptable_extra_fuel_kg_per_pax: float | None = None

    last_connection_of_day: bool = False
    hotel_risk: bool = False
    longhaul_connection: bool = False
    group_booking: bool = False
    passenger_compensation_risk: bool = False


class ConnexScenarioInput(BaseModel):
    """
    Connecting Passenger / Connex Integration.

    Lufthansa-Pilot-Logik:
      affected_pax * acceptable_extra_fuel_kg_per_pax
      = maximal akzeptabler Extra-Fuel-Verbrauch.
    """

    affected_pax: int | None = None
    acceptable_extra_fuel_kg_per_pax: float | None = None
    connex_fuel_budget_kg_override: float | None = None

    # Advanced: mehrere Connex-Gruppen
    connection_groups: list[ConnexGroupInput] = Field(default_factory=list)

    hub_airport: str | None = None
    inbound_hub_wave: str | None = None

    minimum_connection_time_min: float | None = None
    connection_buffer_min: float | None = None

    last_connection_of_day: bool = False
    hotel_risk: bool = False
    longhaul_connection: bool = False
    group_booking: bool = False

    passenger_compensation_risk: bool = False
    passenger_soft_cost_relevant: bool = False


class RerouteScenarioInput(BaseModel):
    """
    Re-route / Distance update.
    """

    reroute_received: bool = False

    old_remaining_distance_nm: float | None = None
    new_remaining_distance_nm: float | None = None
    distance_delta_nm: float | None = None

    reason: str | None = None


class FixedConstraintInput(BaseModel):
    """
    Feste ATC-/Operational Constraints.
    """

    fixed_mach: float | None = None
    fixed_flight_level: int | None = None

    assigned_mach: float | None = None
    assigned_flight_level: int | None = None

    max_mach: float | None = None
    min_mach: float | None = None

    constraint_until: str | None = None


class HoldingScenarioInput(BaseModel):
    """
    Holding / Arrival metering / Sequencing.
    """

    expected_holding_min: float | None = None
    arrival_metering_delay_min: float | None = None
    expected_extra_track_nm: float | None = None

    holding_absorbs_recovery: bool = True


class CrewDutyScenarioInput(BaseModel):
    """
    Crew Duty Pressure.
    """

    prior_duty_time_min: float | None = None
    projected_duty_time_min: float | None = None
    duty_limit_min: float | None = None
    duty_margin_min: float | None = None

    augmented_crew: bool | None = None


class CurfewScenarioInput(BaseModel):
    """
    Curfew Risk.
    """

    curfew_airport: str | None = None
    curfew_time_utc: str | None = None
    curfew_margin_min: float | None = None

    diversion_if_missed: bool = False


class SlotFlowScenarioInput(BaseModel):
    """
    Slot / CTOT / flow restrictions.
    """

    ctot_utc: str | None = None
    ctm_required: bool = False

    slot_margin_min: float | None = None
    flow_delay_min: float | None = None


class WeatherScenarioInput(BaseModel):
    """
    Weather / forecast update.

    Wichtig:
    Diese Daten sind primär neue Performance-/Forecast-Inputs.
    Der Interpreter setzt Objective/Reasons, aber die eigentliche
    Performance-Wirkung passiert über scenario_state_applier oder
    später über eine Route-Forecast-Engine.
    """

    weather_update_received: bool = False

    old_wind_component_kt: float | None = None
    new_wind_component_kt: float | None = None
    wind_error_kt: float | None = None

    old_isa_deviation_c: float | None = None
    new_isa_deviation_c: float | None = None
    isa_error_c: float | None = None

    turbulence_expected: bool = False
    step_climb_blocked: bool = False

    expected_weather_reroute_nm: float | None = None
    arrival_weather_delay_min: float | None = None

    recommended_speed_limit_mach: float | None = None
    updated_forecast_source: str | None = None


class ArrivalUncertaintyInput(BaseModel):
    """
    Pilot3-artige Arrival-Uncertainty.

    Diese Werte sind wichtig, weil eine im Cruise gesparte Minute nicht
    zwingend als gesparte Gate-Minute ankommt.
    """

    expected_holding_min: float | None = None
    holding_std_min: float | None = None

    expected_sequencing_delay_min: float | None = None
    expected_extra_track_nm: float | None = None

    expected_taxi_in_min: float | None = None
    taxi_in_std_min: float | None = None

    # MVP-Faktor:
    # 1.0 = erwartete Holding/Metering absorbiert Speed-Recovery voll
    # 0.5 = nur 50% der erwarteten Arrival-Uncertainty absorbiert Recovery
    recovery_absorption_factor: float = 1.0

    sigma_min: float = 5.0
    """
    Standard deviation of gate arrival time (minutes).

    Captures the combined uncertainty from:
      - Holding time variability
      - Sequencing and merging distance variability
      - Taxi-in time variability

    Used by cost_optimizer to compute E[IROPs(T)] instead of IROPs(E[T]).
    This is the key Pilot3 correction: E[f(T)] != f(E[T]) for a non-linear
    (step-wise) cost function.

    Typical values:
      European major airport, off-peak:  3–5 min
      European major airport, peak hour: 6–10 min
      Known holding expected:            15–25 min (use expected_holding_min too)

    Set to 0.0 to disable distribution integration and use deterministic cost.
    """


class OperationalRiskInput(BaseModel):
    """
    Späterer Expected-Cost-Unterbau.

    Priority ist nur ein Label.
    Diese Felder erlauben langfristig:
      expected_cost = probability * impact
    """

    missed_connection_probability: float | None = None
    curfew_breach_probability: float | None = None
    reactionary_delay_probability: float | None = None
    passenger_compensation_probability: float | None = None

    estimated_missed_connection_cost_eur: float | None = None
    estimated_curfew_breach_cost_eur: float | None = None
    estimated_reactionary_delay_cost_eur: float | None = None
    estimated_compensation_cost_eur: float | None = None


class VirtualAirlineScenarioInput(BaseModel):
    """
    Virtual Airline specific scoring.
    """

    enabled: bool = False
    va_name: str | None = None

    on_time_score_weight: float = 1.0
    fuel_score_weight: float = 1.0
    comfort_score_weight: float = 0.0

    schedule_target_utc: str | None = None
    pirep_late_threshold_min: float | None = 15.0

    fuel_penalty_threshold_kg: float | None = None


class VatsimScenarioInput(BaseModel):
    """
    VATSIM/Event-spezifische Situation.
    """

    enabled: bool = False
    event_mode: bool = False

    expected_holding_min: float | None = None
    atc_reroute: bool = False
    arrival_metering_delay_min: float | None = None

    oceanic_level_restriction: bool = False
    assigned_speed_mach: float | None = None
    assigned_flight_level: int | None = None


# ============================================================
# MAIN SCENARIO INPUT
# ============================================================

class ScenarioInput(BaseModel):
    """
    Zentrales Input-Objekt aus EFB/UI/ACARS/VA/Manual Input.

    Es beschreibt:
    - welcher Trigger die Neuberechnung ausgelöst hat
    - welche operative Situation gilt
    - welche neuen Daten vorliegen
    - welche Constraints und Cost-Faktoren relevant sind
    """

    trigger: OperationalTrigger = OperationalTrigger.MANUAL_RECALCULATION
    source: str | None = None
    raw_message: str | None = None

    scenario_type: ScenarioType = ScenarioType.NORMAL_COST_OPTIMIZATION
    priority: ScenarioPriority = ScenarioPriority.MEDIUM

    flight_context: FlightContextInput = Field(default_factory=FlightContextInput)
    cost: CostScenarioInput = Field(default_factory=CostScenarioInput)
    timing: TimingScenarioInput = Field(default_factory=TimingScenarioInput)
    connex: ConnexScenarioInput = Field(default_factory=ConnexScenarioInput)
    reroute: RerouteScenarioInput = Field(default_factory=RerouteScenarioInput)
    fixed_constraints: FixedConstraintInput = Field(default_factory=FixedConstraintInput)
    holding: HoldingScenarioInput = Field(default_factory=HoldingScenarioInput)
    crew_duty: CrewDutyScenarioInput = Field(default_factory=CrewDutyScenarioInput)
    curfew: CurfewScenarioInput = Field(default_factory=CurfewScenarioInput)
    slot_flow: SlotFlowScenarioInput = Field(default_factory=SlotFlowScenarioInput)
    weather: WeatherScenarioInput = Field(default_factory=WeatherScenarioInput)
    arrival_uncertainty: ArrivalUncertaintyInput = Field(default_factory=ArrivalUncertaintyInput)
    operational_risk: OperationalRiskInput = Field(default_factory=OperationalRiskInput)
    virtual_airline: VirtualAirlineScenarioInput = Field(default_factory=VirtualAirlineScenarioInput)
    vatsim: VatsimScenarioInput = Field(default_factory=VatsimScenarioInput)

    # General optimizer behavior
    allow_speed_up: bool = True
    allow_slow_down: bool = True

    min_mach: float | None = None
    max_mach: float | None = None
    mach_step: float = 0.005

    # Optional hard constraints
    max_extra_fuel_kg: float | None = None
    max_time_loss_min: float | None = None
    required_time_recovery_min: float | None = None


# ============================================================
# INTERPRETED SCENARIO
# ============================================================

class InterpretedScenario(BaseModel):
    """
    Output des scenario_interpreter.

    Der Optimizer sollte möglichst nur dieses Objekt lesen,
    nicht mehr alle Details aus ScenarioInput.
    """

    trigger: OperationalTrigger
    source: str | None = None
    raw_message: str | None = None

    scenario_type: ScenarioType
    objective: ObjectiveType
    priority: ScenarioPriority

    allow_speed_up: bool = True
    allow_slow_down: bool = True

    min_mach: float | None = None
    max_mach: float | None = None
    mach_step: float = 0.005

    # Derived constraints
    max_extra_fuel_kg: float | None = None
    required_time_recovery_min: float | None = None
    max_time_loss_min: float | None = None

    # Timing interpretation
    current_delay_min: float | None = None
    target_delay_min: float | None = None
    expected_holding_min: float | None = None
    arrival_metering_delay_min: float | None = None
    arrival_recovery_absorption_factor: float = 1.0

    # Cost interpretation
    fuel_price_eur_per_kg: float | None = None
    ets_eur_per_kg: float | None = None
    fuel_surcharge_eur_per_kg: float | None = None

    time_cost_override_eur_per_h: float | None = None
    delay_cost_override_eur_per_min: float | None = None
    delay_phase: DelayPhase = DelayPhase.ENROUTE
    reactionary_delay: bool = False

    # High-level explanation/debug
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
