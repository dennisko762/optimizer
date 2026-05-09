from __future__ import annotations

from dataclasses import dataclass

from performance_engine.ci_mach.atmosphere import AtmosphereState
from performance_engine.ci_mach.models import DragPolarConfig
from performance_engine.ci_mach.units import G0_MPS2


@dataclass(frozen=True)
class AeroPoint:
    lift_coefficient: float
    drag_coefficient: float
    drag_n: float
    dynamic_pressure_pa: float


def cruise_drag(
    *,
    mach: float,
    tas_mps: float,
    gross_weight_kg: float,
    atmosphere: AtmosphereState,
    config: DragPolarConfig,
) -> AeroPoint:
    dynamic_pressure_pa = 0.5 * atmosphere.density_kg_m3 * tas_mps**2
    lift_n = gross_weight_kg * G0_MPS2
    cl = lift_n / (dynamic_pressure_pa * config.wing_area_m2)
    mach_excess = max(0.0, mach - config.compressibility_mach)
    compressibility_cd = config.compressibility_drag_factor * mach_excess**2
    cd = config.cd0 + config.induced_drag_factor * cl**2 + compressibility_cd
    drag_n = cd * dynamic_pressure_pa * config.wing_area_m2
    return AeroPoint(
        lift_coefficient=cl,
        drag_coefficient=cd,
        drag_n=drag_n,
        dynamic_pressure_pa=dynamic_pressure_pa,
    )
