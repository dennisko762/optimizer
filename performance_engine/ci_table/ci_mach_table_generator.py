from __future__ import annotations

from collections.abc import Mapping

from performance_engine.ci_table.ci_mach_table_model import (
    CiMachBand,
    CiMachTable,
    CiMachTableKey,
)
from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_registry import (
    get_boeing_fcom_performance,
    resolve_boeing_777_fcom_variant,
)
from performance_engine.remaining_cruise_simulator import (
    RemainingCruiseInput,
    simulate_remaining_cruise,
)
from performance_engine.speed_envelope import cas_to_mach, max_mach_at_altitude


def generate_ci_mach_table(
    *,
    aircraft: str,
    engine_variant: str | None,
    gross_weight_kg: float,
    flight_level: int,
    isa_deviation_c: float,
    cg_percent_mac: float | None,
    wind_component_kt: float,
    aircraft_cfg: Mapping[str, object],
    general_cfg: Mapping[str, object],
    include_recovery: bool = False,
    reference_distance_nm: float = 1000.0,
) -> CiMachTable:
    if flight_level <= 0:
        raise ValueError("flight_level must be greater than zero.")
    if gross_weight_kg <= 0:
        raise ValueError("gross_weight_kg must be greater than zero.")
    if reference_distance_nm <= 0:
        raise ValueError("reference_distance_nm must be greater than zero.")

    aircraft_cfg_dict = dict(aircraft_cfg or {})
    general_cfg_dict = dict(general_cfg or {})
    warnings: list[str] = []

    performance_source = _resolve_performance_source(aircraft_cfg_dict)
    altitude_ft = float(flight_level * 100)
    lrc_anchor_mach = _resolve_lrc_anchor_mach(
        aircraft=aircraft,
        engine_variant=engine_variant,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg_dict,
        warnings=warnings,
    )
    lrc_anchor_kias = _resolve_lrc_anchor_kias(
        aircraft=aircraft,
        engine_variant=engine_variant,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        aircraft_cfg=aircraft_cfg_dict,
    )
    mach_candidates = _build_mach_candidates(
        aircraft_cfg=aircraft_cfg_dict,
        altitude_ft=altitude_ft,
        include_recovery=include_recovery,
        lrc_anchor_mach=lrc_anchor_mach,
        lrc_anchor_kias=lrc_anchor_kias,
    )

    mach_to_time_fuel: dict[float, tuple[float, float]] = {}
    results_by_mach: dict[float, object] = {}

    for mach in mach_candidates:
        result = simulate_remaining_cruise(
            RemainingCruiseInput(
                aircraft=aircraft,
                engine_variant=engine_variant,
                altitude_ft=altitude_ft,
                gross_weight_kg=gross_weight_kg,
                mach=mach,
                remaining_distance_nm=reference_distance_nm,
                wind_component_kt=wind_component_kt,
                isa_deviation_c=isa_deviation_c,
            ),
            aircraft_cfg=aircraft_cfg_dict,
            general_cfg=general_cfg_dict,
        )
        rounded_mach = round(mach, 3)
        results_by_mach[rounded_mach] = result
        mach_to_time_fuel[rounded_mach] = (
            float(result.remaining_time_min),
            float(result.remaining_fuel_kg),
        )
        _extend_unique(warnings, result.warnings)

    from performance_engine.ci_profile import derive_cost_index_profile

    profile = derive_cost_index_profile(
        mach_to_time_fuel=mach_to_time_fuel,
        general_cfg=general_cfg_dict,
        aircraft_cfg=aircraft_cfg_dict,
    )
    _extend_unique(warnings, profile.warnings)

    bands: list[CiMachBand] = []
    for mach in sorted(profile.by_mach):
        profile_entry = profile.by_mach[mach]
        result = results_by_mach.get(round(mach, 3))
        if result is None:
            continue

        band_warnings = list(result.warnings)
        if lrc_anchor_mach is not None and abs(round(mach, 3) - round(lrc_anchor_mach, 3)) < 0.0005:
            band_warnings.append(
                f"Reference LRC Mach M{lrc_anchor_mach:.3f} from Boeing FCOM lookup is included in this table."
            )

        bands.append(
            CiMachBand(
                lower_ci=int(profile_entry.lower_bound_cost_index),
                upper_ci=(
                    int(profile_entry.upper_bound_cost_index)
                    if profile_entry.upper_bound_cost_index is not None
                    else None
                ),
                mach=round(mach, 3),
                fuel_kg_per_h=round(float(result.avg_fuel_flow_kg_h), 2),
                time_min_per_1000nm=round(
                    float(result.remaining_time_min) * (1000.0 / max(float(result.remaining_distance_nm), 1e-9)),
                    2,
                ),
                fuel_per_nm_kg=round(float(result.fuel_per_nm_kg), 4),
                economic_ci_kg_per_min=round(float(profile_entry.economic_ci_kg_per_min), 4),
                source=str(result.performance_model),
                warnings=band_warnings,
            )
        )

    key = CiMachTableKey(
        aircraft=str(aircraft).upper(),
        engine_variant=engine_variant,
        gross_weight_kg=round(float(gross_weight_kg), 2),
        flight_level=int(flight_level),
        isa_deviation_c=round(float(isa_deviation_c), 2),
        cg_percent_mac=round(float(cg_percent_mac), 2) if cg_percent_mac is not None else None,
        wind_component_kt=round(float(wind_component_kt), 2),
        performance_source=performance_source,
    )

    return CiMachTable(
        key=key,
        bands=bands,
        warnings=warnings,
    )


def _resolve_performance_source(aircraft_cfg: Mapping[str, object]) -> str:
    performance = aircraft_cfg.get("performance") or {}
    if not isinstance(performance, Mapping):
        return "openap"
    source = performance.get("source") or {}
    if not isinstance(source, Mapping):
        return "openap"
    primary = source.get("primary")
    return str(primary).strip() if primary is not None else "openap"


def _resolve_lrc_anchor_mach(
    *,
    aircraft: str,
    engine_variant: str | None,
    gross_weight_kg: float,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, object],
    warnings: list[str],
) -> float | None:
    if _resolve_performance_source(aircraft_cfg).lower() != "boeing_fcom_hybrid":
        return None

    variant = resolve_boeing_777_fcom_variant(
        request_aircraft=aircraft,
        aircraft_cfg=aircraft_cfg,
        engine_variant=engine_variant,
    )
    if variant is None:
        warnings.append(
            "Performance-derived CI/Mach table could not resolve a Boeing FCOM variant; using generic Mach candidates only."
        )
        return None

    performance = get_boeing_fcom_performance(variant)
    query = query_lrc_cruise(
        performance,
        weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
    )
    _extend_unique(warnings, query.notes)
    warnings.append(
        f"Reference LRC Mach M{query.mach:.3f} from Boeing FCOM lookup is used as the hybrid anchor."
    )
    return round(float(query.mach), 3)


def _resolve_lrc_anchor_kias(
    *,
    aircraft: str,
    engine_variant: str | None,
    gross_weight_kg: float,
    altitude_ft: float,
    aircraft_cfg: Mapping[str, object],
) -> float | None:
    if _resolve_performance_source(aircraft_cfg).lower() != "boeing_fcom_hybrid":
        return None

    variant = resolve_boeing_777_fcom_variant(
        request_aircraft=aircraft,
        aircraft_cfg=aircraft_cfg,
        engine_variant=engine_variant,
    )
    if variant is None:
        return None

    performance = get_boeing_fcom_performance(variant)
    query = query_lrc_cruise(
        performance,
        weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
    )
    return float(query.kias)


def _build_mach_candidates(
    *,
    aircraft_cfg: Mapping[str, object],
    altitude_ft: float,
    include_recovery: bool,
    lrc_anchor_mach: float | None,
    lrc_anchor_kias: float | None,
) -> list[float]:
    if altitude_ft / 100.0 < _speed_mode_crossover_fl(aircraft_cfg):
        return _build_low_altitude_mach_candidates(
            aircraft_cfg=aircraft_cfg,
            altitude_ft=altitude_ft,
            include_recovery=include_recovery,
            lrc_anchor_mach=lrc_anchor_mach,
            lrc_anchor_kias=lrc_anchor_kias,
        )

    overrides = _table_generation_overrides(aircraft_cfg)
    normal_min = float(overrides.get("normal_min_mach", _default_normal_min_mach(aircraft_cfg)))
    normal_max = float(overrides.get("normal_max_mach", _default_normal_max_mach(aircraft_cfg)))
    mach_step = float(overrides.get("mach_step", 0.005))

    candidates: set[float] = set()
    current = round(normal_min, 3)
    while current <= normal_max + 1e-9:
        candidates.add(round(current, 3))
        current = round(current + mach_step, 6)

    if include_recovery:
        for recovery_mach in overrides.get("recovery_mach_candidates", [0.850, 0.855, 0.860]):
            candidates.add(round(float(recovery_mach), 3))

    if lrc_anchor_mach is not None:
        candidates.add(round(float(lrc_anchor_mach), 3))

    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    recovery_max = _float_or_none(cruise.get("recovery_max_mach"))
    normal_limit = _float_or_none(cruise.get("normal_max_mach"))

    filtered: list[float] = []
    for mach in sorted(candidates):
        if normal_limit is not None and not include_recovery and mach > normal_limit + 1e-9:
            continue
        if recovery_max is not None and include_recovery and mach > recovery_max + 1e-9:
            continue
        filtered.append(round(mach, 3))

    return filtered


def _build_low_altitude_mach_candidates(
    *,
    aircraft_cfg: Mapping[str, object],
    altitude_ft: float,
    include_recovery: bool,
    lrc_anchor_mach: float | None,
    lrc_anchor_kias: float | None,
) -> list[float]:
    envelope = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("speed_envelope"))
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)
    max_allowed = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)

    mach_values: set[float] = set()
    if lrc_anchor_kias is not None and lrc_anchor_kias > 0:
        cas_values = [
            lrc_anchor_kias - 20.0,
            lrc_anchor_kias - 10.0,
            lrc_anchor_kias,
            lrc_anchor_kias + 10.0,
            lrc_anchor_kias + 20.0,
        ]
        if include_recovery:
            cas_values.extend([lrc_anchor_kias + 30.0, lrc_anchor_kias + 40.0])

        for cas_kt in cas_values:
            if cas_kt <= 0 or cas_kt > vmo_kt + 1e-9:
                continue
            mach_values.add(round(cas_to_mach(cas_kt, altitude_ft), 3))

    if lrc_anchor_mach is not None:
        mach_values.add(round(float(lrc_anchor_mach), 3))

    return sorted(
        mach for mach in mach_values
        if mach > 0 and mach <= max_allowed + 1e-9
    )


def _table_generation_overrides(aircraft_cfg: Mapping[str, object]) -> Mapping[str, object]:
    mapping = _mapping_or_empty(aircraft_cfg.get("cost_index_mapping"))
    return _mapping_or_empty(mapping.get("performance_derived_table"))


def _default_normal_min_mach(aircraft_cfg: Mapping[str, object]) -> float:
    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    configured = _float_or_none(cruise.get("normal_min_mach"))
    if configured is None:
        return 0.805
    return max(0.805, configured)


def _default_normal_max_mach(aircraft_cfg: Mapping[str, object]) -> float:
    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    configured = _float_or_none(cruise.get("normal_max_mach"))
    if configured is None:
        return 0.845
    return min(0.845, configured)


def _speed_mode_crossover_fl(aircraft_cfg: Mapping[str, object]) -> float:
    cruise = _mapping_or_empty((_mapping_or_empty(aircraft_cfg.get("performance"))).get("cruise"))
    raw = cruise.get("cas_mach_crossover_fl")
    if raw is None:
        return 280.0
    return float(raw)


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extend_unique(target: list[str], items: list[str]) -> None:
    for item in items:
        text = str(item).strip()
        if text and text not in target:
            target.append(text)
