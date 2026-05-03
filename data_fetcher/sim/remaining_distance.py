import math

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