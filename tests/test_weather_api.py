"""API tests for /api/crew/weather — offline, with a seeded published cycle.

A *synthetic* (clearly not NOAA) but structurally-valid cycle is published to
a temp cache so the routes, polygonization, and sampling code paths are
exercised end-to-end through the real FastAPI app without any network. The
real-data (non-synthetic) integration is covered separately by
``test_weather_ingest_real.py`` (live NOAA GFS cycle).
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore, cache_root
from crew_platform.weather import service


def _synthetic_bundle(tmp_path, cycle="20261005_18", offset=0, box=None):
    """Write a synthetic npz bundle + a stub meta for one (cycle, offset).

    ``box`` defaults to the wide EU/Asia region used by the rest of this
    module; pass another :class:`BoxKey` to stage a second region for the
    same cycle (region-switch regression).
    """
    box = box or BoxKey(25.0, 75.0, 65.0, 10.0)
    lat = np.linspace(box.bottomlat, box.toplat, 25, dtype=np.float32)
    lon = np.linspace(box.leftlon, box.rightlon, 25, dtype=np.float32)
    rng = np.random.default_rng(7)
    u = rng.uniform(-10, 10, (25, 25)).astype(np.float32)
    v = rng.uniform(-10, 10, (25, 25)).astype(np.float32)
    t = (230.0 + rng.uniform(-10, 10, (25, 25))).astype(np.float32)
    r = rng.uniform(20, 90, (25, 25)).astype(np.float32)
    w = rng.uniform(-3, 3, (25, 25)).astype(np.float32)
    # interior radial blobs (peak inside the grid, zero at the edge) so the
    # threshold contours close into rings rather than running off the boundary
    yy, xx = np.mgrid[0:25, 0:25]
    dist = np.hypot(xx - 12.0, yy - 12.0)
    ti = (10.0 - 0.25 * dist * dist).clip(min=0.0)       # max 10 at centre
    ice = (8.0 - 0.35 * np.hypot(xx - 8.0, yy - 8.0) ** 2).clip(min=0.0)
    cape = (6000.0 - 300.0 * np.hypot(xx - 16.0, yy - 16.0) ** 2).clip(min=0.0)
    front = (6.0 - 0.30 * ((xx - 5.0) ** 2 + (yy - 12.0) ** 2)).clip(min=0.0)
    payload = {
        "lat": lat, "lon": lon,
        "u_300": u, "v_300": v, "t_300": t, "r_300": r, "w_300": w,
        "u_200": u * 1.5, "v_200": v * 1.5, "t_200": t - 20, "r_200": r, "w_200": w,
        "u_100": u * 2.0, "v_100": v * 2.0, "t_100": t - 40, "r_100": r, "w_100": w,
        "u_150": u * 1.8, "v_150": v * 1.8, "t_150": t - 30, "r_150": r, "w_150": w,
        "u_250": u * 1.2, "v_250": v * 1.2, "t_250": t - 10, "r_250": r, "w_250": w,
        "u_400": u * 0.8, "v_400": v * 0.8, "t_400": t + 15, "r_400": r, "w_400": w,
        "u_500": u * 0.7, "v_500": v * 0.7, "t_500": t + 22, "r_500": r, "w_500": w,
        "u_700": u * 0.8, "v_700": v * 0.8, "t_700": t + 35, "r_700": r, "w_700": w,
        "u_850": u * 0.6, "v_850": v * 0.6, "t_850": t + 30, "r_850": r, "w_850": w,
        "u_925": u * 0.5, "v_925": v * 0.5, "t_925": t + 40, "r_925": r, "w_925": w,
        "u_50": u * 2.5, "v_50": v * 2.5, "t_50": t - 55, "r_50": r, "w_50": w,
        "ti_300": ti, "ti_200": ti * 0.8, "ti_100": ti * 0.6,
        "ice_850": ice, "ice_700": ice * 0.8, "ice_500": ice * 0.5,
        "cape": cape, "front": front,
    }
    # region-keyed layout (matches CycleStore.write_raw/publish since the
    # region-collision fix)
    path = cache_root() / cycle / str(box) / f"f{offset:03d}.npz"
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(path), **payload)
    # stub raw GRIB so publish validation passes
    raw = path.parent / f"f{offset:03d}.grb2"
    raw.write_bytes(b"\x00" * 32)
    return box


@pytest.fixture()
def weather_env(monkeypatch, tmp_path):
    """Isolated cache dir + an in-memory store with one published cycle."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(cache_dir))
    # no background scheduler thread during tests (deterministic, no network)
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "0")
    store = CycleStore()
    box = _synthetic_bundle(cache_dir)
    store.publish(CycleMeta(
        cycle_id="20261005_18", run_date="20261005", run_hour="18",
        box=box, offsets=[0], fields=["u", "v", "t", "ti", "ice", "cape", "front"],
        n_lat=25, n_lon=25, fetched_at=time.time(),
    ))
    # route the service + routes + scheduler at this isolated store
    monkeypatch.setattr("crew_platform.weather.store.STORE", store)
    monkeypatch.setattr(service, "STORE", store)
    from crew_platform.weather import scheduler as _sched
    monkeypatch.setattr(_sched, "STORE", store)
    # seed an active saved SimBrief plan (offline; no SimBrief fetch)
    from optimizer.api import flightplan_service as fps
    data_dir = tmp_path / "data"
    monkeypatch.setenv("EFB_DATA_DIR", str(data_dir))
    fps.clear_fetch_cache()
    view = {
        "origin": "EDDF", "destination": "RJAA",
        "origin_lat": 50.0, "origin_lon": 8.5,
        "destination_lat": 35.7, "destination_lon": 140.0,
        "cruise_fl": 340, "callsign": "QTR815", "route": "EDDF QTR RJAA",
        "std_utc": "2026-10-06 18:00",
        "waypoints": [
            {"ident": "EDDF", "lat": 50.0, "lon": 30.0, "alt": None, "stage": "DEP"},
            {"ident": "AMZ", "lat": 50.5, "lon": 40.0, "alt": 250},
            {"ident": "NOV", "lat": 51.0, "lon": 50.0, "alt": 340},
            {"ident": "RJAA", "lat": 35.7, "lon": 60.0, "alt": None, "stage": "ARR"},
        ],
    }
    fps.save_plan(view)
    return store


@pytest.fixture()
def client(weather_env):
    from fastapi.testclient import TestClient
    from optimizer.api.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_status_reports_published_cycle(client):
    r = client.get("/api/crew/weather/status")
    assert r.status_code == 200
    st = r.json()
    assert st["current_cycle"] == "20261005_18"
    assert st["last_good"] == "20261005_18"
    assert st["has_plan"] is True
    assert "6-hour" in st["cadence"]


def test_route_returns_exact_waypoints_and_samples(client):
    r = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    # exact route, every waypoint in order
    assert [p["ident"] for p in body["points"]] == ["EDDF", "AMZ", "NOV", "RJAA"]
    assert body["unresolved"] == []
    # per-waypoint samples: wind/OAT present (synthetic grid covers the box)
    s = body["samples"]
    assert s["offset"] == 0
    assert s["fl"] == 300
    assert len(s["points"]) == 4
    p0 = s["points"][0]
    assert p0["wind_speed_kt"] is not None
    assert p0["oat_c"] is not None
    assert p0["turbulence"] is not None
    # provenance + proxy disclosure present
    assert "NOAA" in body["samples"]["provenance"]


def test_layer_turbulence_polygon(client):
    r = client.get("/api/crew/weather/layer/turbulence", params={"fl": 300, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"], "expected at least one turbulence polygon"
    polys = [
        f for f in body["features"]
        if f["type"] == "Feature" and f["geometry"]["type"] == "Polygon"
    ]
    assert polys
    # standards GeoJSON envelope: Feature carries the Polygon geometry
    ring = polys[0]["geometry"]["coordinates"][0]
    assert len(ring) >= 4
    assert ring[0] == ring[-1]
    assert isinstance(ring[0][0], float) and isinstance(ring[0][1], float)
    assert "NOAA" in body["source"]


def test_layer_icing_and_fronts_and_cape(client):
    for prod in ("icing", "fronts", "cape"):
        r = client.get(f"/api/crew/weather/layer/{prod}", params={"fl": 300, "offset": 0})
        assert r.status_code == 200, (prod, r.text)
        assert r.json()["type"] == "FeatureCollection"


def test_layer_jet_derived_from_wind(client):
    """The jet layer is derived from |u,v| at the FL's level (GFS bundles
    carry no dedicated jet grid). It must come back AVAILABLE (no
    ``unavailable`` key) — i.e. the u/v wind-speed grid exists for the FL —
    even where the synthetic wind (<= ~25 m/s) stays below the 30 m/s
    extent threshold and yields no polygons."""
    for fl in (300, 450):
        r = client.get("/api/crew/weather/layer/jet", params={"fl": fl, "offset": 0})
        assert r.status_code == 200, (fl, r.text)
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert body["unit"] == "m/s (wind speed)"
        assert body.get("unavailable") is None, f"FL{fl}: jet grid should be derivable from u/v"
        assert isinstance(body["features"], list)


def test_layer_unknown_product_404(client):
    r = client.get("/api/crew/weather/layer/nope")
    assert r.status_code == 404


def test_cycles_endpoint(client):
    r = client.get("/api/crew/weather/cycles")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert body["cycles"][0]["cycle_id"] == "20261005_18"


def test_ingest_malformed_cycle_id_returns_422(client):
    """POST /ingest with a malformed cycle_id must be a clear 4xx, never the
    uncaught ValueError -> 500 that store._cycle_dir used to raise."""
    r = client.post("/api/crew/weather/ingest", params={"cycle_id": "not-a-cycle"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert "cycle_id" in body["detail"]
    assert "not-a-cycle" in body["detail"]


# ── SimBrief fetch failure must never surface as a 500 (QA finding) ────

def test_status_simbrief_fetch_failure_degrades_not_500(monkeypatch, client):
    """SIMBRIEF_USER set + no saved plan + SimBrief fetch raises
    FlightplanError (bad handle, outage, rate limit, network error) must
    degrade /status to has_plan: False, never an uncaught 500."""
    from optimizer.api import flightplan_service as fps

    listing = fps.list_plans()
    fps.delete_plan(listing["last_plan"])
    monkeypatch.setenv("SIMBRIEF_USER", "qa_test_pilot")

    def _boom(**kwargs):
        raise fps.FlightplanError(
            "SimBrief fetch failed: SimBrief request failed with HTTP 400: "
            '{"fetch":{"status":"Error: Unknown UserID"}}'
        )

    monkeypatch.setattr(fps, "fetch_flightplan_view", _boom)
    r = client.get("/api/crew/weather/status")
    assert r.status_code == 200
    assert r.json()["has_plan"] is False


def test_route_simbrief_fetch_failure_degrades_not_500(monkeypatch, client):
    """Same scenario on /route: must be an honest 4xx with an actionable
    message calling out the SimBrief handle, never a 500."""
    from optimizer.api import flightplan_service as fps

    listing = fps.list_plans()
    fps.delete_plan(listing["last_plan"])
    monkeypatch.setenv("SIMBRIEF_USER", "qa_test_pilot")

    def _boom(**kwargs):
        raise fps.FlightplanError(
            "SimBrief fetch failed: SimBrief request failed with HTTP 400: "
            '{"fetch":{"status":"Error: Unknown UserID"}}'
        )

    monkeypatch.setattr(fps, "fetch_flightplan_view", _boom)
    r = client.get("/api/crew/weather/route")
    assert r.status_code < 500, r.text
    assert "SimBrief" in r.json()["detail"]


def test_status_no_plan_degraded_state(monkeypatch, client):
    # delete the active plan -> /route degrades to 404 with a clear message
    from optimizer.api import flightplan_service as fps
    listing = fps.list_plans()
    key = listing["last_plan"]
    fps.delete_plan(key)
    r = client.get("/api/crew/weather/route")
    assert r.status_code == 404
    assert "SimBrief" in r.json()["detail"] or "plan" in r.json()["detail"].lower()


# ── ETA temporal provenance (#2) ──────────────────────────────────────

def test_route_samples_degrade_honestly_outside_horizon(client):
    """A fresh plan is sampled at each fix's ETA hour, with valid times.

    cycle 20261005_18 (run 18Z). std 2026-10-06 18:00Z == T+24h.
    """
    from optimizer.api import flightplan_service as fps
    view = {
        "origin": "EDDF", "destination": "RJAA",
        "cruise_fl": 340, "callsign": "QTR815",
        "std_utc": "2026-10-06 18:00",
        "waypoints": [
            {"ident": "EDDF", "lat": 50.0, "lon": 30.0, "alt": None, "stage": "DEP", "ete": "0:00"},
            {"ident": "AMZ", "lat": 50.5, "lon": 40.0, "alt": 250, "ete": "12:00"},
            {"ident": "RJAA", "lat": 35.7, "lon": 60.0, "alt": None, "stage": "ARR", "ete": "24:00"},
        ],
    }
    fps.clear_fetch_cache()
    fps.save_plan(view)
    # requested display offset 0 (the only published step)
    r = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    pts = body["samples"]["points"]
    by_ident = {p["ident"]: p for p in pts}
    # the provenance block must be present + per-point valid times when served
    s = body["samples"]
    assert s["provenance"]
    for p in pts:
        if p["offset_served"] is not None:
            assert p["valid_at_utc"], "served sample must carry its valid time"
    # fixture cycle 20261005_18 publishes T+0 only; AMZ ETA is T+12h — OUT of
    # horizon, so the sample is served at T+0 with an honest mismatch flag,
    # never labelled as T+12h (QA #2).
    amz = by_ident["AMZ"]
    assert amz["offset"] == 0
    assert amz["offset_served"] == 0
    assert any("outside published horizon" in u for u in amz["unavailable"])
    assert amz["valid_at_utc"].endswith("T18:00:00Z")  # T+0 of run 18Z
    # a fix with NO eta at all degrades silently-ish: still T+0, flagged
    rj = by_ident["RJAA"]
    assert rj["offset"] == 0


def test_route_samples_use_eta_hour_inside_horizon(client, weather_env):
    """When the ETA hour IS published, the fix must be sampled AT that hour.

    cycle 20261005_18 (run 18Z) + a second bundle at T+6; std 2026-10-05
    23:00Z == T+5h, so:
      AMZ  ETE 1:00 -> abs T+6h  -> published -> sampled AT T+6
      NOV  ETE 2:00 -> abs T+7h  -> in horizon, step not published -> flagged
      RJAA ETE 5:00 -> abs T+10h -> outside published horizon -> flagged
    """
    import time as _time
    from crew_platform.weather.store import BoxKey, CycleMeta

    # publish an extra step (T+6) for the same box, different field values so
    # the served offset is observable
    store = weather_env
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    import crew_platform.weather.store as _st
    # _synthetic_bundle writes the region-keyed npz + raw stub only
    _synthetic_bundle(_st.cache_root(), "20261005_18", 6)
    meta = store.load_meta("20261005_18", box)
    assert meta is not None
    store.publish(CycleMeta(
        cycle_id="20261005_18", run_date="20261005", run_hour="18",
        box=box, offsets=[0, 6], fields=meta.fields,
        n_lat=meta.n_lat, n_lon=meta.n_lon, fetched_at=_time.time(),
    ))

    from optimizer.api import flightplan_service as fps
    view = {
        "origin": "EDDF", "destination": "RJAA",
        "cruise_fl": 340,
        "std_utc": "2026-10-05 23:00",   # T+5h vs run 18Z
        "waypoints": [
            {"ident": "EDDF", "lat": 50.0, "lon": 30.0, "alt": None, "stage": "DEP", "ete": "0:00"},
            {"ident": "AMZ", "lat": 50.5, "lon": 40.0, "alt": 250, "ete": "1:00"},
            {"ident": "NOV", "lat": 51.0, "lon": 50.0, "alt": 340, "ete": "2:00"},
            {"ident": "RJAA", "lat": 35.7, "lon": 60.0, "alt": None, "stage": "ARR", "ete": "5:00"},
        ],
    }
    fps.clear_fetch_cache()
    fps.save_plan(view)
    r = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert r.status_code == 200
    pts = r.json()["samples"]["points"]
    by_ident = {p["ident"]: p for p in pts}
    # AMZ: ETA T+6h is published -> sampled AT T+6, valid time 18Z+6h=00Z
    assert by_ident["AMZ"]["offset"] == 6
    assert by_ident["AMZ"]["offset_served"] == 6
    assert by_ident["AMZ"]["valid_at_utc"].endswith("T00:00:00Z")  # 18Z+6h
    assert not any("horizon" in u or "not published" in u for u in by_ident["AMZ"]["unavailable"])
    # DEP: ETA T+5h in horizon but that step not published (steps 0,6) ->
    # served at requested offset 0, flagged "not published", never T+5h
    dep = by_ident["EDDF"]
    assert dep["offset"] == 0
    assert any("not published" in u for u in dep["unavailable"])
    # NOV: ETA T+7h outside the published horizon (T+0..T+6) -> flagged
    nov = by_ident["NOV"]
    assert nov["offset"] == 0
    assert any("outside published horizon" in u for u in nov["unavailable"])
    # RJAA: ETA T+10h outside published horizon (T+0..T+6) -> flagged
    rj = by_ident["RJAA"]
    assert rj["offset"] == 0
    assert any("outside published horizon" in u for u in rj["unavailable"])


def test_route_stale_plan_predating_run_is_flagged(client):
    """A plan whose STD predates the model run (stale OFP) must flag every
    ETA as pre-run, never silently label the served time as the ETA (QA #2)."""
    from optimizer.api import flightplan_service as fps
    view = {
        "origin": "EDDF", "destination": "RJAA",
        "cruise_fl": 340,
        "std_utc": "2026-10-01 00:00",  # days BEFORE run 20261005_18
        "waypoints": [
            {"ident": "EDDF", "lat": 50.0, "lon": 30.0, "alt": None, "stage": "DEP", "ete": "0:00"},
            {"ident": "AMZ", "lat": 50.5, "lon": 40.0, "alt": 250, "ete": "6:00"},
            {"ident": "RJAA", "lat": 35.7, "lon": 60.0, "alt": None, "stage": "ARR", "ete": "12:00"},
        ],
    }
    fps.clear_fetch_cache()
    fps.save_plan(view)
    r = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert r.status_code == 200
    pts = r.json()["samples"]["points"]
    for p in pts:
        assert p["offset"] == 0, "stale plan samples at requested offset"
        assert any("predates the model run" in u for u in p["unavailable"]),             f"{p['ident']} missing stale-plan flag: {p['unavailable']}"
        # valid time is the SERVED offset (T+0), never the ETA
        assert p["valid_at_utc"].endswith("T18:00:00Z")
