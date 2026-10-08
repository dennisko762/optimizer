"""Background GFS cycle scheduler.

A daemon thread wakes every ``check_interval_s`` (default 180 s), discovers
the most recent published GFS 0.25 deg cycle, and ingests it (bounded subset
per the active route box) when a *new* cycle has appeared since the last
completed one. Completed cycles are served from the atomic disk cache, so
map requests answer in sub-second time and a brand-new cycle becomes visible
the moment its ``meta.json`` is swapped in.

Semantics (per the task):
* "live" means a new 6-hour model cycle lands in the background — raw values
  do not churn continuously. Status always carries ``cycle / valid_at /
  fetched_at / stale`` so the UI can disclose the cadence.
* a single ingest runs at a time (a lock + an in-flight marker prevent
  duplicate concurrent cycle jobs);
* the last *completed* cycle is the offline fallback: if the newest cycle
  cannot be fetched, the previously published one stays authoritative;
* completion is signalled through a simple event queue the SSE endpoint
  tails, so open maps refresh automatically when a new cycle lands.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Any, Optional

from . import cycle as gfs
from .cycle import GfsConfig
from .ingest import IngestRequest, ingest_cycle
from .store import STORE, staleness

LOG = logging.getLogger("crew_weather.scheduler")

#: per-run status (visible to the status endpoint)
_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "state": "idle",          # idle | checking | downloading | error
    "current_cycle": None,    # cycle_id of the last *completed* ingest
    "in_progress_cycle": None,
    "last_error": None,
    "last_check": None,
    "fetched_at": None,
}

#: active route box the scheduler should keep current for (set by the route
#: endpoint; None -> the scheduler idles, nothing to ingest).
_box_lock = threading.Lock()
_active_box: Optional[tuple[float, float, float, float]] = None  # ll, rl, tp, bl

#: completion events (cycle_id, ts) for the SSE refresh feed (bounded).
_events: deque[tuple[str, float]] = deque(maxlen=32)
_events_cond = threading.Condition()

_running = False
_thread: Optional[threading.Thread] = None


def _interval_s() -> float:
    return float(os.environ.get("WEATHER_CHECK_INTERVAL_S", "180"))


def _box() -> Optional[IngestRequest]:
    with _box_lock:
        if _active_box is None:
            return None
        ll, rl, tp, bl = _active_box
    return IngestRequest(cycle_id="", leftlon=ll, rightlon=rl, toplat=tp, bottomlat=bl, offsets=range(0, 37))


def set_active_box(ll: float, rl: float, tp: float, bl: float) -> None:
    """Point the scheduler at the route region to keep current."""
    with _box_lock:
        _active_box = (ll, rl, tp, bl)
    with _status_lock:
        _status["state"] = "checking"


def _mark(state: str, **kw: Any) -> None:
    with _status_lock:
        _status["state"] = state
        _status["last_check"] = time.time()
        _status.update(kw)


def _emit(cycle_id: str) -> None:
    with _events_cond:
        _events.append((cycle_id, time.time()))
        _events_cond.notify_all()


def _run_cycle(config: GfsConfig, cycle_id: str, req: IngestRequest) -> None:
    req2 = IngestRequest(cycle_id=req.cycle_id or cycle_id,
                         leftlon=req.leftlon, rightlon=req.rightlon,
                         toplat=req.toplat, bottomlat=req.bottomlat,
                         offsets=tuple(req.offsets))
    meta = ingest_cycle(config, req2, store=STORE)
    with _status_lock:
        _status["current_cycle"] = meta.cycle_id
        _status["fetched_at"] = meta.fetched_at
        _status["in_progress_cycle"] = None
    _emit(meta.cycle_id)


def _tick(config: GfsConfig) -> None:
    """One scheduler iteration: discover + ingest a newer cycle."""
    req = _box()
    if req is None:
        return
    _mark("checking")
    try:
        cyc = gfs.list_cycles(config)
        if not cyc:
            _mark("idle", last_error="no GFS cycle directories found")
            return
        date = cyc[0]["date"]
        hours = gfs.list_cycle_hours(config, date)
        if not hours:
            _mark("idle", last_error="no run hours found")
            return
        newest = f"{date}_{hours[0]}"

        with _status_lock:
            current = _status["current_cycle"]
            in_progress = _status["in_progress_cycle"]
        if newest == current or newest == in_progress:
            _mark("idle")
            return

        _mark("downloading", in_progress_cycle=newest, current_cycle=newest)
        try:
            _run_cycle(config, newest, req)
            _mark("idle", current_cycle=newest, in_progress_cycle=None, last_error=None)
        except gfs.GfsError as exc:
            # keep the last good cycle authoritative (offline fallback)
            with _status_lock:
                _status["in_progress_cycle"] = None
            _mark("error", last_error=str(exc))
            LOG.warning("ingest of %s failed: %s", newest, exc)
    except Exception as exc:  # noqa: BLE001 — a scheduler bug must not kill the thread
        with _status_lock:
            _status["in_progress_cycle"] = None
        _mark("error", last_error=f"internal: {exc}")
        LOG.exception("scheduler tick failed")


def _loop(config: GfsConfig) -> None:
    interval = _interval_s()
    while True:
        try:
            _tick(config)
        except Exception:  # noqa: BLE001
            LOG.exception("unexpected scheduler error")
        time.sleep(interval)


def start() -> bool:
    """Start the background scheduler (idempotent). True if started now."""
    global _running, _thread
    if _running:
        return False
    if os.environ.get("WEATHER_SCHEDULER_ENABLED", "1") == "0":
        return False
    _running = True
    config = GfsConfig.from_env()
    interval = _interval_s()
    _thread = threading.Thread(target=_loop, args=(config,), name="weather-scheduler", daemon=True)
    _thread.start()
    LOG.info("weather scheduler started (interval %.0fs)", interval)
    return True


def stop() -> None:
    global _running
    _running = False


def status_payload() -> dict[str, Any]:
    """The readiness/status object the UI polls."""
    with _status_lock:
        s = dict(_status)
    with _box_lock:
        has_box = _active_box is not None
    published = STORE.list_published()
    latest = published[0] if published else None
    fetched = s.get("fetched_at") or (latest.fetched_at if latest else None)
    stale_block = staleness(time.time(), fetched) if fetched else None
    return {
        "enabled": os.environ.get("WEATHER_SCHEDULER_ENABLED", "1") != "0",
        "state": s["state"],
        "current_cycle": s["current_cycle"] or (latest.cycle_id if latest else None),
        "latest_published": [m.cycle_id for m in published[:4]],
        "last_good": latest.cycle_id if latest else None,
        "has_route": has_box,
        "fetched_at": fetched,
        "stale": (stale_block or {}).get("stale", None),
        "age_hours": (stale_block or {}).get("age_hours", None),
        "last_error": s["last_error"],
        "cadence": "6-hour model cycles, hourly forecast steps (T+0..T+36)",
        "source": "NOAA NOMADS GFS 0.25 deg (g2sub) — public, unmodified proxy products",
    }


def wait_for_event(timeout: float, last_ts: float) -> Optional[tuple[str, float]]:
    """Block until a completion event newer than ``last_ts`` (or timeout)."""
    with _events_cond:
        while True:
            newest = _events[-1] if _events else None
            if newest and newest[1] > last_ts:
                return newest
            _events_cond.wait(timeout)
            return None
