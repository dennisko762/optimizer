from pydantic import BaseModel


class SimBriefPerformanceSeed(BaseModel):
    aircraft: str | None = None
    aircraft_registration: str | None = None

    flight_number: str | None = None
    callsign: str | None = None
    airline_icao: str | None = None
    airline_iata: str | None = None

    origin: str | None = None
    destination: str | None = None
    alternate: str | None = None

    destination_lat: float | None = None
    destination_lon: float | None = None

    route_distance_nm: float | None = None

    planned_block_time_min: float | None = None
    planned_cruise_fl: int | None = None
    planned_cruise_altitude_ft: int | None = None
    planned_mach: float | None = None
    cost_index: float | None = None

    tow_kg: float | None = None
    zfw_kg: float | None = None
    landing_weight_kg: float | None = None

    block_fuel_kg: float | None = None
    trip_fuel_kg: float | None = None
    reserve_fuel_kg: float | None = None

    pax_count: int | None = None
    cargo_kg: float | None = None

    # OFP wind and atmosphere — parsed from SimBrief P/M notation
    # "P017" → +17.0 kt (tailwind), "M025" → -25.0 kt (headwind)
    sibt_utc: str | None = None
    sobt_utc: str | None = None

    planned_wind_component_kt: float | None = None
    # "M02" → -2.0 °C (below ISA), "P03" → +3.0 °C (above ISA)
    planned_isa_deviation_c: float | None = None
    # Average wind: "231 / 15" → stored as direction_deg and speed_kt
    planned_avg_wind_direction_deg: float | None = None
    planned_avg_wind_speed_kt: float | None = None