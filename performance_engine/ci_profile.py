from __future__ import annotations

from dataclasses import dataclass

from optimizer.cost_model import ci_scale_factor, clamp_ci


@dataclass(frozen=True)
class DerivedCostIndex:
    mach: float

    # True aircraft-agnostic decision metric:
    # kg extra fuel required per minute saved at/near this Mach threshold.
    economic_ci_kg_per_min: float

    # Display/scaled CI for UI/FMC-like presentation. This is not guaranteed to
    # equal a real Boeing/Airbus FMC ECON CI unless the aircraft profile is
    # calibrated accordingly.
    cost_index: int

    # Explicit alias for UI and debugging. Same value as economic_ci_kg_per_min.
    fuel_time_tradeoff_kg_per_min: float


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
    Build an aircraft-agnostic fuel/time tradeoff profile from simulated points.

    The core metric is not a literal FMC Cost Index. It is the break-even fuel
    price of time between two adjacent Mach candidates:

        delta_fuel_kg + CI_kg_per_min * delta_time_min = 0
        CI_kg_per_min = -delta_fuel_kg / delta_time_min

    For a faster Mach point, delta_time_min should be negative and delta_fuel_kg
    usually positive. The result is kg/min: extra fuel required per minute saved.

    The returned cost_index is only a configured display scaling of kg/min.
    Use aircraft YAML cost_index_mapping / performance.ci_scale_factor to make
    this display match the add-on's expected CI range as closely as possible.
    """

    if not mach_to_time_fuel:
        return CiProfileResult(by_mach={}, warnings=[])

    points = sorted(
        (
            (round(mach, 3), float(time_min), float(fuel_kg))
            for mach, (time_min, fuel_kg) in mach_to_time_fuel.items()
        ),
        key=lambda item: item[0],
    )

    scale = _resolve_ci_scale_factor(
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    by_mach: dict[float, DerivedCostIndex] = {}
    warnings: list[str] = []

    running_ci_kg_per_min = 0.0
    has_positive_tradeoff = False
    non_monotonic_time = False
    dominated_points: list[float] = []

    for index, (mach, time_min, fuel_kg) in enumerate(points):
        if index == 0:
            ci_kg_per_min = 0.0
        else:
            previous_mach, previous_time_min, previous_fuel_kg = points[index - 1]
            delta_time_min = time_min - previous_time_min
            delta_fuel_kg = fuel_kg - previous_fuel_kg

            if delta_time_min >= 0:
                # Higher Mach should normally reduce time. If it does not, wind/profile
                # effects or model noise have made this point unsuitable as a speed-up
                # threshold. Keep CI monotonic instead of allowing a misleading dip.
                non_monotonic_time = True
                ci_kg_per_min = running_ci_kg_per_min
            else:
                ci_kg_per_min = _break_even_ci_kg_per_min(
                    delta_fuel_kg=delta_fuel_kg,
                    delta_time_min=delta_time_min,
                )

            if delta_time_min < 0 and delta_fuel_kg <= 0:
                dominated_points.append(mach)

            if previous_mach >= mach:
                warnings.append("Duplicate or unsorted Mach point detected after rounding.")

        running_ci_kg_per_min = max(running_ci_kg_per_min, ci_kg_per_min)

        if running_ci_kg_per_min > 0:
            has_positive_tradeoff = True

        display_ci = clamp_ci(
            int(round(running_ci_kg_per_min * scale)),
            general_cfg=general_cfg,
            aircraft_cfg=aircraft_cfg,
        )

        by_mach[mach] = DerivedCostIndex(
            mach=mach,
            economic_ci_kg_per_min=round(running_ci_kg_per_min, 4),
            fuel_time_tradeoff_kg_per_min=round(running_ci_kg_per_min, 4),
            cost_index=display_ci,
        )

    if not has_positive_tradeoff and len(points) > 1:
        warnings.append(
            "Performance-derived fuel/time profile collapsed to CI 0 across the evaluated Mach range. "
            "The current performance source shows no fuel penalty for higher Mach in this state. "
            "Check aircraft calibration, Mach bounds and wind/profile inputs."
        )

    if non_monotonic_time:
        warnings.append(
            "At least one higher-Mach candidate did not reduce cruise time. "
            "CI profile was kept monotonic to avoid misleading display CI values."
        )

    if dominated_points:
        warnings.append(
            "Some faster Mach points dominated slower points on both time and fuel "
            f"({', '.join(f'M{mach:.3f}' for mach in dominated_points)}). "
            "This usually indicates model noise, too coarse segmentation, or calibration issues."
        )

    return CiProfileResult(
        by_mach=by_mach,
        warnings=warnings,
    )


def _resolve_ci_scale_factor(*, general_cfg: dict, aircraft_cfg: dict) -> float:
    # Preferred new config location.
    mapping = aircraft_cfg.get("cost_index_mapping") or {}
    if isinstance(mapping, dict) and mapping.get("kg_per_min_to_ci_factor") is not None:
        try:
            return float(mapping["kg_per_min_to_ci_factor"])
        except (TypeError, ValueError):
            pass

    # Existing project behavior.
    return ci_scale_factor(
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )


def _break_even_ci_kg_per_min(
    *,
    delta_fuel_kg: float,
    delta_time_min: float,
) -> float:
    if abs(delta_time_min) < 1e-9:
        return 0.0

    return max((-delta_fuel_kg) / delta_time_min, 0.0)
