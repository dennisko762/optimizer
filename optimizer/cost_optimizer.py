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

from performance_engine.remaining_cruise_simulator import (
    CruiseSegment,
    RemainingCruiseInput,
    RemainingCruiseResult,
    simulate_remaining_cruise,
)
from performance_engine.ci_profile import derive_cost_index_profile

from strategy.strategy_generator import generate_mach_strategies


class CostedStrategy(BaseModel):
    cost_index: int | None = None
    mach: float
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

    min_ci, max_ci = _ci_bounds(general_cfg=general_cfg, aircraft_cfg=aircraft_cfg)
    ci_step = _ci_step(general_cfg=general_cfg, aircraft_cfg=aircraft_cfg)

    strategies = generate_mach_strategies(
        current_mach=current_state.mach,
        current_cost_index=current_state.current_cost_index,
        min_mach=interpreted_scenario.min_mach,
        max_mach=interpreted_scenario.max_mach,
        step=interpreted_scenario.mach_step,
        allow_speed_up=interpreted_scenario.allow_speed_up,
        allow_slow_down=interpreted_scenario.allow_slow_down,
        min_ci=min_ci,
        max_ci=max_ci,
        ci_step=ci_step,
    )

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

    costed_strategies: list[CostedStrategy] = []
    mach_to_time_fuel: dict[float, tuple[float, float]] = {}

    for strategy in strategies:
        performance = _simulate_strategy(
            current_state=current_state,
            mach=strategy.mach,
            aircraft_cfg=aircraft_cfg,
            general_cfg=general_cfg,
        )

        mach_to_time_fuel[round(strategy.mach, 3)] = (
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

    ci_profile = derive_cost_index_profile(
        mach_to_time_fuel=mach_to_time_fuel,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    costed_strategies = [
        _apply_derived_cost_index(
            strategy=strategy,
            current_cost_index=current_state.current_cost_index,
            ci_profile=ci_profile.by_mach,
        )
        for strategy in costed_strategies
    ]

    current_strategy = _find_current_strategy(
        strategies=costed_strategies,
        current_mach=current_state.mach,
    )

    best_strategy = _select_best_strategy(
        strategies=costed_strategies,
        objective=interpreted_scenario.objective,
        required_time_recovery_min=interpreted_scenario.required_time_recovery_min,
    )

    is_current_best = abs(best_strategy.mach - current_strategy.mach) < 0.0005

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

    warnings = list(interpreted_scenario.warnings) + ci_profile.warnings
    _extend_unique_warnings(warnings, baseline_performance.warnings)
    _extend_unique_warnings(warnings, current_strategy.performance.warnings)
    _extend_unique_warnings(warnings, best_strategy.performance.warnings)

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
        current_strategy=current_strategy,
        best_strategy=best_strategy,
        strategies=costed_strategies,
        is_current_best=is_current_best,
        recommendation=recommendation,
        reasons=interpreted_scenario.reasons,
        warnings=warnings,
    )


# ─── Internal helpers ─────────────────────────────────────────────────────────

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


def _apply_derived_cost_index(
    *,
    strategy: CostedStrategy,
    current_cost_index: int | None,
    ci_profile: dict,
) -> CostedStrategy:
    profile_entry = ci_profile.get(round(strategy.mach, 3))

    if profile_entry is None:
        return strategy

    if strategy.label == "CURRENT" and current_cost_index is not None:
        # Keep the actual FMS CI for the current strategy
        display_cost_index = current_cost_index
    else:
        # For recommended strategies, show the ECONOMIC CI (what the pilot
        # enters in the FMS) rather than the break-even CI between Mach steps.
        #
        # Break-even CI (from ci_profile) answers:
        #   "what CI justifies THIS Mach vs the previous step?"
        #   → very low if fuel curve is flat (e.g. CI 1 for M0.820 vs M0.810)
        #
        # Economic CI (from cost model) answers:
        #   "what CI should the pilot set given actual time/fuel cost ratio?"
        #   → operationally meaningful (e.g. CI 157 for B772 at these costs)
        #
        # We use the HIGHER of the two: the economic CI is the FMS input value,
        # but if the break-even CI is higher (e.g. large fuel difference), use that.
        economic_ci = strategy.cost.recommended_ci
        breakeven_ci = profile_entry.cost_index
        display_cost_index = max(economic_ci, breakeven_ci)

    return strategy.model_copy(
        update={
            "cost_index": display_cost_index,
            "performance_ci_kg_per_min": profile_entry.economic_ci_kg_per_min,
        }
    )


def _priority_to_time_multiplier(priority: ScenarioPriority) -> float:
    if priority == ScenarioPriority.CRITICAL:
        return 0.50
    if priority == ScenarioPriority.HIGH:
        return 0.25
    if priority == ScenarioPriority.MEDIUM:
        return 0.10
    return 0.0


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
) -> CostedStrategy:
    for s in strategies:
        if abs(s.mach - current_mach) < 0.0005:
            return s

    if not strategies:
        raise ValueError("No strategies available.")

    return min(strategies, key=lambda s: abs(s.mach - current_mach))


def _select_best_strategy(
    *,
    strategies: list[CostedStrategy],
    objective: ObjectiveType,
    required_time_recovery_min: float | None,
) -> CostedStrategy:
    allowed = [s for s in strategies if s.allowed]

    if not allowed:
        return min(strategies, key=lambda s: s.cost.total_cost_eur)

    is_recovery_objective = objective in (
        ObjectiveType.MEET_TARGET_WITH_MIN_FUEL,
        ObjectiveType.MEET_OTP_TARGET_THEN_MINIMIZE_COST,
        ObjectiveType.MAX_RECOVERY_WITHIN_FUEL_BUDGET,
    )

    if is_recovery_objective and required_time_recovery_min and required_time_recovery_min > 0:
        # Try to find strategies that fully meet the recovery target
        meeting_target = [
            s for s in allowed
            if s.gate_time_saved_min >= required_time_recovery_min
        ]

        if meeting_target:
            # Target reachable: pick cheapest among those that meet it
            return min(meeting_target, key=lambda s: s.cost.total_cost_eur)

        # Target NOT reachable: best-effort — pick max gate time saved.
        # But only if the savings are actually meaningful (≥ 2 min).
        # If even the best strategy saves < 2 min vs target of e.g. 80 min,
        # there is no point deviating from current speed — return current.
        best_effort = max(allowed, key=lambda s: s.gate_time_saved_min)

        MIN_MEANINGFUL_RECOVERY_MIN = 2.0
        if best_effort.gate_time_saved_min < MIN_MEANINGFUL_RECOVERY_MIN:
            # Recovery hopeless — return current strategy (maintain CI)
            current_candidates = [s for s in allowed if s.label == "CURRENT"]
            if current_candidates:
                return current_candidates[0]

        return best_effort

    return min(allowed, key=lambda s: s.cost.total_cost_eur)


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
            f"Current speed M{current.mach:.3f} (CI {current.cost_index}) "
            f"is already optimal. No change recommended.{mode_suffix}"
        )

    direction = "Speed up" if best.mach > current.mach else "Slow down"
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

    return (
        f"Set CI {best.cost_index} / Target M.{int(round(best.mach * 1000))}. "
        f"{direction}. {gate_str}{fuel_str} {cost_str}.{recovery_warning}{mode_suffix}"
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
