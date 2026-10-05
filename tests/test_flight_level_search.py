from __future__ import annotations

import unittest

from data_fetcher.sim.sim_models import CurrentFlightState
from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.cost_optimizer import optimize_cost
from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_models import (
    FlightContextInput,
    OperationalTrigger,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
)
from strategy.strategy_generator import generate_flight_level_candidates


class FlightLevelSearchTests(unittest.TestCase):
    def test_generate_flight_level_candidates_default_search(self) -> None:
        candidates = generate_flight_level_candidates(
            current_altitude_ft=35000,
            remaining_distance_nm=900.0,
        )

        self.assertEqual(candidates, [350, 370, 390])

    def test_generate_flight_level_candidates_constrained(self) -> None:
        candidates = generate_flight_level_candidates(
            current_altitude_ft=35000,
            remaining_distance_nm=900.0,
            assigned_flight_level=330,
        )

        self.assertEqual(candidates, [330, 350])

    def test_generate_flight_level_candidates_respect_far_fmc_step_climb(self) -> None:
        candidates = generate_flight_level_candidates(
            current_altitude_ft=33000,
            remaining_distance_nm=900.0,
            fmc_source="PMDG_SDK",
            fmc_cruise_flight_level=390,
            fmc_step_climb_distance_nm=220.0,
        )

        self.assertEqual(candidates, [330])

    def test_generate_flight_level_candidates_allow_near_fmc_step_climb_on_short_route(self) -> None:
        candidates = generate_flight_level_candidates(
            current_altitude_ft=33000,
            remaining_distance_nm=180.0,
            fmc_source="PMDG_SDK",
            fmc_cruise_flight_level=350,
            fmc_step_climb_distance_nm=20.0,
        )

        self.assertEqual(candidates, [330, 350])

    def test_optimize_cost_evaluates_multiple_flight_levels(self) -> None:
        current_state = CurrentFlightState(
            aircraft="A320",
            altitude_ft=35000,
            gross_weight_kg=65000,
            mach=0.78,
            current_cost_index=32,
            remaining_distance_nm=900.0,
            route_distance_nm=1100.0,
            wind_component_kt=-15.0,
            isa_deviation_c=0.0,
            fuel_remaining_kg=5200.0,
            ground_speed_kt=435.0,
        )
        scenario = ScenarioInput(
            trigger=OperationalTrigger.MANUAL_RECALCULATION,
            scenario_type=ScenarioType.NORMAL_COST_OPTIMIZATION,
            priority=ScenarioPriority.MEDIUM,
            flight_context=FlightContextInput(),
        )
        interpreted = interpret_scenario(scenario)

        result = optimize_cost(
            current_state=current_state,
            interpreted_scenario=interpreted,
            general_cfg=load_general_config(),
            aircraft_cfg=load_aircraft_config("a320"),
            flight_level_candidates=[350, 370],
        )

        self.assertEqual(result.current_strategy.flight_level, 350)
        self.assertEqual(
            {strategy.flight_level for strategy in result.strategies},
            {350, 370},
        )
        self.assertIsNotNone(result.best_strategy.flight_level)

    def test_optimize_cost_marks_climb_strategies_with_fmc_step_climb_deferral(self) -> None:
        current_state = CurrentFlightState(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            altitude_ft=33000,
            gross_weight_kg=240000,
            mach=0.84,
            current_cost_index=71,
            fmc_source="PMDG_SDK",
            fmc_cruise_flight_level=350,
            fmc_step_climb_distance_nm=180.0,
            remaining_distance_nm=900.0,
            route_distance_nm=1200.0,
            wind_component_kt=-10.0,
            isa_deviation_c=0.0,
            fuel_remaining_kg=36000.0,
            ground_speed_kt=470.0,
        )
        scenario = ScenarioInput(
            trigger=OperationalTrigger.MANUAL_RECALCULATION,
            scenario_type=ScenarioType.NORMAL_COST_OPTIMIZATION,
            priority=ScenarioPriority.MEDIUM,
            flight_context=FlightContextInput(),
        )
        interpreted = interpret_scenario(scenario)

        result = optimize_cost(
            current_state=current_state,
            interpreted_scenario=interpreted,
            general_cfg=load_general_config(),
            aircraft_cfg=load_aircraft_config("b77w"),
            flight_level_candidates=[330, 350],
        )

        climb_strategies = [strategy for strategy in result.strategies if strategy.flight_level == 350]
        self.assertTrue(climb_strategies)
        self.assertTrue(
            any(
                "FMC step-climb guidance defers higher-level benefit" in warning
                for warning in climb_strategies[0].performance.warnings
            )
        )


if __name__ == "__main__":
    unittest.main()
