from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TrajectoryPhase(str, Enum):
    COMPLETE = "complete"
    MULTIPHASE = "multiphase"
    CRUISE = "cruise"
    CLIMB = "climb"
    DESCENT = "descent"


class WindGridPoint(BaseModel):
    ts: float
    latitude: float
    longitude: float
    h: float
    u: float
    v: float


class CostGridPoint(BaseModel):
    latitude: float
    longitude: float
    height: float
    cost: float
    ts: float | None = None


class TrajectoryOptimizeRequest(BaseModel):
    aircraft: str
    origin: str
    destination: str
    m0: float = Field(description="Initial mass as kg or fraction of MTOW, matching opentop.")
    objective: str | list[str] = "fuel"
    phase: TrajectoryPhase = TrajectoryPhase.COMPLETE
    engine: str | None = None

    wind_grid: list[WindGridPoint] = Field(default_factory=list, alias="windGrid")
    cost_grid: list[CostGridPoint] = Field(default_factory=list, alias="costGrid")
    cost_grid_dimensions: int = Field(default=3, alias="costGridDimensions")
    fuel_weight: float = Field(default=1.0, alias="fuelWeight")
    grid_weight: float = Field(default=1.0, alias="gridWeight")

    multi_start: bool = Field(default=False, alias="multiStart")
    n_starts: int = Field(default=3, alias="nStarts")
    max_fuel_kg: float | None = Field(default=None, alias="maxFuelKg")

    fix_cruise_altitude: bool = Field(default=False, alias="fixCruiseAltitude")
    fix_mach_number: bool = Field(default=False, alias="fixMachNumber")
    fix_track_angle: bool = Field(default=False, alias="fixTrackAngle")
    max_samples: int = Field(default=240, alias="maxSamples")

    model_config = {
        "populate_by_name": True,
    }


class TrajectoryPoint(BaseModel):
    ts: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    altitude_ft: float | None = Field(default=None, alias="altitudeFt")
    mach: float | None = None
    tas_kt: float | None = Field(default=None, alias="tasKt")
    vertical_rate_fpm: float | None = Field(default=None, alias="verticalRateFpm")
    heading_deg: float | None = Field(default=None, alias="headingDeg")
    mass_kg: float | None = Field(default=None, alias="massKg")
    fuel_flow_kg_s: float | None = Field(default=None, alias="fuelFlowKgS")
    fuel_cost_kg: float | None = Field(default=None, alias="fuelCostKg")
    grid_cost: float | None = Field(default=None, alias="gridCost")

    model_config = {
        "populate_by_name": True,
    }


class TrajectorySummary(BaseModel):
    phase: TrajectoryPhase
    objective: str
    fuel_kg: float | None = Field(default=None, alias="fuelKg")
    flight_time_min: float | None = Field(default=None, alias="flightTimeMin")
    max_altitude_ft: float | None = Field(default=None, alias="maxAltitudeFt")
    final_mass_kg: float | None = Field(default=None, alias="finalMassKg")
    solver_status: str | None = Field(default=None, alias="solverStatus")
    iteration_count: int | None = Field(default=None, alias="iterationCount")
    objective_value: float | None = Field(default=None, alias="objectiveValue")

    model_config = {
        "populate_by_name": True,
    }


class TrajectoryOptimizeResponse(BaseModel):
    source: str = "opentop"
    summary: TrajectorySummary
    points: list[TrajectoryPoint]
    warnings: list[str] = Field(default_factory=list)

    model_config = {
        "populate_by_name": True,
    }
