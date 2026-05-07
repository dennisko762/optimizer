from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .fmc_types import FmcTelemetrySnapshot


class FmcDisplayAdapter(ABC):
    @abstractmethod
    def can_parse(self, lines: list[str]) -> bool:
        pass

    @abstractmethod
    def parse(self, lines: list[str], cdu_index: int = 0) -> Optional[FmcTelemetrySnapshot]:
        pass
