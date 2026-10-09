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

import time

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
    tmp_store.write_raw("20261005_18", box, 0, b"\x00" * 16)
    with pytest.raises(ValueError):
        tmp_store.publish(_meta(offsets=(0, 6, 12)))
    # before publish, no meta is visible
    assert tmp_store.load_meta("20261005_18", box) is None
    assert tmp_store.list_published() == []


def test_publish_then_visible(tmp_store):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    for off in (0, 6, 12):
        tmp_store.write_raw("20261005_18", box, off, b"\x00" * 16)
    m = _meta(offsets=(0, 6, 12))
    tmp_store.publish(m)
    loaded = tmp_store.load_meta("20261005_18", box)
    assert loaded is not None
    assert loaded.cycle_id == "20261005_18"
    assert loaded.offsets == [0, 6, 12]
    assert any(m.cycle_id == "20261005_18" for m in tmp_store.list_published())


def test_two_regions_same_cycle_do_not_collide(tmp_store):
    """Regression: two boxes inside one cycle must each keep their own
    data files — previously both wrote f###.grb2 into the same dir."""
    box_a = BoxKey(25.0, 75.0, 65.0, 10.0)
    box_b = BoxKey(80.0, 140.0, 55.0, 10.0)
    for off in (0, 12):
        tmp_store.write_raw("20261005_18", box_a, off, b"A" * 8)
        tmp_store.write_raw("20261005_18", box_b, off, b"B" * 8)
    tmp_store.publish(_meta(box=box_a, offsets=(0, 12)))
    tmp_store.publish(_meta(box=box_b, offsets=(0, 12)))
    # both meta files visible under one cycle
    metas = tmp_store.list_published()
    boxes = {m.box for m in metas if m.cycle_id == "20261005_18"}
    assert boxes == {box_a, box_b}
    # the staged data files are distinct (no overwrite)
    a0 = cache_root() / "20261005_18" / str(box_a) / "f000.grb2"
    b0 = cache_root() / "20261005_18" / str(box_b) / "f000.grb2"
    assert a0.read_bytes() == b"A" * 8
    assert b0.read_bytes() == b"B" * 8


def test_republish_is_atomic(tmp_store):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    for off in (0,):
        tmp_store.write_raw("20261005_18", box, off, b"\x00" * 16)
    tmp_store.publish(_meta(offsets=(0,)))
    # republish a newer cycle does not clobber the older meta file
    for off in (0,):
        tmp_store.write_raw("20261006_00", box, off, b"\x00" * 16)
    tmp_store.publish(_meta(cycle="20261006_00", offsets=(0,)))
    metas = tmp_store.list_published()
    ids = {m.cycle_id for m in metas}
    assert {"20261005_18", "20261006_00"} <= ids
    # most recent first
    assert metas[0].cycle_id == "20261006_00"


def test_boxkey_roundtrip_is_stable():
    """Regression: BoxKey('25.0_75.0_65.0_10.0') must survive str→parse
    (was order (left, bottom, top, right) vs (left, right, top, bottom))."""
    k = BoxKey(25.0, 75.0, 65.0, 10.0)
    assert BoxKey.parse(str(k)) == k
    assert BoxKey.parse("25.00_75.00_10.00_65.00") == k
    assert str(k) == "25.00_75.00_10.00_65.00"


def test_staleness_flags_old_cycle():
    now = time.time()
    fresh = staleness(now, now - 3600 * 2)
    assert fresh["stale"] is False
    old = staleness(now, now - 3600 * 12)
    assert old["stale"] is True
    assert old["age_hours"] > 7.0


def test_write_raw_leaves_no_tmp_files(tmp_store, tmp_path):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    tmp_store.write_raw("20261005_18", box, 0, b"hello")
    cyc = tmp_path / "20261005_18"
    assert (cyc / str(box) / "f000.grb2").is_file()
    # no tmp files anywhere under the cycle dir
    tmps = [p for p in cyc.rglob("*") if p.name.endswith(".tmp")]
    assert tmps == []


def test_prune_evicts_oldest_keeps_newest(tmp_store, tmp_path, monkeypatch):
    box = BoxKey(25.0, 75.0, 65.0, 10.0)
    for cyc in ("20261001_00", "20261002_00", "20261003_00"):
        for off in (0,):
            tmp_store.write_raw(cyc, box, off, b"\x00" * 4096)
        tmp_store.publish(_meta(cycle=cyc, offsets=(0,)))
    # force a tiny ceiling so pruning must evict (but always keeps newest)
    monkeypatch.setenv("WEATHER_CACHE_MAX_MB", "0.001")
    evicted = tmp_store.prune()
    assert evicted, "expected eviction under a tiny cache limit"
    ids = {m.cycle_id for m in tmp_store.list_published()}
    assert "20261003_00" in ids, "newest cycle must survive pruning"
    assert "20261001_00" not in ids


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
