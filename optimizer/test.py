from data_fetcher.sim.sim_models import CurrentFlightState
from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.cost_optimizer import optimize_cost
from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_models import (
    ConnexScenarioInput,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
    TimingScenarioInput,
)


def main() -> None:
    current_state = CurrentFlightState(
        aircraft="A320",
        altitude_ft=33000,
        gross_weight_kg=65000,
        mach=0.78,
        remaining_distance_nm=210,
        wind_component_kt=-15,
        isa_deviation_c=0,
        fuel_remaining_kg=5200,
        ground_speed_kt=435,
    )

    scenario_input = ScenarioInput(
        scenario_type=ScenarioType.CONNEX_RECOVERY,
        priority=ScenarioPriority.HIGH,
        connex=ConnexScenarioInput(
            affected_pax=8,
            acceptable_extra_fuel_kg_per_pax=18,
            hub_airport="EDDF",
            hotel_risk=False,
            last_connection_of_day=False,
            connection_buffer_min=9,
        ),
        timing=TimingScenarioInput(
            current_delay_min=12,
            target_delay_min=5,
        ),
        allow_speed_up=True,
        allow_slow_down=False,
        max_mach=0.82,
        mach_step=0.005,
    )

    interpreted = interpret_scenario(scenario_input)

    general_cfg = load_general_config()
    aircraft_cfg = load_aircraft_config("a320")

    result = optimize_cost(
        current_state=current_state,
        interpreted_scenario=interpreted,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    print(result.recommendation)

    print()
    print("Current:")
    print(f"M{result.current_strategy.mach:.3f}")
    print(f"Fuel: {result.current_strategy.performance.remaining_fuel_kg:.0f} kg")
    print(f"Time: {result.current_strategy.performance.remaining_time_min:.1f} min")
    print(f"Cost: {result.current_strategy.cost.total_cost_eur:.0f} EUR")

    print()
    print("Best Strategy:")
    best = result.best_strategy
    print(f"  Recommended CI: {best.cost.recommended_ci}")
    print(f"  Target Mach:    {best.mach:.3f}")
    print(f"  Fuel:           {best.performance.remaining_fuel_kg:.0f} kg")
    print(f"  Time:           {best.performance.remaining_time_min:.1f} min")
    print(f"  Total cost:     {best.cost.total_cost_eur:.0f} EUR")
    print(f"  Economic CI:    {best.cost.economic_ci_kg_per_min:.2f} kg/min")
    print(f"  Delta Fuel:     {best.delta_fuel_kg:+.0f} kg")
    print(f"  Delta Time:     {best.delta_time_min:+.1f} min")
    print(f"  Delta Cost:     {best.delta_cost_eur:+.0f} EUR")
    print(f"  Allowed:        {best.allowed}")


if __name__ == "__main__":
    main()
