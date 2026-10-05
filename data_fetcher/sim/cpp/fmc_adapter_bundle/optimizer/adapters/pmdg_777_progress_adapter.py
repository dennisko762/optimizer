from __future__ import annotations

import re
from typing import Optional

from .base import FmcDisplayAdapter
from .fmc_types import (
    FmcTelemetrySnapshot,
    StepClimbPrediction,
    WaypointPrediction,
)


class Pmdg777ProgressAdapter(FmcDisplayAdapter):
    """
    Parses PMDG 777 CDU PROGRESS page.

    Known example:
        "    KAL902 PROGRESS 1/4"
        " TO      DTG  ETA   FUEL"
        "DINRO    372 2314z  84.9"
        " NEXT"
        "UDROS    469 2325z  83.2"
        " DEST"
        "RKSI    5026 0822z  15.3"
        " ECON SPD    TO STEP CLB"
        ".842        0000z/ 765nm"
    """

    title_re = re.compile(r"^\s*([A-Z0-9]+)\s+PROGRESS\s+(\d+)\/(\d+)", re.IGNORECASE)
    wp_re = re.compile(r"^\s*([A-Z0-9]{2,8})\s+(\d+)\s+(\d{4}z)\s+([0-9.]+)", re.IGNORECASE)
    econ_step_re = re.compile(r"^\s*\.([0-9]{3})\s+(\d{4}z)\/\s*(\d+)nm", re.IGNORECASE)

    def can_parse(self, lines: list[str]) -> bool:
        return bool(lines and self.title_re.search(lines[0]))

    def parse(self, lines: list[str], cdu_index: int = 0) -> Optional[FmcTelemetrySnapshot]:
        if len(lines) < 9:
            return None

        title = self.title_re.search(lines[0])
        if not title:
            return None

        snapshot = FmcTelemetrySnapshot(
            aircraft="PMDG_777",
            source="PMDG_SDK",
            cdu_index=cdu_index,
            page="PROGRESS",
            flight_number=title.group(1),
            page_index=int(title.group(2)),
            page_count=int(title.group(3)),
            raw_lines=lines,
        )

        to_match = self.wp_re.search(lines[2])
        if to_match:
            snapshot.to_waypoint = WaypointPrediction(
                ident=to_match.group(1),
                dtg_nm=int(to_match.group(2)),
                eta_zulu=to_match.group(3),
                fuel=float(to_match.group(4)),
            )

        next_match = self.wp_re.search(lines[4])
        if next_match:
            snapshot.next_waypoint = WaypointPrediction(
                ident=next_match.group(1),
                dtg_nm=int(next_match.group(2)),
                eta_zulu=next_match.group(3),
                fuel=float(next_match.group(4)),
            )

        dest_match = self.wp_re.search(lines[6])
        if dest_match:
            snapshot.destination = WaypointPrediction(
                ident=dest_match.group(1),
                dtg_nm=int(dest_match.group(2)),
                eta_zulu=dest_match.group(3),
                fuel=float(dest_match.group(4)),
            )

        econ_match = self.econ_step_re.search(lines[8])
        if econ_match:
            snapshot.econ_speed_mach = float(f"0.{econ_match.group(1)}")
            snapshot.step_climb = StepClimbPrediction(
                time_zulu=econ_match.group(2),
                distance_nm=int(econ_match.group(3)),
            )

        return snapshot
