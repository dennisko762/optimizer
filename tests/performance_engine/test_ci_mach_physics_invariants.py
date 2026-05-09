from __future__ import annotations

from pathlib import Path
import unittest

from performance_engine.ci_mach import CiMachRequest, optimize_ci_mach


class CiMachPhysicsInvariantTests(unittest.TestCase):
    def test_recommended_mach_generally_increases_with_ci(self) -> None:
        machs = [_result(ci).recommended_mach for ci in [0, 50, 100, 180, 500]]

        for lower, upper in zip(machs, machs[1:]):
            self.assertLessEqual(lower, upper + 0.001)

    def test_ci_zero_selects_minimum_fuel_per_nm_candidate(self) -> None:
        result = optimize_ci_mach(_request(0), include_candidates=True)
        min_fuel = min(candidate.fuel_kg_per_nm for candidate in result.candidates)

        self.assertAlmostEqual(result.fuel_kg_per_nm, min_fuel, delta=0.001)

    def test_weight_changes_optimum_and_increases_fuel_at_same_mach(self) -> None:
        light = optimize_ci_mach(_request(180, gross_weight_kg=220000.0), include_candidates=True)
        heavy = optimize_ci_mach(_request(180, gross_weight_kg=280000.0), include_candidates=True)

        light_m084 = _candidate_at(light, 0.84)
        heavy_m084 = _candidate_at(heavy, 0.84)
        self.assertGreater(heavy_m084.fuel_flow_kg_h, light_m084.fuel_flow_kg_h)
        self.assertNotEqual(light.recommended_mach, heavy.recommended_mach)

    def test_altitude_and_temperature_change_tas_and_fuel(self) -> None:
        fl310 = optimize_ci_mach(_request(180, flight_level=310.0), include_candidates=True)
        fl390 = optimize_ci_mach(_request(180, flight_level=390.0), include_candidates=True)
        hot = optimize_ci_mach(_request(180, isa_deviation_c=10.0), include_candidates=True)
        std = optimize_ci_mach(_request(180, isa_deviation_c=0.0), include_candidates=True)

        self.assertNotEqual(fl310.recommended_tas_kt, fl390.recommended_tas_kt)
        self.assertNotEqual(_candidate_at(hot, 0.84).fuel_flow_kg_h, _candidate_at(std, 0.84).fuel_flow_kg_h)

    def test_bounds_are_respected(self) -> None:
        request = _request(999)
        request.constraints.min_mach = 0.82
        request.constraints.max_mach = 0.835
        request.constraints.mmo = 0.833

        result = optimize_ci_mach(request, include_candidates=False)
        self.assertGreaterEqual(result.recommended_mach, 0.82)
        self.assertLessEqual(result.recommended_mach, 0.833)

    def test_docs_exist_and_mention_main_sources(self) -> None:
        root = Path(__file__).resolve().parents[2]
        sources = (root / "docs" / "ci_mach_engine_sources.md").read_text(encoding="utf-8")
        math = (root / "docs" / "ci_mach_engine_math.md").read_text(encoding="utf-8")

        self.assertIn("https://www.airinter-va.org/CIPNT/Boeing_CI_2.pdf", sources)
        self.assertIn("https://studylib.net/doc/25749428/777-fms-pilot-guide", sources)
        self.assertIn("https://crewserver1.piac.com.pk/Documents/Manual/B777/777.FCTM.REV.25.27FEB26.pdf", sources)
        self.assertIn("https://openap.dev/drag_thrust.html", sources)
        self.assertIn("10.2322/tjsass.65.56", sources)
        self.assertIn("total_cost_equiv_kg_per_nm", math)
        self.assertIn("No exact Boeing FMC equivalence", sources)


def _request(
    ci: float,
    *,
    gross_weight_kg: float = 240000.0,
    flight_level: float = 350.0,
    isa_deviation_c: float = 0.0,
) -> CiMachRequest:
    return CiMachRequest(
        aircraft_variant="B77W",
        cost_index=ci,
        gross_weight_kg=gross_weight_kg,
        flight_level=flight_level,
        isa_deviation_c=isa_deviation_c,
        remaining_distance_nm=1000.0,
        require_live_fuel_flow=False,
    )


def _result(ci: float):
    return optimize_ci_mach(_request(ci), include_candidates=False)


def _candidate_at(result, mach: float):
    return min(result.candidates, key=lambda candidate: abs(candidate.mach - mach))


if __name__ == "__main__":
    unittest.main()
