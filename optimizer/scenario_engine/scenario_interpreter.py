from __future__ import annotations

from optimizer.scenario_engine.scenario_models import (
    ConnexScenarioInput,
    InterpretedScenario,
    ObjectiveType,
    OperationalTrigger,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
)


def interpret_scenario(scenario: ScenarioInput) -> InterpretedScenario:
    """
    Converts raw operational/EFB/ACARS scenario input into a clean optimization task.

    This function does NOT:
    - simulate aircraft performance
    - calculate fuel burn
    - choose the best Mach
    - calculate total cost
    - mutate CurrentFlightState

    It only answers:
    - What is the optimization objective?
    - Which constraints apply?
    - Which dynamic inputs matter?
    - Which warnings/reasons should be shown?

    Performance-relevant event data, e.g. new wind or reroute distance,
    is applied separately by scenario_state_applier.py.
    """

    resolved_type = _resolve_scenario_type(scenario)

    if resolved_type == ScenarioType.NORMAL_COST_OPTIMIZATION:
        return _interpret_normal_cost_optimization(scenario, resolved_type)

    if resolved_type == ScenarioType.CONNEX_RECOVERY:
        return _interpret_connex_recovery(scenario, resolved_type)

    if resolved_type == ScenarioType.TARGET_ON_BLOCK:
        return _interpret_target_on_block(scenario, resolved_type)

    if resolved_type == ScenarioType.REROUTE_RECOVERY:
        return _interpret_reroute_recovery(scenario, resolved_type)

    if resolved_type == ScenarioType.WEATHER_UPDATE:
        return _interpret_weather_update(scenario, resolved_type)

    if resolved_type == ScenarioType.FIXED_SPEED_FL:
        return _interpret_fixed_speed_fl(scenario, resolved_type)

    if resolved_type == ScenarioType.HOLDING_EXPECTED:
        return _interpret_holding_expected(scenario, resolved_type)

    if resolved_type == ScenarioType.ATC_SPEED_CONSTRAINT:
        return _interpret_atc_speed_constraint(scenario, resolved_type)

    if resolved_type == ScenarioType.ATC_LEVEL_CONSTRAINT:
        return _interpret_atc_level_constraint(scenario, resolved_type)

    if resolved_type == ScenarioType.VATSIM_EVENT_FLOW:
        return _interpret_vatsim_event_flow(scenario, resolved_type)

    if resolved_type == ScenarioType.VA_SCORING:
        return _interpret_va_scoring(scenario, resolved_type)

    if resolved_type == ScenarioType.OFP_DRIFT_CHECK:
        return _interpret_ofp_drift_check(scenario, resolved_type)

    raise ValueError(f"Unsupported scenario type: {resolved_type}")


# ============================================================
# Scenario resolving
# ============================================================

def _resolve_scenario_type(scenario: ScenarioInput) -> ScenarioType:
    """
    Resolves operational trigger into an effective scenario type.

    If user explicitly set scenario_type != NORMAL_COST_OPTIMIZATION,
    we respect that.

    If scenario_type is NORMAL but trigger is specific, we map trigger
    into the appropriate scenario.
    """

    if scenario.scenario_type != ScenarioType.NORMAL_COST_OPTIMIZATION:
        return scenario.scenario_type

    trigger_map = {
        OperationalTrigger.CONNEX_INFO_RECEIVED: ScenarioType.CONNEX_RECOVERY,
        OperationalTrigger.TARGET_ON_BLOCK_UPDATED: ScenarioType.TARGET_ON_BLOCK,
        OperationalTrigger.TIME_DEVIATION_DETECTED: ScenarioType.TARGET_ON_BLOCK,
        OperationalTrigger.REROUTE_RECEIVED: ScenarioType.REROUTE_RECOVERY,
        OperationalTrigger.WEATHER_FORECAST_UPDATED: ScenarioType.WEATHER_UPDATE,
        OperationalTrigger.WIND_PROFILE_UPDATED: ScenarioType.WEATHER_UPDATE,
        OperationalTrigger.TEMPERATURE_PROFILE_UPDATED: ScenarioType.WEATHER_UPDATE,
        OperationalTrigger.ATC_SPEED_CONSTRAINT: ScenarioType.ATC_SPEED_CONSTRAINT,
        OperationalTrigger.ATC_LEVEL_CONSTRAINT: ScenarioType.ATC_LEVEL_CONSTRAINT,
        OperationalTrigger.HOLDING_OR_METERING_EXPECTED: ScenarioType.HOLDING_EXPECTED,
        OperationalTrigger.VATSIM_EVENT_FLOW_UPDATED: ScenarioType.VATSIM_EVENT_FLOW,
        OperationalTrigger.VA_SCORING_RISK_UPDATED: ScenarioType.VA_SCORING,
    }

    return trigger_map.get(
        scenario.trigger,
        ScenarioType.NORMAL_COST_OPTIMIZATION,
    )


# ============================================================
# Scenario-specific interpreters
# ============================================================

def _interpret_normal_cost_optimization(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons = [
        "Normal cost optimization selected.",
        "Objective is to minimize expected total cost using current flight and cost inputs.",
    ]

    if scenario.trigger != OperationalTrigger.MANUAL_RECALCULATION:
        reasons.append(f"Operational trigger: {scenario.trigger.value}.")

    _add_flight_context_reasons(scenario, reasons)

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
        reasons=reasons,
    )


def _interpret_connex_recovery(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = []
    warnings: list[str] = []

    if scenario.trigger != OperationalTrigger.MANUAL_RECALCULATION:
        reasons.append(f"Operational trigger: {scenario.trigger.value}.")

    if scenario.source:
        reasons.append(f"Source: {scenario.source}.")

    _add_flight_context_reasons(scenario, reasons)

    fuel_budget_kg = _calculate_connex_fuel_budget_kg(scenario.connex)

    if fuel_budget_kg is not None:
        reasons.append(f"Connex fuel budget calculated: {fuel_budget_kg:.0f} kg.")
    else:
        warnings.append(
            "Connex recovery selected but no valid affected_pax / "
            "acceptable_extra_fuel_kg_per_pax, connection_groups, "
            "or connex_fuel_budget_kg_override was provided."
        )

    required_recovery_min = _calculate_required_recovery_min(scenario)

    if required_recovery_min is not None:
        reasons.append(f"Required time recovery calculated: {required_recovery_min:.1f} min.")

    if scenario.connex.connection_groups:
        reasons.append(f"Connex groups provided: {len(scenario.connex.connection_groups)}.")

    if scenario.connex.affected_pax is not None:
        reasons.append(f"Affected passengers: {scenario.connex.affected_pax}.")

    if scenario.connex.acceptable_extra_fuel_kg_per_pax is not None:
        reasons.append(
            f"Acceptable extra fuel per passenger: "
            f"{scenario.connex.acceptable_extra_fuel_kg_per_pax:.1f} kg."
        )

    if scenario.connex.hotel_risk:
        reasons.append("Hotel risk detected. Connex priority should be treated as higher.")

    if scenario.connex.last_connection_of_day:
        reasons.append("Last connection of day detected. Recovery value is higher.")

    if scenario.connex.longhaul_connection:
        reasons.append("Long-haul connection affected. Recovery value is higher.")

    if scenario.connex.group_booking:
        reasons.append("Group booking affected. Recovery value is higher.")

    if scenario.connex.passenger_compensation_risk:
        reasons.append("Passenger compensation risk detected.")

    if scenario.connex.connection_buffer_min is not None:
        reasons.append(f"Connection buffer: {scenario.connex.connection_buffer_min:.1f} min.")

    if scenario.curfew.curfew_margin_min is not None:
        reasons.append(f"Curfew margin: {scenario.curfew.curfew_margin_min:.1f} min.")

    if scenario.curfew.diversion_if_missed:
        reasons.append("Missing curfew would require diversion.")

    if scenario.crew_duty.duty_margin_min is not None:
        reasons.append(f"Crew duty margin: {scenario.crew_duty.duty_margin_min:.1f} min.")

    derived_priority = _derive_connex_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    if required_recovery_min is not None and required_recovery_min > 0:
        objective = ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST
        reasons.append(
            "Connex recovery uses target-time objective: meet required recovery if possible, "
            "then minimize expected cost within fuel budget."
        )
    else:
        objective = ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST
        reasons.append(
            "Connex recovery has no positive required recovery; using expected total cost "
            "minimization with fuel budget constraint."
        )

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=objective,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=False,
        max_extra_fuel_kg=fuel_budget_kg,
        required_time_recovery_min=required_recovery_min,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_target_on_block(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Target on-block / time recovery scenario selected.",
        "Objective is to meet target time if possible, then minimize cost.",
    ]
    warnings: list[str] = []

    if scenario.trigger != OperationalTrigger.MANUAL_RECALCULATION:
        reasons.append(f"Operational trigger: {scenario.trigger.value}.")

    _add_flight_context_reasons(scenario, reasons)

    required_recovery_min = _calculate_required_recovery_min(scenario)

    if required_recovery_min is not None:
        reasons.append(f"Required time recovery calculated: {required_recovery_min:.1f} min.")
    else:
        warnings.append(
            "Target on-block selected but no current_delay_min / target_delay_min "
            "or required_time_recovery_min was provided."
        )

    if scenario.timing.target_on_block_utc:
        reasons.append(f"Target on-block time: {scenario.timing.target_on_block_utc}.")

    derived_priority = _derive_time_pressure_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=False,
        required_time_recovery_min=required_recovery_min,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_reroute_recovery(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Reroute recovery scenario selected.",
        "Objective is to minimize expected total cost after changed routing.",
    ]
    warnings: list[str] = []

    if scenario.trigger != OperationalTrigger.MANUAL_RECALCULATION:
        reasons.append(f"Operational trigger: {scenario.trigger.value}.")

    _add_flight_context_reasons(scenario, reasons)

    distance_delta_nm = _calculate_reroute_distance_delta_nm(scenario)

    if distance_delta_nm is not None:
        reasons.append(f"Reroute distance impact: {distance_delta_nm:+.1f} NM.")
    else:
        warnings.append(
            "Reroute recovery selected but no old/new remaining distance or distance_delta_nm was provided."
        )

    if scenario.reroute.reason:
        reasons.append(f"Reroute reason: {scenario.reroute.reason}.")

    required_recovery_min = _calculate_required_recovery_min(scenario)

    if required_recovery_min is not None:
        reasons.append(f"Required time recovery calculated: {required_recovery_min:.1f} min.")

    derived_priority = _derive_time_pressure_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=True,
        required_time_recovery_min=required_recovery_min,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_weather_update(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Weather / forecast update detected.",
        "Updated forecast inputs should be applied before optimization.",
        "Objective is to re-evaluate CI/speed using the updated forecast state.",
    ]
    warnings: list[str] = []

    if scenario.trigger != OperationalTrigger.MANUAL_RECALCULATION:
        reasons.append(f"Operational trigger: {scenario.trigger.value}.")

    if scenario.weather.updated_forecast_source:
        reasons.append(f"Updated forecast source: {scenario.weather.updated_forecast_source}.")

    _add_flight_context_reasons(scenario, reasons)

    old_wind = scenario.weather.old_wind_component_kt
    new_wind = scenario.weather.new_wind_component_kt
    wind_error = scenario.weather.wind_error_kt

    if wind_error is None and old_wind is not None and new_wind is not None:
        wind_error = new_wind - old_wind

    if old_wind is not None:
        reasons.append(f"Old wind component: {old_wind:+.0f} kt.")

    if new_wind is not None:
        reasons.append(f"New wind component: {new_wind:+.0f} kt.")

    if wind_error is not None:
        reasons.append(f"Wind component change: {wind_error:+.0f} kt.")

        if wind_error <= -20:
            reasons.append("Significantly stronger headwind detected.")
        elif wind_error >= 20:
            reasons.append("Significantly stronger tailwind detected.")

    old_isa = scenario.weather.old_isa_deviation_c
    new_isa = scenario.weather.new_isa_deviation_c
    isa_error = scenario.weather.isa_error_c

    if isa_error is None and old_isa is not None and new_isa is not None:
        isa_error = new_isa - old_isa

    if old_isa is not None:
        reasons.append(f"Old ISA deviation: {old_isa:+.1f} °C.")

    if new_isa is not None:
        reasons.append(f"New ISA deviation: {new_isa:+.1f} °C.")

    if isa_error is not None:
        reasons.append(f"ISA deviation change: {isa_error:+.1f} °C.")

    if scenario.weather.turbulence_expected:
        reasons.append("Turbulence expected. Speed-up may be operationally undesirable.")

    if scenario.weather.step_climb_blocked:
        reasons.append("Step climb blocked. Fuel/time prediction should be re-evaluated.")

    if scenario.weather.expected_weather_reroute_nm is not None:
        reasons.append(
            f"Expected weather reroute distance: "
            f"{scenario.weather.expected_weather_reroute_nm:+.1f} NM."
        )

    if scenario.weather.arrival_weather_delay_min is not None:
        reasons.append(
            f"Expected arrival weather delay: "
            f"{scenario.weather.arrival_weather_delay_min:.1f} min."
        )

    if (
        wind_error is None
        and isa_error is None
        and not scenario.weather.turbulence_expected
        and not scenario.weather.step_climb_blocked
        and scenario.weather.expected_weather_reroute_nm is None
        and scenario.weather.arrival_weather_delay_min is None
    ):
        warnings.append(
            "Weather update selected but no wind, ISA, turbulence, step-climb, reroute, "
            "or arrival delay information was provided."
        )

    derived_priority = _derive_weather_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    allow_speed_up = True
    allow_slow_down = True
    max_mach = None

    if scenario.weather.recommended_speed_limit_mach is not None:
        max_mach = scenario.weather.recommended_speed_limit_mach
        reasons.append(f"Weather speed limit applied: M{max_mach:.3f}.")

    if scenario.weather.turbulence_expected:
        allow_speed_up = False

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
        priority=derived_priority,
        allow_speed_up=allow_speed_up,
        allow_slow_down=allow_slow_down,
        max_mach=max_mach,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_fixed_speed_fl(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Cruise CI recalculation selected.",
        "Objective is to optimize CI/Mach at the selected flight level.",
    ]
    warnings: list[str] = []

    fixed = scenario.fixed_constraints

    if fixed.fixed_mach is not None:
        reasons.append(f"Fixed Mach: {fixed.fixed_mach:.3f}.")
        reasons.append("Fixed Mach provided; objective is fixed strategy evaluation.")

    if fixed.fixed_flight_level is not None:
        reasons.append(f"Fixed flight level: FL{fixed.fixed_flight_level}.")

    if fixed.fixed_mach is None and fixed.fixed_flight_level is None:
        warnings.append(
            "Cruise CI selected but no flight level or fixed Mach was provided."
        )

    # Important:
    # Fixed Mach means no speed optimization.
    # Fixed FL alone does NOT mean Mach is fixed.
    if fixed.fixed_mach is not None:
        return _base_interpreted_scenario(
            scenario=scenario,
            resolved_type=resolved_type,
            objective=ObjectiveType.FIXED_STRATEGY_EVALUATION,
            allow_speed_up=False,
            allow_slow_down=False,
            min_mach=fixed.fixed_mach,
            max_mach=fixed.fixed_mach,
            reasons=reasons,
            warnings=warnings,
        )

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
        allow_speed_up=True,
        allow_slow_down=True,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_holding_expected(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Holding / arrival metering scenario selected.",
        "Objective is to avoid wasting fuel on recovery that may be absorbed by arrival uncertainty.",
    ]
    warnings: list[str] = []

    expected_holding_min = _first_not_none(
        scenario.holding.expected_holding_min,
        scenario.vatsim.expected_holding_min,
        scenario.arrival_uncertainty.expected_holding_min,
    )

    arrival_metering_delay_min = _first_not_none(
        scenario.holding.arrival_metering_delay_min,
        scenario.vatsim.arrival_metering_delay_min,
        scenario.arrival_uncertainty.expected_sequencing_delay_min,
    )

    if expected_holding_min is not None:
        reasons.append(f"Expected holding: {expected_holding_min:.1f} min.")

    if arrival_metering_delay_min is not None:
        reasons.append(f"Arrival metering / sequencing delay: {arrival_metering_delay_min:.1f} min.")

    if expected_holding_min is None and arrival_metering_delay_min is None:
        warnings.append(
            "Holding scenario selected but no expected_holding_min or arrival_metering_delay_min was provided."
        )

    derived_priority = _derive_arrival_uncertainty_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_WASTED_RECOVERY,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=True,
        expected_holding_min=expected_holding_min,
        arrival_metering_delay_min=arrival_metering_delay_min,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_atc_speed_constraint(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "ATC speed constraint scenario selected.",
        "Objective is to evaluate the impact of an assigned speed.",
    ]
    warnings: list[str] = []

    assigned_mach = _first_not_none(
        scenario.fixed_constraints.assigned_mach,
        scenario.vatsim.assigned_speed_mach,
    )

    if assigned_mach is not None:
        reasons.append(f"Assigned Mach: {assigned_mach:.3f}.")
    else:
        warnings.append("ATC speed constraint selected but no assigned Mach was provided.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.FIXED_STRATEGY_EVALUATION,
        allow_speed_up=False,
        allow_slow_down=False,
        min_mach=assigned_mach,
        max_mach=assigned_mach,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_atc_level_constraint(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "ATC level constraint scenario selected.",
        "Objective is to evaluate the impact of an assigned flight level.",
    ]
    warnings: list[str] = []

    assigned_fl = _first_not_none(
        scenario.fixed_constraints.assigned_flight_level,
        scenario.vatsim.assigned_flight_level,
    )

    if assigned_fl is not None:
        reasons.append(f"Assigned flight level: FL{assigned_fl}.")
    else:
        warnings.append("ATC level constraint selected but no assigned flight level was provided.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.FIXED_STRATEGY_EVALUATION,
        allow_speed_up=True,
        allow_slow_down=True,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_vatsim_event_flow(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "VATSIM event flow scenario selected.",
        "Objective is to avoid unnecessary fuel burn while accounting for event delays.",
    ]
    warnings: list[str] = []

    expected_holding_min = _first_not_none(
        scenario.vatsim.expected_holding_min,
        scenario.holding.expected_holding_min,
        scenario.arrival_uncertainty.expected_holding_min,
    )

    arrival_metering_delay_min = _first_not_none(
        scenario.vatsim.arrival_metering_delay_min,
        scenario.holding.arrival_metering_delay_min,
        scenario.arrival_uncertainty.expected_sequencing_delay_min,
    )

    if scenario.vatsim.event_mode:
        reasons.append("VATSIM event mode enabled.")

    if scenario.vatsim.atc_reroute:
        reasons.append("ATC reroute reported.")

    if scenario.vatsim.oceanic_level_restriction:
        reasons.append("Oceanic / level restriction reported.")

    if expected_holding_min is not None:
        reasons.append(f"Expected holding: {expected_holding_min:.1f} min.")

    if arrival_metering_delay_min is not None:
        reasons.append(f"Arrival metering delay: {arrival_metering_delay_min:.1f} min.")

    if expected_holding_min is None and arrival_metering_delay_min is None:
        warnings.append(
            "VATSIM event flow selected but no holding or metering delay was provided."
        )

    derived_priority = _derive_vatsim_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_WASTED_RECOVERY,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=True,
        expected_holding_min=expected_holding_min,
        arrival_metering_delay_min=arrival_metering_delay_min,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_va_scoring(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "Virtual airline scoring scenario selected.",
        "Objective is to maximize VA score tradeoff between on-time performance and fuel efficiency.",
    ]
    warnings: list[str] = []

    if scenario.virtual_airline.va_name:
        reasons.append(f"Virtual airline: {scenario.virtual_airline.va_name}.")

    reasons.append(f"On-time score weight: {scenario.virtual_airline.on_time_score_weight:.2f}.")
    reasons.append(f"Fuel score weight: {scenario.virtual_airline.fuel_score_weight:.2f}.")

    if scenario.virtual_airline.pirep_late_threshold_min is not None:
        reasons.append(
            f"PIREP late threshold: {scenario.virtual_airline.pirep_late_threshold_min:.1f} min."
        )

    derived_priority = _derive_va_priority(scenario)
    reasons.append(f"Derived scenario priority: {derived_priority.value}.")

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MAXIMIZE_VA_SCORE,
        priority=derived_priority,
        allow_speed_up=True,
        allow_slow_down=True,
        reasons=reasons,
        warnings=warnings,
    )


def _interpret_ofp_drift_check(
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
) -> InterpretedScenario:
    reasons: list[str] = [
        "OFP drift check scenario selected.",
        "Objective is to check whether actual flight progress still supports current speed strategy.",
    ]

    warnings: list[str] = [
        "OFP drift logic requires planned vs actual fuel/time data in a later module."
    ]

    return _base_interpreted_scenario(
        scenario=scenario,
        resolved_type=resolved_type,
        objective=ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
        allow_speed_up=True,
        allow_slow_down=True,
        reasons=reasons,
        warnings=warnings,
    )


# ============================================================
# Base builder
# ============================================================

def _base_interpreted_scenario(
    *,
    scenario: ScenarioInput,
    resolved_type: ScenarioType,
    objective: ObjectiveType,
    priority: ScenarioPriority | None = None,
    allow_speed_up: bool | None = None,
    allow_slow_down: bool | None = None,
    min_mach: float | None = None,
    max_mach: float | None = None,
    max_extra_fuel_kg: float | None = None,
    required_time_recovery_min: float | None = None,
    max_time_loss_min: float | None = None,
    expected_holding_min: float | None = None,
    arrival_metering_delay_min: float | None = None,
    reasons: list[str] | None = None,
    warnings: list[str] | None = None,
) -> InterpretedScenario:
    return InterpretedScenario(
        trigger=scenario.trigger,
        source=scenario.source,
        raw_message=scenario.raw_message,

        scenario_type=resolved_type,
        objective=objective,
        priority=priority or scenario.priority,

        allow_speed_up=(
            scenario.allow_speed_up if allow_speed_up is None else allow_speed_up
        ),
        allow_slow_down=(
            scenario.allow_slow_down if allow_slow_down is None else allow_slow_down
        ),

        min_mach=_first_not_none(
            min_mach,
            scenario.fixed_constraints.min_mach,
            scenario.min_mach,
        ),
        max_mach=_first_not_none(
            max_mach,
            scenario.fixed_constraints.max_mach,
            scenario.max_mach,
        ),
        mach_step=scenario.mach_step,

        max_extra_fuel_kg=_first_not_none(
            max_extra_fuel_kg,
            scenario.max_extra_fuel_kg,
        ),
        required_time_recovery_min=_first_not_none(
            required_time_recovery_min,
            scenario.required_time_recovery_min,
        ),
        max_time_loss_min=_first_not_none(
            max_time_loss_min,
            scenario.max_time_loss_min,
        ),

        current_delay_min=scenario.timing.current_delay_min,
        target_delay_min=scenario.timing.target_delay_min,
        expected_holding_min=expected_holding_min,
        arrival_metering_delay_min=arrival_metering_delay_min,
        arrival_recovery_absorption_factor=scenario.arrival_uncertainty.recovery_absorption_factor,

        fuel_price_eur_per_kg=scenario.cost.fuel_price_eur_per_kg,
        ets_eur_per_kg=scenario.cost.ets_eur_per_kg,
        fuel_surcharge_eur_per_kg=scenario.cost.fuel_surcharge_eur_per_kg,

        time_cost_override_eur_per_h=scenario.cost.time_cost_override_eur_per_h,
        delay_cost_override_eur_per_min=scenario.cost.delay_cost_override_eur_per_min,
        delay_phase=scenario.cost.delay_phase,
        reactionary_delay=scenario.cost.reactionary_delay,

        reasons=reasons or [],
        warnings=warnings or [],
    )


# ============================================================
# Calculations
# ============================================================

def _calculate_connex_fuel_budget_kg(connex: ConnexScenarioInput) -> float | None:
    if connex.connex_fuel_budget_kg_override is not None:
        return round(connex.connex_fuel_budget_kg_override, 2)

    if connex.connection_groups:
        total_budget = 0.0
        has_budget = False

        for group in connex.connection_groups:
            if group.affected_pax <= 0:
                continue

            if group.acceptable_extra_fuel_kg_per_pax is None:
                continue

            if group.acceptable_extra_fuel_kg_per_pax <= 0:
                continue

            total_budget += group.affected_pax * group.acceptable_extra_fuel_kg_per_pax
            has_budget = True

        if has_budget:
            return round(total_budget, 2)

    if connex.affected_pax is None:
        return None

    if connex.acceptable_extra_fuel_kg_per_pax is None:
        return None

    if connex.affected_pax <= 0:
        return None

    if connex.acceptable_extra_fuel_kg_per_pax <= 0:
        return None

    return round(
        connex.affected_pax * connex.acceptable_extra_fuel_kg_per_pax,
        2,
    )


def _calculate_required_recovery_min(scenario: ScenarioInput) -> float | None:
    if scenario.timing.required_time_recovery_min is not None:
        return round(scenario.timing.required_time_recovery_min, 2)

    if scenario.required_time_recovery_min is not None:
        return round(scenario.required_time_recovery_min, 2)

    current_delay = scenario.timing.current_delay_min
    target_delay = scenario.timing.target_delay_min

    if current_delay is None or target_delay is None:
        return None

    required = current_delay - target_delay

    if required <= 0:
        return 0.0

    return round(required, 2)


def _calculate_reroute_distance_delta_nm(scenario: ScenarioInput) -> float | None:
    if scenario.reroute.distance_delta_nm is not None:
        return round(scenario.reroute.distance_delta_nm, 2)

    old_distance = scenario.reroute.old_remaining_distance_nm
    new_distance = scenario.reroute.new_remaining_distance_nm

    if old_distance is None or new_distance is None:
        return None

    return round(new_distance - old_distance, 2)


# ============================================================
# Priority derivation
# ============================================================

def _derive_connex_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    affected_pax = scenario.connex.affected_pax
    kg_per_pax = scenario.connex.acceptable_extra_fuel_kg_per_pax

    if scenario.connex.connection_groups:
        group_pax = sum(max(group.affected_pax, 0) for group in scenario.connex.connection_groups)
        affected_pax = group_pax if affected_pax is None else max(affected_pax, group_pax)

        if any(group.hotel_risk for group in scenario.connex.connection_groups):
            score += 3

        if any(group.last_connection_of_day for group in scenario.connex.connection_groups):
            score += 3

        if any(group.longhaul_connection for group in scenario.connex.connection_groups):
            score += 2

        if any(group.group_booking for group in scenario.connex.connection_groups):
            score += 1

        if any(group.passenger_compensation_risk for group in scenario.connex.connection_groups):
            score += 2

        buffers = [
            group.connection_buffer_min
            for group in scenario.connex.connection_groups
            if group.connection_buffer_min is not None
        ]

        if buffers:
            min_buffer = min(buffers)
            if min_buffer <= 5:
                score += 2
            elif min_buffer <= 10:
                score += 1

    if affected_pax is not None:
        if affected_pax >= 30:
            score += 3
        elif affected_pax >= 15:
            score += 2
        elif affected_pax >= 5:
            score += 1

    if kg_per_pax is not None:
        if kg_per_pax >= 80:
            score += 3
        elif kg_per_pax >= 40:
            score += 2
        elif kg_per_pax >= 20:
            score += 1

    if scenario.connex.hotel_risk:
        score += 3

    if scenario.connex.last_connection_of_day:
        score += 3

    if scenario.connex.longhaul_connection:
        score += 2

    if scenario.connex.group_booking:
        score += 1

    if scenario.connex.passenger_compensation_risk:
        score += 2

    if scenario.connex.connection_buffer_min is not None:
        if scenario.connex.connection_buffer_min <= 5:
            score += 2
        elif scenario.connex.connection_buffer_min <= 10:
            score += 1

    score += _curfew_priority_score(scenario)
    score += _crew_duty_priority_score(scenario)

    return _score_to_priority(score)


def _derive_time_pressure_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    required_recovery_min = _calculate_required_recovery_min(scenario)

    if required_recovery_min is not None:
        if required_recovery_min >= 20:
            score += 3
        elif required_recovery_min >= 10:
            score += 2
        elif required_recovery_min >= 5:
            score += 1

    current_delay = scenario.timing.current_delay_min

    if current_delay is not None:
        if current_delay >= 45:
            score += 3
        elif current_delay >= 20:
            score += 2
        elif current_delay >= 10:
            score += 1

    score += _curfew_priority_score(scenario)
    score += _crew_duty_priority_score(scenario)

    return _score_to_priority(score)


def _derive_weather_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    old_wind = scenario.weather.old_wind_component_kt
    new_wind = scenario.weather.new_wind_component_kt
    wind_error = scenario.weather.wind_error_kt

    if wind_error is None and old_wind is not None and new_wind is not None:
        wind_error = new_wind - old_wind

    if wind_error is not None:
        if abs(wind_error) >= 50:
            score += 3
        elif abs(wind_error) >= 30:
            score += 2
        elif abs(wind_error) >= 15:
            score += 1

    old_isa = scenario.weather.old_isa_deviation_c
    new_isa = scenario.weather.new_isa_deviation_c
    isa_error = scenario.weather.isa_error_c

    if isa_error is None and old_isa is not None and new_isa is not None:
        isa_error = new_isa - old_isa

    if isa_error is not None:
        if abs(isa_error) >= 15:
            score += 2
        elif abs(isa_error) >= 7:
            score += 1

    if scenario.weather.turbulence_expected:
        score += 2

    if scenario.weather.step_climb_blocked:
        score += 1

    if scenario.weather.expected_weather_reroute_nm is not None:
        if scenario.weather.expected_weather_reroute_nm >= 100:
            score += 3
        elif scenario.weather.expected_weather_reroute_nm >= 40:
            score += 2
        elif scenario.weather.expected_weather_reroute_nm >= 10:
            score += 1

    if scenario.weather.arrival_weather_delay_min is not None:
        if scenario.weather.arrival_weather_delay_min >= 30:
            score += 3
        elif scenario.weather.arrival_weather_delay_min >= 15:
            score += 2
        elif scenario.weather.arrival_weather_delay_min >= 5:
            score += 1

    score += _curfew_priority_score(scenario)
    score += _crew_duty_priority_score(scenario)

    return _score_to_priority(score)


def _derive_arrival_uncertainty_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    holding = _first_not_none(
        scenario.holding.expected_holding_min,
        scenario.vatsim.expected_holding_min,
        scenario.arrival_uncertainty.expected_holding_min,
    )

    if holding is not None:
        if holding >= 30:
            score += 3
        elif holding >= 15:
            score += 2
        elif holding >= 5:
            score += 1

    sequencing = _first_not_none(
        scenario.holding.arrival_metering_delay_min,
        scenario.vatsim.arrival_metering_delay_min,
        scenario.arrival_uncertainty.expected_sequencing_delay_min,
    )

    if sequencing is not None:
        if sequencing >= 30:
            score += 3
        elif sequencing >= 15:
            score += 2
        elif sequencing >= 5:
            score += 1

    return _score_to_priority(score)


def _derive_vatsim_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    if scenario.vatsim.event_mode:
        score += 1

    if scenario.vatsim.atc_reroute:
        score += 1

    if scenario.vatsim.oceanic_level_restriction:
        score += 1

    holding = _first_not_none(
        scenario.vatsim.expected_holding_min,
        scenario.holding.expected_holding_min,
        scenario.arrival_uncertainty.expected_holding_min,
    )

    if holding is not None:
        if holding >= 30:
            score += 2
        elif holding >= 15:
            score += 1

    metering = _first_not_none(
        scenario.vatsim.arrival_metering_delay_min,
        scenario.holding.arrival_metering_delay_min,
        scenario.arrival_uncertainty.expected_sequencing_delay_min,
    )

    if metering is not None:
        if metering >= 30:
            score += 2
        elif metering >= 15:
            score += 1

    return _score_to_priority(score)


def _derive_va_priority(scenario: ScenarioInput) -> ScenarioPriority:
    score = 0

    if scenario.virtual_airline.enabled:
        score += 1

    if scenario.virtual_airline.on_time_score_weight >= 1.5:
        score += 2
    elif scenario.virtual_airline.on_time_score_weight >= 1.1:
        score += 1

    if scenario.virtual_airline.pirep_late_threshold_min is not None:
        if scenario.virtual_airline.pirep_late_threshold_min <= 5:
            score += 2
        elif scenario.virtual_airline.pirep_late_threshold_min <= 15:
            score += 1

    return _score_to_priority(score)


def _curfew_priority_score(scenario: ScenarioInput) -> int:
    score = 0

    if scenario.curfew.diversion_if_missed:
        score += 5

    if scenario.curfew.curfew_margin_min is not None:
        if scenario.curfew.curfew_margin_min <= 20:
            score += 5
        elif scenario.curfew.curfew_margin_min <= 40:
            score += 3
        elif scenario.curfew.curfew_margin_min <= 60:
            score += 1

    return score


def _crew_duty_priority_score(scenario: ScenarioInput) -> int:
    score = 0

    if scenario.crew_duty.duty_margin_min is not None:
        if scenario.crew_duty.duty_margin_min <= 15:
            score += 3
        elif scenario.crew_duty.duty_margin_min <= 30:
            score += 2
        elif scenario.crew_duty.duty_margin_min <= 60:
            score += 1

    return score


def _score_to_priority(score: int) -> ScenarioPriority:
    if score >= 8:
        return ScenarioPriority.CRITICAL

    if score >= 5:
        return ScenarioPriority.HIGH

    if score >= 2:
        return ScenarioPriority.MEDIUM

    return ScenarioPriority.LOW


def _add_flight_context_reasons(scenario: ScenarioInput, reasons: list[str]) -> None:
    ctx = scenario.flight_context

    if ctx.origin and ctx.destination:
        reasons.append(f"Flight context: {ctx.origin} -> {ctx.destination}.")

    if ctx.planned_block_time_min is not None:
        reasons.append(f"Planned block time: {ctx.planned_block_time_min:.0f} min.")

        if ctx.planned_block_time_min > 60:
            reasons.append("Flight time > 1h. In-flight CI re-evaluation is operationally meaningful.")

    if ctx.is_hub_inbound:
        if ctx.hub_airport:
            reasons.append(f"Hub inbound flight detected: {ctx.hub_airport}.")
        else:
            reasons.append("Hub inbound flight detected.")


def _first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None
