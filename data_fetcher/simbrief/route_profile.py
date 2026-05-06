from __future__ import annotations

from data_fetcher.simbrief.simbrief_models import SimBriefRouteWaypoint
from optimizer.route_profile_models import (
    RemainingRouteProfile,
    RemainingRouteSegment,
    cruise_segments_from_remaining_route_profile,
    finalize_remaining_route_profile,
)


def build_cruise_segments_from_waypoints(
    waypoints: list[SimBriefRouteWaypoint],
    *,
    max_segments: int = 120,
) -> list[dict[str, float]]:
    profile = build_remaining_route_profile_from_waypoints(
        waypoints,
        max_segments=max_segments,
    )
    return cruise_segments_from_remaining_route_profile(profile)


def build_remaining_route_profile_from_waypoints(
    waypoints: list[SimBriefRouteWaypoint],
    *,
    max_segments: int = 120,
    source: str = "SIMBRIEF_NAVLOG",
) -> RemainingRouteProfile:
    segments: list[RemainingRouteSegment] = []

    for index, waypoint in enumerate(waypoints[1:], start=1):
        distance_nm = waypoint.distance_from_previous_nm
        if distance_nm is None or distance_nm <= 0:
            continue

        segments.append(
            RemainingRouteSegment(
                sequence=index,
                ident=waypoint.ident,
                lat=round(float(waypoint.lat), 6),
                lon=round(float(waypoint.lon), 6),
                distanceNm=round(float(distance_nm), 2),
                altitudeFt=(
                    round(float(waypoint.altitude_ft), 1)
                    if waypoint.altitude_ft is not None and waypoint.altitude_ft > 0
                    else None
                ),
                windComponentKt=(
                    round(float(waypoint.wind_component_kt), 1)
                    if waypoint.wind_component_kt is not None
                    else None
                ),
                isaDeviationC=(
                    round(float(waypoint.isa_deviation_c), 1)
                    if waypoint.isa_deviation_c is not None
                    else None
                ),
            )
        )

    if len(segments) <= max_segments:
        return finalize_remaining_route_profile(
            RemainingRouteProfile(
                source=source,
                segments=segments,
            )
        ) or RemainingRouteProfile(source=source)

    merged = _merge_to_budget(segments, max_segments=max_segments)
    return finalize_remaining_route_profile(
        RemainingRouteProfile(
            source=source,
            segments=merged,
        )
    ) or RemainingRouteProfile(source=source)


def _merge_to_budget(
    segments: list[RemainingRouteSegment],
    *,
    max_segments: int,
) -> list[RemainingRouteSegment]:
    bucket_distance = sum(segment.distance_nm for segment in segments) / max_segments
    merged: list[RemainingRouteSegment] = []
    bucket: list[RemainingRouteSegment] = []
    distance = 0.0

    for segment in segments:
        bucket.append(segment)
        distance += segment.distance_nm
        if distance >= bucket_distance:
            merged.append(_merge_bucket(bucket))
            bucket = []
            distance = 0.0

    if bucket:
        merged.append(_merge_bucket(bucket))

    return merged[:max_segments]


def _merge_bucket(bucket: list[RemainingRouteSegment]) -> RemainingRouteSegment:
    distance = sum(segment.distance_nm for segment in bucket)
    tail = bucket[-1]

    return RemainingRouteSegment(
        sequence=bucket[0].sequence,
        ident=tail.ident,
        lat=tail.lat,
        lon=tail.lon,
        distanceNm=round(distance, 2),
        altitudeFt=_rounded_average(bucket, "altitude_ft"),
        windComponentKt=_rounded_average(bucket, "wind_component_kt"),
        isaDeviationC=_rounded_average(bucket, "isa_deviation_c"),
    )


def _rounded_average(bucket: list[RemainingRouteSegment], key: str) -> float | None:
    weighted = _distance_weighted_average(bucket, key)
    return round(weighted, 1) if weighted is not None else None


def _distance_weighted_average(
    bucket: list[RemainingRouteSegment],
    key: str,
) -> float | None:
    total_distance = 0.0
    weighted_sum = 0.0

    for segment in bucket:
        value = getattr(segment, key)
        if value is None:
            continue
        distance = segment.distance_nm
        total_distance += distance
        weighted_sum += value * distance

    if total_distance <= 0:
        return None

    return weighted_sum / total_distance
