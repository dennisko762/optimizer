from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.sim_models import RawSimState


LB_TO_KG = 0.45359237
M_TO_FT = 3.280839895013123
M_TO_NM = 0.0005399568034557235
MPS_TO_KT = 1.9438444924406048
DEFAULT_ROUND_DIGITS = 2
POSITION_ROUND_DIGITS = 6


def normalize_raw_sim_state(raw: RawSimState) -> LiveSimState:
    fuel_remaining_lb = _first_not_none(
        raw.fuel_remaining_lb,
        raw.fuel_remaining_lb_ex1,
    )
    pressure_altitude_ft = m_to_ft(raw.pressure_altitude_m)
    display_altitude_ft = _first_not_none(
        raw.indicated_altitude_ft,
        pressure_altitude_ft,
        raw.true_altitude_ft,
    )
    isa_reference_altitude_ft = _first_not_none(
        pressure_altitude_ft,
        raw.indicated_altitude_ft,
        raw.true_altitude_ft,
    )

    return LiveSimState(
        altitude_ft=round_optional(display_altitude_ft),
        pressure_altitude_ft=round_optional(pressure_altitude_ft),
        true_altitude_ft=round_optional(raw.true_altitude_ft),
        flight_level=altitude_to_flight_level(display_altitude_ft),

        mach=round_optional(raw.mach),
        true_airspeed_kt=round_optional(raw.true_airspeed_kt),
        ground_speed_kt=round_optional(raw.ground_speed_kt),
        vertical_speed_fpm=round_optional(fps_to_fpm(raw.vertical_speed_fps)),

        gross_weight_kg=round_optional(lb_to_kg(raw.gross_weight_lb)),
        fuel_remaining_kg=round_optional(lb_to_kg(fuel_remaining_lb)),
        fuel_flow_kg_h=round_optional(raw.fuel_flow_kg_h),
        fuel_flow_source=raw.fuel_flow_source,

        latitude=round_optional(raw.latitude, POSITION_ROUND_DIGITS),
        longitude=round_optional(raw.longitude, POSITION_ROUND_DIGITS),

        wind_velocity_kt=round_optional(raw.wind_velocity_kt),
        wind_direction_deg=round_optional(raw.wind_direction_deg),
        ambient_temperature_c=round_optional(raw.ambient_temperature_c),
        isa_deviation_c=round_optional(
            calculate_isa_deviation_c(
                altitude_ft=isa_reference_altitude_ft,
                ambient_temperature_c=raw.ambient_temperature_c,
            )
        ),

        on_ground=raw.on_ground,
        wind_x_kt=round_optional(raw.wind_x_kt),
        wind_z_kt=round_optional(raw.wind_z_kt),

        gps_is_active_flight_plan=raw.gps_is_active_flight_plan,
        gps_ete_seconds=round_optional(raw.gps_ete_seconds),
        gps_eta_seconds=round_optional(raw.gps_eta_seconds),
        gps_remaining_distance_nm=round_optional(m_to_nm(raw.gps_target_distance_m)),
        gps_waypoint_distance_nm=round_optional(m_to_nm(raw.gps_wp_distance_m)),
        gps_ground_speed_kt=round_optional(mps_to_kt(raw.gps_ground_speed_m_s)),
    )


def round_optional(value: float | None, digits: int = DEFAULT_ROUND_DIGITS) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def lb_to_kg(value_lb: float | None) -> float | None:
    if value_lb is None:
        return None
    return value_lb * LB_TO_KG


def fps_to_fpm(value_fps: float | None) -> float | None:
    if value_fps is None:
        return None
    return value_fps * 60.0


def m_to_ft(value_m: float | None) -> float | None:
    if value_m is None:
        return None
    return value_m * M_TO_FT


def m_to_nm(value_m: float | None) -> float | None:
    if value_m is None:
        return None
    return value_m * M_TO_NM


def mps_to_kt(value_m_s: float | None) -> float | None:
    if value_m_s is None:
        return None
    return value_m_s * MPS_TO_KT


def altitude_to_flight_level(altitude_ft: float | None) -> int | None:
    if altitude_ft is None:
        return None
    return int(round(altitude_ft / 100.0))


def isa_temperature_c(altitude_ft: float) -> float:
    altitude_m = altitude_ft * 0.3048

    if altitude_m <= 11000:
        return 15.0 - 0.0065 * altitude_m

    return -56.5


def calculate_isa_deviation_c(
    altitude_ft: float | None,
    ambient_temperature_c: float | None,
) -> float | None:
    if altitude_ft is None or ambient_temperature_c is None:
        return None

    return ambient_temperature_c - isa_temperature_c(altitude_ft)


def _first_not_none(*values):
    for value in values:
        if value is not None:
            return value
    return None
