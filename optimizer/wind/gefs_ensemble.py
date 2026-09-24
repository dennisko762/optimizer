"""GEFS ensemble wind ingestion and per-segment percentile statistics."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from optimizer.route_profile_models import RemainingRouteSegment
from optimizer.wind.wind_interpolator import interpolate_wind_components
from optimizer.wind.wind_profile import WindProfile


@dataclass
class EnsembleWindStats:
    """Per-segment wind statistics derived from ensemble members."""

    segment_index: int
    distance_nm: float
    altitude_ft: float
    mean_wind_kt: float
    std_wind_kt: float
    percentiles: dict[int, float]  # {5: val, 25: val, 50: val, 75: val, 95: val}


@dataclass
class EnsembleProfile:
    """Wind statistics along a route from ensemble forecast data."""

    source: str  # "gefs_0.25deg"
    forecast_hour: int  # hours ahead
    valid_time_utc: str | None = None
    member_count: int = 21
    segment_stats: list[EnsembleWindStats] = field(default_factory=list)


def compute_ensemble_stats(
    member_profiles: list[WindProfile],
    segments: list[RemainingRouteSegment],
    cruise_altitude_ft: float,
    track_deg_true: float | None = None,
) -> EnsembleProfile:
    """
    Given N ensemble member WindProfiles (each representing one GEFS member),
    interpolate each onto the route segments, then compute per-segment statistics.

    Parameters
    ----------
    member_profiles : list[WindProfile]
        One WindProfile per ensemble member (typically 21 for GEFS).
    segments : list[RemainingRouteSegment]
        Route segments to evaluate.
    cruise_altitude_ft : float
        Cruise altitude for vertical interpolation.
    track_deg_true : float | None
        Track bearing for wind projection.

    Returns
    -------
    EnsembleProfile with per-segment mean, std, and percentile (5/25/50/75/95)
    statistics.
    """
    n_members = len(member_profiles)
    n_segments = len(segments)

    # Interpolate each member onto the route segments
    member_results: list[list[RemainingRouteSegment]] = []
    for profile in member_profiles:
        interpolated = interpolate_wind_components(
            profile, segments, cruise_altitude_ft, track_deg_true
        )
        member_results.append(interpolated)

    # Collect per-segment wind values across all members
    segment_stats: list[EnsembleWindStats] = []
    for seg_idx in range(n_segments):
        seg = segments[seg_idx]
        wind_values: list[float] = []
        for member_segs in member_results:
            wind_kt = member_segs[seg_idx].wind_component_kt
            wind_values.append(wind_kt if wind_kt is not None else 0.0)

        mean_val = statistics.mean(wind_values)

        if n_members >= 2:
            std_val = statistics.stdev(wind_values)
        else:
            std_val = 0.0

        percentiles = _compute_percentiles(wind_values, [5, 25, 50, 75, 95])

        segment_stats.append(
            EnsembleWindStats(
                segment_index=seg_idx,
                distance_nm=float(seg.distance_nm),
                altitude_ft=cruise_altitude_ft,
                mean_wind_kt=round(mean_val, 2),
                std_wind_kt=round(std_val, 2),
                percentiles={k: round(v, 2) for k, v in percentiles.items()},
            )
        )

    # Derive source/valid_time from the first member if available
    source = "gefs_0.25deg"
    valid_time = None
    forecast_hour = 0
    if member_profiles:
        if member_profiles[0].valid_time_utc:
            valid_time = member_profiles[0].valid_time_utc

    return EnsembleProfile(
        source=source,
        forecast_hour=forecast_hour,
        valid_time_utc=valid_time,
        member_count=n_members,
        segment_stats=segment_stats,
    )


def _compute_percentiles(
    values: list[float], percentile_list: list[int]
) -> dict[int, float]:
    """
    Compute percentiles using linear interpolation (stdlib only).

    For a single value, all percentiles equal that value.
    """
    n = len(values)
    if n == 0:
        return {p: 0.0 for p in percentile_list}
    if n == 1:
        return {p: values[0] for p in percentile_list}

    sorted_vals = sorted(values)
    result: dict[int, float] = {}

    for p in percentile_list:
        # Linear interpolation method (same as numpy default)
        rank = (p / 100.0) * (n - 1)
        lower = int(math.floor(rank))
        upper = min(lower + 1, n - 1)
        frac = rank - lower
        result[p] = sorted_vals[lower] + frac * (sorted_vals[upper] - sorted_vals[lower])

    return result


def is_forecast_stale(
    forecast_valid_utc: str,
    current_utc: str | None = None,
    max_age_hours: float = 18.0,
) -> bool:
    """
    Returns True if forecast age exceeds max_age_hours.

    When stale, caller should revert to climatology.

    Parameters
    ----------
    forecast_valid_utc : str
        ISO 8601 datetime string for the forecast validity time.
    current_utc : str | None
        ISO 8601 datetime string for "now". If None, uses datetime.now(UTC).
    max_age_hours : float
        Maximum allowed forecast age in hours. Default 18.0.
    """
    forecast_dt = datetime.fromisoformat(forecast_valid_utc)
    if forecast_dt.tzinfo is None:
        forecast_dt = forecast_dt.replace(tzinfo=timezone.utc)

    if current_utc is not None:
        current_dt = datetime.fromisoformat(current_utc)
        if current_dt.tzinfo is None:
            current_dt = current_dt.replace(tzinfo=timezone.utc)
    else:
        current_dt = datetime.now(timezone.utc)

    age_hours = (current_dt - forecast_dt).total_seconds() / 3600.0
    return age_hours > max_age_hours
