from __future__ import annotations

from dataclasses import dataclass

from openap import FuelFlow, aero


@dataclass
class RemainingCruiseInput:
    aircraft: str
    altitude_ft: float
    gross_weight_kg: float
    mach: float
    remaining_distance_nm: float
    wind_component_kt: float
    isa_deviation_c: float = 0.0
    segment_distance_nm: float = 50.0


@dataclass
class RemainingCruiseResult:
    aircraft: str
    altitude_ft: float
    mach: float

    initial_weight_kg: float
    end_weight_kg: float

    remaining_distance_nm: float
    remaining_time_min: float
    remaining_fuel_kg: float

    tas_kt: float
    ground_speed_kt: float

    avg_fuel_flow_kg_h: float
    fuel_per_nm_kg: float
    fuel_per_min_kg: float


def simulate_remaining_cruise(request: RemainingCruiseInput) -> RemainingCruiseResult:
    """
    Basic remaining cruise simulation.

    Assumptions:
    - level cruise only
    - constant altitude
    - constant Mach
    - constant wind component
    - no climb/descent/step climb
    - fuel burn updates aircraft mass segment by segment
    """

    fuel_flow_model = FuelFlow(request.aircraft)

    altitude_m = request.altitude_ft * aero.ft
    tas_m_s = aero.mach2tas(
        request.mach,
        altitude_m,
        dT=request.isa_deviation_c,
    )
    tas_kt = tas_m_s / aero.kts

    ground_speed_kt = tas_kt + request.wind_component_kt

    if ground_speed_kt <= 0:
        raise ValueError("Ground speed must be greater than zero.")

    distance_left_nm = request.remaining_distance_nm
    current_weight_kg = request.gross_weight_kg

    total_time_h = 0.0
    total_fuel_kg = 0.0

    while distance_left_nm > 0:
        segment_nm = min(request.segment_distance_nm, distance_left_nm)

        fuel_flow_kg_s = fuel_flow_model.enroute(
            mass=current_weight_kg,
            tas=tas_kt,
            alt=request.altitude_ft,
            vs=0,
            acc=0,
            dT=request.isa_deviation_c,
            limit=True,
        )

        fuel_flow_kg_h = float(fuel_flow_kg_s) * 3600.0

        segment_time_h = segment_nm / ground_speed_kt
        segment_fuel_kg = fuel_flow_kg_h * segment_time_h

        current_weight_kg -= segment_fuel_kg
        total_time_h += segment_time_h
        total_fuel_kg += segment_fuel_kg

        distance_left_nm -= segment_nm

    remaining_time_min = total_time_h * 60.0

    return RemainingCruiseResult(
        aircraft=request.aircraft,
        altitude_ft=round(request.altitude_ft, 2),
        mach=round(request.mach, 3),

        initial_weight_kg=round(request.gross_weight_kg, 2),
        end_weight_kg=round(current_weight_kg, 2),

        remaining_distance_nm=round(request.remaining_distance_nm, 2),
        remaining_time_min=round(remaining_time_min, 2),
        remaining_fuel_kg=round(total_fuel_kg, 2),

        tas_kt=round(tas_kt, 2),
        ground_speed_kt=round(ground_speed_kt, 2),

        avg_fuel_flow_kg_h=round(total_fuel_kg / total_time_h, 2),
        fuel_per_nm_kg=round(total_fuel_kg / request.remaining_distance_nm, 2),
        fuel_per_min_kg=round(total_fuel_kg / remaining_time_min, 2),
    )