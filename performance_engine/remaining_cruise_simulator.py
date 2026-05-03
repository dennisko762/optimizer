from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from openap import FuelFlow, aero

from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_model import BoeingFcomPerformance
from performance_engine.database.boeing_fcom_registry import (
    get_boeing_fcom_performance,
    resolve_boeing_777_fcom_variant,
)


@dataclass(frozen=True)
class CruiseSegment:
    """
    Generic cruise segment used by the remaining-cruise integrator.

    All fields are aircraft-agnostic. Aircraft/addon-specific behavior belongs in
    the aircraft YAML profile, not in this simulator.

    distance_nm:
        Segment ground distance.
    altitude_ft:
        Segment cruise altitude. If None, RemainingCruiseInput.altitude_ft is used.
    wind_component_kt:
        Tailwind positive, headwind negative. If None, the request fallback is used.
    isa_deviation_c:
        ISA deviation for the segment. If None, the request fallback is used.
    """

    distance_nm: float
    altitude_ft: float | None = None
    wind_component_kt: float | None = None
    isa_deviation_c: float | None = None


@dataclass
class RemainingCruiseInput:
    """
    Generic remaining cruise simulation input.

    Backwards compatible with the old single-segment API while allowing future
    SimBrief/NAVLOG segment profiles. Keep the code generic; the aircraft YAML
    decides which OpenAP type, limits, calibration factors and speed schedules
    are used.
    """

    aircraft: str
    altitude_ft: float
    gross_weight_kg: float
    mach: float
    remaining_distance_nm: float
    wind_component_kt: float
    isa_deviation_c: float = 0.0
    segment_distance_nm: float = 50.0
    engine_variant: str | None = None

    # Optional new API: when provided, these replace the legacy single average
    # distance/wind/temp/altitude assumptions.
    segments: Sequence[CruiseSegment] | None = None


@dataclass
class RemainingCruiseResult:
    aircraft: str
    altitude_ft: float
    mach: float

    initial_weight_kg: float
    end_weight_kg: float

    remaining_distance_nm: float
    remaining_time_min: float
    remaining_fuel_kg: float

    tas_kt: float
    ground_speed_kt: float

    avg_fuel_flow_kg_h: float
    fuel_per_nm_kg: float
    fuel_per_min_kg: float

    # New metadata. Not required by the existing API response, but useful for UI
    # warnings, debugging, and later model-confidence display.
    performance_model: str = "openap"
    source_aircraft: str | None = None
    segment_count: int = 1
    warnings: list[str] = field(default_factory=list)


def simulate_remaining_cruise(
    request: RemainingCruiseInput,
    *,
    aircraft_cfg: Mapping[str, Any] | None = None,
    general_cfg: Mapping[str, Any] | None = None,
) -> RemainingCruiseResult:
    """
    Generic remaining cruise simulation.

    What this function does:
    - integrates level cruise fuel burn segment by segment
    - supports constant-average input or a segment profile
    - uses OpenAP as a generic physics baseline
    - applies generic aircraft YAML calibration factors/curves
    - updates aircraft mass after every segment

    What this function intentionally does NOT do:
    - hard-code B777/A320/A350-specific logic
    - infer real FMC ECON speed tables from CI
    - perform full climb/descent/step-climb optimization

    Aircraft-specific behavior must come from YAML, for example:
    performance.source.openap_aircraft
    performance.cruise.normal_min_mach
    performance.calibration.fuel_flow_multiplier
    performance.calibration.fuel_flow_factor_by_mach
    """

    _validate_request(request)

    warnings: list[str] = []
    profile = aircraft_cfg or {}
    openap_aircraft = _resolve_openap_aircraft(request=request, aircraft_cfg=profile)
    performance_model = _resolve_performance_model(aircraft_cfg=profile)
    fcom_performance, fcom_variant = _resolve_boeing_fcom_context(
        request=request,
        aircraft_cfg=profile,
        warnings=warnings,
    )
    if fcom_performance is None and performance_model.startswith("boeing_fcom_hybrid"):
        performance_model = performance_model.replace(
            "boeing_fcom_hybrid",
            "openap_fallback_from_boeing_fcom_hybrid",
            1,
        )

    _append_profile_warnings(
        warnings=warnings,
        request=request,
        aircraft_cfg=profile,
        openap_aircraft=openap_aircraft,
    )

    segments = _resolve_segments(request)
    if len(segments) == 1 and request.segments is None:
        warnings.append(
            "Remaining cruise uses one average segment. Provide NAVLOG/profile segments "
            "for more realistic wind, temperature and altitude effects."
        )

    fuel_flow_model = _build_fuel_flow_model(
        openap_aircraft=openap_aircraft,
        warnings=warnings,
    )

    distance_total_nm = 0.0
    distance_left_weighted_tas = 0.0
    distance_left_weighted_gs = 0.0
    current_weight_kg = request.gross_weight_kg
    total_time_h = 0.0
    total_fuel_kg = 0.0

    for segment in segments:
        seg_distance_nm = float(segment.distance_nm)
        seg_altitude_ft = _coalesce_float(segment.altitude_ft, request.altitude_ft)
        seg_wind_component_kt = _coalesce_float(
            segment.wind_component_kt,
            request.wind_component_kt,
        )
        seg_isa_deviation_c = _coalesce_float(
            segment.isa_deviation_c,
            request.isa_deviation_c,
        )

        altitude_m = seg_altitude_ft * aero.ft
        tas_m_s = aero.mach2tas(
            request.mach,
            altitude_m,
            dT=seg_isa_deviation_c,
        )
        tas_kt = tas_m_s / aero.kts
        ground_speed_kt = tas_kt + seg_wind_component_kt

        if ground_speed_kt <= 0:
            raise ValueError(
                "Ground speed must be greater than zero. "
                f"mach={request.mach:.3f}, tas={tas_kt:.1f} kt, "
                f"wind_component={seg_wind_component_kt:.1f} kt."
            )

        segment_fuel_kg, segment_time_h = _simulate_distance_in_subsegments(
            fuel_flow_model=fuel_flow_model,
            aircraft_cfg=profile,
            mach=request.mach,
            initial_weight_kg=current_weight_kg,
            altitude_ft=seg_altitude_ft,
            tas_kt=tas_kt,
            ground_speed_kt=ground_speed_kt,
            isa_deviation_c=seg_isa_deviation_c,
            distance_nm=seg_distance_nm,
            subsegment_distance_nm=request.segment_distance_nm,
            fcom_performance=fcom_performance,
            warnings=warnings,
        )

        current_weight_kg -= segment_fuel_kg
        total_time_h += segment_time_h
        total_fuel_kg += segment_fuel_kg
        distance_total_nm += seg_distance_nm
        distance_left_weighted_tas += tas_kt * seg_distance_nm
        distance_left_weighted_gs += ground_speed_kt * seg_distance_nm

    remaining_time_min = total_time_h * 60.0

    if distance_total_nm <= 0:
        raise ValueError("Total segment distance must be greater than zero.")
    if total_time_h <= 0:
        raise ValueError("Total cruise time must be greater than zero.")

    return RemainingCruiseResult(
        aircraft=request.aircraft,
        altitude_ft=round(_distance_weighted_altitude(segments, request.altitude_ft), 2),
        mach=round(request.mach, 3),
        initial_weight_kg=round(request.gross_weight_kg, 2),
        end_weight_kg=round(current_weight_kg, 2),
        remaining_distance_nm=round(distance_total_nm, 2),
        remaining_time_min=round(remaining_time_min, 2),
        remaining_fuel_kg=round(total_fuel_kg, 2),
        tas_kt=round(distance_left_weighted_tas / distance_total_nm, 2),
        ground_speed_kt=round(distance_left_weighted_gs / distance_total_nm, 2),
        avg_fuel_flow_kg_h=round(total_fuel_kg / total_time_h, 2),
        fuel_per_nm_kg=round(total_fuel_kg / distance_total_nm, 2),
        fuel_per_min_kg=round(total_fuel_kg / remaining_time_min, 2),
        performance_model=performance_model,
        source_aircraft=fcom_variant or openap_aircraft,
        segment_count=len(segments),
        warnings=warnings,
    )


def _simulate_distance_in_subsegments(
    *,
    fuel_flow_model: FuelFlow,
    aircraft_cfg: Mapping[str, Any],
    mach: float,
    initial_weight_kg: float,
    altitude_ft: float,
    tas_kt: float,
    ground_speed_kt: float,
    isa_deviation_c: float,
    distance_nm: float,
    subsegment_distance_nm: float,
    fcom_performance: BoeingFcomPerformance | None,
    warnings: list[str],
) -> tuple[float, float]:
    distance_left_nm = distance_nm
    current_weight_kg = initial_weight_kg
    total_time_h = 0.0
    total_fuel_kg = 0.0

    while distance_left_nm > 1e-9:
        segment_nm = min(subsegment_distance_nm, distance_left_nm)

        raw_fuel_flow_kg_s = fuel_flow_model.enroute(
            mass=current_weight_kg,
            tas=tas_kt,
            alt=altitude_ft,
            vs=0,
            acc=0,
            dT=isa_deviation_c,
            limit=True,
        )

        raw_fuel_flow_kg_h = float(raw_fuel_flow_kg_s) * 3600.0
        if fcom_performance is not None:
            raw_fuel_flow_kg_h = _compute_hybrid_fcom_fuel_flow_kg_h(
                fuel_flow_model=fuel_flow_model,
                fcom_performance=fcom_performance,
                gross_weight_kg=current_weight_kg,
                altitude_ft=altitude_ft,
                isa_deviation_c=isa_deviation_c,
                target_mach=mach,
                target_tas_kt=tas_kt,
                raw_openap_target_fuel_flow_kg_h=raw_fuel_flow_kg_h,
                warnings=warnings,
            )
        calibrated_fuel_flow_kg_h = _apply_fuel_flow_calibration(
            raw_fuel_flow_kg_h=raw_fuel_flow_kg_h,
            aircraft_cfg=aircraft_cfg,
            mach=mach,
            altitude_ft=altitude_ft,
            gross_weight_kg=current_weight_kg,
        )

        segment_time_h = segment_nm / ground_speed_kt
        segment_fuel_kg = calibrated_fuel_flow_kg_h * segment_time_h

        current_weight_kg -= segment_fuel_kg
        total_time_h += segment_time_h
        total_fuel_kg += segment_fuel_kg
        distance_left_nm -= segment_nm

    return total_fuel_kg, total_time_h


def _compute_hybrid_fcom_fuel_flow_kg_h(
    *,
    fuel_flow_model: FuelFlow,
    fcom_performance: BoeingFcomPerformance,
    gross_weight_kg: float,
    altitude_ft: float,
    isa_deviation_c: float,
    target_mach: float,
    target_tas_kt: float,
    raw_openap_target_fuel_flow_kg_h: float,
    warnings: list[str],
) -> float:
    query = query_lrc_cruise(
        fcom_performance,
        weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
    )

    _append_unique_warnings(warnings, query.notes)
    if query.out_of_envelope:
        _append_unique_warnings(
            warnings,
            [
                f"FCOM lookup for {fcom_performance.aircraft_type}/{fcom_performance.engine} "
                f"is outside the published LRC envelope at {gross_weight_kg:.0f} kg and {altitude_ft:.0f} ft."
            ],
        )

    reference_tas_kt = aero.mach2tas(
        query.mach,
        altitude_ft * aero.ft,
        dT=isa_deviation_c,
    ) / aero.kts

    openap_reference_fuel_flow_kg_h = float(
        fuel_flow_model.enroute(
            mass=gross_weight_kg,
            tas=reference_tas_kt,
            alt=altitude_ft,
            vs=0,
            acc=0,
            dT=isa_deviation_c,
            limit=True,
        )
    ) * 3600.0

    if openap_reference_fuel_flow_kg_h <= 1e-9:
        _append_unique_warnings(
            warnings,
            [
                f"OpenAP reference fuel flow collapsed to zero while scaling the Boeing FCOM anchor "
                f"for {fcom_performance.aircraft_type}/{fcom_performance.engine}; using the raw FCOM LRC value."
            ],
        )
        return query.ff_total_kg_h

    speed_factor = raw_openap_target_fuel_flow_kg_h / openap_reference_fuel_flow_kg_h
    if speed_factor <= 0:
        raise ValueError(
            f"Invalid hybrid fuel-flow factor {speed_factor} for Mach {target_mach:.3f} at "
            f"{gross_weight_kg:.0f} kg / {altitude_ft:.0f} ft."
        )

    if abs(target_tas_kt - reference_tas_kt) < 1e-6:
        return query.ff_total_kg_h

    return query.ff_total_kg_h * speed_factor


def _resolve_boeing_fcom_context(
    *,
    request: RemainingCruiseInput,
    aircraft_cfg: Mapping[str, Any],
    warnings: list[str],
) -> tuple[BoeingFcomPerformance | None, str | None]:
    provider = str(_nested_value(aircraft_cfg, "performance", "source", "primary") or "openap").strip().lower()
    if provider != "boeing_fcom_hybrid":
        return None, None

    variant = resolve_boeing_777_fcom_variant(
        request_aircraft=request.aircraft,
        aircraft_cfg=aircraft_cfg,
        engine_variant=request.engine_variant,
    )
    if variant is None:
        _append_unique_warnings(
            warnings,
            [
                "Aircraft profile requests Boeing FCOM hybrid mode, but no supported 777 engine variant "
                "could be resolved. Falling back to the generic OpenAP profile."
            ],
        )
        return None, None

    try:
        return get_boeing_fcom_performance(variant), variant
    except KeyError:
        _append_unique_warnings(
            warnings,
            [
                f"Boeing FCOM variant '{variant}' is configured but not registered. "
                "Falling back to the generic OpenAP profile."
            ],
        )
        return None, None


def _apply_fuel_flow_calibration(
    *,
    raw_fuel_flow_kg_h: float,
    aircraft_cfg: Mapping[str, Any],
    mach: float,
    altitude_ft: float,
    gross_weight_kg: float,
) -> float:
    """
    Generic calibration layer driven entirely by YAML.

    Supported optional keys under performance.calibration:
      fuel_flow_multiplier: 1.03
      openap_fuel_flow_multiplier: 1.03  # legacy alias
      fuel_flow_factor_by_mach:
        - { mach: 0.82, factor: 0.99 }
        - { mach: 0.84, factor: 1.00 }
        - { mach: 0.86, factor: 1.05 }
      fuel_flow_factor_by_altitude_ft:
        - { altitude_ft: 33000, factor: 1.02 }
        - { altitude_ft: 35000, factor: 1.00 }
      fuel_flow_factor_by_weight_kg:
        - { weight_kg: 220000, factor: 0.98 }
        - { weight_kg: 240000, factor: 1.03 }
      high_mach_penalty:
        enabled: true
        reference_mach: 0.84
        coefficient_per_0_01_mach_sq: 0.005

    If no calibration keys are present, factor = 1.0.
    """

    calibration = _nested_mapping(aircraft_cfg, "performance", "calibration")

    factor = _float_from_mapping(
        calibration,
        "fuel_flow_multiplier",
        default=_float_from_mapping(
            calibration,
            "openap_fuel_flow_multiplier",
            default=1.0,
        ),
    )

    factor *= _factor_from_curve(
        calibration.get("fuel_flow_factor_by_mach"),
        x=mach,
        x_keys=("mach", "x"),
    )
    factor *= _factor_from_curve(
        calibration.get("fuel_flow_factor_by_altitude_ft"),
        x=altitude_ft,
        x_keys=("altitude_ft", "altitude", "x"),
    )
    factor *= _factor_from_curve(
        calibration.get("fuel_flow_factor_by_weight_kg"),
        x=gross_weight_kg,
        x_keys=("weight_kg", "gross_weight_kg", "mass_kg", "x"),
    )
    factor *= _high_mach_penalty_factor(calibration.get("high_mach_penalty"), mach=mach)

    if factor <= 0:
        raise ValueError(f"Invalid fuel-flow calibration factor: {factor}.")

    return raw_fuel_flow_kg_h * factor


def _high_mach_penalty_factor(raw: Any, *, mach: float) -> float:
    if not isinstance(raw, Mapping) or not bool(raw.get("enabled", False)):
        return 1.0

    reference_mach = _to_float(raw.get("reference_mach"), default=None)
    coefficient = _to_float(raw.get("coefficient_per_0_01_mach_sq"), default=0.0)
    only_above_reference = bool(raw.get("only_above_reference", True))

    if reference_mach is None or coefficient <= 0:
        return 1.0

    delta = mach - reference_mach
    if only_above_reference and delta <= 0:
        return 1.0

    return 1.0 + coefficient * ((delta / 0.01) ** 2)


def _factor_from_curve(raw_curve: Any, *, x: float, x_keys: tuple[str, ...]) -> float:
    points = _parse_factor_curve(raw_curve, x_keys=x_keys)
    if not points:
        return 1.0

    points = sorted(points, key=lambda item: item[0])

    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]

    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if abs(x1 - x0) < 1e-9:
                return y0
            ratio = (x - x0) / (x1 - x0)
            return y0 + ratio * (y1 - y0)

    return 1.0


def _parse_factor_curve(raw_curve: Any, *, x_keys: tuple[str, ...]) -> list[tuple[float, float]]:
    if not isinstance(raw_curve, Sequence) or isinstance(raw_curve, (str, bytes)):
        return []

    points: list[tuple[float, float]] = []
    for raw_point in raw_curve:
        if isinstance(raw_point, Mapping):
            x_value = None
            for key in x_keys:
                if key in raw_point:
                    x_value = raw_point[key]
                    break
            factor = raw_point.get("factor")
        elif isinstance(raw_point, Sequence) and not isinstance(raw_point, (str, bytes)) and len(raw_point) >= 2:
            x_value = raw_point[0]
            factor = raw_point[1]
        else:
            continue

        x_float = _to_float(x_value, default=None)
        factor_float = _to_float(factor, default=None)
        if x_float is None or factor_float is None or factor_float <= 0:
            continue
        points.append((x_float, factor_float))

    return points


def _resolve_openap_aircraft(*, request: RemainingCruiseInput, aircraft_cfg: Mapping[str, Any]) -> str:
    candidates = [
        _nested_value(aircraft_cfg, "performance", "source", "openap_aircraft"),
        _nested_value(aircraft_cfg, "performance", "source", "openapAircraft"),
        _nested_value(aircraft_cfg, "openap", "aircraft_type"),
        _nested_value(aircraft_cfg, "openap", "aircraftType"),
        aircraft_cfg.get("simbrief_code") if isinstance(aircraft_cfg, Mapping) else None,
        aircraft_cfg.get("aircraft_type") if isinstance(aircraft_cfg, Mapping) else None,
        request.aircraft,
    ]

    for candidate in candidates:
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip().upper()

    raise ValueError("No OpenAP aircraft type could be resolved.")


def _resolve_performance_model(*, aircraft_cfg: Mapping[str, Any]) -> str:
    provider = _nested_value(aircraft_cfg, "performance", "source", "primary")
    if provider is None:
        provider = _nested_value(aircraft_cfg, "performance_model", "provider")
    if provider is None:
        provider = "openap"

    calibration = _nested_mapping(aircraft_cfg, "performance", "calibration")
    has_calibration = any(
        key in calibration
        for key in (
            "fuel_flow_multiplier",
            "openap_fuel_flow_multiplier",
            "fuel_flow_factor_by_mach",
            "fuel_flow_factor_by_altitude_ft",
            "fuel_flow_factor_by_weight_kg",
            "high_mach_penalty",
        )
    )

    return f"{provider}_calibrated" if has_calibration else str(provider)


def _resolve_segments(request: RemainingCruiseInput) -> list[CruiseSegment]:
    if request.segments:
        segments = [segment for segment in request.segments if segment.distance_nm > 0]
        if not segments:
            raise ValueError("At least one cruise segment must have positive distance.")
        return segments

    return [
        CruiseSegment(
            distance_nm=request.remaining_distance_nm,
            altitude_ft=request.altitude_ft,
            wind_component_kt=request.wind_component_kt,
            isa_deviation_c=request.isa_deviation_c,
        )
    ]


def _build_fuel_flow_model(*, openap_aircraft: str, warnings: list[str]) -> FuelFlow:
    try:
        return FuelFlow(openap_aircraft)
    except ValueError as primary_error:
        try:
            model = FuelFlow(openap_aircraft, use_synonym=True)
        except ValueError:
            raise primary_error

        _append_unique_warnings(
            warnings,
            [
                f"OpenAP aircraft '{openap_aircraft}' required synonym fallback during model lookup."
            ],
        )
        return model


def _append_profile_warnings(
    *,
    warnings: list[str],
    request: RemainingCruiseInput,
    aircraft_cfg: Mapping[str, Any],
    openap_aircraft: str,
) -> None:
    if not aircraft_cfg:
        warnings.append(
            "No aircraft YAML profile was passed to the performance engine; "
            "using request aircraft directly as OpenAP type."
        )
        return

    cruise = _nested_mapping(aircraft_cfg, "performance", "cruise")
    min_mach = _to_float(cruise.get("normal_min_mach"), default=None)
    max_mach = _to_float(cruise.get("normal_max_mach"), default=None)
    recovery_max_mach = _to_float(cruise.get("recovery_max_mach"), default=None)

    if min_mach is not None and request.mach < min_mach:
        warnings.append(
            f"Mach {request.mach:.3f} is below configured normal_min_mach {min_mach:.3f}."
        )

    effective_max = recovery_max_mach if recovery_max_mach is not None else max_mach
    if effective_max is not None and request.mach > effective_max:
        warnings.append(
            f"Mach {request.mach:.3f} is above configured max/recovery Mach {effective_max:.3f}."
        )

    if str(openap_aircraft).upper() != str(request.aircraft).upper():
        warnings.append(
            f"Performance source maps request aircraft {request.aircraft} to OpenAP {openap_aircraft}."
        )

    data_quality = _nested_mapping(aircraft_cfg, "data_quality")
    level = data_quality.get("level")
    if level in {"estimated", "unknown"}:
        warnings.append(f"Aircraft performance profile data quality is '{level}'.")


def _validate_request(request: RemainingCruiseInput) -> None:
    if request.mach <= 0:
        raise ValueError("mach must be greater than zero.")
    if request.gross_weight_kg <= 0:
        raise ValueError("gross_weight_kg must be greater than zero.")
    if request.altitude_ft < 0:
        raise ValueError("altitude_ft must be non-negative.")
    if request.segment_distance_nm <= 0:
        raise ValueError("segment_distance_nm must be greater than zero.")
    if request.remaining_distance_nm <= 0 and not request.segments:
        raise ValueError("remaining_distance_nm must be greater than zero when no segments are provided.")


def _distance_weighted_altitude(segments: Sequence[CruiseSegment], fallback_altitude_ft: float) -> float:
    total_distance = sum(max(segment.distance_nm, 0.0) for segment in segments)
    if total_distance <= 0:
        return fallback_altitude_ft
    return sum(
        (segment.altitude_ft if segment.altitude_ft is not None else fallback_altitude_ft) * segment.distance_nm
        for segment in segments
    ) / total_distance


def _nested_mapping(mapping: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    value = _nested_value(mapping, *keys)
    return value if isinstance(value, Mapping) else {}


def _nested_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _float_from_mapping(mapping: Mapping[str, Any], key: str, *, default: float) -> float:
    return _to_float(mapping.get(key), default=default) or default


def _to_float(value: Any, *, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coalesce_float(value: float | None, fallback: float) -> float:
    return fallback if value is None else float(value)


def _append_unique_warnings(warnings: list[str], new_items: Sequence[str]) -> None:
    for item in new_items:
        text = str(item).strip()
        if text and text not in warnings:
            warnings.append(text)
