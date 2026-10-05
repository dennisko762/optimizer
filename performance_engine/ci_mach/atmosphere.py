from __future__ import annotations

from dataclasses import dataclass
import math

from performance_engine.ci_mach.units import ft_to_m, mps_to_kt

GAMMA_AIR = 1.4
R_AIR_J_KG_K = 287.05287
SEA_LEVEL_TEMP_K = 288.15
SEA_LEVEL_PRESSURE_PA = 101325.0
LAPSE_RATE_K_PER_M = -0.0065
TROPOPAUSE_M = 11000.0
G0_MPS2 = 9.80665


@dataclass(frozen=True)
class AtmosphereState:
    altitude_ft: float
    temperature_k: float
    pressure_pa: float
    density_kg_m3: float
    speed_of_sound_mps: float

    @property
    def temperature_c(self) -> float:
        return self.temperature_k - 273.15

    @property
    def speed_of_sound_kt(self) -> float:
        return mps_to_kt(self.speed_of_sound_mps)


def isa_temperature_k(altitude_ft: float) -> float:
    altitude_m = ft_to_m(max(altitude_ft, 0.0))
    if altitude_m <= TROPOPAUSE_M:
        return SEA_LEVEL_TEMP_K + LAPSE_RATE_K_PER_M * altitude_m
    return 216.65


def isa_pressure_pa(altitude_ft: float) -> float:
    altitude_m = ft_to_m(max(altitude_ft, 0.0))
    if altitude_m <= TROPOPAUSE_M:
        theta = isa_temperature_k(altitude_ft) / SEA_LEVEL_TEMP_K
        exponent = -G0_MPS2 / (LAPSE_RATE_K_PER_M * R_AIR_J_KG_K)
        return SEA_LEVEL_PRESSURE_PA * theta**exponent

    pressure_tropopause = isa_pressure_pa(TROPOPAUSE_M / 0.3048)
    return pressure_tropopause * math.exp(
        -G0_MPS2 * (altitude_m - TROPOPAUSE_M) / (R_AIR_J_KG_K * 216.65)
    )


def atmosphere_at(
    altitude_ft: float,
    *,
    outside_air_temperature_c: float | None = None,
    isa_deviation_c: float = 0.0,
) -> AtmosphereState:
    isa_temp_k = isa_temperature_k(altitude_ft)
    temperature_k = (
        outside_air_temperature_c + 273.15
        if outside_air_temperature_c is not None
        else isa_temp_k + isa_deviation_c
    )
    pressure_pa = isa_pressure_pa(altitude_ft)
    density_kg_m3 = pressure_pa / (R_AIR_J_KG_K * temperature_k)
    speed_of_sound_mps = math.sqrt(GAMMA_AIR * R_AIR_J_KG_K * temperature_k)
    return AtmosphereState(
        altitude_ft=altitude_ft,
        temperature_k=temperature_k,
        pressure_pa=pressure_pa,
        density_kg_m3=density_kg_m3,
        speed_of_sound_mps=speed_of_sound_mps,
    )


def mach_to_tas_kt(
    mach: float,
    altitude_ft: float,
    *,
    outside_air_temperature_c: float | None = None,
    isa_deviation_c: float = 0.0,
) -> float:
    atm = atmosphere_at(
        altitude_ft,
        outside_air_temperature_c=outside_air_temperature_c,
        isa_deviation_c=isa_deviation_c,
    )
    return mach * atm.speed_of_sound_kt
