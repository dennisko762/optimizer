from __future__ import annotations

from pydantic import BaseModel, Field


class SpeedCandidate(BaseModel):
    mode: str
    cas_kt: float | None = None
    mach: float | None = None
    label: str | None = None


class CruiseStrategy(BaseModel):
    """
    One candidate strategy evaluated by the optimizer.

    cost_index:
        Candidate CI value.

    mach:
        Target Mach associated with this candidate CI.

    Important:
        `cost_index` is not always a real FMC CI:
        - `display_ci` = internal display scaling / derived UI number
        - `fmc_like_ci_estimate` = 777 fallback approximation
        - `calibrated_fmc_ci` = empirical/calibrated FMC mapping
    """

    mach: float
    speed_mode: str | None = None
    cas_kt: float | None = None
    cost_index: int | None = None
    cost_index_source: str | None = None
    cost_index_label: str | None = None
    flight_level: int | None = None
    label: str | None = None
    warnings: list[str] = Field(default_factory=list)
