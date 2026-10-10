"""Regression: cycle selection must fall back to an older covering cycle.

Follow-up to the PR #16 M5-P weather review (item 4, INFO/non-blocking):
``visible_metas``/``_published_meta`` used to rank candidates by cycle
recency FIRST and region fitness only as a tiebreak within one cycle. That
meant a newer published cycle whose only box was disjoint from the active
route still won over an older cycle whose box fully covered the route, so
``/route`` and ``/layer`` silently degraded every sample to ``None``
("unavailable") instead of falling back to the covering cycle that was
right there in the store.

This pins the fixed contract:
* a newer cycle with a disjoint box must not be selected when an older
  cycle's box covers the route -- the older covering cycle wins and its
  samples are real numbers, not None;
* when NO published cycle covers the route at all, selection still
  degrades honestly (serves the best-effort newest cycle; samples report
  explicit "unavailable", never a fabricated value).

Offline: no network, temp cache dir, synthetic grids (same fixtures as
``test_weather_region_switch.py``).
"""
from __future__ import annotations

import time

import pytest

from crew_platform.weather import service
from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore, cache_root

from tests.test_weather_api import _synthetic_bundle  # noqa: F401 — used by name

OLD_CYCLE = "20261005_12"
NEW_CYCLE = "20261005_18"

# The route lives entirely inside ROUTE_BOX.
ROUTE_BOX = BoxKey(leftlon=30.0, rightlon=60.0, toplat=55.0, bottomlat=20.0)
# Older cycle's only dataset fully covers the route.
COVERING_BOX = BoxKey(leftlon=0.0, rightlon=90.0, toplat=70.0, bottomlat=0.0)
# Newer cycle's only dataset is for a disjoint region (e.g. a different crew's
# route), far away from ROUTE_BOX -- no overlap at all.
DISJOINT_BOX = BoxKey(leftlon=-130.0, rightlon=-60.0, toplat=55.0, bottomlat=10.0)

ROUTE_VIEW = {
    "std_utc": "2026-10-05 12:00",
    "waypoints": [
        {"ident": "AAA", "lat": 25.0, "lon": 40.0, "ete": 0},
        {"ident": "BBB", "lat": 40.0, "lon": 50.0, "ete": "1:00"},
    ],
}


@pytest.fixture()
def cov_store(monkeypatch, tmp_path):
    """Isolated cache dir + in-memory store, bound into the service module."""
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    store = CycleStore()
    monkeypatch.setattr(service, "STORE", store)
    yield store


def _publish(store, cycle, box, offsets=(0,)):
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


def test_newer_disjoint_cycle_is_skipped_for_older_covering_cycle(cov_store):
    """Older covering cycle wins over a newer disjoint one."""
    _publish(cov_store, OLD_CYCLE, COVERING_BOX)
    _publish(cov_store, NEW_CYCLE, DISJOINT_BOX)

    # sanity: the two boxes really are disjoint / covering, respectively
    assert service.box_overlap_area(DISJOINT_BOX, ROUTE_BOX) == 0.0
    assert service.box_covers(COVERING_BOX, ROUTE_BOX) is True

    ranked = service.visible_metas(ROUTE_BOX)
    assert ranked[0].cycle_id == OLD_CYCLE, (
        "newest-cycle-first selection picked a disjoint box over an older "
        "covering cycle"
    )
    assert str(ranked[0].box) == str(COVERING_BOX)

    # the samples are real numbers from the covering (older) cycle, not a
    # silently degraded disjoint-box lookup
    out = service.route_samples(ROUTE_VIEW, OLD_CYCLE, 300.0, 0, box=ROUTE_BOX)
    assert out["box"] == str(COVERING_BOX)
    assert out["cycle"] == OLD_CYCLE
    pts = out["points"]
    assert len(pts) == 2
    for p in pts:
        assert p["wind_speed_kt"] is not None
        assert "wind" not in p["unavailable"]


def test_latest_cycle_route_helper_falls_back_to_covering_cycle(cov_store):
    """routes._latest_cycle(box) must not pin the newest disjoint cycle."""
    from crew_platform.weather import routes

    _publish(cov_store, OLD_CYCLE, COVERING_BOX)
    _publish(cov_store, NEW_CYCLE, DISJOINT_BOX)

    assert routes._latest_cycle(ROUTE_BOX) == OLD_CYCLE
    # with no active route box, true newest-cycle-first ordering is kept
    assert routes._latest_cycle(None) == NEW_CYCLE


def test_no_covering_cycle_degrades_honestly_never_fabricates(cov_store):
    """No published cycle covers the route -> explicit unavailable, no crash."""
    _publish(cov_store, OLD_CYCLE, DISJOINT_BOX)
    _publish(cov_store, NEW_CYCLE, DISJOINT_BOX)

    ranked = service.visible_metas(ROUTE_BOX)
    assert ranked, "store is not empty"
    # no cycle covers the route: falls back to newest-first among the
    # equally-uncovering candidates (today's honest degraded behaviour)
    assert ranked[0].cycle_id == NEW_CYCLE

    out = service.route_samples(ROUTE_VIEW, NEW_CYCLE, 300.0, 0, box=ROUTE_BOX)
    assert out["box"] == str(DISJOINT_BOX)
    for p in out["points"]:
        # outside the grid entirely -> sampler reports None, never a
        # fabricated reading, and the point says so explicitly
        assert p["wind_speed_kt"] is None
        assert "wind" in p["unavailable"]

    # an empty store (no cycle at all) is the other honest-degraded path,
    # already covered by test_latest_cycle_degrades_with_503_when_nothing_is_published
