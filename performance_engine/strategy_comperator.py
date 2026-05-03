from __future__ import annotations

from dataclasses import dataclass

from performance_engine.remaining_cruise_simulator import (
    RemainingCruiseInput,
    RemainingCruiseResult,
    simulate_remaining_cruise,
)


@dataclass
class StrategyResult:
    mach: float
    result: RemainingCruiseResult

    delta_fuel_kg: float
    time_saved_min: float
    kg_per_min_saved: float | None



def generate_mach_candidates(
    *,
    baseline_mach: float,
    max_mach: float = 0.89,
    step: float = 0.005,
    include_slower: bool = False,
    min_mach: float | None = None,
) -> list[float]:
    """
    Generates operationally useful Mach candidates.

    Default:
    baseline -> max_mach in 0.005 steps

    Example:
    baseline 0.85, max 0.89, step 0.005
    => [0.85, 0.855, 0.86, ... 0.89]
    """

    if baseline_mach <= 0:
        raise ValueError("baseline_mach must be greater than zero.")

    if step <= 0:
        raise ValueError("step must be greater than zero.")

    if include_slower:
        start = min_mach if min_mach is not None else max(0.70, baseline_mach - 0.03)
    else:
        start = baseline_mach

    candidates = []
    current = start

    # avoid floating point weirdness
    while current <= max_mach + 1e-9:
        candidates.append(round(current, 3))
        current += step

    # ensure baseline is included
    baseline_rounded = round(baseline_mach, 3)
    if baseline_rounded not in candidates:
        candidates.append(baseline_rounded)

    return sorted(set(candidates))


def compare_mach_strategies(
    *,
    aircraft: str,
    altitude_ft: float,
    gross_weight_kg: float,
    baseline_mach: float,
    candidate_mach_values: list[float],
    remaining_distance_nm: float,
    wind_component_kt: float,
    isa_deviation_c: float = 0.0,
) -> list[StrategyResult]:
    baseline_input = RemainingCruiseInput(
        aircraft=aircraft,
        altitude_ft=altitude_ft,
        gross_weight_kg=gross_weight_kg,
        mach=baseline_mach,
        remaining_distance_nm=remaining_distance_nm,
        wind_component_kt=wind_component_kt,
        isa_deviation_c=isa_deviation_c,
    )

    baseline_result = simulate_remaining_cruise(baseline_input)

    strategies: list[StrategyResult] = []

    for mach in candidate_mach_values:
        request = RemainingCruiseInput(
            aircraft=aircraft,
            altitude_ft=altitude_ft,
            gross_weight_kg=gross_weight_kg,
            mach=mach,
            remaining_distance_nm=remaining_distance_nm,
            wind_component_kt=wind_component_kt,
            isa_deviation_c=isa_deviation_c,
        )

        result = simulate_remaining_cruise(request)

        delta_fuel_kg = result.remaining_fuel_kg - baseline_result.remaining_fuel_kg
        time_saved_min = baseline_result.remaining_time_min - result.remaining_time_min

        if time_saved_min > 0:
            kg_per_min_saved = delta_fuel_kg / time_saved_min
        else:
            kg_per_min_saved = None

        strategies.append(
            StrategyResult(
                mach=mach,
                result=result,
                delta_fuel_kg=round(delta_fuel_kg, 2),
                time_saved_min=round(time_saved_min, 2),
                kg_per_min_saved=round(kg_per_min_saved, 2)
                if kg_per_min_saved is not None
                else None,
            )
        )

    return strategies