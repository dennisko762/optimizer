from __future__ import annotations

from pydantic import BaseModel, Field


class RemainingRouteSegment(BaseModel):
    sequence: int | None = None
    ident: str | None = None
    start_lat: float | None = Field(default=None, alias="startLat")
    start_lon: float | None = Field(default=None, alias="startLon")
    lat: float | None = None
    lon: float | None = None

    distance_nm: float = Field(alias="distanceNm")
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    wind_component_kt: float | None = Field(default=None, alias="windComponentKt")
    isa_deviation_c: float | None = Field(default=None, alias="isaDeviationC")

    model_config = {
        "populate_by_name": True,
    }


class RemainingRouteProfile(BaseModel):
    source: str | None = None
    total_distance_nm: float | None = Field(default=None, alias="totalDistanceNm")
    segment_count: int | None = Field(default=None, alias="segmentCount")
    segments: list[RemainingRouteSegment] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }


def finalize_remaining_route_profile(
    profile: RemainingRouteProfile | None,
) -> RemainingRouteProfile | None:
    if profile is None:
        return None

    segments = _normalized_segments(profile.segments)

    return profile.model_copy(
        update={
            "segments": segments,
            "total_distance_nm": round(sum(segment.distance_nm for segment in segments), 2),
            "segment_count": len(segments),
        }
    )


def clip_remaining_route_profile(
    profile: RemainingRouteProfile | None,
    remaining_distance_nm: float | None,
) -> RemainingRouteProfile | None:
    normalized = finalize_remaining_route_profile(profile)
    if normalized is None:
        return None

    if remaining_distance_nm is None or remaining_distance_nm <= 0:
        return normalized

    if normalized.total_distance_nm is None:
        return normalized

    # Allow a small tolerance for navlog rounding noise.
    if normalized.total_distance_nm <= remaining_distance_nm * 1.03:
        return normalized

    distance_to_drop = normalized.total_distance_nm - remaining_distance_nm
    clipped: list[RemainingRouteSegment] = []

    for segment in normalized.segments:
        distance = max(float(segment.distance_nm), 0.0)
        if distance <= 0:
            continue

        if distance_to_drop >= distance:
            distance_to_drop -= distance
            continue

        kept = segment.model_copy()
        if distance_to_drop > 0:
            kept.distance_nm = round(distance - distance_to_drop, 2)
            distance_to_drop = 0.0
        clipped.append(kept)

    return finalize_remaining_route_profile(
        normalized.model_copy(update={"segments": clipped})
    )


def cruise_segments_from_remaining_route_profile(
    profile: RemainingRouteProfile | None,
    *,
    remaining_distance_nm: float | None = None,
) -> list[dict[str, float]]:
    clipped = clip_remaining_route_profile(profile, remaining_distance_nm)
    if clipped is None:
        return []

    cruise_segments: list[dict[str, float]] = []
    for segment in clipped.segments:
        entry: dict[str, float] = {
            "distanceNm": round(float(segment.distance_nm), 2),
        }
        if segment.altitude_ft is not None:
            entry["altitudeFt"] = round(float(segment.altitude_ft), 1)
        if segment.wind_component_kt is not None:
            entry["windComponentKt"] = round(float(segment.wind_component_kt), 1)
        if segment.isa_deviation_c is not None:
            entry["isaDeviationC"] = round(float(segment.isa_deviation_c), 1)
        cruise_segments.append(entry)

    return cruise_segments


def _normalized_segments(
    segments: list[RemainingRouteSegment],
) -> list[RemainingRouteSegment]:
    normalized: list[RemainingRouteSegment] = []

    for index, segment in enumerate(segments):
        distance = max(float(segment.distance_nm), 0.0)
        if distance <= 0:
            continue

        normalized.append(
            segment.model_copy(
                update={
                    "sequence": segment.sequence if segment.sequence is not None else index + 1,
                    "distance_nm": round(distance, 2),
                }
            )
        )

    return normalized
