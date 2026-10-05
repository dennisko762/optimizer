from __future__ import annotations

from dataclasses import dataclass
import math

from optimizer.cost_model import ci_scale_factor, clamp_ci
from performance_engine.ci_table.ci_mach_table_lookup import (
    cost_index_to_mach_from_table,
    mach_to_cost_index_from_table,
)
from performance_engine.ci_table.ci_mach_table_model import CiMachTable


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
    threshold_cost_index: int
    lower_bound_cost_index: int
    upper_bound_cost_index: int | None

    # Explicit alias for UI and debugging. Same value as economic_ci_kg_per_min.
    fuel_time_tradeoff_kg_per_min: float


@dataclass(frozen=True)
class CiProfileResult:
    by_mach: dict[float, DerivedCostIndex]
    warnings: list[str]


@dataclass(frozen=True)
class CiMachAnchor:
    cost_index: int
    mach: float
    label: str | None = None


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
    anchors = resolve_ci_mach_anchors(aircraft_cfg=aircraft_cfg)

    by_mach: dict[float, DerivedCostIndex] = {}
    warnings: list[str] = []

    running_ci_kg_per_min = 0.0
    has_positive_tradeoff = False
    non_monotonic_time = False
    dominated_points: list[float] = []
    thresholds: list[tuple[float, float, int]] = []

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

        thresholds.append(
            (
                mach,
                round(running_ci_kg_per_min, 4),
                _threshold_cost_index(
                    running_ci_kg_per_min=running_ci_kg_per_min,
                    scale=scale,
                    general_cfg=general_cfg,
                    aircraft_cfg=aircraft_cfg,
                ),
            )
        )

    for index, (mach, ci_kg_per_min, threshold_cost_index) in enumerate(thresholds):
        next_threshold = thresholds[index + 1][2] if index + 1 < len(thresholds) else None
        lower_bound = threshold_cost_index
        upper_bound = _upper_bound_cost_index(lower_bound, next_threshold)
        representative = _representative_cost_index(
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            mach=mach,
            anchors=anchors,
        )

        by_mach[mach] = DerivedCostIndex(
            mach=mach,
            economic_ci_kg_per_min=ci_kg_per_min,
            fuel_time_tradeoff_kg_per_min=ci_kg_per_min,
            cost_index=representative,
            threshold_cost_index=threshold_cost_index,
            lower_bound_cost_index=lower_bound,
            upper_bound_cost_index=upper_bound,
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


def cost_index_to_mach(
    *,
    cost_index: int | float,
    aircraft_cfg: dict,
    table: CiMachTable | None = None,
) -> float | None:
    from_table = cost_index_to_mach_from_table(cost_index, table)
    if from_table is not None:
        return from_table

    anchors = resolve_ci_mach_anchors(aircraft_cfg=aircraft_cfg)
    if not anchors:
        return None

    ci_value = float(cost_index)
    if ci_value <= anchors[0].cost_index:
        return anchors[0].mach
    if ci_value >= anchors[-1].cost_index:
        return anchors[-1].mach

    for left, right in zip(anchors, anchors[1:]):
        if left.cost_index <= ci_value <= right.cost_index:
            return _interpolate(
                x=ci_value,
                x0=float(left.cost_index),
                y0=left.mach,
                x1=float(right.cost_index),
                y1=right.mach,
            )

    return anchors[-1].mach


def mach_to_cost_index(
    *,
    mach: float,
    aircraft_cfg: dict,
    table: CiMachTable | None = None,
) -> int | None:
    from_table = mach_to_cost_index_from_table(mach, table)
    if from_table is not None:
        return from_table

    anchors = resolve_ci_mach_anchors(aircraft_cfg=aircraft_cfg)
    if not anchors:
        return None

    return _mach_to_cost_index_from_anchors(mach=mach, anchors=anchors)


def _mach_to_cost_index_from_anchors(
    *,
    mach: float,
    anchors: list[CiMachAnchor],
) -> int | None:
    if not anchors:
        return None

    if mach <= anchors[0].mach:
        return anchors[0].cost_index
    if mach >= anchors[-1].mach:
        return anchors[-1].cost_index

    for left, right in zip(anchors, anchors[1:]):
        if left.mach <= mach <= right.mach:
            ci_value = _interpolate(
                x=mach,
                x0=left.mach,
                y0=float(left.cost_index),
                x1=right.mach,
                y1=float(right.cost_index),
            )
            return int(round(ci_value))

    return anchors[-1].cost_index


def resolve_ci_mach_anchors(*, aircraft_cfg: dict) -> list[CiMachAnchor]:
    mapping = aircraft_cfg.get("cost_index_mapping") or {}
    raw_anchors = mapping.get("econ_mach_anchors")
    anchors = _parse_ci_mach_anchors(raw_anchors)
    if anchors:
        return anchors

    return _default_ci_mach_anchors(aircraft_cfg=aircraft_cfg)


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


def _threshold_cost_index(
    *,
    running_ci_kg_per_min: float,
    scale: float,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> int:
    scaled = running_ci_kg_per_min * scale
    # Threshold semantics: this is the minimum integer CI that should still
    # select this Mach band, so use ceil rather than round.
    return clamp_ci(
        int(math.ceil(max(scaled, 0.0) - 1e-9)),
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )


def _upper_bound_cost_index(
    lower_bound: int,
    next_threshold: int | None,
) -> int | None:
    if next_threshold is None:
        return None
    return max(lower_bound, next_threshold - 1)


def _representative_cost_index(
    *,
    lower_bound: int,
    upper_bound: int | None,
    mach: float,
    anchors: list[CiMachAnchor],
) -> int:
    anchored = None
    if anchors:
        anchored = _mach_to_cost_index_from_anchors(mach=mach, anchors=anchors)

    if anchored is not None:
        if upper_bound is None:
            return max(lower_bound, anchored)
        return min(max(lower_bound, anchored), upper_bound)

    if upper_bound is None:
        return lower_bound
    return lower_bound + ((upper_bound - lower_bound) // 2)


def _parse_ci_mach_anchors(raw_anchors: object) -> list[CiMachAnchor]:
    if not isinstance(raw_anchors, list):
        return []

    anchors: list[CiMachAnchor] = []
    for raw_anchor in raw_anchors:
        if not isinstance(raw_anchor, dict):
            continue

        ci_raw = raw_anchor.get("ci")
        mach_raw = raw_anchor.get("mach")
        if ci_raw is None or mach_raw is None:
            continue

        try:
            ci_value = int(round(float(ci_raw)))
            mach_value = float(mach_raw)
        except (TypeError, ValueError):
            continue

        if mach_value <= 0:
            continue

        anchors.append(
            CiMachAnchor(
                cost_index=ci_value,
                mach=mach_value,
                label=str(raw_anchor.get("label")).strip() if raw_anchor.get("label") is not None else None,
            )
        )

    anchors.sort(key=lambda anchor: (anchor.cost_index, anchor.mach))
    deduped: list[CiMachAnchor] = []
    for anchor in anchors:
        if deduped and anchor.cost_index == deduped[-1].cost_index:
            deduped[-1] = anchor
        else:
            deduped.append(anchor)

    if len(deduped) < 2:
        return []

    return deduped


def _default_ci_mach_anchors(*, aircraft_cfg: dict) -> list[CiMachAnchor]:
    performance = aircraft_cfg.get("performance") or {}
    cruise = performance.get("cruise") or {}
    min_ci = int((aircraft_cfg.get("cost_index_mapping") or {}).get("min_ci", performance.get("min_ci", 0)) or 0)
    max_ci = int((aircraft_cfg.get("cost_index_mapping") or {}).get("max_ci", performance.get("max_ci", 999)) or 999)

    normal_min = _safe_float(cruise.get("normal_min_mach"))
    reference = _safe_float(cruise.get("reference_mach"))
    normal_max = _safe_float(cruise.get("normal_max_mach"))

    if normal_min is None or reference is None or normal_max is None or max_ci <= min_ci:
        return []

    lrc_ci = min_ci + int(round((max_ci - min_ci) * 0.55))
    high_ci = min_ci + int(round((max_ci - min_ci) * 0.85))

    low_mach = round(normal_min + 0.002, 3)
    lrc_mach = round(min(reference, normal_max - 0.01), 3)
    high_mach = round(min(lrc_mach + 0.012, normal_max - 0.002), 3)

    return [
        CiMachAnchor(cost_index=min_ci, mach=low_mach, label="max_range"),
        CiMachAnchor(cost_index=lrc_ci, mach=lrc_mach, label="lrc_like"),
        CiMachAnchor(cost_index=high_ci, mach=high_mach, label="high_cost"),
        CiMachAnchor(cost_index=max_ci, mach=round(normal_max, 3), label="near_min_time"),
    ]


def _interpolate(*, x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if abs(x1 - x0) < 1e-9:
        return y0
    ratio = (x - x0) / (x1 - x0)
    return y0 + ratio * (y1 - y0)


def _safe_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
