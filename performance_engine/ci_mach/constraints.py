from __future__ import annotations

from performance_engine.ci_mach.models import AircraftCiMachConfig, CiMachConstraints


def resolve_mach_bounds(
    *,
    aircraft_config: AircraftCiMachConfig,
    constraints: CiMachConstraints,
) -> tuple[float, float, list[str]]:
    min_mach = constraints.min_mach if constraints.min_mach is not None else aircraft_config.min_mach
    configured_max = constraints.max_mach if constraints.max_mach is not None else aircraft_config.max_mach
    mmo = constraints.mmo if constraints.mmo is not None else aircraft_config.mmo
    max_mach = min(configured_max, mmo)
    active: list[str] = []
    if max_mach <= configured_max:
        active.append("MMO")
    if constraints.min_mach is not None:
        active.append("requested_min_mach")
    if constraints.max_mach is not None:
        active.append("requested_max_mach")
    if max_mach <= min_mach:
        raise ValueError(f"Invalid Mach bounds: min={min_mach}, max={max_mach}")
    return min_mach, max_mach, active
