from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_registry import (
    get_boeing_fcom_performance,
    resolve_boeing_777_fcom_variant,
)
from performance_engine.speed_envelope import cas_to_mach, mach_to_cas, max_mach_at_altitude


OPTIMIZER_MODE_PERFORMANCE_DERIVED = "performance_derived_speed"
OPTIMIZER_MODE_BOEING_777_FMC_LIKE = "boeing_777_fmc_like_ci"
OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE = "airbus_family_fmc_like_ci"
OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE = "empirical_fmc_ci_table"

COST_INDEX_SOURCE_DISPLAY = "display_ci"
COST_INDEX_SOURCE_FMC_LIKE = "fmc_like_ci_estimate"
COST_INDEX_SOURCE_AIRBUS_FAMILY = "airbus_family_fmc_ci_fallback"
COST_INDEX_SOURCE_CALIBRATED = "calibrated_fmc_ci"
COST_INDEX_SOURCE_CURRENT_INPUT = "current_fmc_ci_input"

SPEED_MODE_CAS = "CAS"
SPEED_MODE_MACH = "MACH"


@dataclass(frozen=True)
class FmcSpeedTarget:
    cost_index: int
    speed_mode: str
    target_cas_kt: float | None
    target_mach: float | None
    primary_value: float
    lrc_anchor_cas_kt: float
    lrc_anchor_mach: float
    mrc_estimated_cas_kt: float
    mrc_estimated_mach: float
    wind_adjustment_kt: float
    source: str
    warnings: list[str] = field(default_factory=list)


def resolve_cost_index_optimizer_mode(aircraft_cfg: Mapping[str, Any]) -> str:
    mapping = _mapping_or_empty(aircraft_cfg.get("cost_index_mapping"))
    mode = (
        mapping.get("optimizer_mode")
        or mapping.get("mode")
        or OPTIMIZER_MODE_PERFORMANCE_DERIVED
    )
    value = str(mode).strip()
    if value in {
        OPTIMIZER_MODE_PERFORMANCE_DERIVED,
        OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
        OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
        OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    }:
        return value
    return OPTIMIZER_MODE_PERFORMANCE_DERIVED


def cost_index_source_label(source: str | None) -> str | None:
    if source == COST_INDEX_SOURCE_DISPLAY:
        return "Display CI, not calibrated FMC CI"
    if source == COST_INDEX_SOURCE_FMC_LIKE:
        return "FMC-like CI estimate"
    if source == COST_INDEX_SOURCE_AIRBUS_FAMILY:
        return "Airbus-family FMC CI fallback"
    if source == COST_INDEX_SOURCE_CALIBRATED:
        return "Calibrated FMC CI"
    if source == COST_INDEX_SOURCE_CURRENT_INPUT:
        return "Current FMC CI input"
    return None


def supports_boeing_777_fmc_like_ci(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: Mapping[str, Any],
) -> bool:
    return (
        resolve_boeing_777_fcom_variant(
            request_aircraft=aircraft,
            aircraft_cfg=aircraft_cfg,
            engine_variant=engine_variant,
        )
        is not None
    )


def estimate_fmc_ci_speed_target(
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
    mode = resolve_cost_index_optimizer_mode(aircraft_cfg)
    if mode == OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE:
        empirical = _estimate_empirical_fmc_ci_speed(
            aircraft=aircraft,
            engine_variant=engine_variant,
            gross_weight_kg=gross_weight_kg,
            altitude_ft=altitude_ft,
            cost_index=cost_index,
            wind_component_kt=wind_component_kt,
            isa_deviation_c=isa_deviation_c,
            aircraft_cfg=aircraft_cfg,
            general_cfg=general_cfg,
        )
        if empirical is not None:
            return empirical

    if mode == OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE:
        from performance_engine.airbus_fmc_ci_speed_model import (
            estimate_airbus_family_fmc_econ_speed,
        )

        return estimate_airbus_family_fmc_econ_speed(
            aircraft=aircraft,
            engine_variant=engine_variant,
            gross_weight_kg=gross_weight_kg,
            altitude_ft=altitude_ft,
            cost_index=cost_index,
            wind_component_kt=wind_component_kt,
            isa_deviation_c=isa_deviation_c,
            aircraft_cfg=aircraft_cfg,
            general_cfg=general_cfg,
        )

    return estimate_boeing_777_fmc_econ_speed(
        aircraft=aircraft,
        engine_variant=engine_variant,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        cost_index=cost_index,
        wind_component_kt=wind_component_kt,
        isa_deviation_c=isa_deviation_c,
        aircraft_cfg=aircraft_cfg,
        general_cfg=general_cfg,
    )


def estimate_boeing_777_fmc_econ_speed(
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
    Continental-style Boeing 777 FMC-like ECON speed fallback.

    This is intentionally not an exact Boeing FMC implementation. It anchors on
    the existing Boeing FCOM LRC lookup, then estimates:
      - CI 0 as a maximum-range region below LRC
      - CI 180 approximately at LRC
      - high CI as a saturating move toward the VMO/MMO-limited region

    Wind corrections are applied around the no-wind speed schedule and low/mid
    altitudes remain CAS-primary.
    """

    if gross_weight_kg <= 0:
        raise ValueError("gross_weight_kg must be greater than zero.")
    if altitude_ft <= 0:
        raise ValueError("altitude_ft must be greater than zero.")

    warnings: list[str] = [
        "Boeing 777 FMC-like CI speed model is a Continental-style fallback approximation built from FCOM LRC anchors and generic speed-envelope logic, not proprietary Boeing FMC code."
    ]

    variant = resolve_boeing_777_fcom_variant(
        request_aircraft=aircraft,
        aircraft_cfg=aircraft_cfg,
        engine_variant=engine_variant,
    )
    if variant is None:
        raise ValueError(
            f"No supported Boeing 777 FCOM variant could be resolved for {aircraft}/{engine_variant or 'default'}."
        )

    performance = get_boeing_fcom_performance(variant)
    lrc = query_lrc_cruise(
        performance,
        weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
    )
    _extend_unique(warnings, lrc.notes)

    if lrc.out_of_envelope:
        warnings.append(
            f"FCOM LRC lookup is outside the published envelope at {gross_weight_kg:.0f} kg / {altitude_ft:.0f} ft."
        )

    fmc_cfg = _fmc_like_cfg(aircraft_cfg)
    lrc_anchor_ci = int(fmc_cfg.get("lrc_anchor_ci") or 180)
    max_ci = int(fmc_cfg.get("max_ci") or 9999)
    soft_anchor_ci = int(fmc_cfg.get("high_speed_soft_anchor_ci") or 5000)
    low_ci_shape = _float_or_default(fmc_cfg.get("low_ci_shape"), 0.85)
    high_ci_shape = _float_or_default(fmc_cfg.get("high_ci_shape"), 0.85)
    high_ci_soft_fraction = _float_or_default(fmc_cfg.get("high_ci_soft_fraction"), 0.88)

    lrc_anchor_cas_kt = float(lrc.kias)
    lrc_anchor_mach = float(lrc.mach)

    zero_wind_mrc_cas = _estimate_mrc_cas_kt(
        lrc_anchor_cas_kt=lrc_anchor_cas_kt,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        fmc_cfg=fmc_cfg,
    )
    zero_wind_mrc_mach = cas_to_mach(zero_wind_mrc_cas, altitude_ft)

    zero_wind_max_cas = _estimate_high_speed_limit_cas_kt(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
        fallback_lrc_cas_kt=lrc_anchor_cas_kt,
        warnings=warnings,
    )

    zero_wind_target_cas = _cost_index_to_zero_wind_target_cas(
        cost_index=max(0, min(int(cost_index), max_ci)),
        lrc_anchor_ci=lrc_anchor_ci,
        max_ci=max_ci,
        soft_anchor_ci=soft_anchor_ci,
        low_ci_shape=low_ci_shape,
        high_ci_shape=high_ci_shape,
        high_ci_soft_fraction=high_ci_soft_fraction,
        mrc_cas_kt=zero_wind_mrc_cas,
        lrc_cas_kt=lrc_anchor_cas_kt,
        max_cas_kt=zero_wind_max_cas,
    )

    adjustment_scale = _high_speed_adjustment_scale(
        cost_index=max(0, min(int(cost_index), max_ci)),
        soft_anchor_ci=soft_anchor_ci,
        max_ci=max_ci,
        fmc_cfg=fmc_cfg,
    )

    wind_adjustment_kt = _wind_adjustment_kt(
        wind_component_kt=wind_component_kt,
        fmc_cfg=fmc_cfg,
    ) * adjustment_scale
    isa_adjustment_kt = _isa_adjustment_kt(
        isa_deviation_c=isa_deviation_c,
        fmc_cfg=fmc_cfg,
    ) * adjustment_scale

    target_cas_kt = zero_wind_target_cas + wind_adjustment_kt + isa_adjustment_kt
    target_cas_kt = max(target_cas_kt, zero_wind_mrc_cas)
    if target_cas_kt > zero_wind_max_cas:
        target_cas_kt = zero_wind_max_cas
        warnings.append("Target ECON speed reached the configured/envelope-limited high-speed region.")

    speed_mode = _speed_mode_for_altitude(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
    )
    target_mach = cas_to_mach(target_cas_kt, altitude_ft)
    target_cas_kt, target_mach, envelope_warnings = _enforce_speed_envelope(
        target_cas_kt=target_cas_kt,
        target_mach=target_mach,
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
    )
    _extend_unique(warnings, envelope_warnings)

    if target_cas_kt > zero_wind_max_cas - 0.5 and cost_index >= soft_anchor_ci:
        warnings.append(
            f"CI {cost_index} is in the near-minimum-time region and is approaching speed limits."
        )

    source = "boeing_777_fmc_like_ci"
    if speed_mode == SPEED_MODE_CAS:
        primary_value = round(target_cas_kt, 1)
        target_cas_value = round(target_cas_kt, 1)
        target_mach_value = round(target_mach, 3)
    else:
        primary_value = round(target_mach, 3)
        target_cas_value = round(target_cas_kt, 1)
        target_mach_value = round(target_mach, 3)

    return FmcSpeedTarget(
        cost_index=int(cost_index),
        speed_mode=speed_mode,
        target_cas_kt=target_cas_value,
        target_mach=target_mach_value,
        primary_value=primary_value,
        lrc_anchor_cas_kt=round(lrc_anchor_cas_kt, 1),
        lrc_anchor_mach=round(lrc_anchor_mach, 3),
        mrc_estimated_cas_kt=round(zero_wind_mrc_cas, 1),
        mrc_estimated_mach=round(zero_wind_mrc_mach, 3),
        wind_adjustment_kt=round(wind_adjustment_kt + isa_adjustment_kt, 1),
        source=source,
        warnings=warnings,
    )


def _estimate_empirical_fmc_ci_speed(
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
) -> FmcSpeedTarget | None:
    mapping = _mapping_or_empty(aircraft_cfg.get("cost_index_mapping"))
    rows = mapping.get("empirical_fmc_ci_table")
    anchors = _parse_empirical_ci_rows(rows)
    if not anchors:
        return None

    lower, upper, ratio = _bracket_empirical_ci_rows(
        anchors=anchors,
        cost_index=cost_index,
    )

    base_cas = _interpolate_optional(lower.get("cas_kt"), upper.get("cas_kt"), ratio)
    base_mach = _interpolate_optional(lower.get("mach"), upper.get("mach"), ratio)
    warnings = [
        "Empirical FMC CI table override is active."
    ]

    if base_cas is None and base_mach is None:
        return None

    speed_mode = _speed_mode_for_altitude(
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
    )

    if base_cas is None and base_mach is not None:
        base_cas = mach_to_cas(base_mach, altitude_ft)
    if base_mach is None and base_cas is not None:
        base_mach = cas_to_mach(base_cas, altitude_ft)

    base_cas = float(base_cas or 0.0)
    base_mach = float(base_mach or 0.0)

    wind_adjustment = _wind_adjustment_kt(
        wind_component_kt=wind_component_kt,
        fmc_cfg=_fmc_like_cfg(aircraft_cfg),
    )
    isa_adjustment = _isa_adjustment_kt(
        isa_deviation_c=isa_deviation_c,
        fmc_cfg=_fmc_like_cfg(aircraft_cfg),
    )

    target_cas_kt = max(base_cas + wind_adjustment + isa_adjustment, 0.0)
    target_mach = cas_to_mach(target_cas_kt, altitude_ft)
    target_cas_kt, target_mach, envelope_warnings = _enforce_speed_envelope(
        target_cas_kt=target_cas_kt,
        target_mach=target_mach,
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg,
    )
    _extend_unique(warnings, envelope_warnings)

    return FmcSpeedTarget(
        cost_index=int(cost_index),
        speed_mode=speed_mode,
        target_cas_kt=round(target_cas_kt, 1),
        target_mach=round(target_mach, 3),
        primary_value=round(target_cas_kt, 1) if speed_mode == SPEED_MODE_CAS else round(target_mach, 3),
        lrc_anchor_cas_kt=round(base_cas, 1),
        lrc_anchor_mach=round(base_mach, 3),
        mrc_estimated_cas_kt=round(base_cas, 1),
        mrc_estimated_mach=round(base_mach, 3),
        wind_adjustment_kt=round(wind_adjustment + isa_adjustment, 1),
        source="empirical_fmc_ci_table",
        warnings=warnings,
    )


def _estimate_mrc_cas_kt(
    *,
    lrc_anchor_cas_kt: float,
    gross_weight_kg: float,
    altitude_ft: float,
    fmc_cfg: Mapping[str, Any],
) -> float:
    altitude_offset = _interpolate_curve(
        fmc_cfg.get("mrc_cas_offset_by_altitude_ft"),
        x=altitude_ft,
        x_keys=("altitude_ft", "altitude", "x"),
        default=15.0,
    )
    weight_offset = _interpolate_curve(
        fmc_cfg.get("mrc_cas_offset_by_weight_kg"),
        x=gross_weight_kg,
        x_keys=("weight_kg", "gross_weight_kg", "mass_kg", "x"),
        default=0.0,
    )
    min_cas_kt = _float_or_default(fmc_cfg.get("mrc_min_cas_kt"), 240.0)
    max_offset = _float_or_default(fmc_cfg.get("mrc_max_cas_offset_kt"), 25.0)
    total_offset = max(0.0, min(altitude_offset + weight_offset, max_offset))
    return max(lrc_anchor_cas_kt - total_offset, min_cas_kt)


def _cost_index_to_zero_wind_target_cas(
    *,
    cost_index: int,
    lrc_anchor_ci: int,
    max_ci: int,
    soft_anchor_ci: int,
    low_ci_shape: float,
    high_ci_shape: float,
    high_ci_soft_fraction: float,
    mrc_cas_kt: float,
    lrc_cas_kt: float,
    max_cas_kt: float,
) -> float:
    if cost_index <= 0:
        return mrc_cas_kt

    if cost_index <= lrc_anchor_ci:
        ratio = _normalized_power_ratio(
            numerator=float(cost_index),
            denominator=float(max(lrc_anchor_ci, 1)),
            exponent=low_ci_shape,
        )
        return _lerp(mrc_cas_kt, lrc_cas_kt, ratio)

    if cost_index <= soft_anchor_ci:
        span = max(soft_anchor_ci - lrc_anchor_ci, 1)
        ratio = _normalized_power_ratio(
            numerator=float(cost_index - lrc_anchor_ci),
            denominator=float(span),
            exponent=high_ci_shape,
        )
        return _lerp(
            lrc_cas_kt,
            lrc_cas_kt + (max_cas_kt - lrc_cas_kt) * high_ci_soft_fraction,
            ratio,
        )

    near_limit_base = lrc_cas_kt + (max_cas_kt - lrc_cas_kt) * high_ci_soft_fraction
    tail_span = max(max_ci - soft_anchor_ci, 1)
    ratio = _normalized_power_ratio(
        numerator=float(cost_index - soft_anchor_ci),
        denominator=float(tail_span),
        exponent=1.35,
    )
    return _lerp(near_limit_base, max_cas_kt, ratio)


def _estimate_high_speed_limit_cas_kt(
    *,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
    fallback_lrc_cas_kt: float,
    warnings: list[str],
) -> float:
    envelope = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("speed_envelope"))
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)

    fmc_cfg = _fmc_like_cfg(aircraft_cfg)
    margin_kt = _float_or_default(fmc_cfg.get("max_target_margin_kt"), 5.0)
    margin_mach = _float_or_default(fmc_cfg.get("max_target_margin_mach"), 0.005)

    max_allowed_mach = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)
    high_target_mach = max(min(max_allowed_mach - margin_mach, mmo), 0.0)
    high_target_cas = min(vmo_kt - margin_kt, mach_to_cas(high_target_mach, altitude_ft))

    if high_target_cas <= fallback_lrc_cas_kt:
        warnings.append(
            "Envelope-derived high-speed target was below the LRC anchor; using a small increment above LRC instead."
        )
        return fallback_lrc_cas_kt + 5.0

    return high_target_cas


def _wind_adjustment_kt(
    *,
    wind_component_kt: float,
    fmc_cfg: Mapping[str, Any],
) -> float:
    headwind_per_100 = _float_or_default(fmc_cfg.get("headwind_adjustment_kt_per_100kt"), 6.0)
    tailwind_per_100 = _float_or_default(fmc_cfg.get("tailwind_adjustment_kt_per_100kt"), 4.0)
    max_headwind = _float_or_default(fmc_cfg.get("max_headwind_adjustment_kt"), 12.0)
    max_tailwind = _float_or_default(fmc_cfg.get("max_tailwind_adjustment_kt"), 10.0)

    if wind_component_kt < 0:
        return min(abs(wind_component_kt) * headwind_per_100 / 100.0, max_headwind)
    if wind_component_kt > 0:
        return -min(abs(wind_component_kt) * tailwind_per_100 / 100.0, max_tailwind)
    return 0.0


def _isa_adjustment_kt(
    *,
    isa_deviation_c: float,
    fmc_cfg: Mapping[str, Any],
) -> float:
    per_c = _float_or_default(fmc_cfg.get("isa_adjustment_kt_per_c"), 0.15)
    max_adjustment = _float_or_default(fmc_cfg.get("max_isa_adjustment_kt"), 4.0)
    adjustment = isa_deviation_c * per_c
    return max(min(adjustment, max_adjustment), -max_adjustment)


def _high_speed_adjustment_scale(
    *,
    cost_index: int,
    soft_anchor_ci: int,
    max_ci: int,
    fmc_cfg: Mapping[str, Any],
) -> float:
    fade_enabled = bool(fmc_cfg.get("fade_wind_and_isa_near_max_ci", True))
    if not fade_enabled or max_ci <= soft_anchor_ci:
        return 1.0

    if cost_index <= soft_anchor_ci:
        return 1.0
    if cost_index >= max_ci:
        return 0.0

    remaining = max_ci - cost_index
    span = max_ci - soft_anchor_ci
    return max(0.0, min(remaining / span, 1.0))


def _speed_mode_for_altitude(
    *,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
) -> str:
    crossover_fl = _speed_mode_crossover_fl(aircraft_cfg)
    if altitude_ft / 100.0 < crossover_fl:
        return SPEED_MODE_CAS
    return SPEED_MODE_MACH


def _enforce_speed_envelope(
    *,
    target_cas_kt: float,
    target_mach: float,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, Any],
) -> tuple[float, float, list[str]]:
    warnings: list[str] = []
    envelope = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("speed_envelope"))
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)
    max_allowed_mach = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)

    adjusted_cas = min(target_cas_kt, vmo_kt)
    if adjusted_cas < target_cas_kt - 1e-9:
        warnings.append(f"CAS target was clamped to VMO {vmo_kt:.0f} KT.")

    adjusted_mach = cas_to_mach(adjusted_cas, altitude_ft)
    if adjusted_mach > max_allowed_mach + 1e-9:
        adjusted_mach = max_allowed_mach
        adjusted_cas = mach_to_cas(adjusted_mach, altitude_ft)
        warnings.append(
            f"Target speed was clamped to the VMO/MMO envelope at M{adjusted_mach:.3f}."
        )

    if adjusted_mach > mmo + 1e-9:
        adjusted_mach = mmo
        adjusted_cas = mach_to_cas(adjusted_mach, altitude_ft)
        warnings.append(f"Target speed was clamped to MMO {mmo:.3f}.")

    return adjusted_cas, adjusted_mach, warnings


def _fmc_like_cfg(aircraft_cfg: Mapping[str, Any]) -> Mapping[str, Any]:
    mapping = _mapping_or_empty(aircraft_cfg.get("cost_index_mapping"))
    return _mapping_or_empty(mapping.get("fmc_like_ci"))


def _speed_mode_crossover_fl(aircraft_cfg: Mapping[str, Any]) -> float:
    fmc_cfg = _fmc_like_cfg(aircraft_cfg)
    raw = fmc_cfg.get("cas_mach_crossover_fl")
    if raw is not None:
        return float(raw)

    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    raw = cruise.get("cas_mach_crossover_fl")
    if raw is not None:
        return float(raw)
    return 280.0


def _parse_empirical_ci_rows(raw_rows: object) -> list[dict[str, float]]:
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes)):
        return []

    rows: list[dict[str, float]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            continue

        ci_value = _float_or_none(raw_row.get("ci"))
        if ci_value is None:
            continue

        mach = _float_or_none(raw_row.get("mach"))
        cas_kt = _float_or_none(raw_row.get("cas_kt") or raw_row.get("casKt"))
        if mach is None and cas_kt is None:
            continue

        rows.append(
            {
                "ci": float(ci_value),
                "mach": float(mach) if mach is not None else None,
                "cas_kt": float(cas_kt) if cas_kt is not None else None,
            }
        )

    rows.sort(key=lambda row: row["ci"])
    return rows


def _bracket_empirical_ci_rows(
    *,
    anchors: list[dict[str, float]],
    cost_index: int,
) -> tuple[dict[str, float], dict[str, float], float]:
    ci_value = float(cost_index)
    if ci_value <= anchors[0]["ci"]:
        return anchors[0], anchors[0], 0.0
    if ci_value >= anchors[-1]["ci"]:
        return anchors[-1], anchors[-1], 0.0

    for lower, upper in zip(anchors, anchors[1:]):
        if lower["ci"] <= ci_value <= upper["ci"]:
            span = upper["ci"] - lower["ci"]
            ratio = 0.0 if span <= 1e-9 else (ci_value - lower["ci"]) / span
            return lower, upper, ratio

    return anchors[-1], anchors[-1], 0.0


def _normalized_power_ratio(*, numerator: float, denominator: float, exponent: float) -> float:
    if denominator <= 1e-9:
        return 0.0
    ratio = max(0.0, min(numerator / denominator, 1.0))
    return ratio ** max(exponent, 1e-6)


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
        y_value = raw_point.get("offset_kt") or raw_point.get("value") or raw_point.get("y")

        x_float = _float_or_none(x_value)
        y_float = _float_or_none(y_value)
        if x_float is None or y_float is None:
            continue
        points.append((x_float, y_float))

    points.sort(key=lambda item: item[0])
    return points


def _interpolate_optional(left: float | None, right: float | None, ratio: float) -> float | None:
    if left is None and right is None:
        return None
    if left is None:
        return right
    if right is None:
        return left
    return _lerp(left, right, ratio)


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


def _extend_unique(target: list[str], items: Sequence[str]) -> None:
    for item in items:
        text = str(item).strip()
        if text and text not in target:
            target.append(text)
