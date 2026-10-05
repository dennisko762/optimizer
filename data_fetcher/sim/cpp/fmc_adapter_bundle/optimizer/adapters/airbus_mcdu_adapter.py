from __future__ import annotations

import re
from typing import Optional

from .base import FmcDisplayAdapter
from .fmc_types import FmcTelemetrySnapshot, WaypointPrediction


class AirbusMcduAdapter(FmcDisplayAdapter):
    """
    Generic Airbus MCDU text-grid parser.

    This is intentionally conservative. It can consume lines extracted from:
      - iniBuilds external MCDU export
      - FBW SimBridge remote MCDU
      - Fenix Web MCDU, if you extract text lines from the web payload

    It does not know how to fetch the lines. It only parses already-extracted text.
    """

    page_title_re = re.compile(
        r"(PROG|PROGRESS|INIT|F-PLN|PERF|DIR|RAD NAV|SEC F-PLN)",
        re.IGNORECASE,
    )
    fl_re = re.compile(r"\bFL\s?(\d{2,3})\b", re.IGNORECASE)
    ci_re = re.compile(r"\bCI\s*[:=]?\s*(\d{1,3})\b", re.IGNORECASE)
    icao_re = re.compile(r"\b[A-Z]{4}\b")

    def __init__(self, aircraft: str, source: str):
        self.aircraft = aircraft
        self.source = source

    def can_parse(self, lines: list[str]) -> bool:
        joined = "\n".join(lines)
        return bool(self.page_title_re.search(joined))

    def parse(self, lines: list[str], cdu_index: int = 0) -> Optional[FmcTelemetrySnapshot]:
        joined = "\n".join(lines)
        title = self.page_title_re.search(joined)

        if not title:
            return None

        snapshot = FmcTelemetrySnapshot(
            aircraft=self.aircraft,
            source=self.source,  # type: ignore[arg-type]
            cdu_index=cdu_index,
            page=title.group(1).upper(),
            raw_lines=lines,
        )

        ci = self.ci_re.search(joined)
        if ci:
            snapshot.cost_index = int(ci.group(1))

        fl = self.fl_re.search(joined)
        if fl:
            snapshot.cruise_flight_level = int(fl.group(1))

        icaos = self.icao_re.findall(joined)
        if len(icaos) >= 2:
            snapshot.destination = WaypointPrediction(ident=icaos[1])
        elif len(icaos) == 1:
            snapshot.destination = WaypointPrediction(ident=icaos[0])

        return snapshot
