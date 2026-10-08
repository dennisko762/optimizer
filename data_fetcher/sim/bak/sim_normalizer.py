from data_fetcher.sim.sim_models import LiveSimState, RawSimState


LB_TO_KG = 0.45359237
DEFAULT_ROUND_DIGITS = 2
POSITION_ROUND_DIGITS = 6


def normalize_raw_sim_state(raw: RawSimState) -> LiveSimState:
    fuel_remaining_lb = _first_not_none(
        raw.fuel_remaining_lb,
        raw.fuel_remaining_lb_ex1,
    )

    return LiveSimState(
        altitude_ft=round_optional(raw.altitude_ft),
        flight_level=altitude_to_flight_level(raw.altitude_ft),

        mach=round_optional(raw.mach),
        true_airspeed_kt=round_optional(raw.true_airspeed_kt),
        ground_speed_kt=round_optional(raw.ground_speed_kt),
        vertical_speed_fpm=round_optional(fps_to_fpm(raw.vertical_speed_fps)),

        gross_weight_kg=round_optional(lb_to_kg(raw.gross_weight_lb)),
        fuel_remaining_kg=round_optional(lb_to_kg(fuel_remaining_lb)),

        latitude=round_optional(raw.latitude, POSITION_ROUND_DIGITS),
        longitude=round_optional(raw.longitude, POSITION_ROUND_DIGITS),

        wind_velocity_kt=round_optional(raw.wind_velocity_kt),
        wind_direction_deg=round_optional(raw.wind_direction_deg),
        ambient_temperature_c=round_optional(raw.ambient_temperature_c),
        isa_deviation_c=round_optional(
            calculate_isa_deviation_c(
                altitude_ft=raw.altitude_ft,
                ambient_temperature_c=raw.ambient_temperature_c,
            )
        ),

        on_ground=raw.on_ground,
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