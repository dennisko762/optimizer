from __future__ import annotations

from pydantic import BaseModel

from data_fetcher.sim.sim_models import CurrentFlightState

from optimizer.cost_model import (
    CostBreakdown,
    DynamicCostFactors,
    IropsConnectionGroup,
    calculate_total_strategy_cost,
)

from optimizer.scenario_engine.scenario_models import (
    InterpretedScenario,
    ObjectiveType,
    ScenarioPriority,
)

from performance_engine.boeing_777_fmc_ci_speed_model import (
    COST_INDEX_SOURCE_AIRBUS_FAMILY,
    COST_INDEX_SOURCE_CALIBRATED,
    COST_INDEX_SOURCE_CURRENT_INPUT,
    COST_INDEX_SOURCE_DISPLAY,
    COST_INDEX_SOURCE_FMC_LIKE,
    OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
    OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
    OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    OPTIMIZER_MODE_PERFORMANCE_DERIVED,
    cost_index_source_label,
    resolve_cost_index_optimizer_mode,
)
from performance_engine.remaining_cruise_simulator import (
    CruiseSegment,
    RemainingCruiseInput,
    RemainingCruiseResult,
    simulate_remaining_cruise,
)
from performance_engine.ci_profile import derive_cost_index_profile
from performance_engine.ci_table.ci_mach_table_generator import generate_ci_mach_table
from performance_engine.ci_table.ci_mach_table_lookup import (
    find_band_for_mach,
    representative_cost_index_for_band,
)
from performance_engine.speed_envelope import max_mach_at_altitude

from strategy.strategy_generator import (
    generate_boeing_777_fmc_ci_strategies,
    generate_speed_strategies,
)


class CostedStrategy(BaseModel):
    cost_index: int | None = None
    mach: float
    speed_mode: str | None = None
    cas_kt: float | None = None
    cost_index_source: str | None = None
    cost_index_label: str | None = None
    flight_level: int | None = None
    label: str | None = None

    performance: RemainingCruiseResult
    cost: CostBreakdown
    performance_ci_kg_per_min: float | None = None

    delta_fuel_kg: float
    delta_time_min: float
    delta_cost_eur: float

    time_saved_min: float
    gate_time_saved_min: float
    extra_fuel_kg: float

    allowed: bool = True
    rejection_reason: str | None = None


class CostOptimizationResult(BaseModel):
    objective: ObjectiveType
    optimizer_mode: str = OPTIMIZER_MODE_PERFORMANCE_DERIVED

    current_strategy: CostedStrategy
    best_strategy: CostedStrategy
    strategies: list[CostedStrategy]

    is_current_best: bool
    recommendation: str

    reasons: list[str]
    warnings: list[str]


def optimize_cost(
    *,
    current_state: CurrentFlightState,
    interpreted_scenario: InterpretedScenario,
    general_cfg: dict,
    aircraft_cfg: dict,
    irops_groups: list[IropsConnectionGroup] | None = None,
    arrival_sigma_min: float = 0.0,
    flight_level_candidates: list[int] | None = None,
) -> CostOptimizationResult:
    """
    Main Dynamic Cost / DCI optimizer.

    New parameters
    --------------
    irops_groups:
        Per-connection LTOP cost thresholds (IropsConnectionGroup list).
        When provided, replaces the linear delay_cost_per_min model with
        the non-linear IROPs step function. Built from resolve_connex_uplink()
        results in CiOptimizationService.

    arrival_sigma_min:
        Standard deviation of gate arrival time (minutes).
        When > 0, evaluates E[IROPs(T)] instead of IROPs(E[T]).
        This is the Jensen correction for non-linear cost under uncertainty.
        Sourced from ArrivalUncertaintyInput.sigma_min.

    Existing behavior
    -----------------
    When irops_groups is None/empty, falls back to the existing linear
    delay_cost_per_min model. No breaking changes.
    """

    baseline_performance = _simulate_strategy(
        current_state=current_state,
        mach=current_state.mach,
        aircraft_cfg=aircraft_cfg,
        general_cfg=general_cfg,
    )

    baseline_dynamic = _build_dynamic_cost_factors(
        interpreted_scenario=interpreted_scenario,
        irops_groups=irops_groups,
        arrival_sigma_min=arrival_sigma_min,
        total_pax=current_state.total_pax,
    )

    baseline_cost = calculate_total_strategy_cost(
        fuel_kg=baseline_performance.remaining_fuel_kg,
        time_min=baseline_performance.remaining_time_min,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
        delay_min=_delay_for_candidate(
            interpreted_scenario=interpreted_scenario,
            candidate_time_min=baseline_performance.remaining_time_min,
            baseline_time_min=baseline_performance.remaining_time_min,
        ),
        delay_phase=interpreted_scenario.delay_phase.value,
        reactionary_delay=interpreted_scenario.reactionary_delay,
        dynamic=baseline_dynamic,
    )

    current_flight_level = _flight_level_from_altitude(current_state.altitude_ft)
    candidate_flight_levels = _normalized_flight_level_candidates(
        current_state=current_state,
        flight_level_candidates=flight_level_candidates,
    )
    optimizer_mode = _resolve_optimizer_mode(
        current_state=current_state,
        interpreted_scenario=interpreted_scenario,
        aircraft_cfg=aircraft_cfg,
    )

    costed_strategies: list[CostedStrategy] = []
    points_by_flight_level: dict[int | None, dict[float, tuple[float, float]]] = {}
    candidate_generation_warnings: list[str] = []
    current_level_strategy_cache: dict[float, RemainingCruiseResult] = {}

    for candidate_flight_level in candidate_flight_levels:
        candidate_state = _state_for_flight_level(
            current_state=current_state,
            flight_level=candidate_flight_level,
        )
        candidate_max_mach = _candidate_max_mach(
            interpreted_scenario=interpreted_scenario,
            aircraft_cfg=aircraft_cfg,
            altitude_ft=candidate_state.altitude_ft,
        )
        if optimizer_mode in {
            OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
            OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
            OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
        }:
            candidate_strategies = generate_boeing_777_fmc_ci_strategies(
                aircraft=current_state.aircraft,
                engine_variant=current_state.engine_variant,
                aircraft_cfg=aircraft_cfg,
                general_cfg=general_cfg,
                current_altitude_ft=candidate_state.altitude_ft,
                gross_weight_kg=candidate_state.gross_weight_kg,
                current_mach=current_state.mach,
                current_cost_index=current_state.current_cost_index,
                wind_component_kt=candidate_state.wind_component_kt,
                isa_deviation_c=candidate_state.isa_deviation_c,
                min_mach=interpreted_scenario.min_mach,
                max_mach=candidate_max_mach,
                allow_speed_up=interpreted_scenario.allow_speed_up,
                allow_slow_down=interpreted_scenario.allow_slow_down,
                include_recovery=_should_include_recovery_speed_candidates(
                    interpreted_scenario=interpreted_scenario,
                ),
                preserve_current_strategy=candidate_flight_level == current_flight_level,
                mark_current_label=candidate_flight_level == current_flight_level,
                ensure_current_mach_present=candidate_flight_level == current_flight_level,
            )
        else:
            candidate_strategies = generate_speed_strategies(
                aircraft=current_state.aircraft,
                engine_variant=current_state.engine_variant,
                aircraft_cfg=aircraft_cfg,
                current_altitude_ft=candidate_state.altitude_ft,
                gross_weight_kg=candidate_state.gross_weight_kg,
                current_mach=current_state.mach,
                current_cost_index=current_state.current_cost_index,
                min_mach=interpreted_scenario.min_mach,
                max_mach=candidate_max_mach,
                step=interpreted_scenario.mach_step,
                isa_deviation_c=candidate_state.isa_deviation_c,
                allow_speed_up=interpreted_scenario.allow_speed_up,
                allow_slow_down=interpreted_scenario.allow_slow_down,
                include_recovery=_should_include_recovery_speed_candidates(
                    interpreted_scenario=interpreted_scenario,
                ),
                preserve_current_strategy=candidate_flight_level == current_flight_level,
                mark_current_label=candidate_flight_level == current_flight_level,
                ensure_current_mach_present=candidate_flight_level == current_flight_level,
            )
        points_by_flight_level.setdefault(candidate_flight_level, {})

        for strategy in candidate_strategies:
            _extend_unique_warnings(candidate_generation_warnings, strategy.warnings)
            _extend_unique_warnings(
                candidate_generation_warnings,
                [strategy.cost_index_label] if strategy.cost_index_label else [],
            )
            performance = _simulate_strategy(
                current_state=candidate_state,
                mach=strategy.mach,
                aircraft_cfg=aircraft_cfg,
                general_cfg=general_cfg,
            )
            if (
                candidate_flight_level is not None
                and current_flight_level is not None
                and candidate_flight_level > current_flight_level
            ):
                current_level_same_mach = current_level_strategy_cache.get(
                    round(strategy.mach, 3)
                )
                if current_level_same_mach is None:
                    current_level_same_mach = _simulate_strategy(
                        current_state=current_state,
                        mach=strategy.mach,
                        aircraft_cfg=aircraft_cfg,
                        general_cfg=general_cfg,
                    )
                    current_level_strategy_cache[round(strategy.mach, 3)] = (
                        current_level_same_mach
                    )
                _apply_fmc_step_climb_deferral(
                    performance=performance,
                    current_level_performance=current_level_same_mach,
                    remaining_distance_nm=current_state.remaining_distance_nm,
                    step_climb_distance_nm=current_state.fmc_step_climb_distance_nm,
                )
            _apply_flight_level_transition_penalty(
                performance=performance,
                current_altitude_ft=current_state.altitude_ft,
                target_altitude_ft=candidate_state.altitude_ft,
                baseline_avg_fuel_flow_kg_h=baseline_performance.avg_fuel_flow_kg_h,
            )

            points_by_flight_level[candidate_flight_level][round(strategy.mach, 3)] = (
                performance.remaining_time_min,
                performance.remaining_fuel_kg,
            )

            dynamic = _build_dynamic_cost_factors(
                interpreted_scenario=interpreted_scenario,
                irops_groups=irops_groups,
                arrival_sigma_min=arrival_sigma_min,
                total_pax=current_state.total_pax,
            )

            delay_min = _delay_for_candidate(
                interpreted_scenario=interpreted_scenario,
                candidate_time_min=performance.remaining_time_min,
                baseline_time_min=baseline_performance.remaining_time_min,
            )

            cost = calculate_total_strategy_cost(
                fuel_kg=performance.remaining_fuel_kg,
                time_min=performance.remaining_time_min,
                general_cfg=general_cfg,
                aircraft_cfg=aircraft_cfg,
                delay_min=delay_min,
                delay_phase=interpreted_scenario.delay_phase.value,
                reactionary_delay=interpreted_scenario.reactionary_delay,
                dynamic=dynamic,
            )

            delta_fuel_kg = round(
                performance.remaining_fuel_kg - baseline_performance.remaining_fuel_kg, 2
            )
            delta_time_min = round(
                performance.remaining_time_min - baseline_performance.remaining_time_min, 2
            )
            delta_cost_eur = round(cost.total_cost_eur - baseline_cost.total_cost_eur, 2)
            time_saved_min = round(-delta_time_min, 2)

            gate_time_saved_min = round(
                _gate_time_saved_for_candidate(
                    interpreted_scenario=interpreted_scenario,
                    candidate_time_min=performance.remaining_time_min,
                    baseline_time_min=baseline_performance.remaining_time_min,
                ),
                2,
            )

            extra_fuel_kg = round(max(delta_fuel_kg, 0.0), 2)

            allowed, rejection_reason = _is_strategy_allowed(
                interpreted_scenario=interpreted_scenario,
                delta_fuel_kg=delta_fuel_kg,
                delta_time_min=delta_time_min,
                gate_time_saved_min=gate_time_saved_min,
            )

            costed_strategies.append(
                CostedStrategy(
                    cost_index=strategy.cost_index,
                    mach=strategy.mach,
                    speed_mode=strategy.speed_mode,
                    cas_kt=strategy.cas_kt,
                    cost_index_source=strategy.cost_index_source,
                    cost_index_label=strategy.cost_index_label,
                    flight_level=candidate_flight_level,
                    label=strategy.label,
                    performance=performance,
                    cost=cost,
                    performance_ci_kg_per_min=None,
                    delta_fuel_kg=delta_fuel_kg,
                    delta_time_min=delta_time_min,
                    delta_cost_eur=delta_cost_eur,
                    time_saved_min=time_saved_min,
                    gate_time_saved_min=gate_time_saved_min,
                    extra_fuel_kg=extra_fuel_kg,
                    allowed=allowed,
                    rejection_reason=rejection_reason,
                )
            )

    ci_profiles, ci_reference_tables, ci_profile_warnings = _build_ci_profiles_by_flight_level(
        points_by_flight_level=points_by_flight_level,
        current_state=current_state,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    costed_strategies = [
        _apply_derived_cost_index(
            strategy=strategy,
            current_cost_index=current_state.current_cost_index,
            ci_profile=ci_profiles.get(strategy.flight_level) or {},
            ci_reference_table=ci_reference_tables.get(strategy.flight_level),
        )
        for strategy in costed_strategies
    ]

    current_strategy = _find_current_strategy(
        strategies=costed_strategies,
        current_mach=current_state.mach,
        current_flight_level=current_flight_level,
    )

    best_strategy = _select_best_strategy(
        strategies=costed_strategies,
        objective=interpreted_scenario.objective,
        required_time_recovery_min=interpreted_scenario.required_time_recovery_min,
        optimizer_mode=optimizer_mode,
    )

    is_current_best = _same_strategy_point(best_strategy, current_strategy)

    irops_active = bool(irops_groups)

    recommendation = _build_recommendation(
        current=current_strategy,
        best=best_strategy,
        objective=interpreted_scenario.objective,
        is_current_best=is_current_best,
        irops_active=irops_active,
        arrival_sigma_min=arrival_sigma_min,
        required_time_recovery_min=interpreted_scenario.required_time_recovery_min,
    )

    warnings = list(interpreted_scenario.warnings) + ci_profile_warnings
    _extend_unique_warnings(warnings, candidate_generation_warnings)
    _extend_unique_warnings(warnings, baseline_performance.warnings)
    _extend_unique_warnings(warnings, current_strategy.performance.warnings)
    _extend_unique_warnings(warnings, best_strategy.performance.warnings)

    if optimizer_mode == OPTIMIZER_MODE_BOEING_777_FMC_LIKE:
        warnings.append(
            "Optimizer is using the Boeing 777 FMC-like CI fallback mode. CI values are estimated from a Continental-style FCOM LRC anchor model, not exact Boeing FMC logic."
        )
    elif optimizer_mode == OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE:
        warnings.append(
            "Optimizer is using Airbus-family FMC-style CI mode. A343 uses Airbus-family calibration guidance, while A346 remains a conservative family fallback until stronger variant-specific data is configured."
        )
    elif optimizer_mode == OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE:
        warnings.append(
            "Optimizer is using an empirical FMC CI table mode. CI values are treated as calibrated only to the extent of the configured table."
        )
    else:
        warnings.append(
            "Optimizer is using performance-derived speed mode. Display CI values are internal speed-band labels, not calibrated FMC CI."
        )

    required = interpreted_scenario.required_time_recovery_min
    is_recovery_obj = interpreted_scenario.objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET,
    )
    if required and required > 0 and best_strategy.gate_time_saved_min < required:
        warnings.append(
            f"Recovery target of {required:.0f} min is not achievable in cruise "
            f"(max possible: {best_strategy.gate_time_saved_min:.1f} min). "
            f"Showing best-effort speed recommendation. "
            f"Consider ground options (gate swap, fast turnaround) for remaining {required - best_strategy.gate_time_saved_min:.0f} min."
        )

    if irops_active and arrival_sigma_min > 0.1:
        warnings.append(
            f"IROPs mode active with arrival uncertainty σ={arrival_sigma_min:.1f} min. "
            f"Costs reflect E[IROPs(T)] — Jensen correction applied."
        )
    elif irops_active:
        warnings.append(
            "IROPs step function active. Set arrival_uncertainty_sigma_min > 0 "
            "to enable E[IROPs(T)] integration (recommended)."
        )

    return CostOptimizationResult(
        objective=interpreted_scenario.objective,
        optimizer_mode=optimizer_mode,
        current_strategy=current_strategy,
        best_strategy=best_strategy,
        strategies=costed_strategies,
        is_current_best=is_current_best,
        recommendation=recommendation,
        reasons=interpreted_scenario.reasons,
        warnings=warnings,
    )


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _resolve_optimizer_mode(
    *,
    current_state: CurrentFlightState,
    interpreted_scenario: InterpretedScenario,
    aircraft_cfg: dict,
) -> str:
    configured = resolve_cost_index_optimizer_mode(aircraft_cfg)
    if configured not in {
        OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
        OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
        OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    }:
        return OPTIMIZER_MODE_PERFORMANCE_DERIVED

    if (
        interpreted_scenario.objective == ObjectiveType.FIXED_STRATEGY_EVALUATION
        and interpreted_scenario.min_mach is not None
        and interpreted_scenario.max_mach is not None
        and abs(interpreted_scenario.min_mach - interpreted_scenario.max_mach) < 0.0005
    ):
        return OPTIMIZER_MODE_PERFORMANCE_DERIVED

    return configured


def _build_dynamic_cost_factors(
    *,
    interpreted_scenario: InterpretedScenario,
    irops_groups: list[IropsConnectionGroup] | None = None,
    arrival_sigma_min: float = 0.0,
    total_pax: int = 0,
) -> DynamicCostFactors:
    """
    Maps InterpretedScenario → DynamicCostFactors.

    IROPs extension:
        When irops_groups is provided, the connection thresholds and sigma
        are added to the dynamic factors so calculate_total_strategy_cost()
        uses the non-linear IROPs model automatically.
    """

    groups = irops_groups or []

    reg261_pax = sum(g.affected_pax for g in groups)
    reg261_eur = groups[0].compensation_eur_per_pax if groups and groups[0].compensation_eur_per_pax > 0 else 0.0

    dynamic = DynamicCostFactors(
        fuel_price_override_eur_per_kg=interpreted_scenario.fuel_price_eur_per_kg,
        ets_override_eur_per_kg=interpreted_scenario.ets_eur_per_kg,
        fuel_surcharge_override_eur_per_kg=interpreted_scenario.fuel_surcharge_eur_per_kg,
        time_cost_override_eur_per_hour=interpreted_scenario.time_cost_override_eur_per_h,
        delay_cost_override_eur_per_min=interpreted_scenario.delay_cost_override_eur_per_min,
        connection_groups=groups,
        arrival_uncertainty_sigma_min=arrival_sigma_min,
        total_pax=total_pax,
        reg261_pax=reg261_pax,
        reg261_eur_per_pax=reg261_eur,
    )

    priority_multiplier = _priority_to_time_multiplier(interpreted_scenario.priority)

    if interpreted_scenario.objective == ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET:
        dynamic.connex_time_multiplier = 0.10 + priority_multiplier

    elif interpreted_scenario.objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
    ):
        dynamic.connex_time_multiplier = priority_multiplier

    elif interpreted_scenario.objective in (
        ObjectiveType.MINIMIZE_TOTAL_COST,
        ObjectiveType.MINIMIZE_EXPECTED_TOTAL_COST,
    ):
        dynamic.connex_time_multiplier = priority_multiplier * 0.5

    if interpreted_scenario.reactionary_delay:
        dynamic.reactionary_time_multiplier = 0.15

    return dynamic


def _simulate_strategy(
    *,
    current_state: CurrentFlightState,
    mach: float,
    aircraft_cfg: dict | None = None,
    general_cfg: dict | None = None,
) -> RemainingCruiseResult:
    request = RemainingCruiseInput(
        aircraft=current_state.aircraft,
        engine_variant=current_state.engine_variant,
        altitude_ft=current_state.altitude_ft,
        gross_weight_kg=current_state.gross_weight_kg,
        mach=mach,
        remaining_distance_nm=current_state.remaining_distance_nm,
        wind_component_kt=current_state.wind_component_kt,
        isa_deviation_c=current_state.isa_deviation_c,
        live_fuel_flow_kg_h=current_state.fuel_flow_kg_h,
        live_fuel_flow_source=current_state.fuel_flow_source,
        fuel_flow_reference_altitude_ft=(
            current_state.fuel_flow_reference_altitude_ft
        ),
        fuel_flow_reference_gross_weight_kg=(
            current_state.fuel_flow_reference_gross_weight_kg
        ),
        fuel_flow_reference_mach=current_state.fuel_flow_reference_mach,
        fuel_flow_reference_isa_deviation_c=(
            current_state.fuel_flow_reference_isa_deviation_c
        ),
        segments=_to_cruise_segments(current_state.cruise_segments),
    )
    return simulate_remaining_cruise(
        request,
        aircraft_cfg=aircraft_cfg,
        general_cfg=general_cfg,
    )


def _to_cruise_segments(raw_segments: list[dict[str, float]]) -> list[CruiseSegment] | None:
    segments: list[CruiseSegment] = []

    for raw in raw_segments or []:
        distance_nm = raw.get("distanceNm") or raw.get("distance_nm")
        if distance_nm is None or float(distance_nm) <= 0:
            continue

        segments.append(
            CruiseSegment(
                distance_nm=float(distance_nm),
                altitude_ft=_optional_float(raw.get("altitudeFt") or raw.get("altitude_ft")),
                wind_component_kt=_optional_float(raw.get("windComponentKt") or raw.get("wind_component_kt")),
                isa_deviation_c=_optional_float(raw.get("isaDeviationC") or raw.get("isa_deviation_c")),
            )
        )

    return segments or None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _delay_for_candidate(
    *,
    interpreted_scenario: InterpretedScenario,
    candidate_time_min: float,
    baseline_time_min: float,
) -> float:
    if interpreted_scenario.current_delay_min is None:
        return 0.0

    delta_time_min = candidate_time_min - baseline_time_min

    if delta_time_min < 0:
        gate_time_saved = _effective_gate_time_saved_from_raw_time_saved(
            raw_time_saved_min=-delta_time_min,
            interpreted_scenario=interpreted_scenario,
        )
        delay_change_min = -gate_time_saved
    else:
        delay_change_min = delta_time_min

    return max(interpreted_scenario.current_delay_min + delay_change_min, 0.0)


def _gate_time_saved_for_candidate(
    *,
    interpreted_scenario: InterpretedScenario,
    candidate_time_min: float,
    baseline_time_min: float,
) -> float:
    raw_time_saved_min = baseline_time_min - candidate_time_min

    if raw_time_saved_min <= 0:
        return raw_time_saved_min

    return _effective_gate_time_saved_from_raw_time_saved(
        raw_time_saved_min=raw_time_saved_min,
        interpreted_scenario=interpreted_scenario,
    )


def _effective_gate_time_saved_from_raw_time_saved(
    *,
    raw_time_saved_min: float,
    interpreted_scenario: InterpretedScenario,
) -> float:
    expected_absorption_min = 0.0

    if interpreted_scenario.expected_holding_min is not None:
        expected_absorption_min += interpreted_scenario.expected_holding_min

    if interpreted_scenario.arrival_metering_delay_min is not None:
        expected_absorption_min += interpreted_scenario.arrival_metering_delay_min

    absorption_factor = max(
        0.0, min(interpreted_scenario.arrival_recovery_absorption_factor, 1.0)
    )
    absorbed_min = expected_absorption_min * absorption_factor

    return max(raw_time_saved_min - absorbed_min, 0.0)


def _extend_unique_warnings(target: list[str], new_items: list[str]) -> None:
    for item in new_items:
        text = str(item).strip()
        if text and text not in target:
            target.append(text)


def _normalized_flight_level_candidates(
    *,
    current_state: CurrentFlightState,
    flight_level_candidates: list[int] | None,
) -> list[int]:
    current_flight_level = _flight_level_from_altitude(current_state.altitude_ft)

    if flight_level_candidates:
        normalized = sorted(
            {
                int(level)
                for level in flight_level_candidates
                if 100 <= int(level) <= 600
            }
        )
        if current_flight_level is not None and current_flight_level not in normalized:
            normalized.append(current_flight_level)
            normalized.sort()
        return normalized

    return [current_flight_level] if current_flight_level is not None else []


def _build_ci_profiles_by_flight_level(
    *,
    points_by_flight_level: dict[int | None, dict[float, tuple[float, float]]],
    current_state: CurrentFlightState,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> tuple[dict[int | None, dict[float, object]], dict[int | None, object], list[str]]:
    profiles: dict[int | None, dict[float, object]] = {}
    reference_tables: dict[int | None, object] = {}
    warnings: list[str] = []
    cruise_cfg = (aircraft_cfg.get("performance") or {}).get("cruise") or {}
    normal_max = float(cruise_cfg.get("normal_max_mach") or 0.845)

    for flight_level, points in points_by_flight_level.items():
        profile = derive_cost_index_profile(
            mach_to_time_fuel=points,
            general_cfg=general_cfg,
            aircraft_cfg=aircraft_cfg,
        )
        profiles[flight_level] = profile.by_mach
        _extend_unique_warnings(warnings, profile.warnings)

        include_recovery = any(mach > normal_max + 0.0005 for mach in points)
        try:
            table = generate_ci_mach_table(
                aircraft=current_state.aircraft,
                engine_variant=current_state.engine_variant,
                gross_weight_kg=current_state.gross_weight_kg,
                flight_level=flight_level or _flight_level_from_altitude(current_state.altitude_ft) or 0,
                isa_deviation_c=current_state.isa_deviation_c,
                cg_percent_mac=None,
                wind_component_kt=current_state.wind_component_kt,
                aircraft_cfg=aircraft_cfg,
                general_cfg=general_cfg,
                include_recovery=include_recovery,
            )
            reference_tables[flight_level] = table
            _extend_unique_warnings(warnings, table.warnings)
        except Exception as error:
            warnings.append(
                f"Performance-derived CI/Mach table generation failed for FL{flight_level or 0}: {error}"
            )

    return profiles, reference_tables, warnings


def _flight_level_from_altitude(altitude_ft: float | None) -> int | None:
    if altitude_ft is None or altitude_ft <= 0:
        return None
    return int(round(float(altitude_ft) / 100.0))


def _state_for_flight_level(
    *,
    current_state: CurrentFlightState,
    flight_level: int | None,
) -> CurrentFlightState:
    if flight_level is None:
        return current_state

    altitude_ft = float(flight_level * 100)
    return current_state.model_copy(
        update={
            "altitude_ft": altitude_ft,
            "cruise_segments": _override_segment_altitudes(
                current_state.cruise_segments,
                altitude_ft=altitude_ft,
            ),
        }
    )


def _override_segment_altitudes(
    raw_segments: list[dict[str, float]],
    *,
    altitude_ft: float,
) -> list[dict[str, float]]:
    updated: list[dict[str, float]] = []

    for raw in raw_segments or []:
        segment = dict(raw)
        segment["altitudeFt"] = altitude_ft
        updated.append(segment)

    return updated


def _candidate_max_mach(
    *,
    interpreted_scenario: InterpretedScenario,
    aircraft_cfg: dict,
    altitude_ft: float,
) -> float | None:
    max_mach = interpreted_scenario.max_mach

    envelope = (aircraft_cfg.get("performance") or {}).get("speed_envelope") or {}
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)
    envelope_limit = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)

    if max_mach is None:
        return envelope_limit

    return min(max_mach, envelope_limit)


def _apply_flight_level_transition_penalty(
    *,
    performance: RemainingCruiseResult,
    current_altitude_ft: float,
    target_altitude_ft: float,
    baseline_avg_fuel_flow_kg_h: float,
) -> None:
    delta_altitude_ft = float(target_altitude_ft - current_altitude_ft)
    if abs(delta_altitude_ft) < 100.0:
        return

    if delta_altitude_ft > 0:
        time_penalty_min = delta_altitude_ft / 1500.0
        fuel_penalty_kg = baseline_avg_fuel_flow_kg_h * (time_penalty_min / 60.0) * 1.15
        performance.warnings.append(
            "Flight-level change uses an approximate climb transition penalty; step point optimization is not modeled yet."
        )
    else:
        # Keep descent penalty conservative until full vertical profile logic exists.
        time_penalty_min = abs(delta_altitude_ft) / 2500.0 * 0.35
        fuel_penalty_kg = 0.0
        performance.warnings.append(
            "Flight-level change uses an approximate descent transition penalty; full descent profile logic is not modeled yet."
        )

    performance.remaining_time_min = round(performance.remaining_time_min + time_penalty_min, 2)
    performance.remaining_fuel_kg = round(performance.remaining_fuel_kg + fuel_penalty_kg, 2)
    _refresh_performance_aggregates(performance)


def _apply_fmc_step_climb_deferral(
    *,
    performance: RemainingCruiseResult,
    current_level_performance: RemainingCruiseResult,
    remaining_distance_nm: float,
    step_climb_distance_nm: float | None,
) -> None:
    if step_climb_distance_nm is None or remaining_distance_nm <= 0:
        return

    deferred_distance_nm = float(step_climb_distance_nm)
    if deferred_distance_nm <= 0:
        return

    deferred_fraction = min(max(deferred_distance_nm / remaining_distance_nm, 0.0), 1.0)
    if deferred_fraction < 0.01:
        return

    active_fraction = 1.0 - deferred_fraction
    performance.remaining_time_min = round(
        current_level_performance.remaining_time_min * deferred_fraction
        + performance.remaining_time_min * active_fraction,
        2,
    )
    performance.remaining_fuel_kg = round(
        current_level_performance.remaining_fuel_kg * deferred_fraction
        + performance.remaining_fuel_kg * active_fraction,
        2,
    )
    performance.end_weight_kg = round(
        current_level_performance.end_weight_kg * deferred_fraction
        + performance.end_weight_kg * active_fraction,
        2,
    )
    performance.tas_kt = round(
        current_level_performance.tas_kt * deferred_fraction
        + performance.tas_kt * active_fraction,
        2,
    )
    performance.ground_speed_kt = round(
        current_level_performance.ground_speed_kt * deferred_fraction
        + performance.ground_speed_kt * active_fraction,
        2,
    )
    performance.warnings.append(
        f"FMC step-climb guidance defers higher-level benefit for about {deferred_distance_nm:.0f} NM, so climb savings are reduced until the planned step point."
    )
    _refresh_performance_aggregates(performance)


def _refresh_performance_aggregates(performance: RemainingCruiseResult) -> None:
    performance.avg_fuel_flow_kg_h = round(
        performance.remaining_fuel_kg / max(performance.remaining_time_min / 60.0, 1e-9),
        2,
    )
    performance.fuel_per_nm_kg = round(
        performance.remaining_fuel_kg / max(performance.remaining_distance_nm, 1e-9),
        2,
    )
    performance.fuel_per_min_kg = round(
        performance.remaining_fuel_kg / max(performance.remaining_time_min, 1e-9),
        2,
    )


def _same_strategy_point(left: CostedStrategy, right: CostedStrategy) -> bool:
    return (
        abs(left.mach - right.mach) < 0.0005
        and left.flight_level == right.flight_level
    )


def _cost_index_prefix(strategy: CostedStrategy) -> str:
    if strategy.cost_index_source == COST_INDEX_SOURCE_DISPLAY:
        return "display CI"
    if strategy.cost_index_source == COST_INDEX_SOURCE_FMC_LIKE:
        return "FMC-like CI"
    if strategy.cost_index_source == COST_INDEX_SOURCE_AIRBUS_FAMILY:
        return "Airbus-family CI"
    if strategy.cost_index_source == COST_INDEX_SOURCE_CALIBRATED:
        return "calibrated CI"
    if strategy.cost_index_source == COST_INDEX_SOURCE_CURRENT_INPUT:
        return "current CI"
    return "CI"


def _profile_label(strategy: CostedStrategy) -> str:
    parts: list[str] = []
    if strategy.flight_level is not None:
        parts.append(f"FL{strategy.flight_level}")
    if strategy.speed_mode == "CAS" and strategy.cas_kt is not None:
        parts.append(f"{strategy.cas_kt:.0f} KT")
        parts.append(f"(M{strategy.mach:.3f})")
    else:
        parts.append(f"M{strategy.mach:.3f}")
        if strategy.cas_kt is not None:
            parts.append(f"({strategy.cas_kt:.0f} KT)")

    ci_prefix = _cost_index_prefix(strategy)
    parts.append(f"({ci_prefix} {strategy.cost_index if strategy.cost_index is not None else '-'})")
    return " / ".join(parts)


def _recommendation_action(
    *,
    current: CostedStrategy,
    best: CostedStrategy,
) -> str:
    level_change = ""
    if best.flight_level is not None and best.flight_level != current.flight_level:
        if current.flight_level is None or best.flight_level > current.flight_level:
            level_change = f"Climb to FL{best.flight_level}"
        else:
            level_change = f"Descend to FL{best.flight_level}"

    speed_change = ""
    if abs(best.mach - current.mach) >= 0.0005:
        verb = "speed up" if best.mach > current.mach else "slow down"
        if best.speed_mode == "CAS" and best.cas_kt is not None:
            speed_change = f"{verb} to {best.cas_kt:.0f} KT (M{best.mach:.3f})"
        else:
            speed_change = f"{verb} to M{best.mach:.3f}"

    if level_change and speed_change:
        return f"{level_change} and {speed_change}"
    if level_change:
        return level_change
    if speed_change:
        return speed_change[:1].upper() + speed_change[1:]

    return f"Maintain {_profile_label(best)}"


def _apply_derived_cost_index(
    *,
    strategy: CostedStrategy,
    current_cost_index: int | None,
    ci_profile: dict,
    ci_reference_table=None,
) -> CostedStrategy:
    profile_entry = ci_profile.get(round(strategy.mach, 3))

    if profile_entry is None:
        return strategy

    if strategy.cost_index_source in {
        COST_INDEX_SOURCE_AIRBUS_FAMILY,
        COST_INDEX_SOURCE_FMC_LIKE,
        COST_INDEX_SOURCE_CALIBRATED,
        COST_INDEX_SOURCE_CURRENT_INPUT,
    }:
        return strategy.model_copy(
            update={
                "performance_ci_kg_per_min": profile_entry.economic_ci_kg_per_min,
                "cost_index_label": strategy.cost_index_label or cost_index_source_label(strategy.cost_index_source),
            }
        )

    table_band = find_band_for_mach(
        mach=strategy.mach,
        table=ci_reference_table,
    )

    if strategy.label == "CURRENT" and current_cost_index is not None:
        if _ci_matches_reference_band(current_cost_index, table_band, profile_entry):
            display_cost_index = current_cost_index
            display_cost_index_source = COST_INDEX_SOURCE_CURRENT_INPUT
        else:
            if table_band is not None:
                display_cost_index = representative_cost_index_for_band(table_band)
            else:
                display_cost_index = profile_entry.cost_index
            display_cost_index_source = COST_INDEX_SOURCE_DISPLAY
    else:
        economic_ci = strategy.cost.recommended_ci
        if _ci_matches_profile_band(economic_ci, profile_entry):
            display_cost_index = economic_ci
        elif table_band is not None:
            display_cost_index = representative_cost_index_for_band(table_band)
        else:
            display_cost_index = profile_entry.cost_index
        display_cost_index_source = COST_INDEX_SOURCE_DISPLAY

    return strategy.model_copy(
        update={
            "cost_index": display_cost_index,
            "cost_index_source": display_cost_index_source,
            "cost_index_label": cost_index_source_label(display_cost_index_source),
            "performance_ci_kg_per_min": profile_entry.economic_ci_kg_per_min,
        }
    )


def _ci_matches_profile_band(cost_index: int | None, profile_entry) -> bool:
    if cost_index is None:
        return False

    lower_bound = getattr(profile_entry, "lower_bound_cost_index", None)
    upper_bound = getattr(profile_entry, "upper_bound_cost_index", None)

    if lower_bound is None:
        return False

    if upper_bound is None:
        return int(cost_index) >= int(lower_bound)

    return int(lower_bound) <= int(cost_index) <= int(upper_bound)


def _ci_matches_reference_band(
    cost_index: int | None,
    table_band,
    profile_entry,
) -> bool:
    if table_band is not None:
        upper_ci = getattr(table_band, "upper_ci", None)
        lower_ci = getattr(table_band, "lower_ci", None)
        if lower_ci is not None:
            if upper_ci is None:
                return int(cost_index or 0) >= int(lower_ci)
            return int(lower_ci) <= int(cost_index or 0) <= int(upper_ci)

    return False


def _priority_to_time_multiplier(priority: ScenarioPriority) -> float:
    if priority == ScenarioPriority.CRITICAL:
        return 0.50
    if priority == ScenarioPriority.HIGH:
        return 0.25
    if priority == ScenarioPriority.MEDIUM:
        return 0.10
    return 0.0


def _should_include_recovery_speed_candidates(
    *,
    interpreted_scenario: InterpretedScenario,
) -> bool:
    if interpreted_scenario.required_time_recovery_min is not None and interpreted_scenario.required_time_recovery_min > 0:
        return True

    if interpreted_scenario.priority in {ScenarioPriority.HIGH, ScenarioPriority.CRITICAL}:
        return True

    return interpreted_scenario.objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET,
    )


def _is_strategy_allowed(
    *,
    interpreted_scenario: InterpretedScenario,
    delta_fuel_kg: float,
    delta_time_min: float,
    gate_time_saved_min: float,
) -> tuple[bool, str | None]:
    if interpreted_scenario.max_extra_fuel_kg is not None:
        if delta_fuel_kg > interpreted_scenario.max_extra_fuel_kg:
            return (
                False,
                f"Extra fuel {delta_fuel_kg:.0f} kg exceeds budget "
                f"{interpreted_scenario.max_extra_fuel_kg:.0f} kg.",
            )

    if interpreted_scenario.max_time_loss_min is not None:
        if delta_time_min > interpreted_scenario.max_time_loss_min:
            return (
                False,
                f"Time loss {delta_time_min:.1f} min exceeds limit "
                f"{interpreted_scenario.max_time_loss_min:.1f} min.",
            )

    return True, None


def _find_current_strategy(
    *,
    strategies: list[CostedStrategy],
    current_mach: float,
    current_flight_level: int | None,
) -> CostedStrategy:
    for s in strategies:
        if (
            abs(s.mach - current_mach) < 0.0005
            and s.flight_level == current_flight_level
        ):
            return s

    if not strategies:
        raise ValueError("No strategies available.")

    return min(strategies, key=lambda s: abs(s.mach - current_mach))


def _select_best_strategy(
    *,
    strategies: list[CostedStrategy],
    objective: ObjectiveType,
    required_time_recovery_min: float | None,
    optimizer_mode: str = OPTIMIZER_MODE_PERFORMANCE_DERIVED,
) -> CostedStrategy:
    allowed = [s for s in strategies if s.allowed]

    if not allowed:
        return min(strategies, key=lambda s: s.cost.total_cost_eur)

    selection_pool = allowed
    if optimizer_mode in {
        OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
        OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
        OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    }:
        modeled_candidates = [
            s
            for s in allowed
            if s.cost_index_source in {
                COST_INDEX_SOURCE_AIRBUS_FAMILY,
                COST_INDEX_SOURCE_FMC_LIKE,
                COST_INDEX_SOURCE_CALIBRATED,
            }
        ]
        if modeled_candidates:
            selection_pool = modeled_candidates

    is_recovery_objective = objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET,
    )

    if is_recovery_objective and required_time_recovery_min and required_time_recovery_min > 0:
        # Try to find strategies that fully meet the recovery target
        meeting_target = [
            s for s in selection_pool
            if s.gate_time_saved_min >= required_time_recovery_min
        ]

        if meeting_target:
            # Target reachable: pick cheapest among those that meet it
            return min(meeting_target, key=lambda s: s.cost.total_cost_eur)

        # Target NOT reachable: best-effort — pick max gate time saved.
        # But only if the savings are actually meaningful (≥ 2 min).
        # If even the best strategy saves < 2 min vs target of e.g. 80 min,
        # there is no point deviating from current speed — return current.
        best_effort = max(selection_pool, key=lambda s: s.gate_time_saved_min)

        MIN_MEANINGFUL_RECOVERY_MIN = 2.0
        if best_effort.gate_time_saved_min < MIN_MEANINGFUL_RECOVERY_MIN:
            # Recovery hopeless — return current strategy (maintain CI)
            current_candidates = [s for s in allowed if s.label == "CURRENT"]
            if current_candidates:
                return current_candidates[0]

        return best_effort

    return min(selection_pool, key=lambda s: s.cost.total_cost_eur)


def _build_recommendation(
    *,
    current: CostedStrategy,
    best: CostedStrategy,
    objective: ObjectiveType,
    is_current_best: bool,
    irops_active: bool = False,
    arrival_sigma_min: float = 0.0,
    required_time_recovery_min: float | None = None,
) -> str:
    mode_suffix = ""
    if irops_active:
        if arrival_sigma_min > 0.1:
            mode_suffix = f" [IROPs E[cost], σ={arrival_sigma_min:.0f} min]"
        else:
            mode_suffix = " [IROPs step function]"

    if is_current_best:
        return (
            f"Current profile {_profile_label(current)} "
            f"is already optimal. No change recommended.{mode_suffix}"
        )

    action = _recommendation_action(current=current, best=best)
    delta_cost = best.delta_cost_eur
    cost_str = f"Net saving: {abs(delta_cost):.0f} EUR" if delta_cost < 0 else f"Net cost: +{delta_cost:.0f} EUR"

    gate_str = ""
    if abs(best.gate_time_saved_min) >= 0.5:
        gate_str = f"Gate time saved: {best.gate_time_saved_min:+.1f} min. "

    fuel_str = f"Extra fuel: {best.delta_fuel_kg:+.0f} kg."

    # Recovery target impossible — add explicit warning
    is_recovery_objective = objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET,
    )

    recovery_warning = ""
    if is_recovery_objective and required_time_recovery_min and required_time_recovery_min > 0:
        saved = best.gate_time_saved_min
        if saved < required_time_recovery_min:
            if saved < 2.0:
                recovery_warning = (
                    f" NOTE: {required_time_recovery_min:.0f} min recovery not achievable "
                    f"in cruise. Connections may already be missed. "
                    f"Maintaining current speed is optimal."
                )
            else:
                recovery_warning = (
                    f" WARNING: target {required_time_recovery_min:.0f} min not achievable. "
                    f"Best effort: {saved:.1f} min saved."
                )

    ci_prefix = _cost_index_prefix(best)
    if best.speed_mode == "CAS" and best.cas_kt is not None:
        speed_prefix = f"{best.cas_kt:.0f} KT / M{best.mach:.3f}"
    else:
        speed_prefix = f"M{best.mach:.3f}"

    return (
        f"Set {ci_prefix} {best.cost_index} / Target {speed_prefix}. "
        f"{action}. {gate_str}{fuel_str} {cost_str}.{recovery_warning}{mode_suffix}"
    )


def _ci_bounds(
    *,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> tuple[int, int]:
    aircraft_perf = aircraft_cfg.get("performance", {})
    min_ci = int(parse_number(
        aircraft_perf.get("min_ci", general_cfg.get("min_ci", 0)),
        default=0,
    ))
    max_ci = int(parse_number(
        aircraft_perf.get("max_ci", general_cfg.get("max_ci", 999)),
        default=999,
    ))
    return min_ci, max_ci


def _ci_step(
    *,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> int:
    aircraft_perf = aircraft_cfg.get("performance", {})
    return int(parse_number(
        aircraft_perf.get("ci_step", general_cfg.get("ci_step", 5)),
        default=5,
    ))


def parse_number(value, default=None):
    from optimizer.number_utils import parse_number as _pn
    return _pn(value, default=default)
