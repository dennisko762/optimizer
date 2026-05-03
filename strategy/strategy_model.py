from __future__ import annotations

from pydantic import BaseModel


class CruiseStrategy(BaseModel):
    """
    One candidate strategy evaluated by the optimizer.

    cost_index:
        Candidate CI value.

    mach:
        Target Mach associated with this candidate CI.

    Important:
        In the current MVP this is still an approximate CI -> Mach mapping.
        Later this should be replaced with aircraft-specific ECON speed tables.
    """

    mach: float
    cost_index: int | None = None
    label: str | None = None