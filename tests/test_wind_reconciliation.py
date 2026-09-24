"""Tests for optimizer.wind.reconciliation module."""

from __future__ import annotations

import json
import math
import os
import tempfile

import pytest

from optimizer.wind.reconciliation import (
    CalibrationReport,
    FlightReconciliation,
    PirepObservation,
    SegmentReconciliation,
    compute_calibration_report,
    load_reconciliations,
    reconcile_flight,
    save_reconciliation,
)
from optimizer.wind.gefs_ensemble import EnsembleProfile, EnsembleWindStats
from optimizer.wind.wind_profile import WindProfile, WaypointWind, WindLayer
from optimizer.route_profile_models import RemainingRouteSegment


# ---------------------------------------------------------------------------
# Helpers for building synthetic data
# ---------------------------------------------------------------------------


def _make_segments(
    n: int,
    distance_nm: float = 100.0,
    altitude_ft: float = 35000.0,
    wind_component_kt: float = 10.0,
    with_coords: bool = True,
) -> list[RemainingRouteSegment]:
    """Create n synthetic route segments with optional lat/lon."""
    segments = []
    for i in range(n):
        kwargs: dict = {
            "distanceNm": distance_nm,
            "altitudeFt": altitude_ft,
            "windComponentKt": wind_component_kt,
        }
        if with_coords:
            # Spread along a rough west-to-east route
            kwargs["startLat"] = 51.0
            kwargs["startLon"] = -5.0 + i * 2.0
            kwargs["lat"] = 51.0
            kwargs["lon"] = -5.0 + (i + 1) * 2.0
        segments.append(RemainingRouteSegment(**kwargs))
    return segments


def _make_pireps(
    forecast_winds: list[float],
    actual_winds: list[float],
    segments: list[RemainingRouteSegment],
) -> list[PirepObservation]:
    """Create PIREP observations positioned near each segment's end-point."""
    obs_list = []
    for i, actual in enumerate(actual_winds):
        seg = segments[i]
        obs_list.append(
            PirepObservation(
                timestamp_utc="2026-09-24T12:00:00Z",
                lat=seg.lat if seg.lat is not None else 51.0,
                lon=seg.lon if seg.lon is not None else -3.0 + i * 2.0,
                altitude_ft=35000.0,
                wind_direction_deg=270.0,
                wind_speed_kt=abs(actual),
                wind_component_kt=actual,
                source="pirep",
            )
        )
    return obs_list


def _make_wind_profile(winds_kt: list[float], n_segments: int) -> WindProfile:
    """Create a single-waypoint WindProfile returning a fixed wind component."""
    # Single waypoint => all segments get the same wind
    # We'll create one waypoint per segment instead for segment-level control
    waypoints = []
    cum = 0.0
    for i, w in enumerate(winds_kt):
        # Wind direction 270 + speed = w gives wind_component = w when track is 90 (east)
        # But with single-layer approach, the interpolator projects direction onto track.
        # For simplicity, just set wind direction = 0 so along-track component calc
        # depends on track. We'll use the EnsembleProfile path for precise control.
        waypoints.append(
            WaypointWind(
                ident=f"WPT{i}",
                lat=51.0,
                lon=-5.0 + (i + 1) * 2.0,
                cumulative_distance_nm=cum + 50.0,
                layers=[
                    WindLayer(
                        altitude_ft=35000.0,
                        wind_direction_deg=270.0,
                        wind_speed_kt=abs(w),
                    )
                ],
            )
        )
        cum += 100.0
    return WindProfile(source="test", waypoints=waypoints)


def _make_ensemble_profile(
    winds_p50: list[float],
    stds: list[float],
) -> EnsembleProfile:
    """Create an EnsembleProfile with controlled P50 and std values."""
    stats = []
    for i, (w, s) in enumerate(zip(winds_p50, stds)):
        stats.append(
            EnsembleWindStats(
                segment_index=i,
                distance_nm=100.0,
                altitude_ft=35000.0,
                mean_wind_kt=w,
                std_wind_kt=s,
                percentiles={5: w - 2 * s, 25: w - s, 50: w, 75: w + s, 95: w + 2 * s},
            )
        )
    return EnsembleProfile(
        source="test_ensemble",
        forecast_hour=6,
        member_count=21,
        segment_stats=stats,
    )


# ===========================================================================
# Test 1: Basic reconciliation
# ===========================================================================


class TestBasicReconciliation:
    """Create forecast and actuals for 5 segments; verify errors, RMSE, MAE."""

    def test_segment_errors(self):
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, [5.0] * 5)

        result = reconcile_flight(
            flight_id="TEST001",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
            forecast_age_hours=3.0,
        )

        assert len(result.segments) == 5
        expected_errors = [f - a for f, a in zip(forecast_winds, actual_winds)]
        # [2.0, -2.0, -2.0, 5.0, -5.0]
        for seg_rec, expected_err in zip(result.segments, expected_errors):
            assert abs(seg_rec.error_kt - expected_err) < 0.01

    def test_rmse_and_mae(self):
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, [5.0] * 5)

        result = reconcile_flight(
            flight_id="TEST001",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        errors = [f - a for f, a in zip(forecast_winds, actual_winds)]
        expected_rmse = math.sqrt(sum(e**2 for e in errors) / len(errors))
        expected_mae = sum(abs(e) for e in errors) / len(errors)

        assert abs(result.rmse_kt - expected_rmse) < 0.01
        assert abs(result.mean_abs_error_kt - expected_mae) < 0.01

    def test_mean_error(self):
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, [5.0] * 5)

        result = reconcile_flight(
            flight_id="TEST001",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        errors = [f - a for f, a in zip(forecast_winds, actual_winds)]
        expected_mean = sum(errors) / len(errors)
        assert abs(result.mean_error_kt - expected_mean) < 0.01


# ===========================================================================
# Test 2: Calibration within sigma
# ===========================================================================


class TestCalibrationSigma:
    """Create forecast with known std; verify calibration_score."""

    def test_calibration_score(self):
        # std = 5.0 for all segments
        # Errors: [2, -2, -2, 5, -5] => abs = [2, 2, 2, 5, 5]
        # within 1-sigma (<=5): all 5 => calibration = 1.0
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        stds = [5.0, 5.0, 5.0, 5.0, 5.0]
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, stds)

        result = reconcile_flight(
            flight_id="TEST_CAL",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        assert result.calibration_score == 1.0

    def test_partial_calibration(self):
        # std = 3.0; errors = [2, -2, -2, 5, -5] => abs = [2, 2, 2, 5, 5]
        # within 1-sigma (<=3): first 3 => calibration = 3/5 = 0.6
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        stds = [3.0] * 5
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, stds)

        result = reconcile_flight(
            flight_id="TEST_CAL2",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        assert abs(result.calibration_score - 0.6) < 0.001

    def test_within_2sigma(self):
        # std = 3.0; errors abs = [2, 2, 2, 5, 5]
        # within 2-sigma (<=6): all 5 => 2sigma frac = 1.0
        forecast_winds = [10.0, 20.0, -5.0, 15.0, 0.0]
        actual_winds = [8.0, 22.0, -3.0, 10.0, 5.0]
        stds = [3.0] * 5
        segments = _make_segments(5)
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, stds)

        result = reconcile_flight(
            flight_id="TEST_CAL3",
            route="KJFK-EGLL",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        within_2s = sum(1 for s in result.segments if s.within_2sigma) / len(
            result.segments
        )
        assert within_2s == 1.0


# ===========================================================================
# Test 3: Calibration report aggregation
# ===========================================================================


class TestCalibrationReport:
    """Create 3 FlightReconciliations, compute report, verify aggregates."""

    def _make_flight_rec(
        self, flight_id: str, errors: list[float], stds: list[float]
    ) -> FlightReconciliation:
        segs = []
        for i, (err, std) in enumerate(zip(errors, stds)):
            abs_err = abs(err)
            segs.append(
                SegmentReconciliation(
                    segment_index=i,
                    distance_nm=100.0,
                    forecast_wind_kt=10.0 + err,
                    forecast_std_kt=std,
                    actual_wind_kt=10.0,
                    error_kt=err,
                    abs_error_kt=abs_err,
                    within_1sigma=abs_err <= std,
                    within_2sigma=abs_err <= 2 * std,
                )
            )
        n = len(segs)
        me = sum(s.error_kt for s in segs) / n
        rmse_val = math.sqrt(sum(s.error_kt**2 for s in segs) / n)
        mae_val = sum(s.abs_error_kt for s in segs) / n
        cal = sum(1 for s in segs if s.within_1sigma) / n
        return FlightReconciliation(
            flight_id=flight_id,
            flight_date_utc="2026-09-24",
            route="KJFK-EGLL",
            forecast_age_hours=3.0,
            segments=segs,
            mean_error_kt=me,
            rmse_kt=rmse_val,
            mean_abs_error_kt=mae_val,
            calibration_score=cal,
        )

    def test_aggregate_metrics(self):
        rec1 = self._make_flight_rec("F1", [2.0, -3.0], [5.0, 5.0])
        rec2 = self._make_flight_rec("F2", [1.0, -1.0, 4.0], [3.0, 3.0, 3.0])
        rec3 = self._make_flight_rec("F3", [-2.0], [5.0])

        report = compute_calibration_report([rec1, rec2, rec3], "2026-09")

        assert report.flight_count == 3
        assert report.pirep_point_count == 6  # 2 + 3 + 1

        # All errors: [2, -3, 1, -1, 4, -2]
        all_errors = [2.0, -3.0, 1.0, -1.0, 4.0, -2.0]
        expected_mean = sum(all_errors) / 6
        expected_rmse = math.sqrt(sum(e**2 for e in all_errors) / 6)
        expected_mae = sum(abs(e) for e in all_errors) / 6

        assert abs(report.mean_error_kt - expected_mean) < 0.01
        assert abs(report.rmse_kt - expected_rmse) < 0.01
        assert abs(report.mean_abs_error_kt - expected_mae) < 0.01

    def test_calibration_fractions(self):
        rec1 = self._make_flight_rec("F1", [2.0, -3.0], [5.0, 5.0])
        rec2 = self._make_flight_rec("F2", [1.0, -1.0, 4.0], [3.0, 3.0, 3.0])
        rec3 = self._make_flight_rec("F3", [-2.0], [5.0])

        report = compute_calibration_report([rec1, rec2, rec3], "2026-09")

        # 1-sigma check: |err| <= std
        # (2<=5)T, (3<=5)T, (1<=3)T, (1<=3)T, (4<=3)F, (2<=5)T => 5/6
        assert abs(report.calibration_1sigma - 5 / 6) < 0.01

        # 2-sigma check: |err| <= 2*std
        # (2<=10)T, (3<=10)T, (1<=6)T, (1<=6)T, (4<=6)T, (2<=10)T => 6/6
        assert abs(report.calibration_2sigma - 1.0) < 0.01


# ===========================================================================
# Test 4: Brier Skill Score
# ===========================================================================


class TestBrierSkillScore:
    """Verify BSS, FPR, TPR from reserve alert/bust data."""

    def test_bss_computation(self):
        # 4 flights: alerts = [1,1,0,0], busts = [1,0,1,0]
        # clim = mean(outcomes) = 2/4 = 0.5
        # BS_forecast = ((1-1)^2 + (1-0)^2 + (0-1)^2 + (0-0)^2) / 4
        #             = (0 + 1 + 1 + 0) / 4 = 0.5
        # BS_clim = ((0.5-1)^2 + (0.5-0)^2 + (0.5-1)^2 + (0.5-0)^2) / 4
        #         = (0.25 + 0.25 + 0.25 + 0.25) / 4 = 0.25
        # BSS = 1 - 0.5/0.25 = 1 - 2 = -1.0

        rec = FlightReconciliation(
            flight_id="X", flight_date_utc="2026-09-24", route="A-B",
            forecast_age_hours=3.0,
        )
        recs = [rec]  # dummy, BSS uses alert/bust lists only

        alerts = [("F1", True), ("F2", True), ("F3", False), ("F4", False)]
        busts = [("F1", True), ("F2", False), ("F3", True), ("F4", False)]

        report = compute_calibration_report(recs, "2026-09", alerts, busts)

        assert report.brier_skill_score is not None
        assert abs(report.brier_skill_score - (-1.0)) < 0.01

    def test_perfect_forecast_bss(self):
        # Perfect forecast: alerts match busts exactly
        alerts = [("F1", True), ("F2", False), ("F3", True), ("F4", False)]
        busts = [("F1", True), ("F2", False), ("F3", True), ("F4", False)]

        rec = FlightReconciliation(
            flight_id="X", flight_date_utc="2026-09-24", route="A-B",
            forecast_age_hours=3.0,
        )
        report = compute_calibration_report([rec], "2026-09", alerts, busts)

        # BS_forecast = 0; BSS = 1 - 0/BS_clim = 1.0
        assert report.brier_skill_score is not None
        assert abs(report.brier_skill_score - 1.0) < 0.01

    def test_fpr_and_tpr(self):
        # alerts: F1=True, F2=True, F3=False, F4=False
        # busts:  F1=True, F2=False, F3=True, F4=False
        alerts = [("F1", True), ("F2", True), ("F3", False), ("F4", False)]
        busts = [("F1", True), ("F2", False), ("F3", True), ("F4", False)]

        rec = FlightReconciliation(
            flight_id="X", flight_date_utc="2026-09-24", route="A-B",
            forecast_age_hours=3.0,
        )
        report = compute_calibration_report([rec], "2026-09", alerts, busts)

        # FPR: among non-busts (F2, F4), how many were alerted?
        # F2=alerted => 1/2 = 0.5
        assert report.false_positive_rate is not None
        assert abs(report.false_positive_rate - 0.5) < 0.01

        # TPR: among busts (F1, F3), how many were alerted?
        # F1=alerted, F3=not => 1/2 = 0.5
        assert report.true_positive_rate is not None
        assert abs(report.true_positive_rate - 0.5) < 0.01


# ===========================================================================
# Test 5: Save and load round-trip
# ===========================================================================


class TestSaveLoad:
    """Save a reconciliation, load it back, verify all fields."""

    def test_round_trip(self, tmp_path):
        seg = SegmentReconciliation(
            segment_index=0,
            distance_nm=100.0,
            forecast_wind_kt=12.0,
            forecast_std_kt=4.0,
            actual_wind_kt=10.0,
            error_kt=2.0,
            abs_error_kt=2.0,
            within_1sigma=True,
            within_2sigma=True,
            percentile_hit=None,
        )
        rec = FlightReconciliation(
            flight_id="RT001",
            flight_date_utc="2026-09-24",
            route="KJFK-EGLL",
            forecast_age_hours=6.5,
            segments=[seg],
            mean_error_kt=2.0,
            rmse_kt=2.0,
            mean_abs_error_kt=2.0,
            calibration_score=1.0,
        )

        storage_dir = str(tmp_path / "recon_data")
        saved_path = save_reconciliation(rec, storage_dir)
        assert os.path.isfile(saved_path)

        loaded = load_reconciliations(storage_dir)
        assert len(loaded) == 1

        loaded_rec = loaded[0]
        assert loaded_rec.flight_id == rec.flight_id
        assert loaded_rec.flight_date_utc == rec.flight_date_utc
        assert loaded_rec.route == rec.route
        assert loaded_rec.forecast_age_hours == rec.forecast_age_hours
        assert loaded_rec.mean_error_kt == rec.mean_error_kt
        assert loaded_rec.rmse_kt == rec.rmse_kt
        assert loaded_rec.mean_abs_error_kt == rec.mean_abs_error_kt
        assert loaded_rec.calibration_score == rec.calibration_score
        assert len(loaded_rec.segments) == 1

        ls = loaded_rec.segments[0]
        assert ls.segment_index == seg.segment_index
        assert ls.forecast_wind_kt == seg.forecast_wind_kt
        assert ls.forecast_std_kt == seg.forecast_std_kt
        assert ls.actual_wind_kt == seg.actual_wind_kt
        assert ls.error_kt == seg.error_kt
        assert ls.within_1sigma == seg.within_1sigma
        assert ls.within_2sigma == seg.within_2sigma

    def test_period_filter(self, tmp_path):
        storage_dir = str(tmp_path / "recon_data")

        for date in ["2026-09-01", "2026-09-15", "2026-10-05"]:
            rec = FlightReconciliation(
                flight_id=f"FL_{date}",
                flight_date_utc=date,
                route="A-B",
                forecast_age_hours=3.0,
            )
            save_reconciliation(rec, storage_dir)

        all_recs = load_reconciliations(storage_dir)
        assert len(all_recs) == 3

        sept = load_reconciliations(storage_dir, period="2026-09")
        assert len(sept) == 2

        oct_recs = load_reconciliations(storage_dir, period="2026-10")
        assert len(oct_recs) == 1


# ===========================================================================
# Test 6: Edge cases
# ===========================================================================


class TestEdgeCases:
    """Empty observations and single observation."""

    def test_empty_observations(self):
        segments = _make_segments(3)
        ensemble = _make_ensemble_profile([10.0, 20.0, 30.0], [5.0, 5.0, 5.0])

        result = reconcile_flight(
            flight_id="EMPTY",
            route="A-B",
            forecast_profile=ensemble,
            actual_observations=[],
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        assert len(result.segments) == 0
        assert result.mean_error_kt == 0.0
        assert result.rmse_kt == 0.0
        assert result.mean_abs_error_kt == 0.0
        assert result.calibration_score == 0.0

    def test_single_observation(self):
        segments = _make_segments(1)
        forecast_winds = [15.0]
        actual_winds = [12.0]
        pireps = _make_pireps(forecast_winds, actual_winds, segments)
        ensemble = _make_ensemble_profile(forecast_winds, [5.0])

        result = reconcile_flight(
            flight_id="SINGLE",
            route="A-B",
            forecast_profile=ensemble,
            actual_observations=pireps,
            segments=segments,
            cruise_altitude_ft=35000.0,
        )

        assert len(result.segments) == 1
        assert abs(result.segments[0].error_kt - 3.0) < 0.01
        assert abs(result.mean_error_kt - 3.0) < 0.01
        assert abs(result.rmse_kt - 3.0) < 0.01
        assert abs(result.mean_abs_error_kt - 3.0) < 0.01

    def test_nonexistent_storage_dir(self):
        loaded = load_reconciliations("/nonexistent/path/nowhere")
        assert loaded == []
