from __future__ import annotations

import unittest

from data_fetcher.sim.sim_models import CurrentFlightState
from data_fetcher.simbrief.route_profile import build_remaining_route_profile_from_waypoints
from data_fetcher.simbrief.simbrief_models import SimBriefRouteWaypoint
from optimizer.api.api_models import OptimizeRequest
from optimizer.app.ci_optimization_service import CiOptimizationService
from optimizer.route_profile_models import (
    RemainingRouteProfile,
    RemainingRouteSegment,
    clip_remaining_route_profile,
    cruise_segments_from_remaining_route_profile,
)
from optimizer.scenario_engine.scenario_models import (
    FlightContextInput,
    OperationalTrigger,
    RerouteScenarioInput,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
)
from optimizer.scenario_engine.scenario_state_applier import apply_scenario_to_current_state


class RemainingRouteProfileTests(unittest.TestCase):
    def test_build_remaining_route_profile_from_waypoints_preserves_segments(self) -> None:
        waypoints = [
            SimBriefRouteWaypoint(ident="TOC", lat=50.0, lon=8.0),
            SimBriefRouteWaypoint(
                ident="FIX01",
                lat=51.0,
                lon=9.0,
                distance_from_previous_nm=120.0,
                altitude_ft=34000,
                wind_component_kt=-20.0,
                isa_deviation_c=1.0,
            ),
            SimBriefRouteWaypoint(
                ident="FIX02",
                lat=52.0,
                lon=10.0,
                distance_from_previous_nm=80.0,
                altitude_ft=36000,
                wind_component_kt=-10.0,
                isa_deviation_c=2.0,
            ),
        ]

        profile = build_remaining_route_profile_from_waypoints(waypoints)

        self.assertEqual(profile.source, "SIMBRIEF_NAVLOG")
        self.assertEqual(profile.segment_count, 2)
        self.assertAlmostEqual(profile.total_distance_nm or 0.0, 200.0)
        self.assertEqual(profile.segments[0].ident, "FIX01")
        self.assertEqual(profile.segments[1].ident, "FIX02")

    def test_clip_profile_and_convert_to_cruise_segments(self) -> None:
        profile = RemainingRouteProfile(
            source="TEST",
            segments=[
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=33000),
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=34000),
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=35000),
            ],
        )

        clipped = clip_remaining_route_profile(profile, 220.0)
        self.assertIsNotNone(clipped)
        self.assertAlmostEqual(clipped.total_distance_nm or 0.0, 220.0)
        self.assertEqual([segment.distance_nm for segment in clipped.segments], [20.0, 100.0, 100.0])

        cruise_segments = cruise_segments_from_remaining_route_profile(
            profile,
            remaining_distance_nm=220.0,
        )
        self.assertEqual(
            [segment["distanceNm"] for segment in cruise_segments],
            [20.0, 100.0, 100.0],
        )

    def test_service_prefers_explicit_remaining_route_profile(self) -> None:
        service = CiOptimizationService()
        request = OptimizeRequest.model_validate(
            {
                "action": "NORMAL_RECALC",
                "aircraftConfig": "a320",
                "flightState": {
                    "aircraft": "A320",
                    "altitudeFt": 35000,
                    "grossWeightKg": 65000,
                    "mach": 0.78,
                    "remainingDistanceNm": 220,
                    "routeDistanceNm": 300,
                    "windComponentKt": -15,
                    "isaDeviationC": 0,
                    "fuelRemainingKg": 5200,
                    "groundSpeedKt": 435,
                    "cruiseSegments": [
                        {"distanceNm": 50.0, "altitudeFt": 31000},
                    ],
                },
                "flightContext": {},
                "remainingRouteProfile": {
                    "source": "TEST",
                    "segments": [
                        {"distanceNm": 100.0, "altitudeFt": 33000},
                        {"distanceNm": 100.0, "altitudeFt": 34000},
                        {"distanceNm": 100.0, "altitudeFt": 35000},
                    ],
                },
            }
        )

        current_state = service._to_current_flight_state(request)

        self.assertIsNotNone(current_state.remaining_route_profile)
        self.assertEqual(
            [segment["distanceNm"] for segment in current_state.cruise_segments],
            [20.0, 100.0, 100.0],
        )

    def test_scenario_state_applier_reclips_segments_from_profile(self) -> None:
        profile = RemainingRouteProfile(
            source="TEST",
            segments=[
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=33000),
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=34000),
                RemainingRouteSegment(distanceNm=100.0, altitudeFt=35000),
            ],
        )
        current_state = CurrentFlightState(
            aircraft="A320",
            altitude_ft=35000,
            gross_weight_kg=65000,
            mach=0.78,
            remaining_distance_nm=300,
            wind_component_kt=-15.0,
            isa_deviation_c=0.0,
            ground_speed_kt=435.0,
            cruise_segments=cruise_segments_from_remaining_route_profile(profile),
            remaining_route_profile=profile,
        )
        scenario = ScenarioInput(
            trigger=OperationalTrigger.REROUTE_RECEIVED,
            scenario_type=ScenarioType.REROUTE_RECOVERY,
            priority=ScenarioPriority.MEDIUM,
            flight_context=FlightContextInput(),
            reroute=RerouteScenarioInput(new_remaining_distance_nm=150.0),
        )

        updated = apply_scenario_to_current_state(
            current_state=current_state,
            scenario_input=scenario,
        )

        self.assertAlmostEqual(updated.remaining_distance_nm, 150.0)
        self.assertEqual(
            [segment["distanceNm"] for segment in updated.cruise_segments],
            [50.0, 100.0],
        )


if __name__ == "__main__":
    unittest.main()
