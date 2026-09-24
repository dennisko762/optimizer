"""Tests for GEFS ensemble wind ingestion and per-segment percentile statistics."""

from __future__ import annotations

import pytest

from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.route_profile_models import RemainingRouteSegment
from optimizer.wind.wind_profile import WindLayer, WaypointWind, WindProfile
from optimizer.wind.wind_interpolator import interpolate_wind_components
from optimizer.wind.gefs_ensemble import (
    EnsembleProfile,
    EnsembleWindStats,
    compute_ensemble_stats,
    is_forecast_stale,
)
from optimizer.wind.uncertainty import run_ensemble_uncertainty_scenarios
from performance_engine.remaining_cruise_simulator import (
    CruiseSegment,
    RemainingCruiseInput,
    RemainingCruiseResult,
    simulate_remaining_cruise,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_route_segments(
    count: int, distance_nm: float = 200.0, wind_kt: float = 0.0
) -> list[RemainingRouteSegment]:
    """Create synthetic RemainingRouteSegment list."""
    return [
        RemainingRouteSegment(
            distanceNm=distance_nm,
            altitudeFt=37000.0,
            windComponentKt=wind_kt,
            isaDeviationC=0.0,
        )
        for _ in range(count)
    ]


def _make_cruise_segments(
    count: int, distance_nm: float = 200.0, wind_kt: float = 0.0
) -> list[CruiseSegment]:
    """Create synthetic cruise segments."""
    return [
        CruiseSegment(
            distance_nm=distance_nm,
            altitude_ft=37000.0,
            wind_component_kt=wind_kt,
            isa_deviation_c=0.0,
        )
        for _ in range(count)
    ]


def _make_wind_profile(wind_speed_kt: float, wind_dir: float = 270.0) -> WindProfile:
    """Create a single-waypoint WindProfile with uniform wind."""
    return WindProfile(
        source="gefs_member",
        waypoints=[
            WaypointWind(
                cumulative_distance_nm=0.0,
                layers=[
                    WindLayer(
                        altitude_ft=37000.0,
                        wind_direction_deg=wind_dir,
                        wind_speed_kt=wind_speed_kt,
                    )
                ],
            ),
        ],
    )


def _make_simulate_fn():
    """Create a simulate_fn wrapping the real cruise simulator with B77W config."""
    aircraft_cfg = load_aircraft_config("b77w")
    general_cfg = load_general_config()

    def simulate_fn(segments: list[CruiseSegment]) -> RemainingCruiseResult:
        total_distance = sum(s.distance_nm for s in segments)
        request = RemainingCruiseInput(
            aircraft="B77W",
            altitude_ft=37000.0,
            gross_weight_kg=250000.0,
            mach=0.840,
            remaining_distance_nm=total_distance,
            wind_component_kt=0.0,
            isa_deviation_c=0.0,
            segments=segments,
        )
        return simulate_remaining_cruise(
            request,
            aircraft_cfg=aircraft_cfg,
            general_cfg=general_cfg,
        )

    return simulate_fn


# ── Test 1: Basic ensemble stats ────────────────────────────────────────────

class TestBasicEnsembleStats:
    def test_five_member_ensemble_stats(self):
        """Create 5 synthetic WindProfiles, verify mean/std/percentiles."""
        # 5 members with known wind speeds: 10, 20, 30, 40, 50 kt
        # All from 270 deg, track 90 deg -> pure tailwind
        wind_speeds = [10.0, 20.0, 30.0, 40.0, 50.0]
        members = [_make_wind_profile(ws) for ws in wind_speeds]
        segments = _make_route_segments(2, distance_nm=300.0)

        result = compute_ensemble_stats(
            member_profiles=members,
            segments=segments,
            cruise_altitude_ft=37000.0,
            track_deg_true=90.0,  # eastbound, wind from 270 = tailwind
        )

        assert result.source == "gefs_0.25deg"
        assert result.member_count == 5
        assert len(result.segment_stats) == 2

        for stat in result.segment_stats:
            # Mean of [10,20,30,40,50] = 30.0
            assert abs(stat.mean_wind_kt - 30.0) < 0.1
            # Std of [10,20,30,40,50] = sqrt(250/4) = ~15.81
            assert abs(stat.std_wind_kt - 15.81) < 0.1
            # Percentiles via linear interpolation on sorted [10,20,30,40,50]
            # P5: rank=0.2 -> 10 + 0.2*(20-10) = 12.0
            assert abs(stat.percentiles[5] - 12.0) < 0.1
            # P25: rank=1.0 -> 20.0
            assert abs(stat.percentiles[25] - 20.0) < 0.1
            # P50: rank=2.0 -> 30.0
            assert abs(stat.percentiles[50] - 30.0) < 0.1
            # P75: rank=3.0 -> 40.0
            assert abs(stat.percentiles[75] - 40.0) < 0.1
            # P95: rank=3.8 -> 40 + 0.8*(50-40) = 48.0
            assert abs(stat.percentiles[95] - 48.0) < 0.1

    def test_stats_have_correct_distances(self):
        """Verify segment metadata is preserved in stats."""
        members = [_make_wind_profile(25.0)]
        segments = _make_route_segments(3, distance_nm=150.0)

        result = compute_ensemble_stats(
            member_profiles=members,
            segments=segments,
            cruise_altitude_ft=37000.0,
            track_deg_true=90.0,
        )

        assert len(result.segment_stats) == 3
        for i, stat in enumerate(result.segment_stats):
            assert stat.segment_index == i
            assert stat.distance_nm == 150.0
            assert stat.altitude_ft == 37000.0


# ── Test 2: Single-member fallback ──────────────────────────────────────────

class TestSingleMemberFallback:
    def test_single_member_std_zero_and_uniform_percentiles(self):
        """1 member -> std=0, all percentiles equal the single value."""
        members = [_make_wind_profile(35.0)]
        segments = _make_route_segments(2, distance_nm=200.0)

        result = compute_ensemble_stats(
            member_profiles=members,
            segments=segments,
            cruise_altitude_ft=37000.0,
            track_deg_true=90.0,
        )

        assert result.member_count == 1
        for stat in result.segment_stats:
            assert stat.std_wind_kt == 0.0
            # All percentiles should equal the single wind component value
            wind_val = stat.mean_wind_kt
            for p in [5, 25, 50, 75, 95]:
                assert abs(stat.percentiles[p] - wind_val) < 0.1


# ── Test 3: Ensemble uncertainty scenarios ──────────────────────────────────

class TestEnsembleUncertaintyScenarios:
    def test_p5_burns_more_than_p50_and_p95(self):
        """P5 (headwind) burns more fuel than P50, P50 more than P95."""
        # Create ensemble with spread: P5=-20, P50=0, P95=+20 per segment
        segment_stats = [
            EnsembleWindStats(
                segment_index=i,
                distance_nm=200.0,
                altitude_ft=37000.0,
                mean_wind_kt=0.0,
                std_wind_kt=12.0,
                percentiles={5: -20.0, 25: -10.0, 50: 0.0, 75: 10.0, 95: 20.0},
            )
            for i in range(5)
        ]

        ensemble = EnsembleProfile(
            source="gefs_0.25deg",
            forecast_hour=6,
            member_count=21,
            segment_stats=segment_stats,
        )

        segments = _make_cruise_segments(5, distance_nm=200.0, wind_kt=0.0)
        simulate_fn = _make_simulate_fn()

        result = run_ensemble_uncertainty_scenarios(
            ensemble=ensemble,
            segments=segments,
            simulate_fn=simulate_fn,
            reserve_fuel_kg=5000.0,
        )

        assert len(result.scenarios) == 3
        p5 = result.scenarios[0]
        p50 = result.scenarios[1]
        p95 = result.scenarios[2]

        assert p5.label == "P5_headwind"
        assert p50.label == "P50_forecast"
        assert p95.label == "P95_tailwind"

        assert p5.cruise_result is not None
        assert p50.cruise_result is not None
        assert p95.cruise_result is not None

        # P5 (headwind) burns more fuel
        assert p5.cruise_result.remaining_fuel_kg > p50.cruise_result.remaining_fuel_kg
        assert p50.cruise_result.remaining_fuel_kg > p95.cruise_result.remaining_fuel_kg

    def test_reserve_at_risk_with_tight_reserves(self):
        """With tight reserves, P5 headwind scenario triggers reserve_at_risk."""
        segment_stats = [
            EnsembleWindStats(
                segment_index=i,
                distance_nm=200.0,
                altitude_ft=37000.0,
                mean_wind_kt=0.0,
                std_wind_kt=15.0,
                percentiles={5: -30.0, 25: -15.0, 50: 0.0, 75: 15.0, 95: 30.0},
            )
            for i in range(5)
        ]

        ensemble = EnsembleProfile(
            source="gefs_0.25deg",
            forecast_hour=6,
            member_count=21,
            segment_stats=segment_stats,
        )

        segments = _make_cruise_segments(5, distance_nm=200.0, wind_kt=0.0)
        simulate_fn = _make_simulate_fn()

        result = run_ensemble_uncertainty_scenarios(
            ensemble=ensemble,
            segments=segments,
            simulate_fn=simulate_fn,
            reserve_fuel_kg=500.0,  # tight reserve
        )

        assert result.reserve_at_risk is True
        assert result.risk_details is not None


# ── Test 4: Stale forecast detection ────────────────────────────────────────

class TestStaleForecastDetection:
    def test_stale_forecast_returns_true(self):
        """Forecast older than 18h is stale."""
        assert is_forecast_stale(
            forecast_valid_utc="2025-01-01T00:00:00",
            current_utc="2025-01-01T19:00:00",
            max_age_hours=18.0,
        ) is True

    def test_fresh_forecast_returns_false(self):
        """Forecast less than 18h old is not stale."""
        assert is_forecast_stale(
            forecast_valid_utc="2025-01-01T00:00:00",
            current_utc="2025-01-01T12:00:00",
            max_age_hours=18.0,
        ) is False

    def test_exactly_at_boundary(self):
        """Forecast exactly at 18h is not stale (> not >=)."""
        assert is_forecast_stale(
            forecast_valid_utc="2025-01-01T00:00:00",
            current_utc="2025-01-01T18:00:00",
            max_age_hours=18.0,
        ) is False

    def test_custom_max_age(self):
        """Custom max_age_hours works correctly."""
        assert is_forecast_stale(
            forecast_valid_utc="2025-01-01T00:00:00",
            current_utc="2025-01-01T07:00:00",
            max_age_hours=6.0,
        ) is True


# ── Test 5: Integration with existing interpolator ──────────────────────────

class TestInterpolatorIntegration:
    def test_compute_ensemble_stats_calls_interpolator_per_member(self):
        """Verify compute_ensemble_stats correctly uses interpolate_wind_components."""
        # Create 3 members with different wind speeds
        members = [
            _make_wind_profile(10.0),
            _make_wind_profile(30.0),
            _make_wind_profile(50.0),
        ]
        segments = _make_route_segments(1, distance_nm=500.0)

        result = compute_ensemble_stats(
            member_profiles=members,
            segments=segments,
            cruise_altitude_ft=37000.0,
            track_deg_true=90.0,
        )

        assert result.member_count == 3
        assert len(result.segment_stats) == 1

        stat = result.segment_stats[0]
        # Mean of [10, 30, 50] = 30.0 (wind from 270 on track 90 = tailwind)
        assert abs(stat.mean_wind_kt - 30.0) < 0.1
        # All percentiles should be within the range [10, 50]
        assert stat.percentiles[5] >= 9.9
        assert stat.percentiles[95] <= 50.1

    def test_multi_waypoint_members_interpolate_correctly(self):
        """Members with multiple waypoints still produce valid statistics."""
        members = []
        for ws in [15.0, 25.0, 35.0]:
            members.append(
                WindProfile(
                    source="gefs_member",
                    waypoints=[
                        WaypointWind(
                            cumulative_distance_nm=0.0,
                            layers=[
                                WindLayer(
                                    altitude_ft=37000.0,
                                    wind_direction_deg=270.0,
                                    wind_speed_kt=ws,
                                )
                            ],
                        ),
                        WaypointWind(
                            cumulative_distance_nm=600.0,
                            layers=[
                                WindLayer(
                                    altitude_ft=37000.0,
                                    wind_direction_deg=270.0,
                                    wind_speed_kt=ws + 10.0,
                                )
                            ],
                        ),
                    ],
                )
            )

        segments = _make_route_segments(3, distance_nm=200.0)

        result = compute_ensemble_stats(
            member_profiles=members,
            segments=segments,
            cruise_altitude_ft=37000.0,
            track_deg_true=90.0,
        )

        assert len(result.segment_stats) == 3
        # Each segment should have valid stats with non-zero std
        for stat in result.segment_stats:
            assert stat.std_wind_kt > 0.0
            assert stat.percentiles[5] < stat.percentiles[95]
