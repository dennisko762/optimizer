"""Post-flight wind reconciliation and Brier skill score calibration."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class PirepObservation:
    """A single PIREP (pilot report) wind observation."""

    timestamp_utc: str  # ISO 8601
    lat: float
    lon: float
    altitude_ft: float
    wind_direction_deg: float
    wind_speed_kt: float
    wind_component_kt: float | None = None  # along-track, if track known
    source: str = "pirep"  # "pirep", "fdr", "acars"


@dataclass
class SegmentReconciliation:
    """Forecast vs actual comparison for one segment."""

    segment_index: int
    distance_nm: float
    forecast_wind_kt: float  # P50 / median forecast
    actual_wind_kt: float  # from PIREP/FDR
    error_kt: float  # forecast - actual (positive = overestimated tailwind)
    abs_error_kt: float
    within_1sigma: bool  # |error| <= std
    within_2sigma: bool  # |error| <= 2*std
    forecast_std_kt: float | None = None  # ensemble std if available
    percentile_hit: int | None = None  # which forecast percentile bracket the actual fell in


@dataclass
class FlightReconciliation:
    """Complete reconciliation for one flight."""

    flight_id: str
    flight_date_utc: str  # ISO 8601 date
    route: str  # e.g. "KJFK-EGLL"
    forecast_age_hours: float
    segments: list[SegmentReconciliation] = field(default_factory=list)
    mean_error_kt: float = 0.0
    rmse_kt: float = 0.0
    mean_abs_error_kt: float = 0.0
    calibration_score: float = 0.0  # fraction of actuals within 1-sigma


@dataclass
class CalibrationReport:
    """Monthly calibration metrics."""

    period: str  # "2026-Q3" or "2026-09"
    flight_count: int
    pirep_point_count: int
    mean_error_kt: float  # bias
    rmse_kt: float
    mean_abs_error_kt: float
    calibration_1sigma: float  # fraction within 1-sigma (ideal: ~68%)
    calibration_2sigma: float  # fraction within 2-sigma (ideal: ~95%)
    brier_skill_score: float | None = None  # BSS for reserve-risk alerts
    false_positive_rate: float | None = None  # reserve alerts that didn't materialize
    true_positive_rate: float | None = None  # actual reserve busts that were alerted


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def reconcile_flight(
    flight_id: str,
    route: str,
    forecast_profile,  # WindProfile | EnsembleProfile
    actual_observations: list[PirepObservation],
    segments: list,  # list[RemainingRouteSegment]
    cruise_altitude_ft: float,
    track_deg_true: float | None = None,
    forecast_age_hours: float = 0.0,
) -> FlightReconciliation:
    """
    Compare forecast winds to actual PIREP observations segment by segment.

    For each segment, finds the nearest PIREP observation and computes the error.
    Computes flight-level aggregate metrics (mean error, RMSE, MAE, calibration score).
    """
    from optimizer.wind.gefs_ensemble import EnsembleProfile
    from optimizer.wind.wind_interpolator import interpolate_wind_components
    from optimizer.wind.wind_profile import WindProfile

    if not actual_observations:
        return FlightReconciliation(
            flight_id=flight_id,
            flight_date_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            route=route,
            forecast_age_hours=forecast_age_hours,
        )

    # 1. Extract per-segment forecast wind (and std if ensemble)
    forecast_winds: list[tuple[float, float | None]] = []

    if isinstance(forecast_profile, EnsembleProfile):
        for i in range(len(segments)):
            if i < len(forecast_profile.segment_stats):
                stats = forecast_profile.segment_stats[i]
                wind_kt = stats.percentiles.get(50, stats.mean_wind_kt)
                std_kt = stats.std_wind_kt
            else:
                wind_kt = 0.0
                std_kt = None
            forecast_winds.append((wind_kt, std_kt))
    elif isinstance(forecast_profile, WindProfile):
        interpolated = interpolate_wind_components(
            forecast_profile, segments, cruise_altitude_ft, track_deg_true
        )
        for seg in interpolated:
            wkt = seg.wind_component_kt if seg.wind_component_kt is not None else 0.0
            forecast_winds.append((wkt, None))
    else:
        raise TypeError(f"Unsupported forecast profile type: {type(forecast_profile)}")

    # 2. Compute cumulative-distance midpoints for each segment
    cum_dist = 0.0
    seg_midpoints: list[float] = []
    for seg in segments:
        mid = cum_dist + float(seg.distance_nm) / 2.0
        seg_midpoints.append(mid)
        cum_dist += float(seg.distance_nm)

    # 3. Project each PIREP onto the route (assign a cumulative distance)
    obs_positions: list[float] = []
    for obs in actual_observations:
        obs_positions.append(
            _project_observation_onto_route(obs, segments, seg_midpoints)
        )

    # 4. Match: for each segment, find nearest observation within 50 nm
    seg_reconciliations: list[SegmentReconciliation] = []

    for i, seg in enumerate(segments):
        best_obs_idx: int | None = None
        best_dist = float("inf")

        for j, obs_pos in enumerate(obs_positions):
            d = abs(seg_midpoints[i] - obs_pos)
            if d < best_dist and d <= 50.0:
                best_dist = d
                best_obs_idx = j

        if best_obs_idx is None:
            continue

        obs = actual_observations[best_obs_idx]
        forecast_wind, forecast_std = forecast_winds[i]

        # Use wind_component_kt (along-track) if available, else raw speed
        actual_wind = (
            obs.wind_component_kt
            if obs.wind_component_kt is not None
            else obs.wind_speed_kt
        )

        error = forecast_wind - actual_wind
        abs_error = abs(error)

        within_1sigma = False
        within_2sigma = False
        if forecast_std is not None and forecast_std > 0:
            within_1sigma = abs_error <= forecast_std
            within_2sigma = abs_error <= 2 * forecast_std

        seg_reconciliations.append(
            SegmentReconciliation(
                segment_index=i,
                distance_nm=float(seg.distance_nm),
                forecast_wind_kt=forecast_wind,
                forecast_std_kt=forecast_std,
                actual_wind_kt=actual_wind,
                error_kt=round(error, 4),
                abs_error_kt=round(abs_error, 4),
                within_1sigma=within_1sigma,
                within_2sigma=within_2sigma,
                percentile_hit=None,
            )
        )

    # 5. Aggregate metrics
    n = len(seg_reconciliations)
    if n > 0:
        mean_error = sum(sr.error_kt for sr in seg_reconciliations) / n
        rmse = math.sqrt(sum(sr.error_kt**2 for sr in seg_reconciliations) / n)
        mae = sum(sr.abs_error_kt for sr in seg_reconciliations) / n
        cal_count = sum(1 for sr in seg_reconciliations if sr.within_1sigma)
        calibration_score = cal_count / n
    else:
        mean_error = rmse = mae = calibration_score = 0.0

    return FlightReconciliation(
        flight_id=flight_id,
        flight_date_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        route=route,
        forecast_age_hours=forecast_age_hours,
        segments=seg_reconciliations,
        mean_error_kt=round(mean_error, 4),
        rmse_kt=round(rmse, 4),
        mean_abs_error_kt=round(mae, 4),
        calibration_score=round(calibration_score, 4),
    )


def compute_calibration_report(
    reconciliations: list[FlightReconciliation],
    period: str,
    reserve_alerts: list[tuple[str, bool]] | None = None,
    reserve_busts: list[tuple[str, bool]] | None = None,
) -> CalibrationReport:
    """
    Aggregate reconciliation data over a period.

    Computes bias (mean error), RMSE, MAE across all segments,
    calibration fractions, and Brier Skill Score for reserve-risk alerts.
    """
    all_segments: list[SegmentReconciliation] = []
    for rec in reconciliations:
        all_segments.extend(rec.segments)

    n = len(all_segments)
    if n > 0:
        mean_error = sum(sr.error_kt for sr in all_segments) / n
        rmse = math.sqrt(sum(sr.error_kt**2 for sr in all_segments) / n)
        mae = sum(sr.abs_error_kt for sr in all_segments) / n
        cal_1sigma = sum(1 for sr in all_segments if sr.within_1sigma) / n
        cal_2sigma = sum(1 for sr in all_segments if sr.within_2sigma) / n
    else:
        mean_error = rmse = mae = cal_1sigma = cal_2sigma = 0.0

    # Brier Skill Score and alert rates
    bss: float | None = None
    fpr: float | None = None
    tpr: float | None = None

    if reserve_alerts is not None and reserve_busts is not None:
        alert_map = {fid: int(was_alert) for fid, was_alert in reserve_alerts}
        bust_map = {fid: int(did_bust) for fid, did_bust in reserve_busts}
        common_ids = sorted(set(alert_map.keys()) & set(bust_map.keys()))

        if common_ids:
            forecasts = [alert_map[fid] for fid in common_ids]
            outcomes = [bust_map[fid] for fid in common_ids]
            k = len(common_ids)
            clim = sum(outcomes) / k  # climatological base rate

            # Brier score for the forecast
            bs_forecast = sum(
                (f - o) ** 2 for f, o in zip(forecasts, outcomes)
            ) / k

            # Brier score for climatology
            bs_clim = sum((clim - o) ** 2 for o in outcomes) / k

            if bs_clim > 0:
                bss = round(1.0 - bs_forecast / bs_clim, 4)
            else:
                # Perfect climatology (all same outcome) => BSS undefined / 0
                bss = 0.0

            # False positive rate: alerts issued when no bust
            non_bust_ids = [fid for fid in common_ids if bust_map[fid] == 0]
            if non_bust_ids:
                false_positives = sum(
                    1 for fid in non_bust_ids if alert_map[fid] == 1
                )
                fpr = round(false_positives / len(non_bust_ids), 4)
            else:
                fpr = 0.0

            # True positive rate (recall): busts that were alerted
            bust_ids = [fid for fid in common_ids if bust_map[fid] == 1]
            if bust_ids:
                true_positives = sum(
                    1 for fid in bust_ids if alert_map[fid] == 1
                )
                tpr = round(true_positives / len(bust_ids), 4)
            else:
                tpr = 0.0

    return CalibrationReport(
        period=period,
        flight_count=len(reconciliations),
        pirep_point_count=n,
        mean_error_kt=round(mean_error, 4),
        rmse_kt=round(rmse, 4),
        mean_abs_error_kt=round(mae, 4),
        calibration_1sigma=round(cal_1sigma, 4),
        calibration_2sigma=round(cal_2sigma, 4),
        brier_skill_score=bss,
        false_positive_rate=fpr,
        true_positive_rate=tpr,
    )


def save_reconciliation(
    reconciliation: FlightReconciliation,
    storage_dir: str = "optimizer/wind/reconciliation_data",
) -> str:
    """Save reconciliation to a JSON file. Returns the file path."""
    os.makedirs(storage_dir, exist_ok=True)
    filename = f"{reconciliation.flight_id}_{reconciliation.flight_date_utc}.json"
    filepath = os.path.join(storage_dir, filename)

    data = asdict(reconciliation)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return filepath


def load_reconciliations(
    storage_dir: str = "optimizer/wind/reconciliation_data",
    period: str | None = None,
) -> list[FlightReconciliation]:
    """Load reconciliation records from storage, optionally filtered by period."""
    if not os.path.isdir(storage_dir):
        return []

    results: list[FlightReconciliation] = []
    for fname in sorted(os.listdir(storage_dir)):
        if not fname.endswith(".json"):
            continue

        filepath = os.path.join(storage_dir, fname)
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Reconstruct dataclasses
        seg_list = [
            SegmentReconciliation(**seg_data) for seg_data in data.pop("segments", [])
        ]
        rec = FlightReconciliation(**data, segments=seg_list)

        # Period filter: match if flight_date_utc starts with the period string
        if period is not None and not rec.flight_date_utc.startswith(period):
            continue

        results.append(rec)

    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles between two lat/lon points."""
    r_nm = 3440.065  # Earth radius in nautical miles
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return r_nm * c


def _project_observation_onto_route(
    obs: PirepObservation,
    segments: list,
    seg_midpoints: list[float],
) -> float:
    """
    Estimate where a PIREP observation falls along the route (cumulative distance).

    Uses geographic proximity to segments when lat/lon is available;
    otherwise falls back to the midpoint of the closest segment by index.
    """
    best_idx = 0
    best_dist = float("inf")

    for i, seg in enumerate(segments):
        seg_lat = getattr(seg, "lat", None)
        seg_lon = getattr(seg, "lon", None)
        start_lat = getattr(seg, "start_lat", None)
        start_lon = getattr(seg, "start_lon", None)

        # Try end-point coords first, then start-point
        if seg_lat is not None and seg_lon is not None:
            d = _haversine_nm(obs.lat, obs.lon, seg_lat, seg_lon)
        elif start_lat is not None and start_lon is not None:
            d = _haversine_nm(obs.lat, obs.lon, start_lat, start_lon)
        else:
            # No coords on segment — cannot match geographically
            continue

        if d < best_dist:
            best_dist = d
            best_idx = i

    return seg_midpoints[best_idx]
