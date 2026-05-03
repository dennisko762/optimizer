from __future__ import annotations

from dataclasses import dataclass

from optimizer.cost_model import ci_scale_factor, clamp_ci


@dataclass(frozen=True)
class DerivedCostIndex:
    mach: float
    economic_ci_kg_per_min: float
    cost_index: int


@dataclass(frozen=True)
class CiProfileResult:
    by_mach: dict[float, DerivedCostIndex]
    warnings: list[str]


def derive_cost_index_profile(
    *,
    mach_to_time_fuel: dict[float, tuple[float, float]],
    general_cfg: dict,
    aircraft_cfg: dict,
) -> CiProfileResult:
    """
    Build a CI profile from simulated performance points.

    `economic_ci_kg_per_min` is interpreted as the break-even threshold
    between two adjacent Mach points:

      delta_fuel_kg + CI_kg_per_min * delta_time_min = 0

    Rearranged:

      CI_kg_per_min = -delta_fuel_kg / delta_time_min

    If the threshold becomes negative, the faster point dominates the
    slower point on both fuel and time, so the practical threshold is 0.
    """

    if not mach_to_time_fuel:
        return CiProfileResult(by_mach={}, warnings=[])

    points = sorted(
        (
            (round(mach, 3), time_min, fuel_kg)
            for mach, (time_min, fuel_kg) in mach_to_time_fuel.items()
        ),
        key=lambda item: item[0],
    )

    scale = ci_scale_factor(
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    by_mach: dict[float, DerivedCostIndex] = {}
    warnings: list[str] = []
    running_ci_kg_per_min = 0.0
    flat_profile = True

    for index, (mach, time_min, fuel_kg) in enumerate(points):
        if index == 0:
            ci_kg_per_min = 0.0
        else:
            _, previous_time_min, previous_fuel_kg = points[index - 1]
            ci_kg_per_min = _break_even_ci_kg_per_min(
                delta_fuel_kg=fuel_kg - previous_fuel_kg,
                delta_time_min=time_min - previous_time_min,
            )

        running_ci_kg_per_min = max(running_ci_kg_per_min, ci_kg_per_min)

        if running_ci_kg_per_min > 0:
            flat_profile = False

        by_mach[mach] = DerivedCostIndex(
            mach=mach,
            economic_ci_kg_per_min=round(running_ci_kg_per_min, 4),
            cost_index=clamp_ci(
                int(round(running_ci_kg_per_min * scale)),
                general_cfg=general_cfg,
                aircraft_cfg=aircraft_cfg,
            ),
        )

    if flat_profile and len(points) > 1:
        warnings.append(
            "Performance-derived CI profile collapsed to CI 0 across the evaluated Mach range. "
            "The current cruise performance source shows no fuel penalty for higher Mach in this state."
        )

    return CiProfileResult(
        by_mach=by_mach,
        warnings=warnings,
    )


def _break_even_ci_kg_per_min(
    *,
    delta_fuel_kg: float,
    delta_time_min: float,
) -> float:
    if abs(delta_time_min) < 1e-9:
        return 0.0

    return max((-delta_fuel_kg) / delta_time_min, 0.0)
