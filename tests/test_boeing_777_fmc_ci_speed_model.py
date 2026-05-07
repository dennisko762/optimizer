from __future__ import annotations

from copy import deepcopy
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
from performance_engine.boeing_777_fmc_ci_speed_model import (
    COST_INDEX_SOURCE_DISPLAY,
    COST_INDEX_SOURCE_FMC_LIKE,
    SPEED_MODE_CAS,
    SPEED_MODE_MACH,
    estimate_boeing_777_fmc_econ_speed,
    estimate_fmc_ci_speed_target,
)
from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_registry import get_boeing_fcom_performance


class Boeing777FmcLikeCiSpeedModelTests(unittest.TestCase):
    def test_ci_180_tracks_fcom_lrc_anchor(self) -> None:
        target = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0)
        lrc = _b77w_lrc(weight_kg=240000.0, altitude_ft=33000.0)

        self.assertAlmostEqual(target.target_mach or 0.0, round(lrc.mach, 3), delta=0.006)
        self.assertAlmostEqual(target.target_cas_kt or 0.0, round(lrc.kias, 1), delta=2.0)

    def test_ci_zero_is_below_lrc(self) -> None:
        zero = _estimate_b77w(cost_index=0, altitude_ft=33000.0, gross_weight_kg=240000.0)
        lrc = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0)

        self.assertLess(zero.target_mach or 0.0, lrc.target_mach or 0.0)
        self.assertLess(zero.target_cas_kt or 0.0, lrc.target_cas_kt or 0.0)

    def test_ci_71_stays_below_lrc(self) -> None:
        ci_71 = _estimate_b77w(cost_index=71, altitude_ft=23000.0, gross_weight_kg=240000.0)
        lrc = _estimate_b77w(cost_index=180, altitude_ft=23000.0, gross_weight_kg=240000.0)

        self.assertLess(ci_71.target_mach or 0.0, lrc.target_mach or 0.0)
        self.assertLess(ci_71.target_cas_kt or 0.0, lrc.target_cas_kt or 0.0)

    def test_ci_100_does_not_map_to_high_mach_at_fl230(self) -> None:
        target = _estimate_b77w(cost_index=100, altitude_ft=23000.0, gross_weight_kg=240000.0)

        self.assertLess(target.target_mach or 0.0, 0.80)
        self.assertLess(target.target_mach or 0.0, 0.75)

    def test_fl230_output_is_cas_primary(self) -> None:
        target = _estimate_b77w(cost_index=180, altitude_ft=23000.0, gross_weight_kg=240000.0)

        self.assertEqual(target.speed_mode, SPEED_MODE_CAS)
        self.assertIsNotNone(target.target_cas_kt)

    def test_high_cruise_output_can_be_mach_primary(self) -> None:
        target = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0)

        self.assertEqual(target.speed_mode, SPEED_MODE_MACH)
        self.assertIsNotNone(target.target_mach)

    def test_headwind_increases_target_speed(self) -> None:
        zero = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0, wind_component_kt=0.0)
        headwind = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0, wind_component_kt=-80.0)

        self.assertGreater(headwind.target_cas_kt or 0.0, zero.target_cas_kt or 0.0)

    def test_tailwind_decreases_but_not_below_zero_wind_mrc(self) -> None:
        tailwind = _estimate_b77w(cost_index=0, altitude_ft=33000.0, gross_weight_kg=240000.0, wind_component_kt=100.0)

        self.assertGreaterEqual(tailwind.target_cas_kt or 0.0, tailwind.mrc_estimated_cas_kt)

    def test_ci_5000_moves_toward_limit_without_normal_ci_reaching_it(self) -> None:
        normal = _estimate_b77w(cost_index=100, altitude_ft=33000.0, gross_weight_kg=240000.0)
        lrc = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0)
        high = _estimate_b77w(cost_index=5000, altitude_ft=33000.0, gross_weight_kg=240000.0)
        maxed = _estimate_b77w(cost_index=9999, altitude_ft=33000.0, gross_weight_kg=240000.0)

        self.assertGreater(high.target_mach or 0.0, normal.target_mach or 0.0)
        self.assertGreater(high.target_mach or 0.0, lrc.target_mach or 0.0)
        self.assertGreater((high.target_mach or 0.0) - (lrc.target_mach or 0.0), 0.03)
        self.assertLess((maxed.target_mach or 0.0) - (high.target_mach or 0.0), 0.01)

    def test_typical_airline_ci_values_stay_below_lrc_equivalent_for_777(self) -> None:
        ci_90 = _estimate_b77w(cost_index=90, altitude_ft=33000.0, gross_weight_kg=240000.0)
        ci_120 = _estimate_b77w(cost_index=120, altitude_ft=33000.0, gross_weight_kg=240000.0)
        ci_150 = _estimate_b77w(cost_index=150, altitude_ft=33000.0, gross_weight_kg=240000.0)
        lrc = _estimate_b77w(cost_index=180, altitude_ft=33000.0, gross_weight_kg=240000.0)

        self.assertLess(ci_90.target_mach or 0.0, lrc.target_mach or 0.0)
        self.assertLess(ci_120.target_mach or 0.0, lrc.target_mach or 0.0)
        self.assertLess(ci_150.target_mach or 0.0, lrc.target_mach or 0.0)

    def test_maximum_ci_is_not_reduced_by_wind_or_isa(self) -> None:
        zero = _estimate_b77w(cost_index=9999, altitude_ft=33000.0, gross_weight_kg=240000.0)
        tailwind = _estimate_b77w(
            cost_index=9999,
            altitude_ft=33000.0,
            gross_weight_kg=240000.0,
            wind_component_kt=100.0,
        )
        headwind = _estimate_b77w(
            cost_index=9999,
            altitude_ft=33000.0,
            gross_weight_kg=240000.0,
            wind_component_kt=-100.0,
        )
        hot_isa = estimate_boeing_777_fmc_econ_speed(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            gross_weight_kg=240000.0,
            altitude_ft=33000.0,
            cost_index=9999,
            wind_component_kt=0.0,
            isa_deviation_c=10.0,
            aircraft_cfg=load_aircraft_config("b77w"),
            general_cfg=load_general_config(),
        )

        self.assertAlmostEqual(tailwind.target_mach or 0.0, zero.target_mach or 0.0, delta=0.0005)
        self.assertAlmostEqual(headwind.target_mach or 0.0, zero.target_mach or 0.0, delta=0.0005)
        self.assertAlmostEqual(hot_isa.target_mach or 0.0, zero.target_mach or 0.0, delta=0.0005)

    def test_empirical_override_takes_precedence_when_configured(self) -> None:
        cfg = load_aircraft_config("b77w")
        cfg = deepcopy(cfg)
        cfg["cost_index_mapping"]["optimizer_mode"] = "empirical_fmc_ci_table"
        cfg["cost_index_mapping"]["empirical_fmc_ci_table"] = [
            {"ci": 0, "cas_kt": 270},
            {"ci": 180, "cas_kt": 285},
            {"ci": 9999, "cas_kt": 325},
        ]

        target = estimate_fmc_ci_speed_target(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            gross_weight_kg=240000.0,
            altitude_ft=23000.0,
            cost_index=180,
            wind_component_kt=0.0,
            isa_deviation_c=0.0,
            aircraft_cfg=cfg,
            general_cfg=load_general_config(),
        )

        self.assertEqual(target.source, "empirical_fmc_ci_table")
        self.assertAlmostEqual(target.target_cas_kt or 0.0, 285.0, delta=0.6)

    def test_optimizer_uses_fmc_like_ci_mode_at_fl230(self) -> None:
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
            scenario_type=ScenarioType.FIXED_SPEED_FL,
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

        self.assertEqual(result.optimizer_mode, "boeing_777_fmc_like_ci")
        matching = [
            strategy
            for strategy in result.strategies
            if strategy.cost_index == 71 and strategy.cost_index_source == COST_INDEX_SOURCE_FMC_LIKE
        ]
        self.assertTrue(matching)
        self.assertEqual(matching[0].speed_mode, SPEED_MODE_CAS)
        self.assertLess(matching[0].mach, 0.75)

    def test_performance_derived_mode_labels_ci_as_display_only(self) -> None:
        current_state = CurrentFlightState(
            aircraft="A320",
            altitude_ft=33000.0,
            gross_weight_kg=62000.0,
            mach=0.78,
            current_cost_index=None,
            remaining_distance_nm=500.0,
            route_distance_nm=700.0,
            wind_component_kt=0.0,
            isa_deviation_c=0.0,
            fuel_remaining_kg=8000.0,
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
            aircraft_cfg=load_aircraft_config("a320"),
            flight_level_candidates=[330],
        )

        self.assertEqual(result.optimizer_mode, "performance_derived_speed")
        self.assertEqual(result.best_strategy.cost_index_source, COST_INDEX_SOURCE_DISPLAY)
        self.assertIn("not calibrated FMC CI", result.best_strategy.cost_index_label or "")


def _estimate_b77w(
    *,
    cost_index: int,
    altitude_ft: float,
    gross_weight_kg: float,
    wind_component_kt: float = 0.0,
):
    return estimate_boeing_777_fmc_econ_speed(
        aircraft="B77W",
        engine_variant="GE90-115BL",
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        cost_index=cost_index,
        wind_component_kt=wind_component_kt,
        isa_deviation_c=0.0,
        aircraft_cfg=load_aircraft_config("b77w"),
        general_cfg=load_general_config(),
    )


def _b77w_lrc(*, weight_kg: float, altitude_ft: float):
    performance = get_boeing_fcom_performance("b77w_ge90_115bl_jaa")
    return query_lrc_cruise(
        performance,
        weight_kg=weight_kg,
        altitude_ft=altitude_ft,
    )


if __name__ == "__main__":
    unittest.main()
