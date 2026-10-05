from __future__ import annotations

import unittest

from data_fetcher.sim.sim_models import CurrentFlightState
from delay_module.eta_calculator import EtaEstimate
from optimizer.app.ci_optimization_service import CiOptimizationService
from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.cost_model import CostBreakdown
from optimizer.cost_optimizer import CostedStrategy, _apply_derived_cost_index, optimize_cost
from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_models import CostScenarioInput, FlightContextInput
from performance_engine.ci_profile import (
    cost_index_to_mach,
    derive_cost_index_profile,
    mach_to_cost_index,
)
from performance_engine.remaining_cruise_simulator import RemainingCruiseResult


class CiMatchingTests(unittest.TestCase):
    def test_ci_profile_uses_threshold_bands_and_representative_ci(self) -> None:
        result = derive_cost_index_profile(
            mach_to_time_fuel={
                0.78: (100.0, 1000.0),
                0.79: (99.0, 1001.2),
                0.80: (98.0, 1003.6),
            },
            general_cfg={},
            aircraft_cfg={
                "performance": {"min_ci": 0, "max_ci": 999},
                "cost_index_mapping": {"kg_per_min_to_ci_factor": 1.0},
            },
        )

        low = result.by_mach[0.78]
        mid = result.by_mach[0.79]
        high = result.by_mach[0.80]

        self.assertEqual(low.lower_bound_cost_index, 0)
        self.assertEqual(low.upper_bound_cost_index, 1)
        self.assertEqual(low.cost_index, 0)

        self.assertEqual(mid.threshold_cost_index, 2)
        self.assertEqual(mid.lower_bound_cost_index, 2)
        self.assertEqual(mid.upper_bound_cost_index, 2)
        self.assertEqual(mid.cost_index, 2)

        self.assertEqual(high.threshold_cost_index, 3)
        self.assertEqual(high.lower_bound_cost_index, 3)
        self.assertIsNone(high.upper_bound_cost_index)
        self.assertEqual(high.cost_index, 3)

    def test_apply_derived_cost_index_prefers_ci_that_matches_mach_band(self) -> None:
        profile = derive_cost_index_profile(
            mach_to_time_fuel={
                0.78: (100.0, 1000.0),
                0.79: (99.0, 1001.2),
                0.80: (98.0, 1003.6),
            },
            general_cfg={},
            aircraft_cfg={
                "performance": {"min_ci": 0, "max_ci": 999},
                "cost_index_mapping": {"kg_per_min_to_ci_factor": 1.0},
            },
        ).by_mach

        strategy = CostedStrategy(
            mach=0.79,
            performance=RemainingCruiseResult(
                aircraft="A320",
                altitude_ft=35000,
                mach=0.79,
                initial_weight_kg=65000,
                end_weight_kg=64000,
                remaining_distance_nm=1000,
                remaining_time_min=99,
                remaining_fuel_kg=1001.2,
                tas_kt=450,
                ground_speed_kt=430,
                avg_fuel_flow_kg_h=6000,
                fuel_per_nm_kg=1.0,
                fuel_per_min_kg=10.0,
            ),
            cost=CostBreakdown(
                fuel_cost_eur=0,
                time_cost_eur=0,
                delay_cost_eur=0,
                irops_cost_eur=0,
                connex_cost_eur=0,
                curfew_cost_eur=0,
                duty_cost_eur=0,
                va_score_cost_eur=0,
                total_cost_eur=0,
                fuel_cost_per_kg_eur=0,
                base_time_cost_per_hour_eur=0,
                effective_time_cost_per_hour_eur=0,
                delay_cost_per_min_eur=0,
                economic_ci_kg_per_min=0,
                economic_ci_kg_per_hour=0,
                recommended_ci=30,
                ci_scale_factor=1.0,
                fuel_kg=0,
                time_min=0,
                delay_min=0,
                connex_risk_min=0,
                curfew_risk_min=0,
                duty_risk_min=0,
                irops_mode_active=False,
            ),
            delta_fuel_kg=0,
            delta_time_min=0,
            delta_cost_eur=0,
            time_saved_min=0,
            gate_time_saved_min=0,
            extra_fuel_kg=0,
        )

        updated = _apply_derived_cost_index(
            strategy=strategy,
            current_cost_index=None,
            ci_profile=profile,
        )

        self.assertEqual(updated.cost_index, 2)

    def test_b77w_ci_to_mach_curve_is_monotonic_and_nonlinear(self) -> None:
        aircraft_cfg = load_aircraft_config("b77w")

        ci_points = [0, 20, 55, 70, 90, 100]
        mach_points = [
            cost_index_to_mach(cost_index=ci, aircraft_cfg=aircraft_cfg)
            for ci in ci_points
        ]

        self.assertTrue(all(mach is not None for mach in mach_points))
        self.assertEqual(sorted(mach_points), mach_points)

        first_slope = (mach_points[2] - mach_points[1]) / (ci_points[2] - ci_points[1])
        second_slope = (mach_points[3] - mach_points[2]) / (ci_points[3] - ci_points[2])
        self.assertNotAlmostEqual(first_slope, second_slope, places=6)

    def test_b77w_ci_zero_and_lrc_anchor_stay_near_econ_speeds(self) -> None:
        aircraft_cfg = load_aircraft_config("b77w")

        ci_zero_mach = cost_index_to_mach(cost_index=0, aircraft_cfg=aircraft_cfg)
        lrc_like_mach = cost_index_to_mach(cost_index=55, aircraft_cfg=aircraft_cfg)
        ci_70_mach = cost_index_to_mach(cost_index=70, aircraft_cfg=aircraft_cfg)

        self.assertIsNotNone(ci_zero_mach)
        self.assertIsNotNone(lrc_like_mach)
        self.assertIsNotNone(ci_70_mach)

        self.assertAlmostEqual(ci_zero_mach, 0.819, places=3)
        self.assertAlmostEqual(lrc_like_mach, 0.832, places=3)
        self.assertLess(ci_70_mach, 0.86)
        self.assertAlmostEqual(ci_70_mach, 0.84, places=3)

    def test_b77w_high_speed_requires_very_high_ci(self) -> None:
        aircraft_cfg = load_aircraft_config("b77w")

        self.assertLess(cost_index_to_mach(cost_index=70, aircraft_cfg=aircraft_cfg), 0.85)
        self.assertGreaterEqual(cost_index_to_mach(cost_index=100, aircraft_cfg=aircraft_cfg), 0.858)
        self.assertGreaterEqual(mach_to_cost_index(mach=0.859, aircraft_cfg=aircraft_cfg), 100)

    def test_early_normal_recalc_disables_speed_up(self) -> None:
        service = CiOptimizationService()
        scenario, _ = service._build_normal_scenario(
            flight_context=FlightContextInput(),
            cost=CostScenarioInput(),
            eta=EtaEstimate(
                remaining_distance_nm=300.0,
                ground_speed_kt=470.0,
                remaining_time_min=38.3,
                eta_utc="12:00",
                sibt_utc="12:10",
                sobt_utc=None,
                delay_min=-10.0,
                delay_status="ON_TIME",
                should_recalculate=False,
                recalculate_reason=None,
                current_delay_min=0.0,
                target_delay_min=0.0,
                delay_label="10 min early",
            ),
            max_mach=0.86,
        )
        interpreted = interpret_scenario(scenario)

        current_state = CurrentFlightState(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            altitude_ft=37000,
            gross_weight_kg=245000.0,
            mach=0.84,
            current_cost_index=70,
            remaining_distance_nm=300.0,
            route_distance_nm=420.0,
            wind_component_kt=-10.0,
            isa_deviation_c=0.0,
            fuel_remaining_kg=42000.0,
            ground_speed_kt=470.0,
        )

        result = optimize_cost(
            current_state=current_state,
            interpreted_scenario=interpreted,
            general_cfg=load_general_config(),
            aircraft_cfg=load_aircraft_config("b77w"),
        )

        self.assertFalse(interpreted.allow_speed_up)
        self.assertLessEqual(result.best_strategy.mach, current_state.mach)


if __name__ == "__main__":
    unittest.main()
