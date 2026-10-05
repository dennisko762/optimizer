from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


CostIndexUnit = Literal["boeing_ratio", "kg_per_min", "lb_per_hr"]
ConfidenceLevel = Literal["low", "medium", "high"]
FlightPhase = Literal["climb", "cruise", "descent"]


class DragPolarConfig(BaseModel):
    wing_area_m2: float
    cd0: float
    induced_drag_factor: float
    compressibility_mach: float = 0.82
    compressibility_drag_factor: float = 0.04


class FuelModelConfig(BaseModel):
    base_tsfc_kg_per_n_s: float
    mach_tsfc_factor_per_0_01: float = 0.0015
    altitude_tsfc_factor_per_1000ft: float = -0.003
    temperature_tsfc_factor_per_c: float = 0.002
    min_tsfc_factor: float = 0.72
    max_tsfc_factor: float = 1.35


class CostIndexCalibration(BaseModel):
    boeing_ci_to_kg_per_min_scalar: float = 0.0095
    lrc_anchor_ci: float = 180.0
    mrc_anchor_mach: float = 0.826
    lrc_anchor_mach: float = 0.840
    notes: list[str] = Field(default_factory=list)


class AircraftCiMachConfig(BaseModel):
    aircraft_variant: str
    display_name: str
    engine_variants: list[str] = Field(default_factory=list)
    mass_reference_kg: float
    default_cruise_weight_kg: float
    min_mach: float = 0.76
    max_mach: float = 0.89
    mmo: float = 0.89
    drag_polar: DragPolarConfig
    fuel_model: FuelModelConfig
    cost_index_calibration: CostIndexCalibration = Field(default_factory=CostIndexCalibration)
    confidence: ConfidenceLevel = "medium"
    source_notes: list[str] = Field(default_factory=list)


class CiMachConstraints(BaseModel):
    min_mach: float | None = None
    max_mach: float | None = None
    mmo: float | None = None
    thrust_margin_required: float | None = None
    buffet_margin_placeholder: float | None = None


class CiMachRequest(BaseModel):
    aircraft_variant: str
    engine_variant: str | None = None
    cost_index: float
    cost_index_unit: CostIndexUnit = "boeing_ratio"
    gross_weight_kg: float
    pressure_altitude_ft: float | None = None
    flight_level: float | None = None
    outside_air_temperature_c: float | None = None
    isa_deviation_c: float = 0.0
    wind_component_kt: float = 0.0
    remaining_distance_nm: float = 500.0
    live_fuel_flow_kg_h: float | None = None
    fuel_flow_reference_mach: float | None = None
    fuel_flow_reference_altitude_ft: float | None = None
    fuel_flow_reference_gross_weight_kg: float | None = None
    fuel_flow_reference_isa_deviation_c: float | None = None
    require_live_fuel_flow: bool = True
    cruise_cg_percent_mac: float | None = None
    constraints: CiMachConstraints = Field(default_factory=CiMachConstraints)

    @field_validator("cost_index", "gross_weight_kg", "remaining_distance_nm")
    @classmethod
    def _positive_or_zero(cls, value: float) -> float:
        if value < 0:
            raise ValueError("value must be non-negative")
        return value

    @property
    def altitude_ft(self) -> float:
        if self.pressure_altitude_ft is not None:
            return self.pressure_altitude_ft
        if self.flight_level is not None:
            return self.flight_level * 100.0
        raise ValueError("pressure_altitude_ft or flight_level is required")


class CiMachCandidate(BaseModel):
    mach: float
    tas_kt: float
    ground_speed_kt: float
    drag_n: float
    fuel_flow_kg_h: float
    fuel_kg_per_nm: float
    time_min_per_nm: float
    time_cost_equiv_kg_per_nm: float
    total_cost_equiv_kg_per_nm: float
    active_constraints: list[str] = Field(default_factory=list)


class CiMachResult(BaseModel):
    recommended_mach: float
    recommended_tas_kt: float
    ground_speed_kt: float
    fuel_flow_kg_h: float
    fuel_kg_per_nm: float
    time_min_per_nm: float
    total_cost_equiv_kg_per_nm: float
    ci_kg_per_min: float
    active_constraints: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel
    explanation: str
    calibration_source_notes: list[str] = Field(default_factory=list)
    candidates: list[CiMachCandidate] = Field(default_factory=list)


class ProfileUpdateRecommendation(BaseModel):
    recommended_ci_range: tuple[float, float]
    recommended_mach_range: tuple[float, float]
    recommended_cas_range_kt: tuple[float, float] | None = None
    expected_fuel_penalty_kg: float
    expected_time_saving_min: float
    most_effective_phase: FlightPhase
    confidence: ConfidenceLevel
    explanation: str
