from __future__ import annotations

import unittest

from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.simconnect_routes import _live_aircraft_code, _live_aircraft_config
from optimizer.configs.aircraft.aircraft_catalog import resolve_aircraft_from_title


class AircraftTitleDetectionTests(unittest.TestCase):
    def test_detects_b777_200er_from_addon_title(self) -> None:
        entry = resolve_aircraft_from_title("PMDG 777-200ER GE90 KLM")

        self.assertIsNotNone(entry)
        self.assertEqual(entry.simbrief_code, "B772")
        self.assertEqual(entry.config_key, "b772")

    def test_detects_b777_300er_from_addon_title(self) -> None:
        entry = resolve_aircraft_from_title("PMDG Boeing 777-300ER Emirates")

        self.assertIsNotNone(entry)
        self.assertEqual(entry.simbrief_code, "B77W")
        self.assertEqual(entry.config_key, "b77w")

    def test_live_patch_helpers_return_code_and_config(self) -> None:
        live = LiveSimState(aircraft_title="777-200ER Trent 895")

        self.assertEqual(_live_aircraft_code(live), "B772")
        self.assertEqual(_live_aircraft_config(live), "b772")


if __name__ == "__main__":
    unittest.main()
