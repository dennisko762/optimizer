from __future__ import annotations

from data_fetcher.simbrief.simbrief_models import SimBriefRouteWaypoint


def build_cruise_segments_from_waypoints(
    waypoints: list[SimBriefRouteWaypoint],
    *,
    max_segments: int = 120,
) -> list[dict[str, float]]:
    segments: list[dict[str, float]] = []

    for waypoint in waypoints[1:]:
        distance_nm = waypoint.distance_from_previous_nm
        if distance_nm is None or distance_nm <= 0:
            continue

        segment: dict[str, float] = {"distanceNm": round(float(distance_nm), 2)}

        if waypoint.altitude_ft is not None and waypoint.altitude_ft > 0:
            segment["altitudeFt"] = round(float(waypoint.altitude_ft), 1)
        if waypoint.wind_component_kt is not None:
            segment["windComponentKt"] = round(float(waypoint.wind_component_kt), 1)
        if waypoint.isa_deviation_c is not None:
            segment["isaDeviationC"] = round(float(waypoint.isa_deviation_c), 1)

        segments.append(segment)

    if len(segments) <= max_segments:
        return segments

    return _merge_to_budget(segments, max_segments=max_segments)


def _merge_to_budget(segments: list[dict[str, float]], *, max_segments: int) -> list[dict[str, float]]:
    bucket_distance = sum(segment["distanceNm"] for segment in segments) / max_segments
    merged: list[dict[str, float]] = []
    bucket: list[dict[str, float]] = []
    distance = 0.0

    for segment in segments:
        bucket.append(segment)
        distance += segment["distanceNm"]
        if distance >= bucket_distance:
            merged.append(_merge_bucket(bucket))
            bucket = []
            distance = 0.0

    if bucket:
        merged.append(_merge_bucket(bucket))

    return merged[:max_segments]


def _merge_bucket(bucket: list[dict[str, float]]) -> dict[str, float]:
    distance = sum(segment["distanceNm"] for segment in bucket)
    merged: dict[str, float] = {"distanceNm": round(distance, 2)}

    for source_key, target_key in (
        ("altitudeFt", "altitudeFt"),
        ("windComponentKt", "windComponentKt"),
        ("isaDeviationC", "isaDeviationC"),
    ):
        weighted = _distance_weighted_average(bucket, source_key)
        if weighted is not None:
            merged[target_key] = round(weighted, 1)

    return merged


def _distance_weighted_average(bucket: list[dict[str, float]], key: str) -> float | None:
    total_distance = 0.0
    weighted_sum = 0.0

    for segment in bucket:
        value = segment.get(key)
        if value is None:
            continue
        distance = segment["distanceNm"]
        total_distance += distance
        weighted_sum += value * distance

    if total_distance <= 0:
        return None

    return weighted_sum / total_distance
