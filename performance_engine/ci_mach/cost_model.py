from __future__ import annotations

from performance_engine.ci_mach.models import AircraftCiMachConfig, CostIndexUnit
from performance_engine.ci_mach.units import lb_per_h_to_kg_per_min


class CostIndexConverter:
    def __init__(self, aircraft_config: AircraftCiMachConfig) -> None:
        self.aircraft_config = aircraft_config

    def to_kg_per_min(self, cost_index: float, unit: CostIndexUnit) -> float:
        if unit == "kg_per_min":
            return cost_index
        if unit == "lb_per_hr":
            return lb_per_h_to_kg_per_min(cost_index)
        if unit == "boeing_ratio":
            scalar = self.aircraft_config.cost_index_calibration.boeing_ci_to_kg_per_min_scalar
            return cost_index * scalar
        raise ValueError(f"Unsupported cost index unit: {unit}")
