from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CiMachTableKey:
    aircraft: str
    engine_variant: str | None
    gross_weight_kg: float
    flight_level: int
    isa_deviation_c: float
    cg_percent_mac: float | None
    wind_component_kt: float
    performance_source: str


@dataclass(frozen=True)
class CiMachBand:
    lower_ci: int
    upper_ci: int | None
    mach: float
    fuel_kg_per_h: float
    time_min_per_1000nm: float
    fuel_per_nm_kg: float
    economic_ci_kg_per_min: float
    source: str
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CiMachTable:
    key: CiMachTableKey
    bands: list[CiMachBand]
    warnings: list[str] = field(default_factory=list)
