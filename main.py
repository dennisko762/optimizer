from __future__ import annotations

from dataclasses import dataclass

from data_fetcher.sim.sim_models import CurrentFlightState

from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.cost_optimizer import optimize_cost
from optimizer.operational_data.connex_resolver import (
    build_demo_connex_uplink,
    resolve_connex_uplink,
)
from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_state_applier import apply_scenario_to_current_state
from optimizer.scenario_engine.scenario_models import (
    ArrivalUncertaintyInput,
    CostScenarioInput,
    FixedConstraintInput,
    FlightContextInput,
    HoldingScenarioInput,
    OperationalTrigger,
    RerouteScenarioInput,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
    TimingScenarioInput,
    VatsimScenarioInput,
    VirtualAirlineScenarioInput,
    WeatherScenarioInput,
)


@dataclass
class AircraftPreset:
    label: str
    config_key: str
    aircraft_code: str
    altitude_ft: float
    gross_weight_kg: float
    mach: float
    current_cost_index: int
    remaining_distance_nm: float
    wind_component_kt: float
    isa_deviation_c: float
    fuel_remaining_kg: float
    ground_speed_kt: float


# Mid-cruise defaults for a representative ~6–8 h long-haul sector.
# All values can be overridden during input.
AIRCRAFT_PRESETS: list[AircraftPreset] = [
    AircraftPreset("Boeing 777-200ER",  "b772", "B772", 35000, 210000, 0.840, 52, 2200, -10, 0, 40000, 485),
    AircraftPreset("Boeing 777-300ER",  "b77w", "B77W", 35000, 265000, 0.840, 52, 2500, -10, 0, 58000, 480),
    AircraftPreset("Boeing 777-200LR",  "b77l", "B77L", 37000, 240000, 0.840, 52, 4000, -15, 0, 88000, 490),
    AircraftPreset("Boeing 777F",       "b77f", "B77F", 35000, 275000, 0.840, 52, 3000,  -5, 0, 72000, 480),
    AircraftPreset("Airbus A330-300",   "a333", "A333", 35000, 175000, 0.820, 40, 1600, -10, 0, 24000, 462),
    AircraftPreset("Airbus A330-200",   "a332", "A332", 35000, 160000, 0.820, 40, 2200, -10, 0, 35000, 458),
    AircraftPreset("Airbus A340-300",   "a343", "A343", 35000, 200000, 0.820, 35, 2000, -10, 0, 42000, 460),
    AircraftPreset("Airbus A340-600",   "a346", "A346", 35000, 255000, 0.820, 35, 2500, -10, 0, 62000, 458),
    AircraftPreset("Boeing 747-400",    "b744", "B744", 35000, 285000, 0.855, 60, 2200, -10, 0, 58000, 495),
    AircraftPreset("Boeing 747-8",      "b748", "B748", 35000, 320000, 0.855, 60, 2500, -10, 0, 68000, 500),
    AircraftPreset("McDonnell Douglas MD-11", "md11", "MD11", 35000, 225000, 0.835, 60, 2500, -10, 0, 62000, 478),
]


def choose_aircraft_preset() -> AircraftPreset:
    print()
    print("============================================================")
    print("AIRCRAFT SELECTION")
    print("============================================================")
    for i, p in enumerate(AIRCRAFT_PRESETS, 1):
        print(f"  {i:>2}  {p.label}")
    print()

    while True:
        raw = input("Choose aircraft [1]: ").strip()
        if raw == "":
            return AIRCRAFT_PRESETS[0]
        try:
            idx = int(raw)
            if 1 <= idx <= len(AIRCRAFT_PRESETS):
                return AIRCRAFT_PRESETS[idx - 1]
        except ValueError:
            pass
        print(f"  Enter a number between 1 and {len(AIRCRAFT_PRESETS)}.")


def ask_str(prompt: str, default: str | None = None) -> str | None:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return default if value == "" else value


def ask_float(prompt: str, default: float | None = None) -> float | None:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()

    if value == "":
        return default

    return float(value.replace(",", "."))


def ask_int(prompt: str, default: int | None = None) -> int | None:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}: ").strip()

    if value == "":
        return default

    return int(float(value.replace(",", ".")))


def ask_bool(prompt: str, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    value = input(f"{prompt}{suffix}: ").strip().lower()

    if value == "":
        return default

    return value in {"y", "yes", "j", "ja", "true", "1"}


def internal_default_priority() -> ScenarioPriority:
    """
    Not a pilot input.

    This fallback exists only because ScenarioInput requires a priority.
    scenario_interpreter derives the actual priority from operational facts.
    """

    return ScenarioPriority.MEDIUM


def build_current_flight_state_from_efb_input(preset: AircraftPreset) -> CurrentFlightState:
    print()
    print("============================================================")
    print("CURRENT FLIGHT STATE")
    print("============================================================")
    print(f"Aircraft: {preset.label}  —  press Enter to accept preset defaults.")

    aircraft = ask_str("Aircraft performance code", preset.aircraft_code) or preset.aircraft_code

    altitude_ft = ask_float("Altitude ft", preset.altitude_ft)
    gross_weight_kg = ask_float("Gross weight kg", preset.gross_weight_kg)
    mach = ask_float("Current Mach", preset.mach)
    current_cost_index = ask_int("Current FMC/SimBrief Cost Index optional", preset.current_cost_index)
    remaining_distance_nm = ask_float("Remaining route distance NM", preset.remaining_distance_nm)

    wind_component_kt = ask_float(
        "Current/forecast avg wind component kt (+tailwind / -headwind)",
        preset.wind_component_kt,
    )

    isa_deviation_c = ask_float("ISA deviation °C", preset.isa_deviation_c)
    fuel_remaining_kg = ask_float("Fuel remaining kg optional", preset.fuel_remaining_kg)
    ground_speed_kt = ask_float("Ground speed kt optional", preset.ground_speed_kt)

    if altitude_ft is None:
        raise ValueError("altitude_ft is required")

    if gross_weight_kg is None:
        raise ValueError("gross_weight_kg is required")

    if mach is None:
        raise ValueError("mach is required")

    if remaining_distance_nm is None:
        raise ValueError("remaining_distance_nm is required")

    return CurrentFlightState(
        aircraft=aircraft,
        altitude_ft=altitude_ft,
        gross_weight_kg=gross_weight_kg,
        mach=mach,
        remaining_distance_nm=remaining_distance_nm,
        wind_component_kt=wind_component_kt or 0.0,
        isa_deviation_c=isa_deviation_c or 0.0,
        fuel_remaining_kg=fuel_remaining_kg,
        ground_speed_kt=ground_speed_kt,
        current_cost_index=current_cost_index,
)


def build_flight_context_input() -> FlightContextInput:
    print()
    print("============================================================")
    print("FLIGHT CONTEXT")
    print("============================================================")
    print("In the real app this comes from SimBrief/OFP.")

    origin = ask_str("Origin", "EDDM")
    destination = ask_str("Destination", "EDDF")

    planned_block_time_min = ask_float("Planned block time min", 65.0)
    elapsed_flight_time_min = ask_float("Elapsed flight time min optional", None)
    remaining_flight_time_min = ask_float("Remaining flight time min optional", None)

    is_hub_inbound = destination in {"EDDF", "EDDM", "MUC", "FRA"}
    hub_airport = destination if is_hub_inbound else None

    flight_number = ask_str("Flight number optional", "LH123")
    airline = ask_str("Airline / VA optional", "LHVirtual")

    return FlightContextInput(
        origin=origin,
        destination=destination,
        planned_block_time_min=planned_block_time_min,
        elapsed_flight_time_min=elapsed_flight_time_min,
        remaining_flight_time_min=remaining_flight_time_min,
        is_hub_inbound=is_hub_inbound,
        hub_airport=hub_airport,
        flight_number=flight_number,
        airline=airline,
    )


def build_cost_input() -> CostScenarioInput:
    """
    In a real EFB this should come from airline config/backend.

    For local testing we allow optional override, but these are not normal pilot inputs.
    """

    print()
    print("============================================================")
    print("COST PROFILE")
    print("============================================================")
    print("In the real app this comes from airline/VA config.")
    override = ask_bool("Override cost profile for debug?", False)

    if not override:
        return CostScenarioInput()

    fuel_price = ask_float("Fuel price €/kg optional", None)
    ets = ask_float("ETS €/kg optional", None)
    surcharge = ask_float("Fuel surcharge €/kg optional", None)
    time_cost_override = ask_float("Time cost override €/h optional", None)
    delay_cost_override = ask_float("Delay cost override €/min optional", None)
    reactionary_delay = ask_bool("Reactionary delay relevant?", False)

    return CostScenarioInput(
        fuel_price_eur_per_kg=fuel_price,
        ets_eur_per_kg=ets,
        fuel_surcharge_eur_per_kg=surcharge,
        time_cost_override_eur_per_h=time_cost_override,
        delay_cost_override_eur_per_min=delay_cost_override,
        reactionary_delay=reactionary_delay,
    )


def build_arrival_uncertainty_from_engine() -> ArrivalUncertaintyInput:
    """
    In a real app this comes from:
    - ATC flow
    - VATSIM event data
    - arrival weather
    - historical arrival/taxi estimates
    - SimBrief/OFP
    """

    return ArrivalUncertaintyInput(
        recovery_absorption_factor=1.0,
    )


def build_optimizer_behavior_from_action(
    *,
    allow_speed_up: bool,
    allow_slow_down: bool,
    max_mach: float | None,
) -> dict:
    """
    In the real UI this is mostly hidden.
    For test mode we expose only basic aircraft envelope behavior.
    """

    debug = ask_bool("Override speed search bounds for debug?", False)

    if not debug:
        return {
            "allow_speed_up": allow_speed_up,
            "allow_slow_down": allow_slow_down,
            "min_mach": None,
            "max_mach": max_mach,
            "mach_step": 0.005,
            "max_extra_fuel_kg": None,
            "max_time_loss_min": None,
            "required_time_recovery_min": None,
        }

    return {
        "allow_speed_up": ask_bool("Allow speed-up?", allow_speed_up),
        "allow_slow_down": ask_bool("Allow slow-down?", allow_slow_down),
        "min_mach": ask_float("Min Mach optional", None),
        "max_mach": ask_float("Max Mach optional", max_mach),
        "mach_step": ask_float("Mach step", 0.005) or 0.005,
        "max_extra_fuel_kg": ask_float("Max extra fuel kg optional", None),
        "max_time_loss_min": ask_float("Max time loss min optional", None),
        "required_time_recovery_min": ask_float("Required time recovery min optional", None),
    }


def choose_efb_action() -> int:
    print()
    print("============================================================")
    print("EFB ACTION SELECTOR")
    print("============================================================")
    print("1  = Recalculate normal cost optimum")
    print("2  = Apply latest Connex uplink")
    print("3  = Set desired on-block time / required recovery")
    print("4  = Apply reroute / updated route distance")
    print("5  = Apply weather / forecast refresh")
    print("6  = Evaluate fixed speed / flight level")
    print("7  = Apply holding / arrival metering information")
    print("8  = Apply ATC speed constraint")
    print("9  = Apply ATC level constraint")
    print("10 = Apply VATSIM event flow")
    print("11 = Apply VA scoring profile")
    print("12 = OFP drift check")

    choice = ask_int("Choose EFB action", 2)

    if choice is None:
        return 2

    if choice < 1 or choice > 12:
        return 2

    return choice


def build_scenario_from_efb_action(
    *,
    action: int,
    current_state: CurrentFlightState,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
) -> ScenarioInput:
    priority = internal_default_priority()

    if action == 1:
        return build_normal_recalc_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 2:
        return build_connex_uplink_scenario(
            current_state=current_state,
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 3:
        return build_target_on_block_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 4:
        return build_reroute_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 5:
        return build_weather_refresh_scenario(
            current_state=current_state,
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 6:
        return build_fixed_speed_fl_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 7:
        return build_holding_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 8:
        return build_atc_speed_constraint_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 9:
        return build_atc_level_constraint_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 10:
        return build_vatsim_event_flow_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    if action == 11:
        return build_va_scoring_scenario(
            flight_context=flight_context,
            cost=cost,
            priority=priority,
        )

    return build_ofp_drift_check_scenario(
        flight_context=flight_context,
        cost=cost,
        priority=priority,
    )


def build_normal_recalc_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=None,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.MANUAL_RECALCULATION,
        source="EFB normal recalculation",
        scenario_type=ScenarioType.NORMAL_COST_OPTIMIZATION,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        arrival_uncertainty=build_arrival_uncertainty_from_engine(),
        **optimizer,
    )


def build_connex_uplink_scenario(
    *,
    current_state: CurrentFlightState,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("CONNEX UPLINK")
    print("============================================================")
    print("Pilot action: apply latest Connex info.")
    print("Passenger/risk/fuel-budget data is resolved by the engine.")

    current_eta_utc = ask_str("Current ETA UTC from FMC/SimBrief", "18:50") or "18:50"

    hub = (
        flight_context.hub_airport
        or flight_context.destination
        or "EDDF"
    )

    uplink = build_demo_connex_uplink(
        hub_airport=hub,
        current_eta_utc=current_eta_utc,
    )

    connex, timing, resolved = resolve_connex_uplink(
        uplink=uplink,
        current_eta_utc=current_eta_utc,
    )

    print()
    print("Resolved Connex data:")
    print(f"  Station:        {resolved.station}")
    print(f"  Hub airport:    {resolved.hub_airport}")
    print(f"  ETA:            {resolved.eta_utc}")
    print(f"  Arrival gate:   {resolved.arrival_gate}")
    print(f"  Arrival pos:    {resolved.arrival_position}")
    print(f"  Connex status:  {'PROTECTED' if resolved.connex_protected else 'AT RISK'}")
    print(f"  Required recov: {resolved.required_time_recovery_min:.1f} min")
    print(f"  Fuel budget:    {resolved.total_fuel_budget_kg:.0f} kg")
    print()

    print("Connections:")
    for connection in resolved.connections:
        status = "PROTECTED" if connection.protected else "AT RISK"
        print(
            f"  {connection.outbound_flight:<7} "
            f"{connection.destination:<4} "
            f"Gate {connection.gate or '-':<4} "
            f"ETD {connection.etd_utc:<5} "
            f"LTOP {connection.ltop_utc:<5} "
            f"{status:<9} "
            f"Margin {connection.margin_to_ltop_min:+.1f} min "
            f"Budget {connection.fuel_budget_kg:.0f} kg"
        )

    print()
    print("Explanation:")
    for line in resolved.explanation:
        print(f"  - {line}")

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=False,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.CONNEX_INFO_RECEIVED,
        source=uplink.source,
        raw_message="Connex uplink applied by engine.",
        scenario_type=ScenarioType.CONNEX_RECOVERY,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        timing=timing,
        connex=connex,
        arrival_uncertainty=build_arrival_uncertainty_from_engine(),
        **optimizer,
    )


def build_target_on_block_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("DESIRED ON-BLOCK")
    print("============================================================")
    print("Pilot action: desired on-block / recover X minutes.")

    current_delay_min = ask_float("Current delay min", 12.0)
    target_delay_min = ask_float("Target delay min", 0.0)
    target_on_block_utc = ask_str("Target on-block UTC optional", None)

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=False,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.TARGET_ON_BLOCK_UPDATED,
        source="EFB target on-block input",
        scenario_type=ScenarioType.TARGET_ON_BLOCK,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        timing=TimingScenarioInput(
            current_delay_min=current_delay_min,
            target_delay_min=target_delay_min,
            target_on_block_utc=target_on_block_utc,
        ),
        arrival_uncertainty=build_arrival_uncertainty_from_engine(),
        **optimizer,
    )


def build_reroute_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("REROUTE / UPDATED ROUTE")
    print("============================================================")
    print("Pilot action: apply reroute / updated route distance.")

    source = ask_str("Reroute source", "ATC / SimBrief refresh")
    new_remaining_distance_nm = ask_float("New remaining distance NM optional", None)
    distance_delta_nm = ask_float("Distance delta NM optional", 45.0)
    reason = ask_str("Reroute reason", "ATC/weather reroute")

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.REROUTE_RECEIVED,
        source=source,
        scenario_type=ScenarioType.REROUTE_RECOVERY,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        reroute=RerouteScenarioInput(
            reroute_received=True,
            new_remaining_distance_nm=new_remaining_distance_nm,
            distance_delta_nm=distance_delta_nm,
            reason=reason,
        ),
        arrival_uncertainty=build_arrival_uncertainty_from_engine(),
        **optimizer,
    )


def build_weather_refresh_scenario(
    *,
    current_state: CurrentFlightState,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("WEATHER / FORECAST REFRESH")
    print("============================================================")
    print("Pilot action: apply updated SimBrief/FMC wind data.")
    print("For this test, the engine simulates a refreshed forecast.")

    source = ask_str("Forecast source", "SimBrief / FMC wind uplink")

    old_wind = current_state.wind_component_kt
    old_isa = current_state.isa_deviation_c

    # Demo engine output. Later replaced by SimBrief navlog/forecast parser.
    new_wind = old_wind - 25.0
    new_isa = old_isa + 2.0

    expected_weather_reroute_nm = 0.0
    if ask_bool("Demo weather reroute distance?", False):
        expected_weather_reroute_nm = ask_float("Engine-provided weather reroute NM", 35.0) or 0.0

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.WEATHER_FORECAST_UPDATED,
        source=source,
        raw_message="Updated weather forecast applied by engine.",
        scenario_type=ScenarioType.WEATHER_UPDATE,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        weather=WeatherScenarioInput(
            weather_update_received=True,
            old_wind_component_kt=old_wind,
            new_wind_component_kt=new_wind,
            old_isa_deviation_c=old_isa,
            new_isa_deviation_c=new_isa,
            expected_weather_reroute_nm=expected_weather_reroute_nm,
            updated_forecast_source=source,
        ),
        arrival_uncertainty=build_arrival_uncertainty_from_engine(),
        **optimizer,
    )


def build_fixed_speed_fl_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("FIXED SPEED / FL")
    print("============================================================")
    print("Pilot action: evaluate fixed speed and/or FL.")

    fixed_mach = ask_float("Fixed Mach optional", 0.780)
    fixed_flight_level = ask_int("Fixed flight level optional", 330)
    constraint_until = ask_str("Constraint until optional", None)

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=False if fixed_mach is not None else True,
        allow_slow_down=False if fixed_mach is not None else True,
        max_mach=fixed_mach,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.MANUAL_RECALCULATION,
        source="EFB fixed speed/FL evaluation",
        scenario_type=ScenarioType.FIXED_SPEED_FL,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        fixed_constraints=FixedConstraintInput(
            fixed_mach=fixed_mach,
            fixed_flight_level=fixed_flight_level,
            constraint_until=constraint_until,
        ),
        **optimizer,
    )


def build_holding_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("HOLDING / ARRIVAL METERING")
    print("============================================================")
    print("Pilot action: apply ATC/arrival-flow information.")

    expected_holding_min = ask_float("Expected holding min from ATC/flow info", 20.0)
    arrival_metering_delay_min = ask_float("Arrival metering delay min optional", 10.0)

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.HOLDING_OR_METERING_EXPECTED,
        source="ATC / arrival flow information",
        scenario_type=ScenarioType.HOLDING_EXPECTED,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        holding=HoldingScenarioInput(
            expected_holding_min=expected_holding_min,
            arrival_metering_delay_min=arrival_metering_delay_min,
            holding_absorbs_recovery=True,
        ),
        arrival_uncertainty=ArrivalUncertaintyInput(
            expected_holding_min=expected_holding_min,
            expected_sequencing_delay_min=arrival_metering_delay_min,
            recovery_absorption_factor=1.0,
        ),
        **optimizer,
    )


def build_atc_speed_constraint_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("ATC SPEED CONSTRAINT")
    print("============================================================")

    assigned_mach = ask_float("Assigned Mach", 0.780)

    return ScenarioInput(
        trigger=OperationalTrigger.ATC_SPEED_CONSTRAINT,
        source="ATC speed assignment",
        scenario_type=ScenarioType.ATC_SPEED_CONSTRAINT,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        fixed_constraints=FixedConstraintInput(
            assigned_mach=assigned_mach,
        ),
        allow_speed_up=False,
        allow_slow_down=False,
        min_mach=assigned_mach,
        max_mach=assigned_mach,
        mach_step=0.005,
    )


def build_atc_level_constraint_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("ATC LEVEL CONSTRAINT")
    print("============================================================")

    assigned_flight_level = ask_int("Assigned flight level", 330)

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.ATC_LEVEL_CONSTRAINT,
        source="ATC level assignment",
        scenario_type=ScenarioType.ATC_LEVEL_CONSTRAINT,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        fixed_constraints=FixedConstraintInput(
            assigned_flight_level=assigned_flight_level,
        ),
        **optimizer,
    )


def build_vatsim_event_flow_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("VATSIM EVENT FLOW")
    print("============================================================")

    expected_holding_min = ask_float("Expected holding min", 25.0)
    metering_delay_min = ask_float("Arrival metering delay min", 15.0)
    atc_reroute = ask_bool("ATC reroute?", False)
    oceanic_level_restriction = ask_bool("Oceanic / level restriction?", False)
    assigned_speed_mach = ask_float("Assigned speed Mach optional", None)
    assigned_flight_level = ask_int("Assigned flight level optional", None)

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.VATSIM_EVENT_FLOW_UPDATED,
        source="VATSIM event flow",
        scenario_type=ScenarioType.VATSIM_EVENT_FLOW,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        vatsim=VatsimScenarioInput(
            enabled=True,
            event_mode=True,
            expected_holding_min=expected_holding_min,
            atc_reroute=atc_reroute,
            arrival_metering_delay_min=metering_delay_min,
            oceanic_level_restriction=oceanic_level_restriction,
            assigned_speed_mach=assigned_speed_mach,
            assigned_flight_level=assigned_flight_level,
        ),
        arrival_uncertainty=ArrivalUncertaintyInput(
            expected_holding_min=expected_holding_min,
            expected_sequencing_delay_min=metering_delay_min,
            recovery_absorption_factor=1.0,
        ),
        **optimizer,
    )


def build_va_scoring_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    print()
    print("============================================================")
    print("VA SCORING PROFILE")
    print("============================================================")
    print("In the real app this comes from VA config/profile.")

    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.VA_SCORING_RISK_UPDATED,
        source="VA profile",
        scenario_type=ScenarioType.VA_SCORING,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        timing=TimingScenarioInput(
            current_delay_min=10.0,
            target_delay_min=0.0,
        ),
        virtual_airline=VirtualAirlineScenarioInput(
            enabled=True,
            va_name=flight_context.airline or "Virtual Airline",
            on_time_score_weight=1.0,
            fuel_score_weight=1.0,
            pirep_late_threshold_min=15.0,
        ),
        **optimizer,
    )


def build_ofp_drift_check_scenario(
    *,
    flight_context: FlightContextInput,
    cost: CostScenarioInput,
    priority: ScenarioPriority,
) -> ScenarioInput:
    optimizer = build_optimizer_behavior_from_action(
        allow_speed_up=True,
        allow_slow_down=True,
        max_mach=0.82,
    )

    return ScenarioInput(
        trigger=OperationalTrigger.MANUAL_RECALCULATION,
        source="OFP drift check",
        scenario_type=ScenarioType.OFP_DRIFT_CHECK,
        priority=priority,
        flight_context=flight_context,
        cost=cost,
        **optimizer,
    )


def print_result(
    *,
    initial_state: CurrentFlightState,
    updated_state: CurrentFlightState,
    interpreted,
    result,
) -> None:
    current = result.current_strategy
    best = result.best_strategy

    print()
    print("============================================================")
    print("STATE APPLIER CHECK")
    print("============================================================")
    print(f"Initial wind component:   {initial_state.wind_component_kt:+.0f} kt")
    print(f"Applied wind component:   {updated_state.wind_component_kt:+.0f} kt")
    print(f"Initial ISA deviation:    {initial_state.isa_deviation_c:+.1f} °C")
    print(f"Applied ISA deviation:    {updated_state.isa_deviation_c:+.1f} °C")
    print(f"Initial remaining dist:   {initial_state.remaining_distance_nm:.1f} NM")
    print(f"Applied remaining dist:   {updated_state.remaining_distance_nm:.1f} NM")

    print()
    print("============================================================")
    print("INTERPRETED SCENARIO")
    print("============================================================")
    print(f"Trigger:      {interpreted.trigger.value}")
    print(f"Scenario:     {interpreted.scenario_type.value}")
    print(f"Objective:    {interpreted.objective.value}")
    print(f"Derived prio: {interpreted.priority.value}")

    print()
    print("Reasons:")
    for reason in interpreted.reasons:
        print(f"  - {reason}")

    if interpreted.warnings:
        print()
        print("Warnings:")
        for warning in interpreted.warnings:
            print(f"  - {warning}")

    print()
    print("============================================================")
    print("DCI / CI RESULT")
    print("============================================================")
    print(result.recommendation)

    if best.mach > current.mach and best.delta_fuel_kg < 0:
        print()
        print("WARNING:")
        print(
            "  Higher Mach is currently shown as saving fuel. "
            "This is suspicious for same altitude/distance/wind and indicates "
            "the performance model must be validated."
        )

    print()
    print("Current Strategy:")
    print(f"  Current CI:        {current.cost_index}")
    print(f"  Current Mach:      M{current.mach:.3f}")
    print(f"  Fuel:              {current.performance.remaining_fuel_kg:.0f} kg")
    print(f"  Time:              {current.performance.remaining_time_min:.1f} min")
    print(f"  Total cost:        {current.cost.total_cost_eur:.0f} EUR")
    print(f"  Economic CI:       {current.cost.economic_ci_kg_per_min:.2f} kg/min")

    print()
    print("Best Strategy:")
    print(f"  Recommended CI:    {best.cost_index}")
    print(f"  Target Mach:       M{best.mach:.3f}")
    print(f"  Fuel:              {best.performance.remaining_fuel_kg:.0f} kg")
    print(f"  Time:              {best.performance.remaining_time_min:.1f} min")
    print(f"  Total cost:        {best.cost.total_cost_eur:.0f} EUR")
    print(f"  Economic CI:       {best.cost.economic_ci_kg_per_min:.2f} kg/min")
    print(f"  Delta Fuel:        {best.delta_fuel_kg:+.0f} kg")
    print(f"  Delta Cruise Time: {best.delta_time_min:+.1f} min")
    print(f"  Gate Time Saved:   {best.gate_time_saved_min:+.1f} min")
    print(f"  Delta Cost:        {best.delta_cost_eur:+.0f} EUR")
    print(f"  Allowed:           {best.allowed}")

    if best.rejection_reason:
        print(f"  Rejection reason:  {best.rejection_reason}")

    print()
    print("Allowed strategies sorted by total cost:")
    allowed = [strategy for strategy in result.strategies if strategy.allowed]
    allowed_sorted = sorted(allowed, key=lambda strategy: strategy.cost.total_cost_eur)

    for strategy in allowed_sorted:
        print(
            f"  CI {strategy.cost_index:>3} | "
            f"M{strategy.mach:.3f} | "
            f"Fuel {strategy.performance.remaining_fuel_kg:.0f} kg | "
            f"Time {strategy.performance.remaining_time_min:.1f} min | "
            f"Gate Δ {strategy.gate_time_saved_min:+.1f} min | "
            f"Cost {strategy.cost.total_cost_eur:.0f} EUR | "
            f"ΔFuel {strategy.delta_fuel_kg:+.0f} kg | "
            f"ΔCruise {strategy.delta_time_min:+.1f} min | "
            f"ΔCost {strategy.delta_cost_eur:+.0f} EUR"
        )

    rejected = [strategy for strategy in result.strategies if not strategy.allowed]

    if rejected:
        print()
        print("Rejected strategies:")
        for strategy in rejected:
            print(
                f"  CI {strategy.cost_index:>3} | "
                f"M{strategy.mach:.3f} | "
                f"Reason: {strategy.rejection_reason}"
            )


def main() -> None:
    general_cfg = load_general_config()

    preset = choose_aircraft_preset()
    aircraft_cfg = load_aircraft_config(preset.config_key)

    current_state = build_current_flight_state_from_efb_input(preset)
    flight_context = build_flight_context_input()
    cost = build_cost_input()

    action = choose_efb_action()

    scenario_input = build_scenario_from_efb_action(
        action=action,
        current_state=current_state,
        flight_context=flight_context,
        cost=cost,
    )

    updated_current_state = apply_scenario_to_current_state(
        current_state=current_state,
        scenario_input=scenario_input,
    )

    interpreted = interpret_scenario(scenario_input)

    result = optimize_cost(
        current_state=updated_current_state,
        interpreted_scenario=interpreted,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    print_result(
        initial_state=current_state,
        updated_state=updated_current_state,
        interpreted=interpreted,
        result=result,
    )


if __name__ == "__main__":
    main()
