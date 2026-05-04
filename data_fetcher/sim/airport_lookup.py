from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path


def lookup_airport_coordinates(code: str | None) -> tuple[float, float] | None:
    normalized = _normalize_code(code)
    if normalized is None:
        return None
    return _airport_coordinates().get(normalized)


@lru_cache(maxsize=1)
def _airport_coordinates() -> dict[str, tuple[float, float]]:
    import openap

    path = Path(openap.__file__).resolve().parent / "data" / "nav" / "airports.csv"
    coordinates: dict[str, tuple[float, float]] = {}

    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = _normalize_code(row.get("icao"))
            lat = _to_float(row.get("lat"))
            lon = _to_float(row.get("lon"))
            if code is None or lat is None or lon is None:
                continue
            coordinates[code] = (lat, lon)

    return coordinates


def _normalize_code(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
