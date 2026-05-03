from pydantic import BaseModel


class RawSimState(BaseModel):
    aircraft_title: str | None = None

    altitude_ft: float | None = None
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

    on_ground: bool | None = None


class LiveSimState(BaseModel):
    altitude_ft: float | None = None
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


class CurrentFlightState(BaseModel):
    aircraft: str

    altitude_ft: float
    gross_weight_kg: float
    mach: float

    remaining_distance_nm: float
    wind_component_kt: float
    isa_deviation_c: float = 0.0

    fuel_remaining_kg: float | None = None
    ground_speed_kt: float | None = None

    # Current cost index selected in FMC / SimBrief / test UI.
    # This must NOT be derived from Mach.
    current_cost_index: int | None = None

    total_pax: int = 0
    """
    Total passengers on board.
    Sourced from SimBrief OFP (weights.pax_count) via the sync endpoint.
    Used by the IROPs cost model for soft cost calculation (€/pax/min).
    """