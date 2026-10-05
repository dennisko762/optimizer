from __future__ import annotations

from dataclasses import dataclass

from performance_engine.ci_mach.atmosphere import AtmosphereState
from performance_engine.ci_mach.models import FuelModelConfig


@dataclass(frozen=True)
class FuelFlowPoint:
    fuel_flow_kg_s: float
    fuel_flow_kg_h: float
    tsfc_kg_per_n_s: float


class InternalCalibratedFuelModel:
    """
    Calibrated turbofan approximation for steady level cruise.

    The model intentionally exposes its TSFC coefficients in YAML. They are
    calibration parameters, not manufacturer engine-deck values.
    """

    def __init__(self, config: FuelModelConfig) -> None:
        self.config = config

    def fuel_flow(
        self,
        *,
        required_thrust_n: float,
        mach: float,
        atmosphere: AtmosphereState,
        isa_deviation_c: float,
    ) -> FuelFlowPoint:
        mach_factor = 1.0 + self.config.mach_tsfc_factor_per_0_01 * ((mach - 0.84) / 0.01)
        altitude_factor = 1.0 + self.config.altitude_tsfc_factor_per_1000ft * (
            atmosphere.altitude_ft / 1000.0 - 35.0
        )
        temperature_factor = 1.0 + self.config.temperature_tsfc_factor_per_c * isa_deviation_c
        factor = max(
            self.config.min_tsfc_factor,
            min(self.config.max_tsfc_factor, mach_factor * altitude_factor * temperature_factor),
        )
        tsfc = self.config.base_tsfc_kg_per_n_s * factor
        fuel_flow_kg_s = required_thrust_n * tsfc
        return FuelFlowPoint(
            fuel_flow_kg_s=fuel_flow_kg_s,
            fuel_flow_kg_h=fuel_flow_kg_s * 3600.0,
            tsfc_kg_per_n_s=tsfc,
        )
