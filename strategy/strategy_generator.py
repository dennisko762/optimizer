from __future__ import annotations

from typing import Any

from performance_engine.boeing_777_fmc_ci_speed_model import (
    COST_INDEX_SOURCE_AIRBUS_FAMILY,
    COST_INDEX_SOURCE_CALIBRATED,
    COST_INDEX_SOURCE_CURRENT_INPUT,
    COST_INDEX_SOURCE_FMC_LIKE,
    OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
    OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
    OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    SPEED_MODE_CAS,
    cost_index_source_label,
    estimate_fmc_ci_speed_target,
    resolve_cost_index_optimizer_mode,
)
from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_registry import (
    get_boeing_fcom_performance,
    resolve_boeing_777_fcom_variant,
)
from performance_engine.speed_envelope import cas_to_mach, mach_to_cas, max_mach_at_altitude
from strategy.strategy_model import CruiseStrategy, SpeedCandidate


def generate_flight_level_candidates(
    *,
    current_altitude_ft: float,
    remaining_distance_nm: float,
    fixed_flight_level: int | None = None,
    assigned_flight_level: int | None = None,
    fmc_source: str | None = None,
    fmc_cruise_flight_level: int | None = None,
    fmc_step_climb_distance_nm: float | None = None,
    min_flight_level: int = 200,
    max_flight_level: int = 430,
) -> list[int]:
    current_flight_level = max(
        min(int(round(current_altitude_ft / 100.0)), max_flight_level),
        min_flight_level,
    )

    constrained_target = fixed_flight_level or assigned_flight_level
    if constrained_target is not None:
        candidates = [current_flight_level, int(constrained_target)]
        return _normalized_flight_levels(
            candidates,
            min_flight_level=min_flight_level,
            max_flight_level=max_flight_level,
        )

    candidates = [current_flight_level]
    step_climb_distance_nm = _positive_float_or_none(fmc_step_climb_distance_nm)

    # Cruise-level search is intentionally conservative until climb/descent
    # transition costs and step points are modeled more explicitly.
    if remaining_distance_nm >= 250 or (
        step_climb_distance_nm is not None
        and step_climb_distance_nm <= 60.0
        and remaining_distance_nm >= 100
    ):
        candidates.extend(
            [
                current_flight_level + 20,
                current_flight_level + 40,
            ]
        )

    return _apply_fmc_level_guidance(
        candidates=candidates,
        current_flight_level=current_flight_level,
        remaining_distance_nm=remaining_distance_nm,
        fmc_source=fmc_source,
        fmc_cruise_flight_level=fmc_cruise_flight_level,
        fmc_step_climb_distance_nm=step_climb_distance_nm,
        min_flight_level=min_flight_level,
        max_flight_level=max_flight_level,
    )


def _apply_fmc_level_guidance(
    *,
    candidates: list[int],
    current_flight_level: int,
    remaining_distance_nm: float,
    fmc_source: str | None,
    fmc_cruise_flight_level: int | None,
    fmc_step_climb_distance_nm: float | None,
    min_flight_level: int,
    max_flight_level: int,
) -> list[int]:
    guidance_active = bool((fmc_source or "").strip()) or (
        fmc_cruise_flight_level is not None or fmc_step_climb_distance_nm is not None
    )
    if not guidance_active:
        return _normalized_flight_levels(
            candidates,
            min_flight_level=min_flight_level,
            max_flight_level=max_flight_level,
        )

    guided = list(candidates)
    cruise_flight_level = _bounded_flight_level(
        fmc_cruise_flight_level,
        min_flight_level=min_flight_level,
        max_flight_level=max_flight_level,
    )

    if cruise_flight_level is not None and cruise_flight_level > current_flight_level:
        guided.append(min(current_flight_level + 20, cruise_flight_level))
        if remaining_distance_nm >= 350 or (
            fmc_step_climb_distance_nm is not None
            and fmc_step_climb_distance_nm <= 40.0
        ):
            guided.append(min(current_flight_level + 40, cruise_flight_level))
        guided = [level for level in guided if level <= cruise_flight_level]

    if fmc_step_climb_distance_nm is not None:
        if fmc_step_climb_distance_nm > 120.0:
            guided = [level for level in guided if level <= current_flight_level]
        elif fmc_step_climb_distance_nm > 40.0:
            guided = [level for level in guided if level <= current_flight_level + 20]

    return _normalized_flight_levels(
        guided,
        min_flight_level=min_flight_level,
        max_flight_level=max_flight_level,
    )


def _positive_float_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number <= 0:
        return None
    return number


def _bounded_flight_level(
    value: int | None,
    *,
    min_flight_level: int,
    max_flight_level: int,
) -> int | None:
    if value is None:
        return None
    flight_level = int(value)
    if flight_level < min_flight_level or flight_level > max_flight_level:
        return None
    return flight_level


def generate_mach_strategies(
    *,
    current_mach: float,
    current_cost_index: int | None = None,
    min_mach: float | None = None,
    max_mach: float | None = None,
    step: float = 0.005,
    allow_speed_up: bool = True,
    allow_slow_down: bool = True,
    min_ci: int = 0,
    max_ci: int = 100,
    ci_step: int = 5,
    preserve_current_strategy: bool = True,
    mark_current_label: bool = True,
    ensure_current_mach_present: bool = True,
) -> list[CruiseStrategy]:
    """
    Generates candidate cruise strategies.

    Candidate generation is Mach-based.

    Important:
    - `step` now controls the Mach search grid directly.
    - Numeric CI values are derived later from the simulated performance curve.
      This avoids fabricating CI values before fuel/time have been evaluated.
    """

    if current_mach <= 0:
        raise ValueError("current_mach must be greater than 0")

    if step <= 0:
        raise ValueError("step must be greater than 0")

    search_min_mach = min_mach
    search_max_mach = max_mach

    if search_min_mach is None:
        search_min_mach = current_mach - 0.03 if allow_slow_down else current_mach

    if search_max_mach is None:
        search_max_mach = current_mach + 0.04 if allow_speed_up else current_mach

    search_min_mach = max(0.65, search_min_mach)
    search_max_mach = min(0.89, search_max_mach)

    if not allow_slow_down:
        search_min_mach = max(search_min_mach, current_mach)

    if not allow_speed_up:
        search_max_mach = min(search_max_mach, current_mach)

    if search_min_mach > search_max_mach:
        fallback_mach = round(current_mach, 3) if ensure_current_mach_present else round(search_max_mach, 3)
        search_min_mach = search_max_mach = fallback_mach

    mach_values = _generate_mach_values(
        search_min_mach=search_min_mach,
        search_max_mach=search_max_mach,
        step=step,
        current_mach=current_mach,
        ensure_current_mach_present=ensure_current_mach_present,
    )

    strategies: list[CruiseStrategy] = []

    for mach in mach_values:
        label = _label_for_candidate(
            mach=mach,
            current_mach=current_mach,
            mark_current_label=mark_current_label,
        )

        strategies.append(
            CruiseStrategy(
                mach=mach,
                cost_index=None,
                label=label,
            )
        )

    if preserve_current_strategy and current_cost_index is not None:
        strategies = _replace_or_add_current_strategy(
            strategies=strategies,
            current_mach=current_mach,
            current_cost_index=current_cost_index,
        )
    elif preserve_current_strategy:
        strategies = _replace_or_add_current_strategy(
            strategies=strategies,
            current_mach=current_mach,
            current_cost_index=None,
        )
    else:
        strategies = _ensure_current_mach_present(
            strategies=strategies,
            current_mach=current_mach,
            mark_current_label=mark_current_label,
        )

    return _dedupe_and_sort(strategies)


def generate_speed_strategies(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: dict,
    current_altitude_ft: float,
    gross_weight_kg: float,
    current_mach: float,
    current_cost_index: int | None = None,
    min_mach: float | None = None,
    max_mach: float | None = None,
    step: float = 0.005,
    isa_deviation_c: float = 0.0,
    allow_speed_up: bool = True,
    allow_slow_down: bool = True,
    include_recovery: bool = False,
    preserve_current_strategy: bool = True,
    mark_current_label: bool = True,
    ensure_current_mach_present: bool = True,
) -> list[CruiseStrategy]:
    crossover_fl = _speed_mode_crossover_fl(aircraft_cfg)
    current_fl = current_altitude_ft / 100.0

    if current_fl < crossover_fl:
        candidates = _generate_cas_speed_candidates(
            aircraft=aircraft,
            engine_variant=engine_variant,
            aircraft_cfg=aircraft_cfg,
            altitude_ft=current_altitude_ft,
            gross_weight_kg=gross_weight_kg,
            current_mach=current_mach,
            min_mach=min_mach,
            max_mach=max_mach,
            isa_deviation_c=isa_deviation_c,
            allow_speed_up=allow_speed_up,
            allow_slow_down=allow_slow_down,
            include_recovery=include_recovery,
            preserve_current_strategy=preserve_current_strategy,
            mark_current_label=mark_current_label,
            ensure_current_mach_present=ensure_current_mach_present,
        )
        return _speed_candidates_to_strategies(
            candidates,
            altitude_ft=current_altitude_ft,
            current_cost_index=current_cost_index,
            current_mach=current_mach,
        )

    return generate_mach_strategies(
        current_mach=current_mach,
        current_cost_index=current_cost_index,
        min_mach=min_mach,
        max_mach=max_mach,
        step=step,
        allow_speed_up=allow_speed_up,
        allow_slow_down=allow_slow_down,
        preserve_current_strategy=preserve_current_strategy,
        mark_current_label=mark_current_label,
        ensure_current_mach_present=ensure_current_mach_present,
    )


def generate_speed_candidates(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: dict,
    altitude_ft: float,
    gross_weight_kg: float,
    current_mach: float,
    isa_deviation_c: float = 0.0,
    allow_speed_up: bool = True,
    allow_slow_down: bool = True,
    include_recovery: bool = False,
    preserve_current_strategy: bool = True,
    mark_current_label: bool = True,
    ensure_current_mach_present: bool = True,
) -> list[SpeedCandidate]:
    crossover_fl = _speed_mode_crossover_fl(aircraft_cfg)
    if altitude_ft / 100.0 < crossover_fl:
        return _generate_cas_speed_candidates(
            aircraft=aircraft,
            engine_variant=engine_variant,
            aircraft_cfg=aircraft_cfg,
            altitude_ft=altitude_ft,
            gross_weight_kg=gross_weight_kg,
            current_mach=current_mach,
            min_mach=None,
            max_mach=None,
            isa_deviation_c=isa_deviation_c,
            allow_speed_up=allow_speed_up,
            allow_slow_down=allow_slow_down,
            include_recovery=include_recovery,
            preserve_current_strategy=preserve_current_strategy,
            mark_current_label=mark_current_label,
            ensure_current_mach_present=ensure_current_mach_present,
        )

    mach_strategies = generate_mach_strategies(
        current_mach=current_mach,
        current_cost_index=None,
        min_mach=None,
        max_mach=None,
        step=0.005,
        allow_speed_up=allow_speed_up,
        allow_slow_down=allow_slow_down,
        preserve_current_strategy=preserve_current_strategy,
        mark_current_label=mark_current_label,
        ensure_current_mach_present=ensure_current_mach_present,
    )
    return [
        SpeedCandidate(mode="MACH", mach=strategy.mach, label=strategy.label)
        for strategy in mach_strategies
    ]


def generate_boeing_777_fmc_ci_strategies(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: dict,
    general_cfg: dict,
    current_altitude_ft: float,
    gross_weight_kg: float,
    current_mach: float,
    current_cost_index: int | None = None,
    wind_component_kt: float = 0.0,
    isa_deviation_c: float = 0.0,
    min_mach: float | None = None,
    max_mach: float | None = None,
    allow_speed_up: bool = True,
    allow_slow_down: bool = True,
    include_recovery: bool = False,
    preserve_current_strategy: bool = True,
    mark_current_label: bool = True,
    ensure_current_mach_present: bool = True,
) -> list[CruiseStrategy]:
    mode = resolve_cost_index_optimizer_mode(aircraft_cfg)
    if mode not in {
        OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
        OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
        OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    }:
        return generate_speed_strategies(
            aircraft=aircraft,
            engine_variant=engine_variant,
            aircraft_cfg=aircraft_cfg,
            current_altitude_ft=current_altitude_ft,
            gross_weight_kg=gross_weight_kg,
            current_mach=current_mach,
            current_cost_index=current_cost_index,
            min_mach=min_mach,
            max_mach=max_mach,
            step=0.005,
            isa_deviation_c=isa_deviation_c,
            allow_speed_up=allow_speed_up,
            allow_slow_down=allow_slow_down,
            include_recovery=include_recovery,
            preserve_current_strategy=preserve_current_strategy,
            mark_current_label=mark_current_label,
            ensure_current_mach_present=ensure_current_mach_present,
        )

    candidate_ci_values = _generate_fmc_ci_candidate_values(
        aircraft_cfg=aircraft_cfg,
        current_cost_index=current_cost_index,
        allow_speed_up=allow_speed_up,
        allow_slow_down=allow_slow_down,
        include_recovery=include_recovery,
    )

    strategies: list[CruiseStrategy] = []
    for candidate_ci in candidate_ci_values:
        target = estimate_fmc_ci_speed_target(
            aircraft=aircraft,
            engine_variant=engine_variant,
            gross_weight_kg=gross_weight_kg,
            altitude_ft=current_altitude_ft,
            cost_index=candidate_ci,
            wind_component_kt=wind_component_kt,
            isa_deviation_c=isa_deviation_c,
            aircraft_cfg=aircraft_cfg,
            general_cfg=general_cfg,
        )

        if target.target_mach is None or target.target_mach <= 0:
            continue
        if min_mach is not None and target.target_mach < min_mach - 0.0005:
            continue
        if max_mach is not None and target.target_mach > max_mach + 0.0005:
            continue

        label = _fmc_ci_candidate_label(
            candidate_mach=target.target_mach,
            current_mach=current_mach,
        )
        cost_index_source = _cost_index_source_for_target(target.source)
        strategies.append(
            CruiseStrategy(
                mach=round(target.target_mach, 3),
                speed_mode=target.speed_mode,
                cas_kt=target.target_cas_kt,
                cost_index=int(candidate_ci),
                cost_index_source=cost_index_source,
                cost_index_label=cost_index_source_label(cost_index_source),
                label=label,
                warnings=list(target.warnings),
            )
        )

    if preserve_current_strategy and ensure_current_mach_present:
        current_speed_mode = SPEED_MODE_CAS if current_altitude_ft / 100.0 < _speed_mode_crossover_fl(aircraft_cfg) else "MACH"
        strategies.append(
            CruiseStrategy(
                mach=round(current_mach, 3),
                speed_mode=current_speed_mode,
                cas_kt=round(mach_to_cas(current_mach, current_altitude_ft), 1),
                cost_index=current_cost_index,
                cost_index_source=(
                    COST_INDEX_SOURCE_CURRENT_INPUT
                    if current_cost_index is not None
                    else None
                ),
                cost_index_label=cost_index_source_label(COST_INDEX_SOURCE_CURRENT_INPUT) if current_cost_index is not None else None,
                label="CURRENT" if mark_current_label else None,
                warnings=[],
            )
        )

    return _dedupe_and_sort(strategies)


def _generate_mach_values(
    *,
    search_min_mach: float,
    search_max_mach: float,
    step: float,
    current_mach: float,
    ensure_current_mach_present: bool,
) -> list[float]:
    values: list[float] = []
    current = round(search_min_mach, 3)

    while current <= search_max_mach + 1e-9:
        values.append(round(current, 3))
        current = round(current + step, 6)

    current_rounded = round(current_mach, 3)

    if ensure_current_mach_present and current_rounded not in values:
        values.append(current_rounded)

    return sorted(set(values))


def _generate_fmc_ci_candidate_values(
    *,
    aircraft_cfg: dict,
    current_cost_index: int | None,
    allow_speed_up: bool,
    allow_slow_down: bool,
    include_recovery: bool,
) -> list[int]:
    mapping = aircraft_cfg.get("cost_index_mapping") or {}
    fmc_cfg = _ci_speed_mapping_cfg(aircraft_cfg)

    normal_values = _parse_int_list(
        fmc_cfg.get("candidate_ci_values"),
        default=[0, 30, 60, 90, 120, 180, 300, 500, 1000, 2000],
    )
    recovery_values = _parse_int_list(
        fmc_cfg.get("recovery_candidate_ci_values"),
        default=[3000, 5000, 7500, 9999],
    )

    candidates = set(normal_values)
    if include_recovery:
        candidates.update(recovery_values)

    if current_cost_index is not None:
        candidates.add(int(current_cost_index))
        if not allow_speed_up:
            candidates = {ci for ci in candidates if ci <= int(current_cost_index)}
        if not allow_slow_down:
            candidates = {ci for ci in candidates if ci >= int(current_cost_index)}

    min_ci = int(fmc_cfg.get("min_ci", 0) or 0)
    max_ci = int(fmc_cfg.get("max_ci", 9999) or 9999)
    return sorted(ci for ci in candidates if min_ci <= ci <= max_ci)


def _parse_int_list(raw: Any, *, default: list[int]) -> list[int]:
    if not isinstance(raw, list):
        return list(default)

    values: list[int] = []
    for item in raw:
        try:
            values.append(int(round(float(item))))
        except (TypeError, ValueError):
            continue
    return values or list(default)


def _fmc_ci_candidate_label(
    *,
    candidate_mach: float,
    current_mach: float,
) -> str | None:
    if candidate_mach > current_mach + 0.0005:
        return "SPEED_UP"
    if candidate_mach < current_mach - 0.0005:
        return "SLOW_DOWN"
    return None


def _label_for_candidate(
    *,
    mach: float,
    current_mach: float,
    mark_current_label: bool,
) -> str | None:
    if abs(mach - round(current_mach, 3)) < 0.0005:
        return "CURRENT" if mark_current_label else None

    if mach > current_mach:
        return "SPEED_UP"

    return "SLOW_DOWN"


def _replace_or_add_current_strategy(
    *,
    strategies: list[CruiseStrategy],
    current_mach: float,
    current_cost_index: int | None,
) -> list[CruiseStrategy]:
    """
    Ensures the current FMC strategy is represented exactly.
    """

    current_mach_rounded = round(current_mach, 3)

    if current_cost_index is None:
        if any(abs(strategy.mach - current_mach_rounded) < 0.0005 for strategy in strategies):
            return strategies

        strategies.append(
            CruiseStrategy(
                mach=current_mach_rounded,
                cost_index=None,
                label="CURRENT",
            )
        )

        return strategies

    filtered = [
        strategy
        for strategy in strategies
        if strategy.cost_index != current_cost_index
    ]

    filtered.append(
        CruiseStrategy(
            mach=current_mach_rounded,
            cost_index=current_cost_index,
            label="CURRENT",
        )
    )

    return filtered


def _ensure_current_mach_present(
    *,
    strategies: list[CruiseStrategy],
    current_mach: float,
    mark_current_label: bool,
) -> list[CruiseStrategy]:
    current_mach_rounded = round(current_mach, 3)

    if any(abs(strategy.mach - current_mach_rounded) < 0.0005 for strategy in strategies):
        return strategies

    strategies.append(
        CruiseStrategy(
            mach=current_mach_rounded,
            cost_index=None,
            label="CURRENT" if mark_current_label else None,
        )
    )
    return strategies


def _dedupe_and_sort(strategies: list[CruiseStrategy]) -> list[CruiseStrategy]:
    """
    Deduplicate by CI when available, otherwise by Mach.
    """

    by_key: dict[tuple[str, int | float], CruiseStrategy] = {}

    for strategy in strategies:
        if strategy.label == "CURRENT":
            key = ("current", round(strategy.mach, 3))
        elif strategy.cost_index is not None:
            key = ("ci", strategy.cost_index)
        else:
            key = ("mach", strategy.mach)

        existing = by_key.get(key)

        if existing is None:
            by_key[key] = strategy
            continue

        if strategy.label == "CURRENT":
            by_key[key] = strategy

    return sorted(
        by_key.values(),
        key=lambda strategy: (
            strategy.cost_index if strategy.cost_index is not None else 999999,
            strategy.mach,
        ),
    )


def _generate_cas_speed_candidates(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: dict,
    altitude_ft: float,
    gross_weight_kg: float,
    current_mach: float,
    min_mach: float | None,
    max_mach: float | None,
    isa_deviation_c: float,
    allow_speed_up: bool,
    allow_slow_down: bool,
    include_recovery: bool,
    preserve_current_strategy: bool,
    mark_current_label: bool,
    ensure_current_mach_present: bool,
) -> list[SpeedCandidate]:
    anchor_cas = _resolve_lrc_anchor_cas(
        aircraft=aircraft,
        engine_variant=engine_variant,
        aircraft_cfg=aircraft_cfg,
        altitude_ft=altitude_ft,
        gross_weight_kg=gross_weight_kg,
        current_mach=current_mach,
    )
    current_cas = mach_to_cas(current_mach, altitude_ft)

    candidate_cas_values = [
        round(anchor_cas + delta, 1)
        for delta in (-20.0, -10.0, 0.0, 10.0, 20.0)
    ]
    if include_recovery:
        candidate_cas_values.extend([
            round(anchor_cas + 30.0, 1),
            round(anchor_cas + 40.0, 1),
        ])

    envelope = (aircraft_cfg.get("performance") or {}).get("speed_envelope") or {}
    vmo_kt = float(envelope.get("vmo_kt") or 320.0)
    mmo = float(envelope.get("mmo") or 0.82)

    candidates: list[SpeedCandidate] = []
    for cas_kt in sorted(set(candidate_cas_values)):
        mach = round(cas_to_mach(cas_kt, altitude_ft), 3)
        if mach <= 0:
            continue
        if min_mach is not None and mach < min_mach - 0.0005:
            continue
        if max_mach is not None and mach > max_mach + 0.0005:
            continue
        if mach > max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo) + 1e-9:
            continue
        if not allow_slow_down and cas_kt < current_cas - 0.1:
            continue
        if not allow_speed_up and cas_kt > current_cas + 0.1:
            continue

        label = None
        if abs(cas_kt - anchor_cas) < 0.6:
            label = "LRC"
        if abs(cas_kt - current_cas) < 0.6 and mark_current_label:
            label = "CURRENT"
        elif label is None and cas_kt > current_cas + 0.1:
            label = "SPEED_UP"
        elif label is None and cas_kt < current_cas - 0.1:
            label = "SLOW_DOWN"

        candidates.append(
            SpeedCandidate(
                mode="CAS",
                cas_kt=cas_kt,
                mach=mach,
                label=label,
            )
        )

    if preserve_current_strategy and ensure_current_mach_present:
        current_label = "CURRENT" if mark_current_label else None
        if not any(candidate.label == "CURRENT" for candidate in candidates):
            candidates.append(
                SpeedCandidate(
                    mode="CAS",
                    cas_kt=round(current_cas, 1),
                    mach=round(current_mach, 3),
                    label=current_label,
                )
            )

    return _dedupe_speed_candidates(candidates)


def _speed_candidates_to_strategies(
    candidates: list[SpeedCandidate],
    *,
    altitude_ft: float,
    current_cost_index: int | None,
    current_mach: float,
) -> list[CruiseStrategy]:
    strategies: list[CruiseStrategy] = []
    for candidate in candidates:
        mach = candidate.mach if candidate.mach is not None else 0.0
        strategies.append(
            CruiseStrategy(
                mach=round(mach, 3),
                cas_kt=round(candidate.cas_kt, 1) if candidate.cas_kt is not None else None,
                speed_mode=candidate.mode,
                cost_index=current_cost_index if candidate.label == "CURRENT" else None,
                label=candidate.label,
            )
        )

    return _dedupe_and_sort(strategies)


def _dedupe_speed_candidates(candidates: list[SpeedCandidate]) -> list[SpeedCandidate]:
    by_mach: dict[float, SpeedCandidate] = {}
    for candidate in candidates:
        if candidate.mach is None:
            continue
        key = round(candidate.mach, 3)
        existing = by_mach.get(key)
        if existing is None or candidate.label == "CURRENT":
            by_mach[key] = candidate
    return sorted(by_mach.values(), key=lambda candidate: (candidate.mach or 0.0))


def _resolve_lrc_anchor_cas(
    *,
    aircraft: str,
    engine_variant: str | None,
    aircraft_cfg: dict,
    altitude_ft: float,
    gross_weight_kg: float,
    current_mach: float,
) -> float:
    mode = resolve_cost_index_optimizer_mode(aircraft_cfg)
    if mode in {
        OPTIMIZER_MODE_BOEING_777_FMC_LIKE,
        OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE,
        OPTIMIZER_MODE_EMPIRICAL_FMC_CI_TABLE,
    }:
        cfg = _ci_speed_mapping_cfg(aircraft_cfg)
        lrc_anchor_ci = int(cfg.get("lrc_anchor_ci") or 50)
        target = estimate_fmc_ci_speed_target(
            aircraft=aircraft,
            engine_variant=engine_variant,
            gross_weight_kg=gross_weight_kg,
            altitude_ft=altitude_ft,
            cost_index=lrc_anchor_ci,
            wind_component_kt=0.0,
            isa_deviation_c=0.0,
            aircraft_cfg=aircraft_cfg,
            general_cfg={},
        )
        if target.target_cas_kt is not None and target.target_cas_kt > 0:
            return float(target.target_cas_kt)

    variant = resolve_boeing_777_fcom_variant(
        request_aircraft=aircraft,
        aircraft_cfg=aircraft_cfg,
        engine_variant=engine_variant,
    )
    if variant is not None:
        performance = get_boeing_fcom_performance(variant)
        query = query_lrc_cruise(
            performance,
            weight_kg=gross_weight_kg,
            altitude_ft=altitude_ft,
        )
        return float(query.kias)

    return mach_to_cas(current_mach, altitude_ft)


def _speed_mode_crossover_fl(aircraft_cfg: dict) -> float:
    mapping = aircraft_cfg.get("cost_index_mapping") or {}
    for key in ("airbus_family_ci", "fmc_like_ci"):
        mode_cfg = mapping.get(key) or {}
        raw = mode_cfg.get("cas_mach_crossover_fl")
        if raw is not None:
            return float(raw)

    cruise = (aircraft_cfg.get("performance") or {}).get("cruise") or {}
    raw = cruise.get("cas_mach_crossover_fl")
    if raw is None:
        return 280.0
    return float(raw)


def _ci_speed_mapping_cfg(aircraft_cfg: dict) -> dict:
    mapping = aircraft_cfg.get("cost_index_mapping") or {}
    mode = resolve_cost_index_optimizer_mode(aircraft_cfg)
    if mode == OPTIMIZER_MODE_AIRBUS_FAMILY_FMC_LIKE:
        return mapping.get("airbus_family_ci") or {}
    return mapping.get("fmc_like_ci") or {}


def _cost_index_source_for_target(target_source: str | None) -> str:
    if target_source == "empirical_fmc_ci_table":
        return COST_INDEX_SOURCE_CALIBRATED
    if target_source == "airbus_family_calibrated_ci":
        return COST_INDEX_SOURCE_CALIBRATED
    if target_source == "airbus_family_fmc_ci_fallback":
        return COST_INDEX_SOURCE_AIRBUS_FAMILY
    return COST_INDEX_SOURCE_FMC_LIKE


def _normalized_flight_levels(
    candidates: list[int],
    *,
    min_flight_level: int,
    max_flight_level: int,
) -> list[int]:
    normalized = {
        int(level)
        for level in candidates
        if min_flight_level <= int(level) <= max_flight_level
    }
    return sorted(normalized)
