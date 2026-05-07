from __future__ import annotations

import unittest
from unittest.mock import patch

from data_fetcher.sim.sim_models import RawSimState
from data_fetcher.sim.sim_normalizer import normalize_raw_sim_state
from data_fetcher.sim.simconnect_client import LB_TO_KG, SimConnectClient
from performance_engine.remaining_cruise_simulator import (
    RemainingCruiseInput,
    simulate_remaining_cruise,
)


class _FakeAircraftRequests:
    def __init__(self, values: dict[str, float]) -> None:
        self._values = values

    def get(self, key: str):  # noqa: ANN001 - test fake mirrors SimConnect API
        return self._values.get(key)


class LiveFuelFlowTests(unittest.TestCase):
    def test_prefers_turbine_pph_and_sums_all_engines(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "TURB_ENG_FUEL_FLOW_PPH:1": 10000.0,
                "TURB_ENG_FUEL_FLOW_PPH:2": 10500.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=None,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "TURB_ENG_FUEL_FLOW_PPH")
        self.assertIsNotNone(fuel_flow_kg_h)
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 20500.0 * LB_TO_KG, places=2)

    def test_converts_gph_to_kg_per_hour_using_fuel_density(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "ENG_FUEL_FLOW_GPH:1": 500.0,
                "ENG_FUEL_FLOW_GPH:2": 500.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=6.7,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "ENG_FUEL_FLOW_GPH")
        self.assertIsNotNone(fuel_flow_kg_h)
        self.assertAlmostEqual(
            fuel_flow_kg_h or 0.0,
            1000.0 * 6.7 * LB_TO_KG,
            places=2,
        )

    def test_falls_back_to_fuel_total_burn_delta_when_no_engine_flow_simvar_exists(
        self,
    ) -> None:
        client = SimConnectClient()

        with patch(
            "data_fetcher.sim.simconnect_client.time.monotonic",
            side_effect=[100.0, 160.0],
        ):
            self.assertIsNone(client._estimate_fuel_flow_from_total_fuel_lb(100000.0))
            fuel_flow_kg_h = client._estimate_fuel_flow_from_total_fuel_lb(99000.0)

        self.assertIsNotNone(fuel_flow_kg_h)
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 1000.0 * LB_TO_KG * 60.0, places=2)

    def test_normalizer_preserves_live_fuel_flow_metadata(self) -> None:
        raw = RawSimState(
            fuel_flow_kg_h=8450.6,
            fuel_flow_source="TURB_ENG_FUEL_FLOW_PPH",
        )

        live = normalize_raw_sim_state(raw)

        self.assertEqual(live.fuel_flow_kg_h, 8450.6)
        self.assertEqual(live.fuel_flow_source, "TURB_ENG_FUEL_FLOW_PPH")

    def test_live_fuel_flow_anchor_scales_cruise_burn(self) -> None:
        baseline_request = RemainingCruiseInput(
            aircraft="A320",
            altitude_ft=35000.0,
            gross_weight_kg=65000.0,
            mach=0.78,
            remaining_distance_nm=300.0,
            wind_component_kt=0.0,
        )
        baseline = simulate_remaining_cruise(baseline_request)

        anchored = simulate_remaining_cruise(
            RemainingCruiseInput(
                aircraft="A320",
                altitude_ft=35000.0,
                gross_weight_kg=65000.0,
                mach=0.78,
                remaining_distance_nm=300.0,
                wind_component_kt=0.0,
                live_fuel_flow_kg_h=baseline.avg_fuel_flow_kg_h * 1.25,
                live_fuel_flow_source="TURB_ENG_FUEL_FLOW_PPH",
                fuel_flow_reference_altitude_ft=35000.0,
                fuel_flow_reference_gross_weight_kg=65000.0,
                fuel_flow_reference_mach=0.78,
                fuel_flow_reference_isa_deviation_c=0.0,
            )
        )

        self.assertIn("simconnect_anchor", anchored.performance_model)
        self.assertAlmostEqual(
            anchored.avg_fuel_flow_kg_h / baseline.avg_fuel_flow_kg_h,
            1.25,
            delta=0.03,
        )

    def test_live_fuel_flow_anchor_preserves_mach_sensitivity(self) -> None:
        reference = simulate_remaining_cruise(
            RemainingCruiseInput(
                aircraft="A320",
                altitude_ft=35000.0,
                gross_weight_kg=65000.0,
                mach=0.78,
                remaining_distance_nm=300.0,
                wind_component_kt=0.0,
            )
        )

        slow = simulate_remaining_cruise(
            RemainingCruiseInput(
                aircraft="A320",
                altitude_ft=35000.0,
                gross_weight_kg=65000.0,
                mach=0.78,
                remaining_distance_nm=300.0,
                wind_component_kt=0.0,
                live_fuel_flow_kg_h=reference.avg_fuel_flow_kg_h,
                live_fuel_flow_source="TURB_ENG_FUEL_FLOW_PPH",
                fuel_flow_reference_altitude_ft=35000.0,
                fuel_flow_reference_gross_weight_kg=65000.0,
                fuel_flow_reference_mach=0.78,
                fuel_flow_reference_isa_deviation_c=0.0,
            )
        )
        fast = simulate_remaining_cruise(
            RemainingCruiseInput(
                aircraft="A320",
                altitude_ft=35000.0,
                gross_weight_kg=65000.0,
                mach=0.80,
                remaining_distance_nm=300.0,
                wind_component_kt=0.0,
                live_fuel_flow_kg_h=reference.avg_fuel_flow_kg_h,
                live_fuel_flow_source="TURB_ENG_FUEL_FLOW_PPH",
                fuel_flow_reference_altitude_ft=35000.0,
                fuel_flow_reference_gross_weight_kg=65000.0,
                fuel_flow_reference_mach=0.78,
                fuel_flow_reference_isa_deviation_c=0.0,
            )
        )

        self.assertGreater(fast.avg_fuel_flow_kg_h, slow.avg_fuel_flow_kg_h)
        self.assertLess(fast.remaining_time_min, slow.remaining_time_min)


if __name__ == "__main__":
    unittest.main()
