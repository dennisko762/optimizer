from __future__ import annotations

import unittest

from data_fetcher.sim.sim_models import LiveSimState, RawSimState
from data_fetcher.sim.sim_normalizer import normalize_raw_sim_state


class LiveWindComponentTests(unittest.TestCase):
    def test_prefers_longitudinal_aircraft_wind_z_over_lateral_x(self) -> None:
        live = LiveSimState(
            wind_x_kt=-33.0,
            wind_z_kt=40.0,
        )

        self.assertEqual(live.wind_component_along_track(), 40.0)

    def test_fallback_ambient_wind_returns_positive_tailwind(self) -> None:
        live = LiveSimState(
            wind_velocity_kt=55.0,
            wind_direction_deg=243.0,
        )

        component = live.wind_component_along_track(track_deg_true=98.0)

        self.assertIsNotNone(component)
        self.assertAlmostEqual(component or 0.0, 45.0, delta=1.0)

    def test_normalizer_preserves_aircraft_wind_z(self) -> None:
        raw = RawSimState(
            wind_x_kt=-33.4,
            wind_z_kt=40.6,
        )

        live = normalize_raw_sim_state(raw)

        self.assertEqual(live.wind_x_kt, -33.4)
        self.assertEqual(live.wind_z_kt, 40.6)


if __name__ == "__main__":
    unittest.main()
