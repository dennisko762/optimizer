from __future__ import annotations

from pydantic import BaseModel, Field


class FmcWaypointPredictionData(BaseModel):
    ident: str
    dtg_nm: int | None = Field(default=None, alias="dtgNm")
    eta_zulu: str | None = Field(default=None, alias="etaZulu")
    fuel: float | None = None

    model_config = {
        "populate_by_name": True,
    }


class FmcStepClimbPredictionData(BaseModel):
    time_zulu: str | None = Field(default=None, alias="timeZulu")
    distance_nm: int | None = Field(default=None, alias="distanceNm")

    model_config = {
        "populate_by_name": True,
    }


class FmcTelemetrySnapshotData(BaseModel):
    aircraft: str
    source: str
    cdu_index: int = Field(alias="cduIndex")
    page: str | None = None
    page_index: int | None = Field(default=None, alias="pageIndex")
    page_count: int | None = Field(default=None, alias="pageCount")
    flight_number: str | None = Field(default=None, alias="flightNumber")

    to_waypoint: FmcWaypointPredictionData | None = Field(
        default=None,
        alias="toWaypoint",
    )
    next_waypoint: FmcWaypointPredictionData | None = Field(
        default=None,
        alias="nextWaypoint",
    )
    destination: FmcWaypointPredictionData | None = None

    econ_speed_mach: float | None = Field(default=None, alias="econSpeedMach")
    cost_index: int | None = Field(default=None, alias="costIndex")
    cruise_flight_level: int | None = Field(default=None, alias="cruiseFlightLevel")
    step_climb: FmcStepClimbPredictionData | None = Field(
        default=None,
        alias="stepClimb",
    )

    raw_lines: list[str] = Field(default_factory=list, alias="rawLines")
    adapter_key: str | None = Field(default=None, alias="adapterKey")

    model_config = {
        "populate_by_name": True,
    }

    def destination_distance_nm(self) -> float | None:
        if self.destination is None or self.destination.dtg_nm is None:
            return None
        if self.destination.dtg_nm <= 0:
            return None
        return float(self.destination.dtg_nm)
