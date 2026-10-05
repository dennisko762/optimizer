from __future__ import annotations

import unittest
from unittest.mock import patch

from data_fetcher.sim.sim_models import RawSimState
from data_fetcher.sim.sim_normalizer import normalize_raw_sim_state
from data_fetcher.sim.simconnect_client import (
    LB_TO_KG,
    SimConnectClient,
    _convert_fuel_flow_to_kg_h,
    _normalize_fuel_flow_unit,
)
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

    def test_uses_eng_fuel_flow_pph_when_turbine_var_is_absent(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "ENG_FUEL_FLOW_PPH:1": 6100.0,
                "ENG_FUEL_FLOW_PPH:2": 6200.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=None,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "ENG_FUEL_FLOW_PPH")
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 12300.0 * LB_TO_KG, places=2)

    def test_uses_deprecated_ssl_pph_when_that_is_the_available_var(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "ENG_FUEL_FLOW_PPH_SSL:1": 6000.0,
                "ENG_FUEL_FLOW_PPH_SSL:2": 6000.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=None,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "ENG_FUEL_FLOW_PPH_SSL")
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 12000.0 * LB_TO_KG, places=2)

    def test_ignores_partial_engine_index_data_for_known_engine_count(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "TURB_ENG_FUEL_FLOW_PPH:1": 12000.0,
                "ENG_FUEL_FLOW_PPH:1": 6100.0,
                "ENG_FUEL_FLOW_PPH:2": 6200.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=None,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "ENG_FUEL_FLOW_PPH")
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 12300.0 * LB_TO_KG, places=2)

    def test_ignores_implausibly_large_direct_var_and_uses_next_source(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "TURB_ENG_FUEL_FLOW_PPH:1": 999999.0,
                "TURB_ENG_FUEL_FLOW_PPH:2": 999999.0,
                "ENG_FUEL_FLOW_PPH:1": 6100.0,
                "ENG_FUEL_FLOW_PPH:2": 6200.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=None,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "ENG_FUEL_FLOW_PPH")
        self.assertAlmostEqual(fuel_flow_kg_h or 0.0, 12300.0 * LB_TO_KG, places=2)

    def test_uses_fuelsystem_line_flow_as_last_resort(self) -> None:
        client = SimConnectClient()
        aq = _FakeAircraftRequests(
            {
                "FUELSYSTEM_LINE_FUEL_FLOW:1": 400.0,
                "FUELSYSTEM_LINE_FUEL_FLOW:2": 400.0,
            }
        )

        fuel_flow_kg_h, source = client._resolve_fuel_flow_measurement(
            aq,
            engine_count=2,
            engine_type=1,
            fuel_weight_per_gallon_lb=6.7,
            fuel_remaining_lb=50000.0,
        )

        self.assertEqual(source, "FUELSYSTEM_LINE_FUEL_FLOW")
        self.assertAlmostEqual(
            fuel_flow_kg_h or 0.0,
            800.0 * 6.7 * LB_TO_KG,
            places=2,
        )

    def test_fuel_flow_unit_conversions_are_explicit(self) -> None:
        self.assertEqual(_normalize_fuel_flow_unit("pph"), "lb_per_hour")
        self.assertEqual(_normalize_fuel_flow_unit("lb/min"), "lb_per_minute")
        self.assertEqual(_normalize_fuel_flow_unit("kg/hr"), "kg_per_hour")
        self.assertEqual(_normalize_fuel_flow_unit("kg/min"), "kg_per_minute")
        self.assertEqual(_normalize_fuel_flow_unit("gph"), "gallon_per_hour")
        self.assertEqual(_normalize_fuel_flow_unit("gal/min"), "gallon_per_minute")

        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(6000.0, unit="lb_per_hour", fuel_weight_per_gallon_lb=6.7) or 0.0,
            6000.0 * LB_TO_KG,
            places=5,
        )
        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(100.0, unit="lb_per_minute", fuel_weight_per_gallon_lb=6.7) or 0.0,
            100.0 * 60.0 * LB_TO_KG,
            places=5,
        )
        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(2800.0, unit="kg_per_hour", fuel_weight_per_gallon_lb=6.7) or 0.0,
            2800.0,
            places=5,
        )
        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(46.0, unit="kg_per_minute", fuel_weight_per_gallon_lb=6.7) or 0.0,
            2760.0,
            places=5,
        )
        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(400.0, unit="gallon_per_hour", fuel_weight_per_gallon_lb=6.7) or 0.0,
            400.0 * 6.7 * LB_TO_KG,
            places=5,
        )
        self.assertAlmostEqual(
            _convert_fuel_flow_to_kg_h(6.0, unit="gallon_per_minute", fuel_weight_per_gallon_lb=6.7) or 0.0,
            6.0 * 60.0 * 6.7 * LB_TO_KG,
            places=5,
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
