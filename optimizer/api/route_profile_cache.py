from __future__ import annotations

from copy import deepcopy
from threading import Lock


_lock = Lock()
_latest_segments: list[dict[str, float]] = []


def set_latest_cruise_segments(segments: list[dict[str, float]]) -> None:
    global _latest_segments
    with _lock:
        _latest_segments = deepcopy(segments)


def get_latest_cruise_segments(*, remaining_distance_nm: float | None = None) -> list[dict[str, float]]:
    with _lock:
        segments = deepcopy(_latest_segments)

    if remaining_distance_nm is None or remaining_distance_nm <= 0:
        return segments

    return _clip_to_remaining_distance(segments, remaining_distance_nm)


def _clip_to_remaining_distance(
    segments: list[dict[str, float]],
    remaining_distance_nm: float,
) -> list[dict[str, float]]:
    total_distance = sum(max(float(segment.get("distanceNm") or 0.0), 0.0) for segment in segments)
    if total_distance <= remaining_distance_nm * 1.03:
        return segments

    distance_to_drop = total_distance - remaining_distance_nm
    clipped: list[dict[str, float]] = []

    for segment in segments:
        distance = max(float(segment.get("distanceNm") or 0.0), 0.0)
        if distance <= 0:
            continue

        if distance_to_drop >= distance:
            distance_to_drop -= distance
            continue

        kept = deepcopy(segment)
        if distance_to_drop > 0:
            kept["distanceNm"] = round(distance - distance_to_drop, 2)
            distance_to_drop = 0.0
        clipped.append(kept)

    return clipped
