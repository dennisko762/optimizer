from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_models import ConnexScenarioInput, ScenarioInput, ScenarioPriority, ScenarioType, TimingScenarioInput


scenario = ScenarioInput(
    scenario_type=ScenarioType.CONNEX_RECOVERY,
    priority=ScenarioPriority.HIGH,
    connex=ConnexScenarioInput(
        affected_pax=12,
        acceptable_extra_fuel_kg_per_pax=35,
        hub_airport="EDDF",
        hotel_risk=True,
        last_connection_of_day=True,
    ),
    timing=TimingScenarioInput(
        current_delay_min=14,
        target_delay_min=5,
    ),
    allow_speed_up=True,
    allow_slow_down=False,
)

interpreted = interpret_scenario(scenario)

print(interpreted.model_dump_json(indent=2))