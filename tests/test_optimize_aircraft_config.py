"""Aircraft performance config resolution for /api/optimize.

Pins the contract that the live EFB (efb-ui liveMappers.buildLiveOptimizeRequest)
relies on, and the data-integrity rule from AGENTS.md: never compute a
recommendation from another airframe's performance tables.

Background: ``aircraft_config`` used to be ``str = Field(default="a320")``. A
Pydantic default applies only to an ABSENT key, so the live optimizer — which
sent ``"aircraftConfig": null`` whenever SimConnect had no config — 422'd on
every call and the recommendation panel never produced a recommendation.
"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from optimizer.api.aircraft_config_resolver import (
    AircraftConfigUnresolvedError,
    resolve_aircraft_config,
)
from optimizer.api.api_models import OptimizeRequest
from optimizer.config_loader import aircraft_config_exists


def _request_payload(**overrides):
    body = {
        "action": "NORMAL_RECALC",
        "flightState": {
            "altitudeFt": 35000,
            "grossWeightKg": 210000,
            "mach": 0.84,
            "remainingDistanceNm": 1200,
            "fuelFlowKgH": 6100,
        },
        "flightContext": {},
    }
    body.update(overrides)
    return body


class OptimizeRequestAircraftConfigContractTests(unittest.TestCase):
    """The request model must accept absent AND explicitly-null keys."""

    def test_absent_aircraft_config_is_none(self) -> None:
        request = OptimizeRequest.model_validate(_request_payload())

        self.assertIsNone(request.aircraft_config)

    def test_explicit_null_aircraft_config_is_accepted(self) -> None:
        # This exact body used to raise "Input should be a valid string".
        request = OptimizeRequest.model_validate(
            _request_payload(aircraftConfig=None)
        )

        self.assertIsNone(request.aircraft_config)

    def test_explicit_null_aircraft_identifier_is_accepted(self) -> None:
        payload = _request_payload()
        payload["flightState"]["aircraft"] = None

        request = OptimizeRequest.model_validate(payload)

        self.assertIsNone(request.flight_state.aircraft)

    def test_explicit_aircraft_config_round_trips(self) -> None:
        request = OptimizeRequest.model_validate(
            _request_payload(aircraftConfig="a359")
        )

        self.assertEqual(request.aircraft_config, "a359")

    def test_required_live_fields_are_still_required(self) -> None:
        payload = _request_payload()
        payload["flightState"].pop("altitudeFt")

        with self.assertRaises(ValidationError):
            OptimizeRequest.model_validate(payload)


class ResolveAircraftConfigTests(unittest.TestCase):
    def test_explicit_key_wins(self) -> None:
        resolved = resolve_aircraft_config("b77w", aircraft="A359")

        self.assertEqual(resolved.config_key, "b77w")
        self.assertEqual(resolved.source, "request")

    def test_a350_900_ofp_type_maps_to_a359(self) -> None:
        resolved = resolve_aircraft_config(None, aircraft="A359")

        self.assertEqual(resolved.config_key, "a359")
        self.assertEqual(resolved.icao_type, "A359")
        self.assertEqual(resolved.source, "aircraft:A359")

    def test_a350_900_long_form_maps_to_a359(self) -> None:
        self.assertEqual(
            resolve_aircraft_config(None, aircraft="A350-900").config_key,
            "a359",
        )

    def test_simconnect_title_maps_to_a359(self) -> None:
        resolved = resolve_aircraft_config(
            None,
            aircraft="Airbus A350-900 Qatar Airways",
        )

        self.assertEqual(resolved.config_key, "a359")

    def test_777_types_map_to_their_own_keys(self) -> None:
        for identifier, expected in (
            ("B77W", "b77w"),
            ("777-300ER", "b77w"),
            ("PMDG Boeing 777-300ER Emirates", "b77w"),
            ("B772", "b772"),
            ("777-200ER", "b772"),
            ("B77L", "b77l"),
        ):
            with self.subTest(identifier=identifier):
                self.assertEqual(
                    resolve_aircraft_config(None, aircraft=identifier).config_key,
                    expected,
                )

    def test_nothing_resolvable_is_refused_not_defaulted(self) -> None:
        # The old behaviour silently used a320 — A320 tables for an A350 is a
        # data-integrity defect, so the request must be refused instead.
        with self.assertRaises(AircraftConfigUnresolvedError) as ctx:
            resolve_aircraft_config(None, aircraft=None)

        self.assertNotIn("a320", str(ctx.exception).lower())
        self.assertIn("not resolved", str(ctx.exception))

    def test_blank_values_count_as_unresolved(self) -> None:
        with self.assertRaises(AircraftConfigUnresolvedError):
            resolve_aircraft_config("   ", aircraft="  ")

    def test_unknown_type_is_refused(self) -> None:
        with self.assertRaises(AircraftConfigUnresolvedError) as ctx:
            resolve_aircraft_config(None, aircraft="ZZZZ")

        self.assertIn("not in the aircraft catalog", str(ctx.exception))

    def test_explicit_key_without_a_yaml_profile_is_refused(self) -> None:
        with self.assertRaises(AircraftConfigUnresolvedError) as ctx:
            resolve_aircraft_config("not_a_real_airframe", aircraft="A359")

        self.assertIn("no performance profile", str(ctx.exception))

    def test_config_existence_check_rejects_path_traversal(self) -> None:
        self.assertFalse(aircraft_config_exists("../general"))
        self.assertFalse(aircraft_config_exists(None))
        self.assertFalse(aircraft_config_exists(""))
        self.assertTrue(aircraft_config_exists("a359"))


if __name__ == "__main__":
    unittest.main()
