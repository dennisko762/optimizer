from __future__ import annotations

import math

from pydantic import BaseModel


class RawSimState(BaseModel):
    aircraft_title: str | None = None

    # Cockpit/altimeter indication from INDICATED ALTITUDE (ft).
    indicated_altitude_ft: float | None = None
    # Geometric/true altitude from PLANE ALTITUDE (ft MSL).
    true_altitude_ft: float | None = None
    # Standard altitude from PRESSURE ALTITUDE (meters at 1013.25 hPa).
    pressure_altitude_m: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    mach: float | None = None
    true_airspeed_kt: float | None = None
    ground_speed_kt: float | None = None

    # MSFS SDK: VERTICAL SPEED is feet per second
    vertical_speed_fps: float | None = None

    # MSFS SimVars return these in pounds
    gross_weight_lb: float | None = None
    fuel_remaining_lb: float | None = None
    fuel_remaining_lb_ex1: float | None = None

    wind_velocity_kt: float | None = None
    wind_direction_deg: float | None = None
    ambient_temperature_c: float | None = None

    # AIRCRAFT WIND X: longitudinal wind component (positive = tailwind).
    wind_x_kt: float | None = None

    on_ground: bool | None = None

    gps_is_active_flight_plan: bool | None = None
    gps_ete_seconds: float | None = None
    gps_eta_seconds: float | None = None
    gps_target_distance_m: float | None = None
    gps_wp_distance_m: float | None = None
    gps_ground_speed_m_s: float | None = None


class LiveSimState(BaseModel):
    # Primary altitude shown to the UI and passed into optimization.
    # This prefers the aircraft's indicated altitude so the displayed FL
    # matches what the crew sees in the cockpit.
    altitude_ft: float | None = None
    # Pressure altitude in feet, kept for ISA/performance debug.
    pressure_altitude_ft: float | None = None
    # Geometric/true altitude in feet, kept only for debug/inspection.
    true_altitude_ft: float | None = None
    flight_level: int | None = None

    mach: float | None = None
    true_airspeed_kt: float | None = None
    ground_speed_kt: float | None = None
    vertical_speed_fpm: float | None = None

    gross_weight_kg: float | None = None
    fuel_remaining_kg: float | None = None

    latitude: float | None = None
    longitude: float | None = None

    wind_velocity_kt: float | None = None
    wind_direction_deg: float | None = None
    ambient_temperature_c: float | None = None
    isa_deviation_c: float | None = None

    on_ground: bool | None = None

    # Wind component along aircraft track (positive = tailwind).
    # Populated from AIRCRAFT_WIND_X SimVar when available.
    wind_x_kt: float | None = None

    gps_is_active_flight_plan: bool | None = None
    gps_ete_seconds: float | None = None
    gps_eta_seconds: float | None = None
    gps_remaining_distance_nm: float | None = None
    gps_waypoint_distance_nm: float | None = None
    gps_ground_speed_kt: float | None = None

    def get_remaining_distance_nm(
        self,
        *,
        destination_lat: float | None,
        destination_lon: float | None,
    ) -> float | None:
        """
        Great-circle distance from current position to destination in NM.

        Returns None if any coordinate is missing.
        """
        if (
            self.latitude is None
            or self.longitude is None
            or destination_lat is None
            or destination_lon is None
        ):
            return None

        return _haversine_nm(
            lat1=self.latitude,
            lon1=self.longitude,
            lat2=destination_lat,
            lon2=destination_lon,
        )

    def wind_component_along_track(
        self,
        *,
        track_deg_true: float | None = None,
    ) -> float | None:
        """
        Wind component along the flight path (tailwind positive, headwind negative).

        Prefers AIRCRAFT_WIND_X SimVar (direct longitudinal component from MSFS).
        Falls back to trigonometric calculation from wind velocity/direction and
        the provided true track if both are available.

        Returns None if insufficient data.
        """
        # MSFS AIRCRAFT WIND X = longitudinal axis, positive = tailwind.
        if self.wind_x_kt is not None:
            return self.wind_x_kt

        if (
            self.wind_velocity_kt is not None
            and self.wind_direction_deg is not None
            and track_deg_true is not None
        ):
            # Wind direction is the direction wind comes FROM (met convention).
            # Component along track = velocity * cos(wind_dir_from - track)
            wind_from_rad = math.radians(self.wind_direction_deg)
            track_rad = math.radians(track_deg_true)
            return self.wind_velocity_kt * math.cos(wind_from_rad - track_rad)

        return None


def _haversine_nm(
    *,
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in NM (WGS-84 mean radius)."""
    R_NM = 3440.065

    lat1_r, lon1_r, lat2_r, lon2_r = (
        math.radians(lat1),
        math.radians(lon1),
        math.radians(lat2),
        math.radians(lon2),
    )

    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r

    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2.0) ** 2
    )

    return R_NM * 2.0 * math.asin(math.sqrt(a))


class CurrentFlightState(BaseModel):
    aircraft: str
    engine_variant: str | None = None

    altitude_ft: float
    gross_weight_kg: float
    mach: float

    remaining_distance_nm: float
    route_distance_nm: float | None = None
    wind_component_kt: float
    isa_deviation_c: float = 0.0

    fuel_remaining_kg: float | None = None
    ground_speed_kt: float | None = None

    # Current cost index selected in FMC / SimBrief / test UI.
    # This must NOT be derived from Mach.
    current_cost_index: int | None = None

    # Total passengers on board (from SimBrief OFP or manual entry).
    total_pax: int | None = None
