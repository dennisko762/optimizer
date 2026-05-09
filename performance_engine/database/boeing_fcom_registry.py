from __future__ import annotations

from collections.abc import Callable
from typing import Any, Mapping

from performance_engine.database.boeing_fcom_model import BoeingFcomPerformance
from performance_engine.database.boeing_777_200er_ge90_94b import (
    build_b772_ge90_94b_faa,
)
from performance_engine.database.boeing_777_bada import build_b77w_ge90_115bl_jaa


_VARIANT_BUILDERS: dict[str, Callable[[], BoeingFcomPerformance]] = {
    "b772_ge90_94b_faa": build_b772_ge90_94b_faa,
    "b77w_ge90_115bl_jaa": build_b77w_ge90_115bl_jaa,
}


_AIRCRAFT_ALIASES = {
    "777300ER": "B77W",
    "B773ER": "B77W",
    "B77W": "B77W",
    "777200ER": "B772",
    "B772": "B772",
    "777200LR": "B77L",
    "B77L": "B77L",
    "777F": "B77F",
    "B77F": "B77F",
}


_ENGINE_ALIASES = {
    "GE90": "GE90",
    "GE90115BL": "GE90-115BL",
    "GE90110B1": "GE90-110B1",
    "GE9090B": "GE90-90B",
    "GE9092B": "GE90-92B",
    "GE9094B": "GE90-94B",
    "GE9098B": "GE90-98B",
    "PW4000": "PW4000",
    "PW4000112": "PW4000-112",
    "PW4084": "PW4084",
    "PW4090": "PW4090",
    "PW4098": "PW4098",
    "TRENT800": "TRENT800",
    "TRENT884": "TRENT884",
    "TRENT890": "TRENT890",
    "TRENT895": "TRENT895",
    "TRENT898": "TRENT898",
}


def get_boeing_fcom_performance(variant_key: str) -> BoeingFcomPerformance:
    builder = _VARIANT_BUILDERS.get(str(variant_key).strip().lower())
    if builder is None:
        raise KeyError(f"Unsupported Boeing FCOM variant: {variant_key}")
    return builder()


def resolve_boeing_777_fcom_variant(
    *,
    request_aircraft: str | None,
    aircraft_cfg: Mapping[str, Any] | None = None,
    engine_variant: str | None = None,
) -> str | None:
    cfg = aircraft_cfg or {}

    requested_engine = normalize_boeing_engine_variant(engine_variant)

    explicit_variant = _string_or_none(
        _nested_value(cfg, "performance", "source", "fcom_variant")
        or _nested_value(cfg, "performance", "source", "fcomVariant")
    )
    if explicit_variant is not None and requested_engine is None:
        key = explicit_variant.lower()
        if key in _VARIANT_BUILDERS:
            return key

    aircraft = normalize_boeing_777_aircraft(
        request_aircraft
        or _string_or_none(cfg.get("simbrief_code"))
        or _string_or_none(_nested_value(cfg, "aircraft", "icao_type"))
        or _string_or_none(cfg.get("aircraft_type"))
    )
    engine = requested_engine or normalize_boeing_engine_variant(
        _string_or_none(_nested_value(cfg, "engine", "model"))
        or _string_or_none(_nested_value(cfg, "engine", "variant_key"))
        or _string_or_none(cfg.get("engine_variant"))
    )

    if aircraft == "B77W" and engine in {None, "GE90-115BL"}:
        return "b77w_ge90_115bl_jaa"
    if aircraft == "B772" and engine in {None, "GE90-94B"}:
        return "b772_ge90_94b_faa"

    return None


def supported_boeing_fcom_variants() -> list[str]:
    return sorted(_VARIANT_BUILDERS)


def normalize_boeing_777_aircraft(value: str | None) -> str | None:
    text = _normalize_token(value)
    if text is None:
        return None
    return _AIRCRAFT_ALIASES.get(text, text)


def normalize_boeing_engine_variant(value: str | None) -> str | None:
    text = _normalize_token(value)
    if text is None:
        return None
    return _ENGINE_ALIASES.get(text, text)


def _normalize_token(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    return (
        text.replace("-", "")
        .replace("_", "")
        .replace("/", "")
        .replace(" ", "")
    )


def _nested_value(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
