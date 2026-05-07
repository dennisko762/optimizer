from __future__ import annotations

import unittest
from unittest.mock import patch

from optimizer.config_loader import load_aircraft_config, load_general_config
from performance_engine.ci_table.ci_mach_table_generator import generate_ci_mach_table
from performance_engine.ci_table.ci_mach_table_lookup import (
    cost_index_to_mach_from_table,
)
from performance_engine.database.boeing_fcom_lookup import query_lrc_cruise
from performance_engine.database.boeing_fcom_registry import get_boeing_fcom_performance
from performance_engine.remaining_cruise_simulator import RemainingCruiseResult


class CiMachTableTests(unittest.TestCase):
    def test_b77w_table_generation_returns_monotonic_ci_bands(self) -> None:
        table = _build_b77w_table()

        lower_bounds = [band.lower_ci for band in table.bands]
        self.assertEqual(lower_bounds, sorted(lower_bounds))

        for band in table.bands:
            if band.upper_ci is not None:
                self.assertGreaterEqual(band.upper_ci, band.lower_ci)

    def test_ci_70_does_not_map_to_mach_0860_in_normal_table(self) -> None:
        table = _build_b77w_table()

        mach = cost_index_to_mach_from_table(70, table)

        self.assertIsNotNone(mach)
        self.assertLess(mach, 0.860)

    def test_mach_0860_only_appears_when_recovery_is_enabled(self) -> None:
        normal = _build_b77w_table(include_recovery=False)
        recovery = _build_b77w_table(include_recovery=True)

        self.assertNotIn(0.860, [band.mach for band in normal.bands])
        self.assertIn(0.860, [band.mach for band in recovery.bands])

    def test_fcom_hybrid_warnings_are_propagated(self) -> None:
        table = _build_b77w_table(flight_level=430, gross_weight_kg=300000.0)

        joined = " ".join(table.warnings)
        self.assertIn("Reference LRC Mach", joined)
        self.assertTrue(
            "outside the published LRC envelope" in joined
            or "exceeds highest reachable FCOM point" in joined
        )

    def test_lrc_mach_from_query_is_used_as_anchor(self) -> None:
        aircraft_cfg = load_aircraft_config("b77w")
        performance = get_boeing_fcom_performance("b77w_ge90_115bl_jaa")
        query = query_lrc_cruise(
            performance,
            weight_kg=280000.0,
            altitude_ft=33000.0,
        )

        table = generate_ci_mach_table(
            aircraft="B77W",
            engine_variant="GE90-115BL",
            gross_weight_kg=280000.0,
            flight_level=330,
            isa_deviation_c=0.0,
            cg_percent_mac=None,
            wind_component_kt=0.0,
            aircraft_cfg=aircraft_cfg,
            general_cfg=load_general_config(),
        )

        self.assertIn(round(query.mach, 3), [band.mach for band in table.bands])

    def test_higher_mach_produces_lower_time_and_higher_fuel(self) -> None:
        table = _build_b77w_table()

        lowest = min(table.bands, key=lambda band: band.mach)
        highest = max(table.bands, key=lambda band: band.mach)

        self.assertLess(highest.time_min_per_1000nm, lowest.time_min_per_1000nm)
        self.assertGreater(highest.fuel_kg_per_h, lowest.fuel_kg_per_h)

    def test_generated_ci_bands_are_stable_for_1000nm_reference_distance(self) -> None:
        table_a = _build_b77w_table(reference_distance_nm=1000.0)
        table_b = _build_b77w_table(reference_distance_nm=1000.0)

        self.assertEqual(
            [(band.mach, band.lower_ci, band.upper_ci) for band in table_a.bands],
            [(band.mach, band.lower_ci, band.upper_ci) for band in table_b.bands],
        )

    def test_profile_collapse_warning_is_exposed(self) -> None:
        def _flat_simulation(*args, **kwargs):
            request = args[0]
            return RemainingCruiseResult(
                aircraft=request.aircraft,
                altitude_ft=request.altitude_ft,
                mach=round(request.mach, 3),
                initial_weight_kg=request.gross_weight_kg,
                end_weight_kg=request.gross_weight_kg - 5000.0,
                remaining_distance_nm=request.remaining_distance_nm,
                remaining_time_min=120.0,
                remaining_fuel_kg=10000.0,
                tas_kt=450.0,
                ground_speed_kt=440.0,
                avg_fuel_flow_kg_h=5000.0,
                fuel_per_nm_kg=10.0,
                fuel_per_min_kg=83.3,
                performance_model="mock_flat",
                source_aircraft=request.aircraft,
                segment_count=1,
                warnings=[],
            )

        with patch(
            "performance_engine.ci_table.ci_mach_table_generator.simulate_remaining_cruise",
            side_effect=_flat_simulation,
        ):
            table = _build_b77w_table()

        self.assertTrue(
            any("collapsed to CI 0" in warning for warning in table.warnings)
        )


def _build_b77w_table(
    *,
    include_recovery: bool = False,
    gross_weight_kg: float = 280000.0,
    flight_level: int = 330,
    reference_distance_nm: float = 1000.0,
):
    return generate_ci_mach_table(
        aircraft="B77W",
        engine_variant="GE90-115BL",
        gross_weight_kg=gross_weight_kg,
        flight_level=flight_level,
        isa_deviation_c=0.0,
        cg_percent_mac=None,
        wind_component_kt=0.0,
        aircraft_cfg=load_aircraft_config("b77w"),
        general_cfg=load_general_config(),
        include_recovery=include_recovery,
        reference_distance_nm=reference_distance_nm,
    )


if __name__ == "__main__":
    unittest.main()
