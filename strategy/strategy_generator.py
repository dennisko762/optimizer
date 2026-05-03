from __future__ import annotations

from strategy.strategy_model import CruiseStrategy


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
        search_min_mach = search_max_mach = round(current_mach, 3)

    mach_values = _generate_mach_values(
        search_min_mach=search_min_mach,
        search_max_mach=search_max_mach,
        step=step,
        current_mach=current_mach,
    )

    strategies: list[CruiseStrategy] = []

    for mach in mach_values:
        label = _label_for_candidate(
            mach=mach,
            current_mach=current_mach,
        )

        strategies.append(
            CruiseStrategy(
                mach=mach,
                cost_index=None,
                label=label,
            )
        )

    if current_cost_index is not None:
        strategies = _replace_or_add_current_strategy(
            strategies=strategies,
            current_mach=current_mach,
            current_cost_index=current_cost_index,
        )
    else:
        strategies = _replace_or_add_current_strategy(
            strategies=strategies,
            current_mach=current_mach,
            current_cost_index=None,
        )

    return _dedupe_and_sort(strategies)


def _generate_mach_values(
    *,
    search_min_mach: float,
    search_max_mach: float,
    step: float,
    current_mach: float,
) -> list[float]:
    values: list[float] = []
    current = round(search_min_mach, 3)

    while current <= search_max_mach + 1e-9:
        values.append(round(current, 3))
        current = round(current + step, 6)

    current_rounded = round(current_mach, 3)

    if current_rounded not in values:
        values.append(current_rounded)

    return sorted(set(values))


def _label_for_candidate(
    *,
    mach: float,
    current_mach: float,
) -> str:
    if abs(mach - round(current_mach, 3)) < 0.0005:
        return "CURRENT"

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


def _dedupe_and_sort(strategies: list[CruiseStrategy]) -> list[CruiseStrategy]:
    """
    Deduplicate by CI when available, otherwise by Mach.
    """

    by_key: dict[tuple[str, int | float], CruiseStrategy] = {}

    for strategy in strategies:
        if strategy.cost_index is not None:
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
