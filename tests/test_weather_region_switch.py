"""Regression: weather cycle selection must be region-aware (cycle, box).

Independent review of PR #16 found two region bugs:

* ``scheduler._tick`` skipped ingest whenever the *cycle id* matched the
  last completed one, so changing the route (``set_active_box``) inside one
  6-hour cycle never fetched the new region — the crew kept being served
  the previous route's box until the next cycle rolled over;
* ``service``/``routes`` picked the *first* published dataset for a cycle
  regardless of region, so even once the new box existed the old one could
  still be served.

These tests pin the fixed contract: a new box in the SAME cycle is ingested
and is the one served (API + service level), while the previously ingested
box stays published and usable.

Offline: no network, temp cache dir, synthetic grids.
"""
from __future__ import annotations

import time

import pytest

from crew_platform.weather import scheduler, service
from crew_platform.weather.cycle import GfsConfig
from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore, cache_root

# reuse the module-level synthetic bundle writer + the seeded app fixtures
from tests.test_weather_api import (  # noqa: F401 — fixtures used by name
    _synthetic_bundle,
    client,
    weather_env,
)

CYCLE = "20261005_18"
BOX_A = BoxKey(25.0, 75.0, 65.0, 10.0)      # wide EU/Asia box
BOX_B = BoxKey(-10.0, 20.0, 60.0, 35.0)     # different region, same cycle


def _publish(store, box, cycle=CYCLE, offsets=(0,)):
    """Stage + publish a synthetic dataset for one (cycle, box)."""
    for off in offsets:
        _synthetic_bundle(cache_root(), cycle=cycle, offset=off, box=box)
    meta = CycleMeta(
        cycle_id=cycle, run_date=cycle.split("_")[0], run_hour=cycle.split("_")[1],
        box=box, offsets=sorted(offsets),
        fields=["u", "v", "t", "ti", "ice", "cape", "front"],
        n_lat=25, n_lon=25, fetched_at=time.time(),
    )
    store.publish(meta)
    return meta


# --- scheduler: a new box in the same cycle is ingested ---------------------


@pytest.fixture()
def sched_store(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    store = CycleStore()
    monkeypatch.setattr(scheduler, "STORE", store)
    monkeypatch.setattr(service, "STORE", store)
    monkeypatch.setattr(scheduler.gfs, "list_cycles", lambda config: [{"date": "20261005"}])
    monkeypatch.setattr(scheduler.gfs, "list_cycle_hours", lambda config, d: ["18"])
    scheduler._active_box = None
    with scheduler._status_lock:
        scheduler._status.update(
            state="idle", current_cycle=None, current_box=None,
            in_progress_cycle=None, in_progress_box=None, last_error=None,
            last_check=None, fetched_at=None, last_failed_cycle=None,
            last_failed_box=None, last_failed_at=None,
        )
    yield store
    scheduler._active_box = None
    with scheduler._status_lock:
        scheduler._status.update(
            state="idle", current_cycle=None, current_box=None,
            in_progress_cycle=None, in_progress_box=None, last_error=None,
            fetched_at=None, last_failed_cycle=None, last_failed_box=None,
            last_failed_at=None,
        )


def test_box_switch_in_same_cycle_is_ingested(sched_store, monkeypatch):
    """set_active_box to a new region must trigger ingest within one cycle."""
    requested: list[BoxKey] = []

    def _ingest(config, req, store=None):
        store = store or sched_store
        box = BoxKey(req.leftlon, req.rightlon, req.toplat, req.bottomlat)
        requested.append(box)
        for off in req.offsets:
            store.write_raw(req.cycle_id, box, off, b"\x00" * 16)
        meta = CycleMeta(
            cycle_id=req.cycle_id, run_date=req.cycle_id.split("_")[0],
            run_hour=req.cycle_id.split("_")[1], box=box,
            offsets=sorted(int(o) for o in req.offsets),
            fields=["u", "v", "t"], n_lat=4, n_lon=4, fetched_at=time.time(),
        )
        store.publish(meta)
        return meta

    monkeypatch.setattr(scheduler, "ingest_cycle", _ingest)

    # cycle rolls in for route box A
    scheduler.set_active_box(BOX_A.leftlon, BOX_A.rightlon, BOX_A.toplat, BOX_A.bottomlat)
    scheduler._tick(GfsConfig.from_env())
    assert [str(b) for b in requested] == [str(BOX_A)]
    st = scheduler.status_payload()
    assert (st["current_cycle"], st["current_box"]) == (CYCLE, str(BOX_A))

    # same box again inside the same cycle: still skipped (no re-download)
    scheduler._tick(GfsConfig.from_env())
    assert [str(b) for b in requested] == [str(BOX_A)]

    # the crew loads a different route -> the new box MUST be ingested even
    # though the cycle id is unchanged (the reviewed regression)
    scheduler.set_active_box(BOX_B.leftlon, BOX_B.rightlon, BOX_B.toplat, BOX_B.bottomlat)
    scheduler._tick(GfsConfig.from_env())
    assert [str(b) for b in requested] == [str(BOX_A), str(BOX_B)], (
        "box change inside one cycle did not trigger ingest"
    )
    assert sched_store.load_meta(CYCLE, BOX_B) is not None
    # the earlier region stays published (offline fallback is not destroyed)
    assert sched_store.load_meta(CYCLE, BOX_A) is not None
    st = scheduler.status_payload()
    assert (st["current_cycle"], st["current_box"]) == (CYCLE, str(BOX_B))

    # an already-published box is not re-downloaded when switching back
    scheduler.set_active_box(BOX_A.leftlon, BOX_A.rightlon, BOX_A.toplat, BOX_A.bottomlat)
    scheduler._tick(GfsConfig.from_env())
    assert [str(b) for b in requested] == [str(BOX_A), str(BOX_B)]
    assert scheduler.status_payload()["current_box"] == str(BOX_A)


def test_failed_box_backoff_is_per_box(sched_store, monkeypatch):
    """A failed box must not back off a *different* box in the same cycle."""
    from crew_platform.weather.cycle import GfsDownloadError

    attempts: list[str] = []

    def _fail(config, req, store=None):
        attempts.append(str(BoxKey(req.leftlon, req.rightlon, req.toplat, req.bottomlat)))
        raise GfsDownloadError("simulated download failure")

    monkeypatch.setattr(scheduler, "ingest_cycle", _fail)
    monkeypatch.setenv("WEATHER_RETRY_BACKOFF_S", "600")

    scheduler.set_active_box(BOX_A.leftlon, BOX_A.rightlon, BOX_A.toplat, BOX_A.bottomlat)
    scheduler._tick(GfsConfig.from_env())
    scheduler._tick(GfsConfig.from_env())  # inside backoff -> no retry
    assert attempts == [str(BOX_A)]

    scheduler.set_active_box(BOX_B.leftlon, BOX_B.rightlon, BOX_B.toplat, BOX_B.bottomlat)
    scheduler._tick(GfsConfig.from_env())
    assert attempts == [str(BOX_A), str(BOX_B)], (
        "a different box was suppressed by another box's backoff"
    )


# --- service: the active box is the one served ------------------------------


def test_service_selects_the_active_box_within_one_cycle(sched_store):
    _publish(sched_store, BOX_A)
    _publish(sched_store, BOX_B)

    assert str(service._published_meta(CYCLE, BOX_B).box) == str(BOX_B)
    assert str(service._published_meta(CYCLE, BOX_A).box) == str(BOX_A)
    # ranked visibility: the active box is first for that box
    assert str(service.visible_metas(BOX_B)[0].box) == str(BOX_B)
    assert str(service.visible_metas(BOX_A)[0].box) == str(BOX_A)
    # no active route -> store order (newest first), nothing filtered out
    assert len(service.visible_metas(None)) == 2

    layer = service.hazard_layer(CYCLE, "turbulence", 300.0, 0, box=BOX_B)
    assert layer["box"] == str(BOX_B)
    assert layer["cycle"] == CYCLE


def test_wider_box_still_serves_a_route_inside_it(sched_store):
    """A region that covers the route is served (no false 'no cycle')."""
    _publish(sched_store, BOX_A)
    inner = BoxKey(30.0, 60.0, 55.0, 20.0)   # fully inside BOX_A
    assert service.box_covers(BOX_A, inner) is True
    assert str(service._published_meta(CYCLE, inner).box) == str(BOX_A)
    assert str(service.visible_metas(inner)[0].box) == str(BOX_A)


def test_box_usable_for_rejects_unusable_regions(sched_store):
    """The status helper only reports a region that can serve the route."""
    assert scheduler._box_usable_for(str(BOX_A), None) is True     # no route
    assert scheduler._box_usable_for(str(BOX_A), BOX_A) is True    # exact
    assert scheduler._box_usable_for(None, BOX_A) is False         # not ingested
    assert scheduler._box_usable_for("not-a-box", BOX_A) is False  # unparseable
    assert scheduler._box_usable_for(str(BOX_B), BOX_A) is False   # other region
    inner = BoxKey(30.0, 60.0, 55.0, 20.0)
    assert scheduler._box_usable_for(str(BOX_A), inner) is True    # covering


def test_latest_cycle_degrades_with_503_when_nothing_is_published(sched_store):
    """An empty store is a 503 (downloading), never a fabricated cycle."""
    from fastapi import HTTPException

    from crew_platform.weather import routes

    with pytest.raises(HTTPException) as exc:
        routes._latest_cycle(BOX_A)
    assert exc.value.status_code == 503
    assert "selected route box" in exc.value.detail
    with pytest.raises(HTTPException) as exc_any:
        routes._latest_cycle(None)
    assert exc_any.value.status_code == 503


# --- API: /route and /layer serve the active route box ----------------------


def test_route_endpoint_serves_the_active_route_box(client, weather_env):  # noqa: F811
    """End-to-end: publishing the plan's own box makes /route serve it."""
    from crew_platform.weather import routes

    store = weather_env
    view = routes._active_plan()
    ll, rl, tp, bl = routes._route_box(view)
    plan_box = BoxKey(ll, rl, tp, bl)
    assert str(plan_box) != str(BOX_A), "fixture box must differ from the plan box"

    # only the seeded (partial-coverage) box A exists: it is still served
    # rather than blanking the panel, and the served region is disclosed
    first = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert first.status_code == 200
    assert first.json()["samples"]["box"] == str(BOX_A)

    # the scheduler lands the plan's own region inside the same cycle
    _publish(store, plan_box)

    second = client.get("/api/crew/weather/route", params={"fl": 300, "offset": 0})
    assert second.status_code == 200
    body = second.json()
    assert body["samples"]["cycle"] == CYCLE
    assert body["samples"]["box"] == str(plan_box), (
        "/route kept serving the stale region after the active box was published"
    )
    # route itself is unchanged (no behaviour regression)
    assert [p["ident"] for p in body["points"]] == ["EDDF", "AMZ", "NOV", "RJAA"]

    # the hazard layer follows the same active box
    layer = client.get(
        "/api/crew/weather/layer/turbulence", params={"fl": 300, "offset": 0}
    )
    assert layer.status_code == 200
    assert layer.json()["box"] == str(plan_box)
