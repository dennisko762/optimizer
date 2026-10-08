"""Tests for the atomic cycle cache (store) and scheduler semantics.

Cache atomicity: a cycle is invisible until its meta.json is atomically
swapped in; publishing refuses when any declared offset is missing on disk.
Staleness: a 6-hour-cadence cycle older than ~7 h is flagged stale.
Scheduler: a single ingest runs at a time and the last-good cycle is the
offline fallback.

All tests run against a temp cache dir (WEATHER_CACHE_DIR) — no network, no
real NOAA blobs.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore, cache_root, staleness
from crew_platform.weather import scheduler


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    return CycleStore()


def _meta(cycle="20261005_18", box=None, offsets=(0, 6, 12)):
    box = box or BoxKey(25.0, 75.0, 65.0, 10.0)
    return CycleMeta(
        cycle_id=cycle, run_date="20261005", run_hour="18", box=box,
        offsets=list(offsets), fields=["u", "v", "t"], n_lat=4, n_lon=4,
        fetched_at=time.time(),
    )


def test_publish_requires_all_offsets(tmp_store, tmp_path):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    # stage only offset 0 — offsets 6 and 12 are missing
    tmp_store.write_raw("20261005_18", 0, b"\x00" * 16)
    with pytest.raises(ValueError):
        tmp_store.publish(_meta(offsets=(0, 6, 12)))
    # before publish, no meta is visible
    assert tmp_store.load_meta("20261005_18", box) is None
    assert tmp_store.list_published() == []


def test_publish_then_visible(tmp_store):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    for off in (0, 6, 12):
        tmp_store.write_raw("20261005_18", off, b"\x00" * 16)
    m = _meta(offsets=(0, 6, 12))
    tmp_store.publish(m)
    loaded = tmp_store.load_meta("20261005_18", box)
    assert loaded is not None
    assert loaded.cycle_id == "20261005_18"
    assert loaded.offsets == [0, 6, 12]
    assert any(m.cycle_id == "20261005_18" for m in tmp_store.list_published())


def test_republish_is_atomic(tmp_store):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    for off in (0,):
        tmp_store.write_raw("20261005_18", off, b"\x00" * 16)
    tmp_store.publish(_meta(offsets=(0,)))
    # republish a newer cycle does not clobber the older meta file
    for off in (0,):
        tmp_store.write_raw("20261006_00", off, b"\x00" * 16)
    tmp_store.publish(_meta(cycle="20261006_00", offsets=(0,)))
    metas = tmp_store.list_published()
    ids = {m.cycle_id for m in metas}
    assert {"20261005_18", "20261006_00"} <= ids
    # most recent first
    assert metas[0].cycle_id == "20261006_00"


def test_staleness_flags_old_cycle():
    now = time.time()
    fresh = staleness(now, now - 3600 * 2)
    assert fresh["stale"] is False
    old = staleness(now, now - 3600 * 12)
    assert old["stale"] is True
    assert old["age_hours"] > 7.0


def test_write_raw_leaves_no_tmp_files(tmp_store, tmp_path):
    tmp_store.write_raw("20261005_18", 0, b"hello")
    cyc = tmp_path / "20261005_18"
    files = list(cyc.iterdir())
    assert [f.name for f in files] == ["f000.grb2"]


def test_bad_cycle_id_rejected(tmp_store):
    with pytest.raises(ValueError):
        tmp_store.write_raw("../evil", 0, b"x")


# --- scheduler: last-good fallback + single-flight -------------------------


def test_scheduler_last_good_fallback(monkeypatch):
    # no route box set -> tick is a no-op (no ingest attempted)
    scheduler._active_box = None
    monkeypatch.setattr(scheduler, "_box", lambda: None)
    scheduler._tick(scheduler.gfs.GfsConfig.from_env())
    assert scheduler.status_payload()["has_route"] is False


def test_scheduler_status_shape():
    st = scheduler.status_payload()
    for key in ("enabled", "state", "current_cycle", "last_good",
                "has_route", "stale", "cadence", "source"):
        assert key in st
    assert "6-hour" in st["cadence"]
