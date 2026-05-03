from __future__ import annotations

from data_fetcher.sim.sim_models import CurrentFlightState
from optimizer.scenario_engine.scenario_models import (
    OperationalTrigger,
    ScenarioInput,
    ScenarioType,
)


def apply_scenario_to_current_state(
    *,
    current_state: CurrentFlightState,
    scenario_input: ScenarioInput,
) -> CurrentFlightState:
    """
    Applies scenario-specific performance-state updates before optimization.

    Important separation:
    - scenario_interpreter.py interprets objective/constraints/priority.
    - scenario_state_applier.py updates the performance state input.

    Examples:
    - Weather update provides new wind component -> CurrentFlightState.wind_component_kt
    - Weather update provides new ISA deviation -> CurrentFlightState.isa_deviation_c
    - Reroute provides new remaining distance -> CurrentFlightState.remaining_distance_nm

    This function does not calculate costs and does not choose a strategy.
    """

    updates: dict[str, float | int | str | None] = {}

    _apply_weather_update(
        updates=updates,
        scenario_input=scenario_input,
    )

    _apply_reroute_update(
        updates=updates,
        current_state=current_state,
        scenario_input=scenario_input,
    )

    if not updates:
        return current_state

    return current_state.model_copy(update=updates)


def _apply_weather_update(
    *,
    updates: dict[str, float | int | str | None],
    scenario_input: ScenarioInput,
) -> None:
    """
    Applies weather/forecast fields to CurrentFlightState.

    Applied fields:
    - new_wind_component_kt -> wind_component_kt
    - new_isa_deviation_c   -> isa_deviation_c
    """

    is_weather_relevant = (
        scenario_input.scenario_type == ScenarioType.WEATHER_UPDATE
        or scenario_input.trigger
        in {
            OperationalTrigger.WEATHER_FORECAST_UPDATED,
            OperationalTrigger.WIND_PROFILE_UPDATED,
            OperationalTrigger.TEMPERATURE_PROFILE_UPDATED,
        }
        or scenario_input.weather.weather_update_received
        or scenario_input.weather.new_wind_component_kt is not None
        or scenario_input.weather.new_isa_deviation_c is not None
    )

    if not is_weather_relevant:
        return

    if scenario_input.weather.new_wind_component_kt is not None:
        wind = scenario_input.weather.new_wind_component_kt
        _validate_wind_component_kt(wind)
        updates["wind_component_kt"] = wind

    if scenario_input.weather.new_isa_deviation_c is not None:
        isa = scenario_input.weather.new_isa_deviation_c
        _validate_isa_deviation_c(isa)
        updates["isa_deviation_c"] = isa


def _apply_reroute_update(
    *,
    updates: dict[str, float | int | str | None],
    current_state: CurrentFlightState,
    scenario_input: ScenarioInput,
) -> None:
    """
    Applies reroute fields to CurrentFlightState.remaining_distance_nm.

    Priority:
    1. reroute.new_remaining_distance_nm
    2. current remaining distance + reroute.distance_delta_nm
    3. current remaining distance + weather.expected_weather_reroute_nm
    """

    is_reroute_relevant = (
        scenario_input.scenario_type == ScenarioType.REROUTE_RECOVERY
        or scenario_input.trigger == OperationalTrigger.REROUTE_RECEIVED
        or scenario_input.reroute.reroute_received
        or scenario_input.reroute.new_remaining_distance_nm is not None
        or scenario_input.reroute.distance_delta_nm is not None
        or scenario_input.weather.expected_weather_reroute_nm is not None
    )

    if not is_reroute_relevant:
        return

    if scenario_input.reroute.new_remaining_distance_nm is not None:
        new_distance = scenario_input.reroute.new_remaining_distance_nm
        _validate_remaining_distance_nm(new_distance)
        updates["remaining_distance_nm"] = new_distance
        return

    if scenario_input.reroute.distance_delta_nm is not None:
        new_distance = (
            current_state.remaining_distance_nm
            + scenario_input.reroute.distance_delta_nm
        )
        _validate_remaining_distance_nm(new_distance)
        updates["remaining_distance_nm"] = new_distance
        return

    if scenario_input.weather.expected_weather_reroute_nm is not None:
        new_distance = (
            current_state.remaining_distance_nm
            + scenario_input.weather.expected_weather_reroute_nm
        )
        _validate_remaining_distance_nm(new_distance)
        updates["remaining_distance_nm"] = new_distance
        return


def _validate_wind_component_kt(value: float) -> None:
    """
    Basic sanity validation.

    Positive = tailwind
    Negative = headwind
    """

    if value < -250 or value > 250:
        raise ValueError(
            f"Unreasonable wind component: {value:.1f} kt. "
            "Expected range roughly -250..+250 kt."
        )


def _validate_isa_deviation_c(value: float) -> None:
    if value < -80 or value > 80:
        raise ValueError(
            f"Unreasonable ISA deviation: {value:.1f} °C. "
            "Expected range roughly -80..+80 °C."
        )


def _validate_remaining_distance_nm(value: float) -> None:
    if value <= 0:
        raise ValueError(
            f"Invalid remaining distance after scenario application: "
            f"{value:.1f} NM. Remaining distance must be > 0."
        )