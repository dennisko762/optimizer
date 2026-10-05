from __future__ import annotations

import unittest

from performance_engine.ci_mach.atmosphere import atmosphere_at, mach_to_tas_kt
from performance_engine.ci_mach.units import (
    flight_level_to_ft,
    ft_to_m,
    kg_to_lb,
    kt_to_mps,
    lb_per_h_to_kg_per_min,
    lb_to_kg,
    mps_to_kt,
)


class CiMachUnitTests(unittest.TestCase):
    def test_basic_unit_conversions(self) -> None:
        self.assertAlmostEqual(ft_to_m(1000.0), 304.8)
        self.assertAlmostEqual(flight_level_to_ft(350.0), 35000.0)
        self.assertAlmostEqual(kt_to_mps(100.0), 51.444444, places=5)
        self.assertAlmostEqual(mps_to_kt(51.444444), 100.0, places=4)
        self.assertAlmostEqual(lb_to_kg(1000.0), 453.59237)
        self.assertAlmostEqual(kg_to_lb(453.59237), 1000.0)
        self.assertAlmostEqual(lb_per_h_to_kg_per_min(6000.0), 45.359237)

    def test_atmosphere_and_mach_to_tas_respond_to_temperature(self) -> None:
        isa = atmosphere_at(35000.0)
        hot = atmosphere_at(35000.0, isa_deviation_c=10.0)

        self.assertGreater(hot.temperature_k, isa.temperature_k)
        self.assertGreater(hot.speed_of_sound_mps, isa.speed_of_sound_mps)
        self.assertGreater(
            mach_to_tas_kt(0.84, 35000.0, isa_deviation_c=10.0),
            mach_to_tas_kt(0.84, 35000.0, isa_deviation_c=0.0),
        )


if __name__ == "__main__":
    unittest.main()
