from pathlib import Path
from typing import Any

import yaml


CONFIG_DIR = Path(__file__).resolve().parent / "configs"


def load_general_config() -> dict[str, Any]:
    path = CONFIG_DIR / "general.yaml"

    if not path.exists():
        raise FileNotFoundError(f"General config not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_aircraft_config(aircraft_key: str) -> dict[str, Any]:
    path = CONFIG_DIR / "aircraft" / f"{aircraft_key}.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Aircraft config not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)