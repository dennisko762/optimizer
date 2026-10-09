"""/api/optimize must compute from the real airframe, or refuse.

Covers the M3 live-optimizer path end to end through the FastAPI app:

- the body the live EFB actually sends (``aircraftConfig`` absent, aircraft
  type carried on ``flightState.aircraft``) returns 200 with a recommendation;
- the pre-fix body (``"aircraftConfig": null``) no longer 422s;
- the response states which performance profile was used, so the crew can see
  the numbers are not from another airframe's tables;
- an unresolvable airframe is REFUSED with a readable 422 rather than silently
  optimized against the old "a320" default.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from optimizer.api.app import create_app


def _body(**overrides) -> dict:
    """A complete live-state body — fuel flow included, as the service requires."""
    flight_state = {
        "aircraft": "A359",
        "altitudeFt": 37000.0,
        "grossWeightKg": 210000.0,
        "mach": 0.84,
        "currentCostIndex": 60,
        "remainingDistanceNm": 420.0,
        "windComponentKt": -15.0,
        "isaDeviationC": 3.0,
        "fuelRemainingKg": 28000.0,
        "fuelFlowKgH": 6400.0,
        "fuelFlowSource": "simconnect",
        "groundSpeedKt": 470.0,
    }
    flight_state.update(overrides.pop("flightState", {}))
    body = {
        "action": "NORMAL_RECALC",
        "flightState": flight_state,
        "flightContext": {"origin": "EHAM", "destination": "ENZV"},
        "payload": {},
    }
    body.update(overrides)
    return body


class LiveOptimizeAircraftConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(create_app())

    def test_live_body_without_aircraft_config_returns_a_recommendation(self) -> None:
        response = self.client.post("/api/optimize", json=_body())

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["recommendation"])
        self.assertIn("bestStrategy", payload)

    def test_explicit_null_aircraft_config_no_longer_422s(self) -> None:
        # The exact pre-fix request: SimConnect had no config, so the client
        # sent an explicit JSON null and Pydantic rejected it.
        response = self.client.post("/api/optimize", json=_body(aircraftConfig=None))

        self.assertEqual(response.status_code, 200, response.text)

    def test_response_states_the_config_actually_used(self) -> None:
        payload = self.client.post("/api/optimize", json=_body()).json()

        self.assertEqual(payload["aircraftConfig"], "a359")
        self.assertEqual(payload["aircraftConfigSource"], "aircraft:A359")

    def test_simconnect_title_resolves_to_the_same_config(self) -> None:
        payload = self.client.post(
            "/api/optimize",
            json=_body(flightState={"aircraft": "Airbus A350-900 Qatar Airways"}),
        ).json()

        self.assertEqual(payload["aircraftConfig"], "a359")

    def test_777_type_uses_its_own_config_not_the_airbus_one(self) -> None:
        payload = self.client.post(
            "/api/optimize",
            json=_body(flightState={"aircraft": "B77W", "grossWeightKg": 280000.0}),
        ).json()

        self.assertEqual(payload["aircraftConfig"], "b77w")

    def test_explicit_config_key_is_honoured(self) -> None:
        payload = self.client.post(
            "/api/optimize",
            json=_body(aircraftConfig="a359"),
        ).json()

        self.assertEqual(payload["aircraftConfig"], "a359")
        self.assertEqual(payload["aircraftConfigSource"], "request")

    def test_unresolvable_airframe_is_refused_with_a_readable_reason(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json=_body(flightState={"aircraft": None}),
        )

        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, str)
        self.assertIn("Aircraft type not resolved", detail)
        # No silent default airframe.
        self.assertNotIn("a320", detail.lower())

    def test_unknown_airframe_is_refused(self) -> None:
        response = self.client.post(
            "/api/optimize",
            json=_body(flightState={"aircraft": "ZZZZ"}),
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("not in the aircraft catalog", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
