from __future__ import annotations

from typing import Optional

from .base import FmcDisplayAdapter
from .fmc_types import FmcTelemetrySnapshot
from .inibuilds_a340_adapter import IniBuildsA340McduAdapter
from .pmdg_777_progress_adapter import Pmdg777ProgressAdapter


class FmcAdapterRegistry:
    def __init__(self, adapters: list[FmcDisplayAdapter] | None = None):
        self.adapters = adapters or [
            Pmdg777ProgressAdapter(),
            IniBuildsA340McduAdapter(),
        ]

    def parse_first(self, lines: list[str], cdu_index: int = 0) -> Optional[FmcTelemetrySnapshot]:
        for adapter in self.adapters:
            if adapter.can_parse(lines):
                parsed = adapter.parse(lines, cdu_index=cdu_index)
                if parsed:
                    return parsed

        return None
