from __future__ import annotations

import unittest

from optimizer.api.api_models import EfbAction, OptimizeRequest
from optimizer.app.ci_optimization_service import CiOptimizationService


class LiveFuelFlowRequiredTests(unittest.TestCase):
    def test_service_refuses_optimization_without_simconnect_fuel_flow(self) -> None:
        request = OptimizeRequest(
            action=EfbAction.NORMAL_RECALC,
            aircraftConfig="a320",
            flightState={
                "aircraft": "A320",
                "altitudeFt": 33000.0,
                "grossWeightKg": 65000.0,
                "mach": 0.78,
                "remainingDistanceNm": 300.0,
                "windComponentKt": 0.0,
                "isaDeviationC": 0.0,
                "fuelRemainingKg": 5000.0,
                "groundSpeedKt": 450.0,
            },
            flightContext={},
            payload={},
        )

        with self.assertRaisesRegex(ValueError, "Live SimConnect fuel flow is required"):
            CiOptimizationService().optimize(request)


if __name__ == "__main__":
    unittest.main()
