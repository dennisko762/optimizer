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
from performance_engine.ci_table.ci_mach_table_generator import generate_ci_mach_table
from performance_engine.ci_table.ci_mach_table_lookup import cost_index_to_mach_from_table
from strategy.strategy_generator import generate_speed_candidates


class LowAltitudeSpeedCandidateTests(unittest.TestCase):
    def test_b77w_fl230_normal_context_generates_cas_candidates(self) -> None:
        candidates = generate_speed_candidates(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            aircraft_cfg=load_aircraft_config("b77w"),
            altitude_ft=23000.0,
            gross_weight_kg=240000.0,
            current_mach=0.75,
            allow_speed_up=True,
            allow_slow_down=True,
            include_recovery=False,
        )

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.mode == "CAS" for candidate in candidates))

    def test_b77w_fl230_lrc_anchor_converts_to_low_mach_not_high_mach(self) -> None:
        candidates = generate_speed_candidates(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            aircraft_cfg=load_aircraft_config("b77w"),
            altitude_ft=23000.0,
            gross_weight_kg=240000.0,
            current_mach=0.75,
            allow_speed_up=True,
            allow_slow_down=True,
            include_recovery=False,
        )

        lrc_candidate = next(candidate for candidate in candidates if candidate.label == "LRC")
        self.assertIsNotNone(lrc_candidate.mach)
        self.assertLess(lrc_candidate.mach, 0.72)
        self.assertGreater(lrc_candidate.mach, 0.68)

    def test_ci_71_at_fl230_does_not_map_to_m075(self) -> None:
        table = generate_ci_mach_table(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            gross_weight_kg=240000.0,
            flight_level=230,
            isa_deviation_c=0.0,
            cg_percent_mac=None,
            wind_component_kt=0.0,
            aircraft_cfg=load_aircraft_config("b77w"),
            general_cfg=load_general_config(),
            include_recovery=False,
        )

        mach = cost_index_to_mach_from_table(71, table)

        self.assertIsNotNone(mach)
        self.assertLess(mach, 0.75)

    def test_fl230_high_speed_candidates_require_recovery_context(self) -> None:
        normal = generate_speed_candidates(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            aircraft_cfg=load_aircraft_config("b77w"),
            altitude_ft=23000.0,
            gross_weight_kg=220000.0,
            current_mach=0.75,
            allow_speed_up=True,
            allow_slow_down=True,
            include_recovery=False,
        )
        recovery = generate_speed_candidates(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            aircraft_cfg=load_aircraft_config("b77w"),
            altitude_ft=23000.0,
            gross_weight_kg=220000.0,
            current_mach=0.75,
            allow_speed_up=True,
            allow_slow_down=True,
            include_recovery=True,
        )

        normal_max_cas = max(candidate.cas_kt for candidate in normal if candidate.cas_kt is not None)
        recovery_max_cas = max(candidate.cas_kt for candidate in recovery if candidate.cas_kt is not None)
        self.assertGreaterEqual(recovery_max_cas, normal_max_cas)
        self.assertIn(324.0, [candidate.cas_kt for candidate in recovery if candidate.cas_kt is not None])
        self.assertNotIn(324.0, [candidate.cas_kt for candidate in normal if candidate.cas_kt is not None])

    def test_above_fl330_candidates_remain_mach_based(self) -> None:
        candidates = generate_speed_candidates(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            aircraft_cfg=load_aircraft_config("b77w"),
            altitude_ft=33000.0,
            gross_weight_kg=240000.0,
            current_mach=0.84,
            allow_speed_up=True,
            allow_slow_down=True,
            include_recovery=False,
        )

        self.assertTrue(candidates)
        self.assertTrue(all(candidate.mode == "MACH" for candidate in candidates))

    def test_optimizer_does_not_present_ci71_with_unproven_m075_pairing(self) -> None:
        current_state = CurrentFlightState(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            altitude_ft=23000.0,
            gross_weight_kg=240000.0,
            mach=0.75,
            current_cost_index=71,
            remaining_distance_nm=900.0,
            route_distance_nm=1200.0,
            wind_component_kt=0.0,
            isa_deviation_c=0.0,
            fuel_remaining_kg=30000.0,
            ground_speed_kt=430.0,
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
            flight_level_candidates=[230],
        )

        self.assertFalse(
            result.best_strategy.cost_index == 71 and result.best_strategy.mach >= 0.75
        )


if __name__ == "__main__":
    unittest.main()
