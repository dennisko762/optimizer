"""Forecast uncertainty engine: P10/P50/P90 wind perturbation scenarios."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, TYPE_CHECKING

from performance_engine.remaining_cruise_simulator import (
    CruiseSegment,
    RemainingCruiseResult,
)

if TYPE_CHECKING:
    from optimizer.wind.gefs_ensemble import EnsembleProfile


@dataclass
class WindScenario:
    """A single wind perturbation scenario."""

    label: str  # "P10_headwind", "P50_forecast", "P90_tailwind"
    percentile: int  # 10, 50, 90
    wind_perturbation_kt: float  # additive to each segment's wind_component_kt
    segments: list[CruiseSegment] = field(default_factory=list)
    cruise_result: RemainingCruiseResult | None = None  # filled after simulation


@dataclass
class UncertaintyResult:
    """Aggregated result of running all uncertainty scenarios."""

    scenarios: list[WindScenario] = field(default_factory=list)
    reserve_at_risk: bool = False
    risk_details: str | None = None  # human-readable explanation


def run_uncertainty_scenarios(
    segments: list[CruiseSegment],
    simulate_fn: Callable[[list[CruiseSegment]], RemainingCruiseResult],
    reserve_fuel_kg: float,
    wind_uncertainty_kt: float = 20.0,
) -> UncertaintyResult:
    """
    Run P10/P50/P90 wind perturbation scenarios through the cruise simulator.

    Parameters
    ----------
    segments : list[CruiseSegment]
        Baseline cruise segments with current wind_component_kt values.
    simulate_fn : Callable
        A function that accepts a list of CruiseSegment and returns a
        RemainingCruiseResult. Typically wraps simulate_remaining_cruise
        with the current aircraft state pre-bound.
    reserve_fuel_kg : float
        Required fuel reserves in kg. Used to assess if the P10 headwind
        scenario threatens reserves.
    wind_uncertainty_kt : float
        Magnitude of wind perturbation in knots. Default 20 kt, representing
        typical SIGWX-level forecast error.

    Returns
    -------
    UncertaintyResult
        Three scenarios (P10/P50/P90) with reserve_at_risk assessment.
    """
    scenario_defs = [
        ("P10_headwind", 10, -wind_uncertainty_kt),
        ("P50_forecast", 50, 0.0),
        ("P90_tailwind", 90, +wind_uncertainty_kt),
    ]

    scenarios: list[WindScenario] = []
    for label, percentile, perturbation in scenario_defs:
        perturbed = _perturb_segments(segments, perturbation)
        result = simulate_fn(perturbed)
        scenarios.append(
            WindScenario(
                label=label,
                percentile=percentile,
                wind_perturbation_kt=perturbation,
                segments=perturbed,
                cruise_result=result,
            )
        )

    # Assess reserve risk from P10 (worst case headwind)
    p10 = scenarios[0]  # P10_headwind
    p50 = scenarios[1]  # P50_forecast

    reserve_at_risk = False
    risk_details: str | None = None

    if p10.cruise_result is not None and p50.cruise_result is not None:
        p10_fuel = p10.cruise_result.remaining_fuel_kg
        p50_fuel = p50.cruise_result.remaining_fuel_kg
        extra_burn = p10_fuel - p50_fuel

        # The caller provides total fuel available minus what's needed for reserves.
        # If P10 burns more than available fuel minus reserves, we're at risk.
        # remaining_fuel_kg in the result is total fuel burned, not remaining.
        # Check if the extra burn from headwinds erodes reserves below minimum.
        available_margin = reserve_fuel_kg  # the reserve itself
        # If the P10 extra burn exceeds the reserve margin, flag risk
        if extra_burn > 0 and extra_burn >= reserve_fuel_kg:
            reserve_at_risk = True
            margin_remaining = max(reserve_fuel_kg - extra_burn, 0)
            risk_details = (
                f"P10 headwind scenario burns {extra_burn:,.0f} kg more than forecast; "
                f"reserve margin reduced to {margin_remaining:,.0f} kg "
                f"(minimum {reserve_fuel_kg:,.0f} kg)"
            )

    return UncertaintyResult(
        scenarios=scenarios,
        reserve_at_risk=reserve_at_risk,
        risk_details=risk_details,
    )


def _perturb_segments(
    segments: list[CruiseSegment],
    perturbation_kt: float,
) -> list[CruiseSegment]:
    """Clone segments with an additive wind perturbation. Never mutates inputs."""
    result: list[CruiseSegment] = []
    for seg in segments:
        base_wind = seg.wind_component_kt if seg.wind_component_kt is not None else 0.0
        result.append(
            CruiseSegment(
                distance_nm=seg.distance_nm,
                altitude_ft=seg.altitude_ft,
                wind_component_kt=base_wind + perturbation_kt,
                isa_deviation_c=seg.isa_deviation_c,
            )
        )
    return result


def run_ensemble_uncertainty_scenarios(
    ensemble: EnsembleProfile,
    segments: list[CruiseSegment],
    simulate_fn: Callable[[list[CruiseSegment]], RemainingCruiseResult],
    reserve_fuel_kg: float,
) -> UncertaintyResult:
    """
    Like run_uncertainty_scenarios but uses actual GEFS ensemble spread.

    Creates 3 scenarios:
    - P5 (5th percentile, worst-case headwind): uses ensemble p5 winds
    - P50 (median forecast): uses ensemble p50 winds
    - P95 (95th percentile, best-case tailwind): uses ensemble p95 winds

    Assesses reserve risk from P5 scenario vs reserve_fuel_kg.

    Parameters
    ----------
    ensemble : EnsembleProfile
        Pre-computed ensemble statistics with per-segment percentiles.
    segments : list[CruiseSegment]
        Baseline cruise segments.
    simulate_fn : Callable
        Cruise simulator function.
    reserve_fuel_kg : float
        Required fuel reserves in kg.

    Returns
    -------
    UncertaintyResult
        Three scenarios (P5/P50/P95) with reserve_at_risk assessment.
    """

    scenario_defs = [
        ("P5_headwind", 5),
        ("P50_forecast", 50),
        ("P95_tailwind", 95),
    ]

    scenarios: list[WindScenario] = []
    for label, percentile in scenario_defs:
        perturbed = _apply_ensemble_percentile(
            segments, ensemble, percentile
        )
        result = simulate_fn(perturbed)
        scenarios.append(
            WindScenario(
                label=label,
                percentile=percentile,
                wind_perturbation_kt=0.0,  # not a fixed perturbation
                segments=perturbed,
                cruise_result=result,
            )
        )

    # Assess reserve risk from P5 (worst case) vs P50
    p5 = scenarios[0]
    p50 = scenarios[1]

    reserve_at_risk = False
    risk_details: str | None = None

    if p5.cruise_result is not None and p50.cruise_result is not None:
        p5_fuel = p5.cruise_result.remaining_fuel_kg
        p50_fuel = p50.cruise_result.remaining_fuel_kg
        extra_burn = p5_fuel - p50_fuel

        if extra_burn > 0 and extra_burn >= reserve_fuel_kg:
            reserve_at_risk = True
            margin_remaining = max(reserve_fuel_kg - extra_burn, 0)
            risk_details = (
                f"P5 headwind scenario burns {extra_burn:,.0f} kg more than forecast; "
                f"reserve margin reduced to {margin_remaining:,.0f} kg "
                f"(minimum {reserve_fuel_kg:,.0f} kg)"
            )

    return UncertaintyResult(
        scenarios=scenarios,
        reserve_at_risk=reserve_at_risk,
        risk_details=risk_details,
    )


def _apply_ensemble_percentile(
    segments: list[CruiseSegment],
    ensemble: EnsembleProfile,
    percentile: int,
) -> list[CruiseSegment]:
    """
    Replace each segment's wind with the given percentile from ensemble stats.

    If ensemble has fewer segment stats than segments, the last available stat
    is reused for remaining segments.
    """
    result: list[CruiseSegment] = []
    n_stats = len(ensemble.segment_stats)

    for i, seg in enumerate(segments):
        if n_stats > 0:
            stat_idx = min(i, n_stats - 1)
            wind_kt = ensemble.segment_stats[stat_idx].percentiles.get(
                percentile, 0.0
            )
        else:
            wind_kt = seg.wind_component_kt if seg.wind_component_kt is not None else 0.0

        result.append(
            CruiseSegment(
                distance_nm=seg.distance_nm,
                altitude_ft=seg.altitude_ft,
                wind_component_kt=wind_kt,
                isa_deviation_c=seg.isa_deviation_c,
            )
        )
    return result
