from __future__ import annotations

import unittest

from performance_engine.ci_mach import CiMachRequest, optimize_ci_mach
from performance_engine.ci_mach.calibration import available_b777_ci_mach_variants
from performance_engine.ci_mach.cost_model import CostIndexConverter
from performance_engine.ci_mach.calibration import load_aircraft_ci_mach_config


class CiMachEngineTests(unittest.TestCase):
    def test_demo_scenarios_are_reasonable(self) -> None:
        ci0 = _result(ci=0, wind=0)
        ci100 = _result(ci=100, wind=0)
        ci180 = _result(ci=180, wind=0)
        headwind = _result(ci=180, wind=-80)
        tailwind = _result(ci=180, wind=80)

        self.assertLess(ci0.recommended_mach, ci100.recommended_mach)
        self.assertLess(ci100.recommended_mach, ci180.recommended_mach)
        self.assertAlmostEqual(ci0.recommended_mach, 0.826, delta=0.008)
        self.assertAlmostEqual(ci180.recommended_mach, 0.840, delta=0.008)
        self.assertGreater(headwind.recommended_mach, ci180.recommended_mach)
        self.assertLess(tailwind.recommended_mach, ci180.recommended_mach)
        self.assertGreaterEqual(tailwind.recommended_mach, ci0.recommended_mach)

    def test_cost_index_converter_does_not_silently_treat_boeing_ci_as_kg_min(self) -> None:
        cfg = load_aircraft_ci_mach_config("B77W")
        converter = CostIndexConverter(cfg)

        self.assertEqual(converter.to_kg_per_min(10.0, "kg_per_min"), 10.0)
        self.assertAlmostEqual(converter.to_kg_per_min(60.0, "lb_per_hr"), 0.45359237)
        self.assertNotEqual(converter.to_kg_per_min(180.0, "boeing_ratio"), 180.0)

    def test_aircraft_data_files_cover_requested_b777_variants(self) -> None:
        self.assertEqual(
            available_b777_ci_mach_variants(),
            ["B772", "B773", "B77E", "B77F", "B77L", "B77W"],
        )

    def test_live_fuel_flow_anchor_scales_current_point(self) -> None:
        result = optimize_ci_mach(
            CiMachRequest(
                aircraft_variant="B772",
                cost_index=500,
                gross_weight_kg=185000.0,
                flight_level=400.0,
                wind_component_kt=-51.0,
                isa_deviation_c=0.78,
                live_fuel_flow_kg_h=5600.0,
                fuel_flow_reference_mach=0.854,
                fuel_flow_reference_gross_weight_kg=185000.0,
                fuel_flow_reference_isa_deviation_c=0.78,
                remaining_distance_nm=1000.0,
            ),
            include_candidates=True,
        )
        current = min(result.candidates, key=lambda candidate: abs(candidate.mach - 0.854))

        self.assertAlmostEqual(current.fuel_flow_kg_h, 5600.0, delta=15.0)
        self.assertIn("Live fuel-flow anchor scale factor", result.explanation)

    def test_refuses_static_fuel_flow_by_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "Live SimConnect fuel flow is required"):
            optimize_ci_mach(
                CiMachRequest(
                    aircraft_variant="B772",
                    cost_index=500,
                    gross_weight_kg=185000.0,
                    flight_level=400.0,
                    wind_component_kt=-51.0,
                    isa_deviation_c=0.78,
                    remaining_distance_nm=1000.0,
                ),
                include_candidates=False,
            )


def _result(*, ci: float, wind: float):
    return optimize_ci_mach(
        CiMachRequest(
            aircraft_variant="B77W",
            cost_index=ci,
            gross_weight_kg=240000.0,
            flight_level=350.0,
            wind_component_kt=wind,
            remaining_distance_nm=1000.0,
            require_live_fuel_flow=False,
        ),
        include_candidates=False,
    )


if __name__ == "__main__":
    unittest.main()
