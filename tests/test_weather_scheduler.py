"""Regression + semantics tests for the background GFS cycle scheduler.

QA round 1 reproduced a critical live-update failure: ``set_active_box``
assigned the module-global ``_active_box`` as a *local* (missing ``global``),
so a route request set the box and the very next ``_box()`` read returned
None — the scheduler therefore never ingested the required 37 hourly steps.
These tests pin that fix and the surrounding scheduler contract:

* set_active_box persists; _box() returns the exact region afterwards;
* a full tick discovers a new cycle, ingests it, atomically publishes it and
  emits an SSE completion event (tick -> ingest -> publish -> SSE);
* a failed ingest keeps the last-good cycle authoritative, clears in-flight
  state, and is retried after a bounded backoff (not abandoned);
* stop() actually terminates the loop thread;
* the manual ``POST /ingest`` path and the scheduler share one job lock so
  two ingests can never run concurrently.

All tests run against a temp cache dir (WEATHER_CACHE_DIR) — no network, no
real NOAA blobs.
"""
from __future__ import annotations

import threading
import time

import pytest

from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore
from crew_platform.weather import scheduler
from crew_platform.weather.cycle import GfsConfig, GfsDownloadError
from crew_platform.weather.ingest import IngestRequest


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    store = CycleStore()
    # point the scheduler + its status/readers at this isolated store
    monkeypatch.setattr(scheduler, "STORE", store)
    return store


@pytest.fixture(autouse=True)
def _reset_scheduler_state():
    """Each test starts from a clean scheduler status/box/events state."""
    scheduler._active_box = None
    scheduler._stop_evt.clear()
    with scheduler._status_lock:
        scheduler._status.update(
            state="idle", current_cycle=None, in_progress_cycle=None,
            last_error=None, last_check=None, fetched_at=None,
            last_failed_cycle=None, last_failed_at=None,
        )
    with scheduler._events_cond:
        scheduler._events.clear()
    yield
    scheduler.stop(timeout=2)
    scheduler._active_box = None
    with scheduler._status_lock:
        scheduler._status.update(
            state="idle", current_cycle=None, in_progress_cycle=None,
            last_error=None, last_check=None, fetched_at=None,
            last_failed_cycle=None, last_failed_at=None,
        )


# --- the reproduced regression ---------------------------------------------


def test_set_active_box_persists(tmp_store):
    """QA repro: set_active_box must mutate the module global, not a local."""
    scheduler.set_active_box(25.0, 75.0, 65.0, 10.0)
    assert scheduler._active_box == (25.0, 75.0, 65.0, 10.0)
    req = scheduler._box()
    assert req is not None, "box was set but _box() returned None (regression)"
    assert (req.leftlon, req.rightlon, req.toplat, req.bottomlat) == (25.0, 75.0, 65.0, 10.0)
    # the 37 required hourly steps are the default offsets
    assert list(req.offsets) == list(range(0, 37))
    # status now reports a route is active
    assert scheduler.status_payload()["has_route"] is True


def test_set_active_box_replaces_previous(tmp_store):
    scheduler.set_active_box(25.0, 75.0, 65.0, 10.0)
    scheduler.set_active_box(0.0, 10.0, 50.0, 40.0)
    req = scheduler._box()
    assert (req.leftlon, req.rightlon, req.toplat, req.bottomlat) == (0.0, 10.0, 50.0, 40.0)


# --- tick -> ingest -> publish -> SSE --------------------------------------


def _discover(monkeypatch, date="20261005", hour="18"):
    monkeypatch.setattr(scheduler.gfs, "list_cycles", lambda config: [{"date": date}])
    monkeypatch.setattr(scheduler.gfs, "list_cycle_hours", lambda config, d: [hour])


def _fake_ingest_publish(tmp_store, offsets=(0, 6, 12), box=None):
    """A stand-in ingest_cycle that really stages + publishes via the store."""
    box = box or BoxKey(25.0, 75.0, 65.0, 10.0)

    def _ingest(config, req, store=None):
        store = store or tmp_store
        box = BoxKey(round(req.leftlon, 2), round(req.rightlon, 2),
                     round(req.toplat, 2), round(req.bottomlat, 2))
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

    return _ingest


def test_tick_ingests_publishes_and_emits_sse(tmp_store, monkeypatch):
    _discover(monkeypatch)
    monkeypatch.setattr(scheduler, "ingest_cycle", _fake_ingest_publish(tmp_store))
    scheduler.set_active_box(25.0, 75.0, 65.0, 10.0)

    scheduler._tick(GfsConfig.from_env())

    st = scheduler.status_payload()
    assert st["current_cycle"] == "20261005_18"
    assert st["in_progress_cycle"] is None
    assert st["state"] == "idle"
    assert tmp_store.load_meta("20261005_18", BoxKey(25.0, 75.0, 65.0, 10.0)) is not None
    # the SSE completion feed got the cycle
    ev = scheduler.wait_for_event(0.5, 0.0)
    assert ev is not None
    assert ev[0] == "20261005_18"


def test_tick_no_route_is_noop(tmp_store, monkeypatch):
    # no box set: the tick must not attempt any ingest
    ingested = []

    def _no_ingest(config, req, store=None):
        ingested.append(req)
        raise AssertionError("ingest must not run with no active route box")

    monkeypatch.setattr(scheduler, "ingest_cycle", _no_ingest)
    _discover(monkeypatch)
    scheduler._tick(GfsConfig.from_env())
    assert ingested == []


def test_failed_ingest_retains_last_good_and_retries(tmp_store, monkeypatch):
    """A failed cycle must not clobber current_cycle; it must be retried."""
    # seed a last-good cycle so there is something to retain
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    tmp_store.write_raw("20261005_12", box, 0, b"\x00" * 16)
    tmp_store.publish(CycleMeta(
        cycle_id="20261005_12", run_date="20261005", run_hour="12", box=box,
        offsets=[0], fields=["u"], n_lat=4, n_lon=4, fetched_at=time.time()))
    with scheduler._status_lock:
        scheduler._status["current_cycle"] = "20261005_12"

    _discover(monkeypatch, date="20261005", hour="18")  # newer than last-good
    monkeypatch.setenv("WEATHER_RETRY_BACKOFF_S", "600")

    attempts = {"n": 0}

    def _fail(config, req, store=None):
        attempts["n"] += 1
        raise GfsDownloadError("simulated download failure")

    monkeypatch.setattr(scheduler, "ingest_cycle", _fail)
    scheduler.set_active_box(25.0, 75.0, 65.0, 10.0)

    # first tick: fails, but last-good must survive
    scheduler._tick(GfsConfig.from_env())
    st = scheduler.status_payload()
    assert st["current_cycle"] == "20261005_12", "failure clobbered last-good cycle"
    assert st["in_progress_cycle"] is None
    assert st["state"] == "error"
    with scheduler._status_lock:
        assert scheduler._status["last_failed_cycle"] == "20261005_18"

    # immediate second tick: backoff not yet elapsed -> no retry
    scheduler._tick(GfsConfig.from_env())
    assert attempts["n"] == 1, "retried before backoff window elapsed"

    # after backoff, the same failed cycle IS retried
    with scheduler._status_lock:
        scheduler._status["last_failed_at"] = time.time() - 601
    scheduler._tick(GfsConfig.from_env())
    assert attempts["n"] == 2, "failed cycle was not retried after backoff"


# --- shutdown ----------------------------------------------------------------


def test_stop_terminates_loop_thread(tmp_store, monkeypatch):
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(scheduler, "_interval_s", lambda: 0.05)
    ticks = {"n": 0}
    real_tick = scheduler._tick

    def _counting_tick(config):
        ticks["n"] += 1
        real_tick(config)

    monkeypatch.setattr(scheduler, "_tick", _counting_tick)

    assert scheduler.start() is True
    t = scheduler._thread
    assert t is not None and t.is_alive()
    time.sleep(0.15)
    scheduler.stop(timeout=2)
    assert not t.is_alive(), "loop thread did not stop"
    assert scheduler._running is False
    # the loop actually ran a few ticks before stopping
    assert ticks["n"] >= 1


def test_start_is_idempotent(tmp_store, monkeypatch):
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(scheduler, "_interval_s", lambda: 0.5)
    assert scheduler.start() is True
    assert scheduler.start() is False  # already running
    scheduler.stop(timeout=2)


# --- shared job lock (concurrent manual + scheduled) ------------------------


def test_job_lock_prevents_concurrent_ingest(tmp_store):
    assert scheduler.acquire_job() is True
    # while the manual job holds the lock, a second acquire must fail
    assert scheduler.acquire_job(timeout=0) is False
    assert scheduler.job_available() is False
    scheduler.release_job()
    assert scheduler.job_available() is True
    assert scheduler.acquire_job(timeout=0) is True
    scheduler.release_job()


def test_tick_skips_when_manual_job_holds_lock(tmp_store, monkeypatch):
    _discover(monkeypatch)
    started = []

    def _slow_ingest(config, req, store=None):
        started.append(req)
        time.sleep(0.05)
        return CycleMeta(
            cycle_id=req.cycle_id, run_date=req.cycle_id.split("_")[0],
            run_hour=req.cycle_id.split("_")[1], box=BoxKey(25.0, 75.0, 65.0, 10.0),
            offsets=[0], fields=["u"], n_lat=4, n_lon=4, fetched_at=time.time())

    monkeypatch.setattr(scheduler, "ingest_cycle", _slow_ingest)
    scheduler.set_active_box(25.0, 75.0, 65.0, 10.0)

    # a manual job holds the shared lock
    assert scheduler.acquire_job() is True
    try:
        scheduler._tick(GfsConfig.from_env())
        assert started == [], "tick ingested while manual job held the lock"
        assert scheduler.status_payload()["in_progress_cycle"] is None
    finally:
        scheduler.release_job()
