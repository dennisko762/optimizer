from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

import yaml


def _get_config_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "optimizer" / "configs"
    return Path(__file__).resolve().parent / "configs"


CONFIG_DIR = _get_config_dir()


def load_general_config() -> dict[str, Any]:
    path = CONFIG_DIR / "general.yaml"

    if not path.exists():
        raise FileNotFoundError(f"General config not found: {path}")

    data = _load_yaml(path)
    return data


def load_aircraft_config(aircraft_key: str) -> dict[str, Any]:
    """
    Load an aircraft/add-on YAML profile.

    The returned dict is backwards compatible with the existing cost engine while
    also normalizing the new generic performance-profile structure used by the
    remaining cruise simulator.
    """

    path = CONFIG_DIR / "aircraft" / f"{aircraft_key}.yaml"

    if not path.exists():
        raise FileNotFoundError(f"Aircraft config not found: {path}")

    raw = _load_yaml(path)
    return normalize_aircraft_config(raw, aircraft_key=aircraft_key)


def normalize_aircraft_config(raw: Mapping[str, Any], *, aircraft_key: str | None = None) -> dict[str, Any]:
    """
    Normalize old and new YAML formats into one generic aircraft profile.

    This keeps current code working:
      aircraft_cfg["performance"]["min_ci"]
      aircraft_cfg["certification"]["mtow_kg"]
      aircraft_cfg["ff"]

    while also guaranteeing the newer structure:
      aircraft.key
      aircraft.icao_type
      performance.source.primary
      performance.source.openap_aircraft
      performance.cruise
      performance.calibration
      cost_index_mapping
      data_quality
    """

    cfg: dict[str, Any] = dict(raw or {})

    aircraft = _ensure_dict(cfg, "aircraft")
    aircraft.setdefault("key", aircraft_key or cfg.get("aircraft_key") or cfg.get("simbrief_code") or cfg.get("aircraft_type"))
    aircraft.setdefault("display_name", cfg.get("display_name") or cfg.get("aircraft_type") or aircraft.get("key"))
    aircraft.setdefault("icao_type", cfg.get("simbrief_code") or cfg.get("icao_type") or cfg.get("aircraft_type"))
    aircraft.setdefault("family", cfg.get("family"))
    aircraft.setdefault("addon", cfg.get("addon"))

    openap = _ensure_dict(cfg, "openap")
    openap.setdefault("enabled", True)
    openap.setdefault(
        "aircraft_type",
        _nested_get(cfg, "performance", "source", "openap_aircraft")
        or cfg.get("openap_aircraft")
        or cfg.get("simbrief_code")
        or aircraft.get("icao_type")
        or cfg.get("aircraft_type"),
    )

    performance = _ensure_dict(cfg, "performance")

    # Preserve old CI keys but add sane defaults if absent.
    performance.setdefault("min_ci", _nested_get(cfg, "cost_index_mapping", "min_ci") or 0)
    performance.setdefault("max_ci", _nested_get(cfg, "cost_index_mapping", "max_ci") or 999)
    performance.setdefault("ci_step", 5)
    performance.setdefault("ci_scale_factor", cfg.get("ci_factors", 1.0))

    source = _ensure_dict(performance, "source")
    source.setdefault("primary", "openap")
    source.setdefault("openap_aircraft", openap.get("aircraft_type"))

    cruise = _ensure_dict(performance, "cruise")
    if cfg.get("cruise_mach") is not None:
        cruise.setdefault("reference_mach", cfg.get("cruise_mach"))
    cruise.setdefault("default_segment_distance_nm", 50.0)

    calibration = _ensure_dict(performance, "calibration")
    calibration.setdefault("fuel_flow_multiplier", 1.0)

    cost_index_mapping = _ensure_dict(cfg, "cost_index_mapping")
    cost_index_mapping.setdefault("min_ci", performance.get("min_ci", 0))
    cost_index_mapping.setdefault("max_ci", performance.get("max_ci", 999))
    cost_index_mapping.setdefault("kg_per_min_to_ci_factor", performance.get("ci_scale_factor", 1.0))

    data_quality = _ensure_dict(cfg, "data_quality")
    if "support_status" in cfg and isinstance(cfg["support_status"], Mapping):
        estimated = bool(cfg["support_status"].get("estimated", True))
        data_quality.setdefault("level", "estimated" if estimated else "curated")
    else:
        data_quality.setdefault("level", "estimated")

    data_quality.setdefault("notes", [])

    return cfg


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {path}")

    return data


def _ensure_dict(mapping: dict[str, Any], key: str) -> dict[str, Any]:
    value = mapping.get(key)
    if not isinstance(value, dict):
        value = {}
        mapping[key] = value
    return value


def _nested_get(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current
