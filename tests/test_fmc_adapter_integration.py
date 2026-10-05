from __future__ import annotations

import unittest

from data_fetcher.sim.fmc_bridge import (
    _PmdgTextBlockParser,
    _resolve_fmc_bridge_spec,
)
from data_fetcher.sim.fmc_models import (
    FmcTelemetrySnapshotData,
    FmcWaypointPredictionData,
)
from data_fetcher.sim.sim_models import LiveSimState
from data_fetcher.sim.simconnect_routes import _live_state_to_patch, _resolve_remaining_distance
from optimizer.route_profile_models import RemainingRouteProfile, RemainingRouteSegment


class FmcAdapterIntegrationTests(unittest.TestCase):
    def test_detects_pmdg_777_aircraft_title(self) -> None:
        spec = _resolve_fmc_bridge_spec("PMDG Boeing 777-300ER")

        self.assertIsNotNone(spec)
        self.assertEqual(spec.adapter_key, "PMDG_777")

    def test_pmdg_text_block_parser_extracts_complete_cdu_page(self) -> None:
        parser = _PmdgTextBlockParser()
        raw_lines = [
            "================ CDU 0 ================\n",
            "    KAL902 PROGRESS 1/4\n",
            " TO      DTG  ETA   FUEL\n",
            "DINRO    356 2314z  84.9\n",
            " NEXT\n",
            "UDROS    453 2325z  83.2\n",
            " DEST\n",
            "RKSI    5010 0822z  15.3\n",
            " ECON SPD    TO STEP CLB\n",
            ".842        0000z/ 749nm\n",
            "\n",
            "\n",
            "<POS REPORT     POS REF>\n",
            "2037\n",
            "======================================\n",
        ]

        emitted = []
        for line in raw_lines:
            emitted.extend(parser.feed_line(line))

        self.assertEqual(len(emitted), 1)
        cdu_index, lines = emitted[0]
        self.assertEqual(cdu_index, 0)
        self.assertEqual(lines[0].strip(), "KAL902 PROGRESS 1/4")
        self.assertEqual(lines[6].strip(), "RKSI    5010 0822z  15.3")

    def test_remaining_distance_prefers_fmc_adapter_before_route_profile(self) -> None:
        from data_fetcher.sim import simconnect_routes as routes

        original_profile = routes._remaining_route_profile
        try:
            routes._remaining_route_profile = RemainingRouteProfile(
                source="SIMBRIEF",
                totalDistanceNm=240.0,
                segmentCount=1,
                segments=[
                    RemainingRouteSegment(
                        ident="DEST",
                        startLat=50.0,
                        startLon=8.0,
                        lat=51.0,
                        lon=9.0,
                        distanceNm=240.0,
                    )
                ],
            )

            live = LiveSimState(
                latitude=50.1,
                longitude=8.1,
                gps_is_active_flight_plan=True,
                gps_remaining_distance_nm=180.0,
                fmc_snapshot=FmcTelemetrySnapshotData(
                    aircraft="PMDG_777",
                    source="PMDG_SDK",
                    cduIndex=0,
                    page="PROGRESS",
                    destination=FmcWaypointPredictionData(
                        ident="RKSI",
                        dtgNm=5010,
                        etaZulu="0822z",
                        fuel=15.3,
                    ),
                ),
            )

            remaining_nm, source, details = _resolve_remaining_distance(
                live,
                destination_lat=37.46,
                destination_lon=126.44,
            )

            self.assertEqual(source, "FMC_ADAPTER")
            self.assertEqual(remaining_nm, 5010.0)
            self.assertEqual(details["destinationIdent"], "RKSI")
        finally:
            routes._remaining_route_profile = original_profile

    def test_live_patch_uses_fmc_cost_index_when_available(self) -> None:
        live = LiveSimState(
            altitude_ft=35000.0,
            gross_weight_kg=240000.0,
            mach=0.84,
            fuel_remaining_kg=50000.0,
            ground_speed_kt=480.0,
            fmc_snapshot=FmcTelemetrySnapshotData(
                aircraft="INIBUILDS_A340",
                source="INIBUILDS_MCDU_EXPORT",
                cduIndex=0,
                page="PROG",
                costIndex=42,
            ),
        )

        patch, _, _ = _live_state_to_patch(live, destination_lat=None, destination_lon=None)

        self.assertEqual(patch.current_cost_index, 42)


if __name__ == "__main__":
    unittest.main()
