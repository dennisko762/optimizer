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
from performance_engine.boeing_777_fmc_ci_speed_model import (
    COST_INDEX_SOURCE_AIRBUS_FAMILY,
    COST_INDEX_SOURCE_CALIBRATED,
    SPEED_MODE_CAS,
    SPEED_MODE_MACH,
    estimate_fmc_ci_speed_target,
)


class AirbusFamilyFmcCiSpeedModelTests(unittest.TestCase):
    def test_a343_ci50_tracks_family_lrc_at_cruise(self) -> None:
        target = _estimate_airbus(
            aircraft_key="a343",
            aircraft="A343",
            cost_index=50,
            altitude_ft=35000.0,
            gross_weight_kg=230000.0,
        )

        self.assertEqual(target.speed_mode, SPEED_MODE_MACH)
        self.assertAlmostEqual(target.target_mach or 0.0, 0.82, delta=0.015)

    def test_a343_ci100_at_fl230_stays_below_m080(self) -> None:
        target = _estimate_airbus(
            aircraft_key="a343",
            aircraft="A343",
            cost_index=100,
            altitude_ft=23000.0,
            gross_weight_kg=230000.0,
        )

        self.assertEqual(target.speed_mode, SPEED_MODE_CAS)
        self.assertLess(target.target_mach or 0.0, 0.80)
        self.assertGreater(target.target_mach or 0.0, 0.70)

    def test_a343_headwind_increases_econ_target_speed(self) -> None:
        zero = _estimate_airbus(
            aircraft_key="a343",
            aircraft="A343",
            cost_index=50,
            altitude_ft=35000.0,
            gross_weight_kg=230000.0,
            wind_component_kt=0.0,
        )
        headwind = _estimate_airbus(
            aircraft_key="a343",
            aircraft="A343",
            cost_index=50,
            altitude_ft=35000.0,
            gross_weight_kg=230000.0,
            wind_component_kt=-50.0,
        )

        self.assertGreater(headwind.target_mach or 0.0, zero.target_mach or 0.0)

    def test_a343_tailwind_does_not_go_below_zero_wind_mrc(self) -> None:
        target = _estimate_airbus(
            aircraft_key="a343",
            aircraft="A343",
            cost_index=0,
            altitude_ft=33000.0,
            gross_weight_kg=220000.0,
            wind_component_kt=100.0,
        )

        self.assertGreaterEqual(target.target_cas_kt or 0.0, target.mrc_estimated_cas_kt)

    def test_a343_optimizer_uses_airbus_family_mode_with_calibrated_label(self) -> None:
        result = _optimize_airbus(
            aircraft_key="a343",
            aircraft="A343",
            altitude_ft=23000.0,
            mach=0.77,
            current_cost_index=100,
            gross_weight_kg=230000.0,
        )

        self.assertEqual(result.optimizer_mode, "airbus_family_fmc_like_ci")
        modeled = [
            strategy
            for strategy in result.strategies
            if strategy.cost_index_source == COST_INDEX_SOURCE_CALIBRATED
        ]
        self.assertTrue(modeled)
        self.assertTrue(
            any("Calibrated FMC CI" in (strategy.cost_index_label or "") for strategy in modeled)
        )

    def test_a346_uses_conservative_airbus_family_fallback_label(self) -> None:
        result = _optimize_airbus(
            aircraft_key="a346",
            aircraft="A346",
            altitude_ft=23000.0,
            mach=0.78,
            current_cost_index=100,
            gross_weight_kg=300000.0,
        )

        self.assertEqual(result.optimizer_mode, "airbus_family_fmc_like_ci")
        fallback = [
            strategy
            for strategy in result.strategies
            if strategy.cost_index_source == COST_INDEX_SOURCE_AIRBUS_FAMILY
        ]
        self.assertTrue(fallback)
        self.assertTrue(
            any("Airbus-family FMC CI fallback" in (strategy.cost_index_label or "") for strategy in fallback)
        )

    def test_a346_high_cruise_can_be_mach_primary(self) -> None:
        target = _estimate_airbus(
            aircraft_key="a346",
            aircraft="A346",
            cost_index=50,
            altitude_ft=35000.0,
            gross_weight_kg=300000.0,
        )

        self.assertEqual(target.speed_mode, SPEED_MODE_MACH)


def _estimate_airbus(
    *,
    aircraft_key: str,
    aircraft: str,
    cost_index: int,
    altitude_ft: float,
    gross_weight_kg: float,
    wind_component_kt: float = 0.0,
):
    return estimate_fmc_ci_speed_target(
        aircraft=aircraft,
        engine_variant=None,
        gross_weight_kg=gross_weight_kg,
        altitude_ft=altitude_ft,
        cost_index=cost_index,
        wind_component_kt=wind_component_kt,
        isa_deviation_c=0.0,
        aircraft_cfg=load_aircraft_config(aircraft_key),
        general_cfg=load_general_config(),
    )


def _optimize_airbus(
    *,
    aircraft_key: str,
    aircraft: str,
    altitude_ft: float,
    mach: float,
    current_cost_index: int,
    gross_weight_kg: float,
):
    current_state = CurrentFlightState(
        aircraft=aircraft,
        altitude_ft=altitude_ft,
        gross_weight_kg=gross_weight_kg,
        mach=mach,
        current_cost_index=current_cost_index,
        remaining_distance_nm=1200.0,
        route_distance_nm=1500.0,
        wind_component_kt=0.0,
        isa_deviation_c=0.0,
        fuel_remaining_kg=28000.0,
        ground_speed_kt=430.0,
    )
    scenario = ScenarioInput(
        trigger=OperationalTrigger.MANUAL_RECALCULATION,
        scenario_type=ScenarioType.NORMAL_COST_OPTIMIZATION,
        priority=ScenarioPriority.MEDIUM,
        flight_context=FlightContextInput(),
    )
    interpreted = interpret_scenario(scenario)

    return optimize_cost(
        current_state=current_state,
        interpreted_scenario=interpreted,
        general_cfg=load_general_config(),
        aircraft_cfg=load_aircraft_config(aircraft_key),
        flight_level_candidates=[int(round(altitude_ft / 100.0))],
    )


if __name__ == "__main__":
    unittest.main()
