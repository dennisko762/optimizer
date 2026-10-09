"""Real-data integration test: an ACTUAL NOAA GFS 0.25 deg cycle.

Skipped by default (no network in the normal gate). Run explicitly with::

    SET WEATHER_TEST_REAL_GFS=1
    .venv-qa\Scripts\python.exe -m pytest tests/test_weather_ingest_real.py -v

What it proves (the task's hard "non-fabricated weather" acceptance):

* cycle discovery against the live NOMADS g2sub endpoint (real cycle ids,
  not synthetic),
* a bounded real subset download (20E-70E x 10N-65N, offsets 0 and 24 only),
* GRIB2 parsing via cfgrib/eccodes (real field grids),
* atomic publish through the real CycleStore,
* non-empty REAL hazard polygons + REAL route samples through the real
  FastAPI routes (status / route / layer).

The cycle id and validity are recorded into a small JSON receipt file under
the temp dir so a reviewer can see exactly which real cycle was validated.
No fixture derived from this test may be presented as "live validation" —
this test itself is the live validation.
"""
from __future__ import annotations

import json
import os
import re
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("WEATHER_TEST_REAL_GFS") != "1",
    reason="real NOAA GFS network test — set WEATHER_TEST_REAL_GFS=1 to run",
)

# Bounded, reviewable region + horizon (keeps the download a few MB).
BOX = dict(leftlon=20.0, rightlon=70.0, toplat=65.0, bottomlat=10.0)
OFFSETS = (0, 24)


def _latest_cycle():
    from crew_platform.weather import cycle as gfs
    config = gfs.GfsConfig.from_env()
    cycles = gfs.list_cycles(config)
    assert cycles, "NOMADS g2sub index returned no /gfs.YYYYMMDD dirs"
    latest = cycles[0]
    hours = gfs.list_cycle_hours(config, latest["date"])
    assert hours, f"no run hours found for gfs.{latest['date']}"
    return config, latest["date"], hours[0]


@pytest.fixture()
def real_env(tmp_path, monkeypatch):
    """Isolated cache + a REAL published GFS cycle (discovered, downloaded,
    parsed, hazard-derived and atomically published)."""
    from crew_platform.weather import service
    from crew_platform.weather.ingest import IngestRequest, ingest_cycle
    from crew_platform.weather.store import CycleStore

    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "0")

    config, date, hour = _latest_cycle()
    cycle_id = f"{date}_{hour}"

    store = CycleStore()
    req = IngestRequest(cycle_id, BOX["leftlon"], BOX["rightlon"],
                        BOX["toplat"], BOX["bottomlat"], offsets=OFFSETS)
    meta = ingest_cycle(config, req, store=store)
    assert meta.cycle_id == cycle_id
    assert set(OFFSETS) <= set(meta.offsets), meta.offsets

    monkeypatch.setattr("crew_platform.weather.store.STORE", store)
    monkeypatch.setattr(service, "STORE", store)
    from crew_platform.weather import scheduler as _sched
    monkeypatch.setattr(_sched, "STORE", store)

    # active saved SimBrief plan — every fix sits INSIDE the downloaded box
    from optimizer.api import flightplan_service as fps
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path / "data"))
    fps.clear_fetch_cache()
    view = {
        "origin": "OJAI", "destination": "VHHH",
        "origin_lat": 25.27, "origin_lon": 55.30,
        "destination_lat": 51.38, "destination_lon": 35.92,
        "cruise_fl": 340, "callsign": "QTR815", "route": "OJAI QTR VHHH",
        "std_utc": "2026-10-06 18:00",
        "waypoints": [
            {"ident": "OJAI", "lat": 25.27, "lon": 55.30, "alt": None, "stage": "DEP"},
            {"ident": "DOK", "lat": 30.0, "lon": 48.0, "alt": 300},
            {"ident": "KAZ", "lat": 40.0, "lon": 42.0, "alt": 340},
            {"ident": "BAGAN", "lat": 45.0, "lon": 38.0, "alt": 340},
            {"ident": "VHHH", "lat": 51.38, "lon": 35.92, "alt": None, "stage": "ARR"},
        ],
    }
    fps.save_plan(view)

    receipt = {
        "cycle_id": cycle_id,
        "run_date": date,
        "run_hour": hour,
        "box": BOX,
        "offsets": list(meta.offsets),
        "grid_shape": [meta.n_lat, meta.n_lon],
        "fields": meta.fields,
        "fetched_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "endpoint": config.base_url,
    }
    (tmp_path / "real-gfs-receipt.json").write_text(json.dumps(receipt, indent=2))
    return store


@pytest.fixture()
def client(real_env):
    from fastapi.testclient import TestClient
    from optimizer.api.app import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_real_cycle_status(client, tmp_path):
    """The published REAL cycle is visible through the real status route."""
    r = client.get("/api/crew/weather/status")
    assert r.status_code == 200, r.text
    st = r.json()
    receipt = json.loads((tmp_path / "real-gfs-receipt.json").read_text())
    assert st["current_cycle"] == receipt["cycle_id"]
    assert st["has_plan"] is True
    # 6-hour cadence disclosure must be present
    assert "6-hour" in st["cadence"]


def test_real_route_samples_non_empty(client):
    """REAL wind/OAT/turbulence values at every navlog waypoint — none null."""
    r = client.get("/api/crew/weather/route", params={"fl": 340, "offset": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["ident"] for p in body["points"]] == ["OJAI", "DOK", "KAZ", "BAGAN", "VHHH"]
    s = body["samples"]
    assert len(s["points"]) == 5
    for p in s["points"]:
        # the real GFS field must be present at every fix in the box
        assert p["u_ms"] is not None, f"{p['ident']}: missing real u wind"
        assert p["v_ms"] is not None, f"{p['ident']}: missing real v wind"
        assert p["wind_speed_kt"] is not None and p["wind_speed_kt"] > 0
        assert p["wind_from_deg"] is not None and 0 <= p["wind_from_deg"] < 360
        assert p["oat_c"] is not None and -100 < p["oat_c"] < 60, p["oat_c"]
        assert "NOAA" in s["provenance"]


def test_real_turbulence_polygons_non_empty(client):
    """REAL turbulence polygonization: at least one closed polygon with
    plausible coordinates inside the downloaded box."""
    r = client.get("/api/crew/weather/layer/turbulence", params={"fl": 300, "offset": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert body["features"], "real GFS cycle produced no turbulence polygons"
    # standards-based GeoJSON: Feature wrappers carrying Polygon geometries
    polys = [
        f for f in body["features"]
        if f["type"] == "Feature" and f["geometry"]["type"] == "Polygon"
    ]
    assert polys, "no Feature(Polygon) features — malformed GeoJSON envelope"
    ring = polys[0]["geometry"]["coordinates"][0]
    assert len(ring) >= 4 and ring[0] == ring[-1], "outer ring must be closed"
    for x, y in ring:
        assert 15 <= x <= 75, f"lon {x} outside box"
        assert 5 <= y <= 70, f"lat {y} outside box"
    assert "NOAA" in body["source"]


def test_real_layer_family_and_receipt(client, tmp_path):
    """All five hazard products respond for the REAL cycle; the receipt file
    records exactly which cycle/validity was validated."""
    for prod in ("turbulence", "icing", "cape", "fronts", "jet"):
        r = client.get(f"/api/crew/weather/layer/{prod}",
                       params={"fl": 300, "offset": 0})
        assert r.status_code == 200, (prod, r.text)
        body = r.json()
        assert body["type"] == "FeatureCollection"
        assert body["cycle"] is not None
    receipt = json.loads((tmp_path / "real-gfs-receipt.json").read_text())
    assert re.match(r"^\d{8}_\d{2}$", receipt["cycle_id"])
    assert int(receipt["grid_shape"][0]) > 50, "0.25 deg box should be fine-grained"
