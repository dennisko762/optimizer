import math

from optimizer.route_profile_models import (
    RemainingRouteProfile,
    RemainingRouteSegment,
    finalize_remaining_route_profile,
)

EARTH_RADIUS_NM = 3440.065


def great_circle_distance_nm(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    lat1 = math.radians(lat1_deg)
    lon1 = math.radians(lon1_deg)
    lat2 = math.radians(lat2_deg)
    lon2 = math.radians(lon2_deg)

    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_NM * c


def calculate_direct_remaining_distance_nm(
    current_lat: float | None,
    current_lon: float | None,
    destination_lat: float | None,
    destination_lon: float | None,
) -> float | None:
    if (
        current_lat is None
        or current_lon is None
        or destination_lat is None
        or destination_lon is None
    ):
        return None

    return round(
        great_circle_distance_nm(
            current_lat,
            current_lon,
            destination_lat,
            destination_lon,
        ),
        2,
    )


def initial_bearing_deg(
    lat1_deg: float,
    lon1_deg: float,
    lat2_deg: float,
    lon2_deg: float,
) -> float:
    """
    Bearing/track from current position to destination.
    Returns degrees true, 0..360.
    """
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    d_lon = math.radians(lon2_deg - lon1_deg)

    x = math.sin(d_lon) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    )

    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def calculate_wind_component_kt(
    wind_velocity_kt: float | None,
    wind_direction_deg: float | None,
    track_deg: float | None,
) -> float | None:
    """
    Positive = tailwind
    Negative = headwind

    wind_direction_deg is the direction the wind comes FROM.
    track_deg is aircraft track TO destination.
    """
    if wind_velocity_kt is None or wind_direction_deg is None or track_deg is None:
        return None

    angle_rad = math.radians(wind_direction_deg - track_deg)

    # If wind comes from same direction as track -> headwind -> negative
    # If wind comes from opposite direction -> tailwind -> positive
    return round(-wind_velocity_kt * math.cos(angle_rad), 2)


def calculate_direct_track_to_destination_deg(
    current_lat: float | None,
    current_lon: float | None,
    destination_lat: float | None,
    destination_lon: float | None,
) -> float | None:
    if (
        current_lat is None
        or current_lon is None
        or destination_lat is None
        or destination_lon is None
    ):
        return None

    return round(
        initial_bearing_deg(
            current_lat,
            current_lon,
            destination_lat,
            destination_lon,
        ),
        2,
    )


def calculate_route_remaining_distance_nm(
    current_lat: float | None,
    current_lon: float | None,
    route_profile: RemainingRouteProfile | None,
) -> float | None:
    estimate = estimate_route_remaining_distance(
        current_lat=current_lat,
        current_lon=current_lon,
        route_profile=route_profile,
    )
    if estimate is None:
        return None
    return float(estimate["remainingDistanceNm"])


def estimate_route_remaining_distance(
    *,
    current_lat: float | None,
    current_lon: float | None,
    route_profile: RemainingRouteProfile | None,
) -> dict[str, float | int | str | None] | None:
    if current_lat is None or current_lon is None:
        return None

    normalized = finalize_remaining_route_profile(route_profile)
    if normalized is None or not normalized.segments:
        return None

    best_candidate: dict[str, float | int | str | None] | None = None

    for index, segment in enumerate(normalized.segments):
        end_lat = segment.lat
        end_lon = segment.lon
        if end_lat is None or end_lon is None:
            continue

        start_lat, start_lon = _segment_start_coordinates(normalized.segments, index)
        if start_lat is not None and start_lon is not None:
            deviation_nm = _distance_to_segment_nm(
                current_lat=current_lat,
                current_lon=current_lon,
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
            )
        else:
            deviation_nm = great_circle_distance_nm(
                current_lat,
                current_lon,
                end_lat,
                end_lon,
            )

        distance_to_waypoint_nm = great_circle_distance_nm(
            current_lat,
            current_lon,
            end_lat,
            end_lon,
        )
        downstream_nm = sum(
            max(float(next_segment.distance_nm), 0.0)
            for next_segment in normalized.segments[index + 1 :]
        )
        remaining_distance_nm = distance_to_waypoint_nm + downstream_nm

        candidate = {
            "remainingDistanceNm": round(remaining_distance_nm, 2),
            "distanceToNextWaypointNm": round(distance_to_waypoint_nm, 2),
            "activeSegmentIndex": index,
            "activeWaypointIdent": segment.ident,
            "segmentDeviationNm": round(deviation_nm, 2),
        }

        if best_candidate is None:
            best_candidate = candidate
            continue

        candidate_key = (
            float(candidate["segmentDeviationNm"]),
            float(candidate["remainingDistanceNm"]),
            int(candidate["activeSegmentIndex"]),
        )
        best_key = (
            float(best_candidate["segmentDeviationNm"]),
            float(best_candidate["remainingDistanceNm"]),
            int(best_candidate["activeSegmentIndex"]),
        )
        if candidate_key < best_key:
            best_candidate = candidate

    return best_candidate


def _segment_start_coordinates(
    segments: list[RemainingRouteSegment],
    index: int,
) -> tuple[float | None, float | None]:
    segment = segments[index]
    if segment.start_lat is not None and segment.start_lon is not None:
        return segment.start_lat, segment.start_lon

    if index > 0:
        previous = segments[index - 1]
        if previous.lat is not None and previous.lon is not None:
            return previous.lat, previous.lon

    return None, None


def _distance_to_segment_nm(
    *,
    current_lat: float,
    current_lon: float,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> float:
    segment_length_nm = great_circle_distance_nm(
        start_lat,
        start_lon,
        end_lat,
        end_lon,
    )
    if segment_length_nm <= 0.1:
        return great_circle_distance_nm(
            current_lat,
            current_lon,
            end_lat,
            end_lon,
        )

    along_track_nm, cross_track_nm = _along_and_cross_track_nm(
        current_lat=current_lat,
        current_lon=current_lon,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
    )

    if along_track_nm < 0:
        return great_circle_distance_nm(
            current_lat,
            current_lon,
            start_lat,
            start_lon,
        )
    if along_track_nm > segment_length_nm:
        return great_circle_distance_nm(
            current_lat,
            current_lon,
            end_lat,
            end_lon,
        )

    return abs(cross_track_nm)


def _along_and_cross_track_nm(
    *,
    current_lat: float,
    current_lon: float,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> tuple[float, float]:
    distance_start_to_current_nm = great_circle_distance_nm(
        start_lat,
        start_lon,
        current_lat,
        current_lon,
    )
    if distance_start_to_current_nm <= 0:
        return 0.0, 0.0

    theta_13 = math.radians(
        initial_bearing_deg(
            start_lat,
            start_lon,
            current_lat,
            current_lon,
        )
    )
    theta_12 = math.radians(
        initial_bearing_deg(
            start_lat,
            start_lon,
            end_lat,
            end_lon,
        )
    )
    delta_13 = distance_start_to_current_nm / EARTH_RADIUS_NM
    delta_xt = math.asin(
        _clamp_unit_interval(
            math.sin(delta_13) * math.sin(theta_13 - theta_12)
        )
    )
    delta_at = math.atan2(
        math.sin(delta_13) * math.cos(theta_13 - theta_12),
        math.cos(delta_13),
    )
    return delta_at * EARTH_RADIUS_NM, delta_xt * EARTH_RADIUS_NM


def _clamp_unit_interval(value: float) -> float:
    return max(-1.0, min(1.0, value))
