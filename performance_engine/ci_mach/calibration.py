from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from performance_engine.ci_mach.models import AircraftCiMachConfig

DATA_DIR = Path(__file__).resolve().parents[1] / "aircraft_data" / "b777"


def load_aircraft_ci_mach_config(aircraft_variant: str) -> AircraftCiMachConfig:
    key = aircraft_variant.strip().lower()
    path = DATA_DIR / f"{key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"B777 CI/Mach aircraft data not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        raw: dict[str, Any] = yaml.safe_load(file) or {}
    return AircraftCiMachConfig.model_validate(raw)


def available_b777_ci_mach_variants() -> list[str]:
    return sorted(path.stem.upper() for path in DATA_DIR.glob("*.yaml"))
