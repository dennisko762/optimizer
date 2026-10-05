"""Tests for the EFB SimBrief flightplan pipeline (M2).

Covers:
- ``optimizer.api.flightplan_service.build_flightplan`` against the real
  SimBrief JSON v2 structure (fixture captured from a live API response,
  anonymized: flight number / airline / registration / callsign / user ids).
- Navlog → waypoint table derivation (dep row, REM NM, ETE, FL, wind,
  burn, planned fuel).
- Plan store (save / list / get / delete / last-plan) against a temp dir.
- The API routes with a MOCKED SimBrief client (no live network call):
  live flightplan, import + persistence, list/get/delete, readiness, and
  the 503 path when SIMBRIEF_USER is unset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from optimizer.api.app import app
from optimizer.api import flightplan_service as fps

FIXTURE = Path(__file__).parent / "fixtures" / "simbrief" / "live_ofp_v2.json"


@pytest.fixture(scope="module")
def raw_ofp() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def plan(tmp_path, monkeypatch) -> dict:
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
    return fps.build_flightplan(json.loads(FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def client_no_creds(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.delenv("SIMBRIEF_USER", raising=False)
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
    return TestClient(app)


@pytest.fixture
def client_mocked(raw_ofp, tmp_path, monkeypatch) -> TestClient:
    """API client with the SimBrief client mocked to the fixture payload."""
    monkeypatch.setenv("SIMBRIEF_USER", "testpilot")
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))

    import data_fetcher.simbrief.simbrief_client as sbc

    class _FakeClient:
        def __init__(self, timeout_seconds: float = 15.0) -> None:
            self.timeout_seconds = timeout_seconds

        async def fetch_latest_ofp(self, *, username=None, user_id=None, static_id=None):
            assert username == "testpilot"
            return json.loads(FIXTURE.read_text(encoding="utf-8"))

    monkeypatch.setattr(sbc, "SimBriefClient", _FakeClient)
    fps.clear_fetch_cache()
    return TestClient(app)


# ---------------------------------------------------------------------------
# build_flightplan against the real v2 structure
# ---------------------------------------------------------------------------

class TestBuildFlightplan:
    def test_hero_facts(self, plan):
        assert plan["origin"] == "EDDF"
        assert plan["destination"] == "RJAA"
        assert plan["flight_number"] == "1234"
        assert plan["callsign"] == "TST1234"
        assert plan["airline_icao"] == "TST"
        assert plan["aircraft"] == "B777-F"
        assert plan["aircraft_icao"] == "B77L"
        assert plan["registration"] == "D-TEST"
        assert plan["origin_name"] == "FRANKFURT/MAIN"
        assert plan["destination_name"] == "NARITA INTL"
        assert plan["origin_lat"] == pytest.approx(50.033306)
        assert plan["destination_lon"] == pytest.approx(140.385556)
        assert plan["route_distance_nm"] == pytest.approx(6379.0)
        assert plan["std_utc"] == "2026-10-03T20:50:00Z"
        assert plan["sta_utc"] == "2026-10-04T10:10:00Z"
        assert plan["cruise_fl"] == 310
        assert plan["cruise_mach"] == pytest.approx(0.83)
        assert plan["cost_index"] == pytest.approx(50.0)
        assert plan["alternate"] == "RJTT"

    def test_ete_from_sched_block(self, plan):
        # times.sched_block = "13:20:00" → 13h 20m
        assert plan["ete_min"] == pytest.approx(13 * 60 + 20)

    def test_fuel_row_tonnes(self, plan):
        fuel = plan["fuel"]
        # fixture units are kgs → kg/1000 with 1 decimal
        assert fuel["block"] == pytest.approx(101.4)
        assert fuel["takeoff"] == pytest.approx(101.0)
        assert fuel["trip"] == pytest.approx(88.8)
        assert fuel["landing"] == pytest.approx(12.2)
        assert fuel["taxi"] == pytest.approx(0.3)
        assert fuel["reserve"] == pytest.approx(2.9)
        # RES + ALTN = reserve + alternate_burn
        assert fuel["reserve_alt"] == pytest.approx(2.9 + 4.4, abs=0.1)
        assert fuel["extra"] == pytest.approx(2.5)

    def test_weights_tonnes(self, plan):
        w = plan["weights"]
        assert w["tow"] == pytest.approx(297.1)
        assert w["zfw"] == pytest.approx(196.1)
        assert w["ldw"] == pytest.approx(208.2)

    def test_pdf_url_built_from_files_section(self, plan):
        assert plan["pdf_url"] and plan["pdf_url"].endswith(".pdf")
        assert "simbrief.com" in plan["pdf_url"]

    def test_missing_sections_are_tolerated(self):
        plan = fps.build_flightplan({})
        assert plan["origin"] is None
        assert plan["waypoints"] == []
        assert plan["fuel"]["block"] is None

    def test_lb_units_converted_to_kg(self, raw_ofp):
        raw = json.loads(json.dumps(raw_ofp))
        raw["params"]["units"] = "lbs"
        plan = fps.build_flightplan(raw)
        assert plan["fuel"]["block"] == pytest.approx(101376 * 0.45359237 / 1000.0, abs=0.1)


# ---------------------------------------------------------------------------
# Waypoint table
# ---------------------------------------------------------------------------

class TestWaypointTable:
    def test_dep_and_arr_rows(self, plan):
        rows = plan["waypoints"]
        assert rows[0]["ident"] == "EDDF"
        assert rows[0]["name"] == "FRANKFURT/MAIN"
        assert rows[-1]["ident"] == "RJAA"
        assert rows[-1]["rem_nm"] == 0.0

    def test_row_count_matches_navlog_plus_dep(self, plan, raw_ofp):
        rows = plan["waypoints"]
        navlog = [r for r in raw_ofp["navlog"] if str(r.get("type", "")).lower() not in {"ltlg", ""}]
        assert len(rows) == len(navlog) + 1  # + departure row

    def test_rem_nm_decreasing(self, plan):
        rows = plan["waypoints"]
        rems = [r["rem_nm"] for r in rows if r["rem_nm"] is not None]
        assert rems == sorted(rems, reverse=True)
        assert rems[0] > 5000  # full route ≈ 6379 NM
        assert rems[-1] == 0.0

    def test_cruise_row_fields(self, plan):
        crz = next(r for r in plan["waypoints"] if r["stage"] == "CRZ")
        assert crz["alt"] is not None and 100 <= crz["alt"] <= 600
        assert crz["leg_nm"] is not None
        assert crz["ete"] is not None and ":" in crz["ete"]
        assert crz["leg_ete"] is not None
        assert crz["burn"] is not None and crz["burn"] > 0
        assert crz["plan_fuel_t"] is not None and 0 < crz["plan_fuel_t"] < 101.4

    def test_wind_cell_format(self, plan):
        import re
        winds = [r["wind"] for r in plan["waypoints"] if r["wind"]]
        assert winds, "expected wind cells in a real OFP"
        assert all(re.fullmatch(r"\d{2,3}/\d{1,3}", w) for w in winds)

    def test_airway_and_fir_present(self, plan):
        assert any(r["airway"] for r in plan["waypoints"])
        assert any(r["fir"] for r in plan["waypoints"])

    def test_first_cruise_ete_after_dep(self, plan):
        first_crz = next(r for r in plan["waypoints"] if r["stage"] == "CRZ")
        assert first_crz["ete"] is not None


# ---------------------------------------------------------------------------
# Plan store
# ---------------------------------------------------------------------------

class TestPlanStore:
    def test_save_list_get_last(self, plan, tmp_path, monkeypatch):
        monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
        fps.save_plan(plan)
        listing = fps.list_plans()
        assert listing["last_plan"] == "EDDFRJAA-1234"
        assert len(listing["plans"]) == 1
        got = fps.get_plan("EDDFRJAA-1234")
        assert got["flightplan"]["origin"] == "EDDF"

    def test_multiple_plans_most_recent_first(self, plan, monkeypatch):
        first = dict(plan)
        fps.save_plan(first)
        second = dict(plan)
        second["origin"], second["destination"] = "KJFK", "EGLL"
        second["flight_number"] = "9999"
        import time as _t
        _t.sleep(0.01)
        fps.save_plan(second)
        listing = fps.list_plans()
        assert [p["key"] for p in listing["plans"]] == [
            "KJFKEGLL-9999", "EDDFRJAA-1234",
        ]
        assert listing["last_plan"] == "KJFKEGLL-9999"

    def test_delete_and_last_plan_falls_back(self, plan, monkeypatch):
        fps.save_plan(plan)
        second = dict(plan)
        second["origin"], second["destination"] = "KJFK", "EGLL"
        second["flight_number"] = "9999"
        import time as _t
        _t.sleep(0.01)
        fps.save_plan(second)
        assert fps.delete_plan("EDDFRJAA-1234") is True
        listing = fps.list_plans()
        assert listing["last_plan"] == "KJFKEGLL-9999"
        assert fps.delete_plan("KJFKEGLL-9999") is True
        assert fps.list_plans()["plans"] == []
        assert fps.delete_plan("KJFKEGLL-9999") is False  # unknown

    def test_corrupt_store_recovers(self, plan, tmp_path, monkeypatch):
        monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
        store = tmp_path / "efb" / "flightplans.json"
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json", encoding="utf-8")
        listing = fps.list_plans()
        assert listing["last_plan"] is None
        assert listing["plans"] == []


# ---------------------------------------------------------------------------
# API routes (mocked client — no live SimBrief call)
# ---------------------------------------------------------------------------

class TestFlightplanRoutes:
    def test_live_flightplan(self, client_mocked):
        resp = client_mocked.get("/api/simbrief/flightplan/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "SimBrief"
        assert body["origin"] == "EDDF"
        assert body["destination"] == "RJAA"
        assert body["fuel"]["block"] == pytest.approx(101.4)
        assert len(body["waypoints"]) > 50

    def test_live_cache_serves_without_refetch(self, client_mocked, raw_ofp):
        first = client_mocked.get("/api/simbrief/flightplan/live")
        assert first.status_code == 200
        # Break the client — a cache hit must still succeed.
        import data_fetcher.simbrief.simbrief_client as sbc

        class _Broken:
            def __init__(self, timeout_seconds: float = 15.0) -> None:
                raise AssertionError("cache should have prevented a refetch")

        monkey_broken = _Broken
        sbc.SimBriefClient = monkey_broken
        try:
            second = client_mocked.get("/api/simbrief/flightplan/live")
            assert second.status_code == 200
        finally:
            fps.clear_fetch_cache()

    def test_import_saves_plan(self, client_mocked):
        resp = client_mocked.post("/api/simbrief/flightplan/import")
        assert resp.status_code == 200
        body = resp.json()
        assert body["key"] == "EDDFRJAA-1234"
        listing = client_mocked.get("/api/simbrief/flightplans")
        assert listing.status_code == 200
        assert listing.json()["last_plan"] == "EDDFRJAA-1234"

    def test_saved_plan_get_and_delete(self, client_mocked):
        assert client_mocked.post("/api/simbrief/flightplan/import").status_code == 200
        got = client_mocked.get("/api/simbrief/flightplans/EDDFRJAA-1234")
        assert got.status_code == 200
        assert got.json()["flightplan"]["flight_number"] == "1234"
        dele = client_mocked.delete("/api/simbrief/flightplans/EDDFRJAA-1234")
        assert dele.status_code == 200
        assert client_mocked.get("/api/simbrief/flightplans/EDDFRJAA-1234").status_code == 404

    def test_unknown_plan_404(self, client_mocked):
        assert client_mocked.get("/api/simbrief/flightplans/NOSUCH-1").status_code == 404

    def test_readiness_reports_configuration(self, client_mocked, client_no_creds, monkeypatch):
        monkeypatch.setenv("SIMBRIEF_USER", "testpilot")
        assert client_mocked.get("/api/simbrief/config/readiness").json() == {"configured": True}
        monkeypatch.delenv("SIMBRIEF_USER", raising=False)
        assert client_no_creds.get("/api/simbrief/config/readiness").json() == {"configured": False}

    def test_unconfigured_bridge_503(self, client_no_creds):
        fps.clear_fetch_cache()
        resp = client_no_creds.get("/api/simbrief/flightplan/live")
        assert resp.status_code == 503
        assert "SIMBRIEF_USER" in resp.json()["detail"]
        resp = client_no_creds.post("/api/simbrief/flightplan/import")
        assert resp.status_code == 503

    def test_fetch_failure_502(self, monkeypatch, tmp_path):
        import data_fetcher.simbrief.simbrief_client as sbc

        class _Failing:
            def __init__(self, timeout_seconds: float = 15.0) -> None:
                pass

            async def fetch_latest_ofp(self, **kwargs):
                raise sbc.SimBriefClientError("upstream down")

        monkeypatch.setenv("SIMBRIEF_USER", "testpilot")
        monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(sbc, "SimBriefClient", _Failing)
        fps.clear_fetch_cache()
        client = TestClient(app)
        resp = client.get("/api/simbrief/flightplan/live")
        assert resp.status_code == 502
        assert "SimBrief" in resp.json()["detail"]

    def test_no_credentials_leaked_in_responses(self, client_mocked):
        client_mocked.get("/api/simbrief/flightplan/live")
        for url in ("/api/simbrief/flightplan/live", "/api/simbrief/flightplans"):
            body = client_mocked.get(url).text
            assert "testpilot" not in body
