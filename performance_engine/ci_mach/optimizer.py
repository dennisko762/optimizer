from __future__ import annotations

from dataclasses import dataclass

from performance_engine.ci_mach.aero_model import cruise_drag
from performance_engine.ci_mach.atmosphere import atmosphere_at
from performance_engine.ci_mach.calibration import load_aircraft_ci_mach_config
from performance_engine.ci_mach.constraints import resolve_mach_bounds
from performance_engine.ci_mach.cost_model import CostIndexConverter
from performance_engine.ci_mach.fuel_model import InternalCalibratedFuelModel
from performance_engine.ci_mach.models import (
    AircraftCiMachConfig,
    CiMachCandidate,
    CiMachRequest,
    CiMachResult,
    ProfileUpdateRecommendation,
)
from performance_engine.ci_mach.units import kt_to_mps

MIN_GROUND_SPEED_KT = 120.0
DEFAULT_GRID_STEP_MACH = 0.001


@dataclass(frozen=True)
class _EvaluationContext:
    aircraft_config: AircraftCiMachConfig
    request: CiMachRequest
    ci_kg_per_min: float
    floor_mach: float | None
    live_fuel_flow_scale_factor: float | None


def optimize_ci_mach(
    request: CiMachRequest,
    *,
    aircraft_config: AircraftCiMachConfig | None = None,
    grid_step_mach: float = DEFAULT_GRID_STEP_MACH,
    include_candidates: bool = True,
) -> CiMachResult:
    cfg = aircraft_config or load_aircraft_ci_mach_config(request.aircraft_variant)
    ci_kg_per_min = CostIndexConverter(cfg).to_kg_per_min(request.cost_index, request.cost_index_unit)
    floor_mach = _tailwind_mrc_floor(request=request, aircraft_config=cfg) if request.wind_component_kt > 0 else None
    live_fuel_flow_scale_factor = _resolve_live_fuel_flow_scale_factor(
        request=request,
        aircraft_config=cfg,
    )
    if request.require_live_fuel_flow and live_fuel_flow_scale_factor is None:
        raise ValueError(
            "Live SimConnect fuel flow is required. Refusing to use a static or purely modeled fuel-flow value."
        )
    ctx = _EvaluationContext(
        aircraft_config=cfg,
        request=request,
        ci_kg_per_min=ci_kg_per_min,
        floor_mach=floor_mach,
        live_fuel_flow_scale_factor=live_fuel_flow_scale_factor,
    )

    lower, upper, bound_constraints = resolve_mach_bounds(
        aircraft_config=cfg,
        constraints=request.constraints,
    )
    if floor_mach is not None:
        lower = max(lower, floor_mach)
        bound_constraints.append("tailwind_mrc_floor")

    candidates = [_evaluate_candidate(mach, ctx) for mach in _mach_grid(lower, upper, grid_step_mach)]
    best = min(candidates, key=lambda item: item.total_cost_equiv_kg_per_nm)
    active_constraints = sorted(set(bound_constraints + best.active_constraints))
    notes = list(cfg.source_notes) + list(cfg.cost_index_calibration.notes)
    explanation = (
        f"Selected M{best.mach:.3f} by minimizing fuel kg/NM plus CI time-cost equivalent "
        f"({ci_kg_per_min:.3f} kg/min) over {cfg.aircraft_variant}; this is a calibrated "
        "approximation, not an exact Boeing FMC performance-database result."
    )
    if live_fuel_flow_scale_factor is not None:
        explanation += f" Live fuel-flow anchor scale factor: {live_fuel_flow_scale_factor:.3f}."
    return CiMachResult(
        recommended_mach=round(best.mach, 3),
        recommended_tas_kt=round(best.tas_kt, 1),
        ground_speed_kt=round(best.ground_speed_kt, 1),
        fuel_flow_kg_h=round(best.fuel_flow_kg_h, 1),
        fuel_kg_per_nm=round(best.fuel_kg_per_nm, 4),
        time_min_per_nm=round(best.time_min_per_nm, 5),
        total_cost_equiv_kg_per_nm=round(best.total_cost_equiv_kg_per_nm, 4),
        ci_kg_per_min=round(ci_kg_per_min, 5),
        active_constraints=active_constraints,
        confidence=cfg.confidence,
        explanation=explanation,
        calibration_source_notes=notes,
        candidates=candidates if include_candidates else [],
    )


def recommend_profile_update(
    current_state: CiMachRequest,
    target_arrival_time: object | None,
    remaining_route: object | None,
    weather_forecast: object | None,
    current_ci: float,
    allowed_speed_constraints: object | None = None,
) -> ProfileUpdateRecommendation:
    del target_arrival_time, remaining_route, weather_forecast, allowed_speed_constraints
    base = optimize_ci_mach(current_state, include_candidates=False)
    faster_request = current_state.model_copy(update={"cost_index": max(current_ci, 180.0)})
    faster = optimize_ci_mach(faster_request, include_candidates=False)
    distance_nm = current_state.remaining_distance_nm
    time_saving_min = max(0.0, (base.time_min_per_nm - faster.time_min_per_nm) * distance_nm)
    fuel_penalty_kg = max(0.0, (faster.fuel_kg_per_nm - base.fuel_kg_per_nm) * distance_nm)
    return ProfileUpdateRecommendation(
        recommended_ci_range=(min(current_ci, 180.0), max(current_ci, 180.0)),
        recommended_mach_range=(min(base.recommended_mach, faster.recommended_mach), max(base.recommended_mach, faster.recommended_mach)),
        recommended_cas_range_kt=None,
        expected_fuel_penalty_kg=round(fuel_penalty_kg, 1),
        expected_time_saving_min=round(time_saving_min, 1),
        most_effective_phase="cruise",
        confidence="low",
        explanation=(
            "Future-facing profile-update advisor stub based on Mori-style CI/speed range thinking; "
            "climb and descent phase models are not calibrated yet."
        ),
    )


def _evaluate_candidate(mach: float, ctx: _EvaluationContext) -> CiMachCandidate:
    request = ctx.request
    cfg = ctx.aircraft_config
    altitude_ft = request.altitude_ft
    atm = atmosphere_at(
        altitude_ft,
        outside_air_temperature_c=request.outside_air_temperature_c,
        isa_deviation_c=request.isa_deviation_c,
    )
    tas_kt = mach * atm.speed_of_sound_kt
    gs_kt = tas_kt + request.wind_component_kt
    active_constraints: list[str] = []
    if gs_kt < MIN_GROUND_SPEED_KT:
        active_constraints.append("minimum_ground_speed")
        gs_kt = MIN_GROUND_SPEED_KT
    tas_mps = kt_to_mps(tas_kt)
    aero = cruise_drag(
        mach=mach,
        tas_mps=tas_mps,
        gross_weight_kg=request.gross_weight_kg,
        atmosphere=atm,
        config=cfg.drag_polar,
    )
    fuel = InternalCalibratedFuelModel(cfg.fuel_model).fuel_flow(
        required_thrust_n=aero.drag_n,
        mach=mach,
        atmosphere=atm,
        isa_deviation_c=request.isa_deviation_c,
    )
    fuel_flow_kg_h = fuel.fuel_flow_kg_h
    if ctx.live_fuel_flow_scale_factor is not None:
        fuel_flow_kg_h *= ctx.live_fuel_flow_scale_factor
    fuel_kg_per_nm = fuel_flow_kg_h / gs_kt
    time_min_per_nm = 60.0 / gs_kt
    time_cost = ctx.ci_kg_per_min * time_min_per_nm
    return CiMachCandidate(
        mach=round(mach, 4),
        tas_kt=tas_kt,
        ground_speed_kt=gs_kt,
        drag_n=aero.drag_n,
        fuel_flow_kg_h=fuel_flow_kg_h,
        fuel_kg_per_nm=fuel_kg_per_nm,
        time_min_per_nm=time_min_per_nm,
        time_cost_equiv_kg_per_nm=time_cost,
        total_cost_equiv_kg_per_nm=fuel_kg_per_nm + time_cost,
        active_constraints=active_constraints,
    )


def _tailwind_mrc_floor(
    *,
    request: CiMachRequest,
    aircraft_config: AircraftCiMachConfig,
) -> float:
    zero_wind_request = request.model_copy(update={"cost_index": 0.0, "wind_component_kt": 0.0})
    result = optimize_ci_mach(
        zero_wind_request,
        aircraft_config=aircraft_config,
        include_candidates=False,
    )
    return result.recommended_mach


def _resolve_live_fuel_flow_scale_factor(
    *,
    request: CiMachRequest,
    aircraft_config: AircraftCiMachConfig,
) -> float | None:
    if request.live_fuel_flow_kg_h is None or request.live_fuel_flow_kg_h <= 0:
        return None

    anchor_request = request.model_copy(
        update={
            "cost_index": 0.0,
            "live_fuel_flow_kg_h": None,
            "pressure_altitude_ft": request.fuel_flow_reference_altitude_ft
            if request.fuel_flow_reference_altitude_ft is not None
            else request.pressure_altitude_ft,
            "flight_level": None
            if request.fuel_flow_reference_altitude_ft is not None
            else request.flight_level,
            "gross_weight_kg": request.fuel_flow_reference_gross_weight_kg
            if request.fuel_flow_reference_gross_weight_kg is not None
            else request.gross_weight_kg,
            "isa_deviation_c": request.fuel_flow_reference_isa_deviation_c
            if request.fuel_flow_reference_isa_deviation_c is not None
            else request.isa_deviation_c,
        }
    )
    reference_mach = request.fuel_flow_reference_mach
    if reference_mach is None:
        reference_mach = request.mach if request.mach > 0 else None
    if reference_mach is None:
        return None

    anchor_ctx = _EvaluationContext(
        aircraft_config=aircraft_config,
        request=anchor_request,
        ci_kg_per_min=0.0,
        floor_mach=None,
        live_fuel_flow_scale_factor=None,
    )
    modeled_anchor = _evaluate_candidate(reference_mach, anchor_ctx)
    if modeled_anchor.fuel_flow_kg_h <= 0:
        return None

    scale = request.live_fuel_flow_kg_h / modeled_anchor.fuel_flow_kg_h
    if scale < 0.35 or scale > 2.5:
        return None
    return scale


def _mach_grid(lower: float, upper: float, step: float) -> list[float]:
    values: list[float] = []
    current = lower
    while current <= upper + 1e-9:
        values.append(round(current, 4))
        current += step
    return values
