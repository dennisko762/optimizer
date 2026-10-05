from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal, Optional


AircraftSource = Literal[
    "PMDG_SDK",
    "INIBUILDS_MCDU_EXPORT",
    "FBW_SIMBRIDGE",
    "FENIX_WEB_MCDU",
    "UNKNOWN",
]


@dataclass
class WaypointPrediction:
    ident: str
    dtg_nm: Optional[int] = None
    eta_zulu: Optional[str] = None
    fuel: Optional[float] = None


@dataclass
class StepClimbPrediction:
    time_zulu: Optional[str] = None
    distance_nm: Optional[int] = None


@dataclass
class FmcTelemetrySnapshot:
    aircraft: str
    source: AircraftSource
    cdu_index: int
    page: Optional[str] = None
    page_index: Optional[int] = None
    page_count: Optional[int] = None
    flight_number: Optional[str] = None

    to_waypoint: Optional[WaypointPrediction] = None
    next_waypoint: Optional[WaypointPrediction] = None
    destination: Optional[WaypointPrediction] = None

    econ_speed_mach: Optional[float] = None
    cost_index: Optional[int] = None
    cruise_flight_level: Optional[int] = None
    step_climb: Optional[StepClimbPrediction] = None

    raw_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
