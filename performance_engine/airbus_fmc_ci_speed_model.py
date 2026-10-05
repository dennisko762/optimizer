from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from performance_engine.boeing_777_fmc_ci_speed_model import (
    FmcSpeedTarget,
    SPEED_MODE_CAS,
    SPEED_MODE_MACH,
)
from performance_engine.speed_envelope import cas_to_mach, mach_to_cas, max_mach_at_altitude


def estimate_airbus_family_fmc_econ_speed(
    *,
    aircraft: str,
    engine_variant: str | None,
    gross_weight_kg: float,
    altitude_ft: float,
    cost_index: int,
    wind_component_kt: float,
    isa_deviation_c: float,
    aircraft_cfg: Mapping[str, Any],
    general_cfg: Mapping[str, Any],
) -> FmcSpeedTarget:
    """
    Airbus-family FMC-style ECON speed fallback.

    This is intentionally not an exact Airbus FMGC implementation. It uses the
    Airbus family guidance available in the local Airbus CI reference together
    with the existing shared physics layer:
      - ECON depends on gross weight, altitude, cost index and wind.
      - Below crossover, CAS is primary.
      - Above crossover, Mach is primary.
      - Wind corrections are applied around a no-wind ECON schedule.
      - Cruise speed is capped below the speed envelope.
    """

    del engine_variant
    del general_cfg

    if gross_weight_kg <= 0:
        raise ValueError("gross_weight_kg must be greater than zero.")
    if altitude_ft <= 0:
        raise ValueError("altitude_ft must be greater than zero.")

    cfg = _airbus_family_cfg(aircraft_cfg)
    warnings: list[str] = [
        "Airbus-family FMC-style CI model is based on Airbus family CI guidance plus shared simulator physics, not proprietary FMGC software."
    ]

    confidence = str(cfg.get("confidence_level") or "fallback").strip().lower()
    if confidence == "calibrated":
        source = "airbus_family_calibrated_ci"
        warnings.append(
            f"{str(aircraft).upper()} uses Airbus-family calibrated CI guidance for the displayed FMC CI path."
        )
    else:
        source = "airbus_family_fmc_ci_fallback"
        warnings.append(
            f"{str(aircraft).upper()} is using a conservative Airbus-family CI fallback because variant-specific calibration is incomplete."
        )

    speed_mode = _speed_mode_for_altitude(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
    )

    lrc_anchor_ci = int(cfg.get("lrc_anchor_ci") or 50)
    max_ci = int(cfg.get("max_ci") or 300)
    soft_anchor_ci = int(cfg.get("high_speed_soft_anchor_ci") or max(max_ci, 200))
    low_ci_shape = _float_or_default(cfg.get("low_ci_shape"), 0.85)
    high_ci_shape = _float_or_default(cfg.get("high_ci_shape"), 0.95)
    high_ci_soft_fraction = _float_or_default(cfg.get("high_ci_soft_fraction"), 0.55)

    zero_wind_lrc_mach = _estimate_lrc_anchor_mach(
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        isa_deviation_c=isa_deviation_c,
        aircraft_cfg=aircraft_cfg,
        cfg=cfg,
    )
    zero_wind_lrc_cas = mach_to_cas(zero_wind_lrc_mach, altitude_ft)

    zero_wind_mrc_cas = _estimate_mrc_cas_kt(
        lrc_anchor_cas_kt=zero_wind_lrc_cas,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        cfg=cfg,
    )
    zero_wind_mrc_mach = cas_to_mach(zero_wind_mrc_cas, altitude_ft)

    zero_wind_max_mach = _estimate_econ_max_mach(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
        cfg=cfg,
    )
    zero_wind_target_mach = _cost_index_to_zero_wind_target_mach(
        cost_index=max(0, min(int(cost_index), max_ci)),
        lrc_anchor_ci=lrc_anchor_ci,
        max_ci=max_ci,
        soft_anchor_ci=soft_anchor_ci,
        low_ci_shape=low_ci_shape,
        high_ci_shape=high_ci_shape,
        high_ci_soft_fraction=high_ci_soft_fraction,
        mrc_mach=zero_wind_mrc_mach,
        lrc_mach=zero_wind_lrc_mach,
        max_mach=zero_wind_max_mach,
    )

    zero_wind_target_cas = mach_to_cas(zero_wind_target_mach, altitude_ft)
    wind_adjustment_mach = _wind_adjustment_mach(
        wind_component_kt=wind_component_kt,
        cfg=cfg,
    )
    isa_adjustment_mach = _isa_adjustment_mach(
        isa_deviation_c=isa_deviation_c,
        cfg=cfg,
    )

    target_mach = zero_wind_target_mach + wind_adjustment_mach + isa_adjustment_mach
    target_mach = max(target_mach, zero_wind_mrc_mach)
    if target_mach > zero_wind_max_mach:
        target_mach = zero_wind_max_mach
        warnings.append(
            "Target ECON speed reached the configured Airbus-family high-speed / envelope-limited region."
        )

    target_cas_kt = mach_to_cas(target_mach, altitude_ft)
    if target_cas_kt < zero_wind_mrc_cas:
        target_cas_kt = zero_wind_mrc_cas
        target_mach = cas_to_mach(target_cas_kt, altitude_ft)

    target_cas_kt, target_mach, envelope_warnings = _enforce_speed_envelope(
        target_cas_kt=target_cas_kt,
        target_mach=target_mach,
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
        cfg=cfg,
    )
    _extend_unique(warnings, envelope_warnings)

    if abs(altitude_ft / 100.0 - _speed_mode_crossover_fl(aircraft_cfg, cfg)) <= 10.0:
        warnings.append(
            "Target speed is close to the Airbus-family CAS/Mach crossover region; display authority may switch with level changes."
        )

    if cost_index >= soft_anchor_ci:
        warnings.append(
            f"CI {cost_index} is in the Airbus-family high-speed region and may be constrained by the speed envelope."
        )

    primary_value = round(target_cas_kt, 1) if speed_mode == SPEED_MODE_CAS else round(target_mach, 3)
    return FmcSpeedTarget(
        cost_index=int(cost_index),
        speed_mode=speed_mode,
        target_cas_kt=round(target_cas_kt, 1),
        target_mach=round(target_mach, 3),
        primary_value=primary_value,
        lrc_anchor_cas_kt=round(zero_wind_lrc_cas, 1),
        lrc_anchor_mach=round(zero_wind_lrc_mach, 3),
        mrc_estimated_cas_kt=round(zero_wind_mrc_cas, 1),
        mrc_estimated_mach=round(zero_wind_mrc_mach, 3),
        wind_adjustment_kt=round(mach_to_cas(zero_wind_target_mach + wind_adjustment_mach + isa_adjustment_mach, altitude_ft) - zero_wind_target_cas, 1),
        source=source,
        warnings=warnings,
    )


def _airbus_family_cfg(aircraft_cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    mapping = _mapping_or_empty(aircraft_cfg.get("cost_index_mapping"))
    return _mapping_or_empty(mapping.get("airbus_family_ci"))


def _speed_mode_for_altitude(
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
) -> str:
    crossover_fl = _speed_mode_crossover_fl(aircraft_cfg, _airbus_family_cfg(aircraft_cfg))
    return SPEED_MODE_CAS if altitude_ft / 100.0 < crossover_fl else SPEED_MODE_MACH


def _speed_mode_crossover_fl(
    aircraft_cfg: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> float:
    raw = cfg.get("cas_mach_crossover_fl")
    if raw is not None:
        return float(raw)
    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    raw = cruise.get("cas_mach_crossover_fl")
    if raw is not None:
        return float(raw)
    return 300.0


def _estimate_lrc_anchor_mach(
    *,
    gross_weight_kg: float,
    altitude_ft: float,
    isa_deviation_c: float,
    aircraft_cfg: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> float:
    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    base_mach = _float_or_default(
        cfg.get("reference_lrc_mach"),
        _float_or_default(cruise.get("reference_mach"), 0.82),
    )

    reference_fl = _float_or_default(cfg.get("reference_flight_level"), 350.0)
    reference_weight_kg = _float_or_default(
        cfg.get("reference_gross_weight_kg"),
        gross_weight_kg,
    )

    altitude_curve = _interpolate_curve(
        cfg.get("lrc_mach_delta_by_flight_level"),
        x=altitude_ft / 100.0,
        x_keys=("flight_level", "fl", "x"),
        default=0.0,
    )
    reference_altitude_curve = _interpolate_curve(
        cfg.get("lrc_mach_delta_by_flight_level"),
        x=reference_fl,
        x_keys=("flight_level", "fl", "x"),
        default=0.0,
    )

    weight_curve = _interpolate_curve(
        cfg.get("lrc_mach_delta_by_weight_kg"),
        x=gross_weight_kg,
        x_keys=("weight_kg", "gross_weight_kg", "x"),
        default=0.0,
    )
    reference_weight_curve = _interpolate_curve(
        cfg.get("lrc_mach_delta_by_weight_kg"),
        x=reference_weight_kg,
        x_keys=("weight_kg", "gross_weight_kg", "x"),
        default=0.0,
    )

    isa_adjustment = _isa_reference_adjustment_mach(
        isa_deviation_c=isa_deviation_c,
        cfg=cfg,
    )

    lrc_mach = base_mach + (altitude_curve - reference_altitude_curve) + (weight_curve - reference_weight_curve) + isa_adjustment
    min_mach = _float_or_default(cfg.get("minimum_lrc_mach"), 0.68)
    max_mach = _estimate_econ_max_mach(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
        cfg=cfg,
    ) - _float_or_default(cfg.get("lrc_to_envelope_margin_mach"), 0.01)
    return max(min_mach, min(lrc_mach, max_mach))


def _estimate_mrc_cas_kt(
    *,
    lrc_anchor_cas_kt: float,
    gross_weight_kg: float,
    altitude_ft: float,
    cfg: Mapping[str, Any],
) -> float:
    altitude_offset = _interpolate_curve(
        cfg.get("mrc_cas_offset_by_altitude_ft"),
        x=altitude_ft,
        x_keys=("altitude_ft", "altitude", "x"),
        default=12.0,
    )
    weight_offset = _interpolate_curve(
        cfg.get("mrc_cas_offset_by_weight_kg"),
        x=gross_weight_kg,
        x_keys=("weight_kg", "gross_weight_kg", "mass_kg", "x"),
        default=0.0,
    )
    min_cas_kt = _float_or_default(cfg.get("mrc_min_cas_kt"), 240.0)
    total_offset = max(0.0, altitude_offset + weight_offset)
    return max(lrc_anchor_cas_kt - total_offset, min_cas_kt)


def _estimate_econ_max_mach(
    *,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> float:
    envelope = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("speed_envelope"))
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)
    max_allowed = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)
    econ_margin = _float_or_default(cfg.get("econ_max_mmo_margin"), 0.02)
    return max(0.0, min(max_allowed, mmo - econ_margin))


def _cost_index_to_zero_wind_target_mach(
    *,
    cost_index: int,
    lrc_anchor_ci: int,
    max_ci: int,
    soft_anchor_ci: int,
    low_ci_shape: float,
    high_ci_shape: float,
    high_ci_soft_fraction: float,
    mrc_mach: float,
    lrc_mach: float,
    max_mach: float,
) -> float:
    if cost_index <= 0:
        return mrc_mach

    if cost_index <= lrc_anchor_ci:
        ratio = _normalized_power_ratio(
            numerator=float(cost_index),
            denominator=float(max(lrc_anchor_ci, 1)),
            exponent=low_ci_shape,
        )
        return _lerp(mrc_mach, lrc_mach, ratio)

    if cost_index <= soft_anchor_ci:
        span = max(soft_anchor_ci - lrc_anchor_ci, 1)
        ratio = _normalized_power_ratio(
            numerator=float(cost_index - lrc_anchor_ci),
            denominator=float(span),
            exponent=high_ci_shape,
        )
        near_limit_mach = lrc_mach + (max_mach - lrc_mach) * high_ci_soft_fraction
        return _lerp(lrc_mach, near_limit_mach, ratio)

    tail_span = max(max_ci - soft_anchor_ci, 1)
    ratio = _normalized_power_ratio(
        numerator=float(cost_index - soft_anchor_ci),
        denominator=float(tail_span),
        exponent=1.2,
    )
    near_limit_mach = lrc_mach + (max_mach - lrc_mach) * high_ci_soft_fraction
    return _lerp(near_limit_mach, max_mach, ratio)


def _wind_adjustment_mach(
    *,
    wind_component_kt: float,
    cfg: Mapping[str, Any],
) -> float:
    headwind_per_50 = _float_or_default(cfg.get("headwind_mach_per_50kt"), 0.005)
    tailwind_per_50 = _float_or_default(cfg.get("tailwind_mach_per_50kt"), 0.005)
    max_headwind = _float_or_default(cfg.get("max_headwind_mach_adjustment"), 0.015)
    max_tailwind = _float_or_default(cfg.get("max_tailwind_mach_adjustment"), 0.015)

    if wind_component_kt < 0:
        return min(abs(wind_component_kt) / 50.0 * headwind_per_50, max_headwind)
    if wind_component_kt > 0:
        return -min(abs(wind_component_kt) / 50.0 * tailwind_per_50, max_tailwind)
    return 0.0


def _isa_adjustment_mach(
    *,
    isa_deviation_c: float,
    cfg: Mapping[str, Any],
) -> float:
    per_c = _float_or_default(cfg.get("isa_adjustment_mach_per_c"), 0.0)
    max_adjustment = _float_or_default(cfg.get("max_isa_mach_adjustment"), 0.005)
    adjustment = isa_deviation_c * per_c
    return max(min(adjustment, max_adjustment), -max_adjustment)


def _isa_reference_adjustment_mach(
    *,
    isa_deviation_c: float,
    cfg: Mapping[str, Any],
) -> float:
    per_c = _float_or_default(cfg.get("isa_reference_mach_per_c"), 0.0)
    max_adjustment = _float_or_default(cfg.get("max_isa_reference_mach_adjustment"), 0.005)
    adjustment = isa_deviation_c * per_c
    return max(min(adjustment, max_adjustment), -max_adjustment)


def _enforce_speed_envelope(
    *,
    target_cas_kt: float,
    target_mach: float,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> tuple[float, float, list[str]]:
    warnings: list[str] = []
    envelope = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("speed_envelope"))
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)
    max_allowed = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)
    econ_cap = min(max_allowed, mmo - _float_or_default(cfg.get("econ_max_mmo_margin"), 0.02))

    adjusted_mach = min(target_mach, econ_cap)
    if adjusted_mach < target_mach - 1e-9:
        warnings.append(
            f"Target speed was clamped to the Airbus ECON envelope at M{adjusted_mach:.3f}."
        )

    adjusted_cas = mach_to_cas(adjusted_mach, altitude_ft)
    if adjusted_cas > vmo_kt + 1e-9:
        adjusted_cas = vmo_kt
        adjusted_mach = cas_to_mach(adjusted_cas, altitude_ft)
        warnings.append(f"CAS target was clamped to VMO {vmo_kt:.0f} KT.")

    return adjusted_cas, adjusted_mach, warnings


def _interpolate_curve(
    raw_curve: object,
    *,
    x: float,
    x_keys: tuple[str, ...],
    default: float,
) -> float:
    points = _parse_curve_points(raw_curve, x_keys=x_keys)
    if not points:
        return default

    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]

    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return _lerp(y0, y1, (x - x0) / max(x1 - x0, 1e-9))

    return default


def _parse_curve_points(
    raw_curve: object,
    *,
    x_keys: tuple[str, ...],
) -> list[tuple[float, float]]:
    if not isinstance(raw_curve, Sequence) or isinstance(raw_curve, (str, bytes)):
        return []

    points: list[tuple[float, float]] = []
    for raw_point in raw_curve:
        if not isinstance(raw_point, Mapping):
            continue

        x_value = None
        for key in x_keys:
            if raw_point.get(key) is not None:
                x_value = raw_point.get(key)
                break
        y_value = raw_point.get("delta_mach")
        if y_value is None:
            y_value = raw_point.get("offset_kt") or raw_point.get("value") or raw_point.get("y")

        x_float = _float_or_none(x_value)
        y_float = _float_or_none(y_value)
        if x_float is None or y_float is None:
            continue

        points.append((x_float, y_float))

    points.sort(key=lambda item: item[0])
    return points


def _normalized_power_ratio(*, numerator: float, denominator: float, exponent: float) -> float:
    if denominator <= 1e-9:
        return 0.0
    ratio = max(0.0, min(numerator / denominator, 1.0))
    return ratio ** max(exponent, 1e-6)


def _lerp(left: float, right: float, ratio: float) -> float:
    return left + (right - left) * ratio


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_default(value: object, default: float) -> float:
    parsed = _float_or_none(value)
    return default if parsed is None else parsed


def _extend_unique(target: list[str], items: list[str]) -> None:
    for item in items:
        text = str(item).strip()
        if text and text not in target:
            target.append(text)
