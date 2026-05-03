from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _get_airports_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "optimizer" / "configs" / "airports"
    return Path(__file__).resolve().parent / "configs" / "airports"


AIRPORTS_DIR = _get_airports_dir()
_FALLBACK_KEY = "_FALLBACK"
_cache: dict[str, "AirportConfig"] = {}


@dataclass
class TaxiTimes:
    out_p50_min: float = 14.0
    out_p75_min: float = 20.0
    in_p50_min: float = 9.0
    in_p75_min: float = 13.0
    source: str = "fallback"


@dataclass
class MCT:
    dd_min: int = 30
    di_min: int = 45
    id_min: int = 45
    ii_min: int = 45
    cross_terminal_ii_min: int | None = None
    schengen_applies: bool = False
    notes: str = ""
    source: str = ""


@dataclass
class NightCurfew:
    restricted: bool = False
    banned: bool = False
    start_local: str | None = None
    end_local: str | None = None
    timezone: str | None = None
    notes: str = ""


@dataclass
class AirportConfig:
    icao: str
    iata: str | None
    name: str
    city: str | None
    country: str | None
    continent: str | None
    category: str = "medium"
    taxi: TaxiTimes = field(default_factory=TaxiTimes)
    mct: MCT = field(default_factory=MCT)
    night_curfew: NightCurfew = field(default_factory=NightCurfew)
    slot_coordinated: bool = False
    slot_level: int = 0
    is_fallback: bool = False


def load_airport(icao: str) -> AirportConfig:
    """
    Return the AirportConfig for *icao* (case-insensitive).
    Falls back to a generic median profile when no specific config exists.
    Results are cached per process.
    """
    key = icao.upper().strip()
    if key in _cache:
        return _cache[key]

    path = AIRPORTS_DIR / f"{key}.yaml"
    if not path.exists():
        cfg = _get_fallback()
        _cache[key] = cfg
        return cfg

    raw = _load_yaml(path)
    cfg = _parse(raw, is_fallback=False)
    _cache[key] = cfg
    return cfg


def get_taxi_out_min(icao: str, *, simbrief_taxi_out_min: float | None = None) -> float:
    """
    Return taxi-out time in minutes.
    SimBrief value takes priority; falls back to airport p50 median.
    """
    if simbrief_taxi_out_min is not None and simbrief_taxi_out_min > 0:
        return simbrief_taxi_out_min
    return load_airport(icao).taxi.out_p50_min


def get_taxi_in_min(icao: str, *, simbrief_taxi_in_min: float | None = None) -> float:
    """
    Return taxi-in time in minutes.
    SimBrief value takes priority; falls back to airport p50 median.
    """
    if simbrief_taxi_in_min is not None and simbrief_taxi_in_min > 0:
        return simbrief_taxi_in_min
    return load_airport(icao).taxi.in_p50_min


def get_mct_min(
    icao: str,
    *,
    inbound_is_international: bool = True,
    outbound_is_international: bool = True,
    cross_terminal: bool = False,
) -> int:
    """
    Return minimum connection time in minutes for the given connection type.
    """
    mct = load_airport(icao).mct

    if cross_terminal and mct.cross_terminal_ii_min is not None:
        return mct.cross_terminal_ii_min

    if inbound_is_international and outbound_is_international:
        return mct.ii_min
    elif inbound_is_international:
        return mct.id_min
    elif outbound_is_international:
        return mct.di_min
    else:
        return mct.dd_min


def has_night_curfew(icao: str) -> bool:
    return load_airport(icao).night_curfew.restricted or load_airport(icao).night_curfew.banned


def _get_fallback() -> AirportConfig:
    key = _FALLBACK_KEY
    if key in _cache:
        return _cache[key]
    path = AIRPORTS_DIR / f"{key}.yaml"
    if path.exists():
        raw = _load_yaml(path)
        cfg = _parse(raw, is_fallback=True)
    else:
        cfg = AirportConfig(
            icao=_FALLBACK_KEY,
            iata=None,
            name="Generic Fallback",
            city=None,
            country=None,
            continent=None,
            is_fallback=True,
        )
    _cache[key] = cfg
    return cfg


def _parse(raw: dict[str, Any], *, is_fallback: bool) -> AirportConfig:
    taxi_raw = raw.get("taxi") or {}
    mct_raw = raw.get("mct") or {}
    curfew_raw = raw.get("night_curfew") or {}

    taxi = TaxiTimes(
        out_p50_min=float(taxi_raw.get("out_p50_min", 14)),
        out_p75_min=float(taxi_raw.get("out_p75_min", 20)),
        in_p50_min=float(taxi_raw.get("in_p50_min", 9)),
        in_p75_min=float(taxi_raw.get("in_p75_min", 13)),
        source=str(taxi_raw.get("source", "")),
    )

    mct = MCT(
        dd_min=int(mct_raw.get("dd_min", 30)),
        di_min=int(mct_raw.get("di_min", 45)),
        id_min=int(mct_raw.get("id_min", 45)),
        ii_min=int(mct_raw.get("ii_min", 45)),
        cross_terminal_ii_min=(
            int(mct_raw["cross_terminal_ii_min"])
            if "cross_terminal_ii_min" in mct_raw
            else None
        ),
        schengen_applies=bool(mct_raw.get("schengen_applies", False)),
        notes=str(mct_raw.get("notes", "")),
        source=str(mct_raw.get("source", "")),
    )

    night_curfew = NightCurfew(
        restricted=bool(curfew_raw.get("restricted", False)),
        banned=bool(curfew_raw.get("banned", False)),
        start_local=curfew_raw.get("start_local"),
        end_local=curfew_raw.get("end_local"),
        timezone=curfew_raw.get("timezone"),
        notes=str(curfew_raw.get("notes", "")),
    )

    return AirportConfig(
        icao=str(raw.get("icao", _FALLBACK_KEY)),
        iata=raw.get("iata"),
        name=str(raw.get("name", "")),
        city=raw.get("city"),
        country=raw.get("country"),
        continent=raw.get("continent"),
        category=str(raw.get("category", "medium")),
        taxi=taxi,
        mct=mct,
        night_curfew=night_curfew,
        slot_coordinated=bool(raw.get("slot_coordinated", False)),
        slot_level=int(raw.get("slot_level", 0)),
        is_fallback=is_fallback,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Airport config must be a YAML mapping: {path}")
    return data
