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
from .store import BoxKey, STORE, staleness

LOG = logging.getLogger("crew_weather.scheduler")

#: per-run status (visible to the status endpoint)
_status_lock = threading.Lock()
_status: dict[str, Any] = {
    "state": "idle",          # idle | checking | downloading | error
    "current_cycle": None,    # cycle_id of the last *completed* ingest
    "current_box": None,      # box string of the last *completed* ingest
    "in_progress_cycle": None,
    "in_progress_box": None,
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

#: shared job lock — only one ingest (scheduled OR manual) at a time.
_job_lock = threading.Lock()

#: stop event so stop() actually terminates the loop thread.
_stop_evt = threading.Event()


def _interval_s() -> float:
    return float(os.environ.get("WEATHER_CHECK_INTERVAL_S", "180"))


def _box() -> Optional[IngestRequest]:
    with _box_lock:
        if _active_box is None:
            return None
        ll, rl, tp, bl = _active_box
    return IngestRequest(cycle_id="", leftlon=ll, rightlon=rl, toplat=tp, bottomlat=bl, offsets=range(0, 37))


def active_box_key() -> Optional[BoxKey]:
    """Return the currently selected route box using the store's identity."""
    with _box_lock:
        if _active_box is None:
            return None
        ll, rl, tp, bl = _active_box
    return BoxKey(ll, rl, tp, bl)


def _box_usable_for(box_text: Optional[str], active: Optional[BoxKey]) -> bool:
    """True when the dataset region ``box_text`` can serve ``active``.

    No active route (``active is None``) means any region is reportable; an
    unparseable/absent region is not.
    """
    if active is None:
        return True
    if not box_text:
        return False
    from . import service  # local import: avoids an import cycle at module load

    try:
        box = BoxKey.parse(box_text)
    except ValueError:
        return False
    return service.same_box(box, active) or service.box_covers(box, active)


def set_active_box(ll: float, rl: float, tp: float, bl: float) -> None:
    """Point the scheduler at the route region to keep current."""
    global _active_box
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
    if not _job_lock.acquire(timeout=0):
        # a manual (or other) ingest job holds the lock — skip this tick,
        # the next one will retry.
        with _status_lock:
            _status["in_progress_cycle"] = None
            _status["in_progress_box"] = None
        _mark("idle", last_error="ingest in progress (manual job holds the lock)")
        return
    try:
        meta = ingest_cycle(config, req2, store=STORE)
    finally:
        _job_lock.release()
    # only after a successful publish does this (cycle, box) become current
    with _status_lock:
        _status["current_cycle"] = meta.cycle_id
        _status["current_box"] = str(meta.box)
        _status["fetched_at"] = meta.fetched_at
        _status["in_progress_cycle"] = None
        _status["in_progress_box"] = None
        _status["last_failed_cycle"] = None
        _status["last_failed_box"] = None
        _status["last_failed_at"] = None
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
        target_box = str(BoxKey(req.leftlon, req.rightlon, req.toplat, req.bottomlat))

        with _status_lock:
            current = (_status["current_cycle"], _status.get("current_box"))
            in_progress = (_status["in_progress_cycle"], _status.get("in_progress_box"))
            failed = (_status.get("last_failed_cycle"), _status.get("last_failed_box"))
            failed_at = _status.get("last_failed_at") or 0.0
        target = (newest, target_box)
        if target == current or target == in_progress:
            _mark("idle")
            return
        if STORE.load_meta(newest, BoxKey.parse(target_box)) is not None:
            _mark(
                "idle", current_cycle=newest, current_box=target_box,
                in_progress_cycle=None, in_progress_box=None, last_error=None,
            )
            return
        if target == failed:
            # a failed cycle is retried after a bounded backoff instead of
            # being abandoned for the life of the process.
            if time.time() - failed_at < _retry_backoff_s():
                _mark("idle")
                return

        # keep the last-good (cycle, box) authoritative until the target is
        # fully ingested (current identity is only advanced on success)
        _mark("downloading", in_progress_cycle=newest, in_progress_box=target_box)
        try:
            _run_cycle(config, newest, req)
            _mark(
                "idle", current_cycle=newest, current_box=target_box,
                in_progress_cycle=None, in_progress_box=None, last_error=None,
            )
        except gfs.GfsError as exc:
            # keep the last good dataset authoritative and remember the full
            # failed identity so another box in the same cycle is not backed off
            with _status_lock:
                _status["in_progress_cycle"] = None
                _status["in_progress_box"] = None
                _status["last_failed_cycle"] = newest
                _status["last_failed_box"] = target_box
                _status["last_failed_at"] = time.time()
            _mark("error", last_error=str(exc))
            LOG.warning("ingest of %s failed: %s", newest, exc)
    except Exception as exc:  # noqa: BLE001 — a scheduler bug must not kill the thread
        with _status_lock:
            _status["in_progress_cycle"] = None
            _status["in_progress_box"] = None
        _mark("error", last_error=f"internal: {exc}")
        LOG.exception("scheduler tick failed")


def _retry_backoff_s() -> float:
    return float(os.environ.get("WEATHER_RETRY_BACKOFF_S", "300"))


def _loop(config: GfsConfig) -> None:
    interval = _interval_s()
    while not _stop_evt.is_set():
        try:
            _tick(config)
        except Exception:  # noqa: BLE001
            LOG.exception("unexpected scheduler error")
        _stop_evt.wait(interval)
    global _running
    _running = False


def start() -> bool:
    """Start the background scheduler (idempotent). True if started now."""
    global _running, _thread
    if _running:
        return False
    if os.environ.get("WEATHER_SCHEDULER_ENABLED", "1") == "0":
        return False
    _running = True
    _stop_evt.clear()
    config = GfsConfig.from_env()
    interval = _interval_s()
    _thread = threading.Thread(target=_loop, args=(config,), name="weather-scheduler", daemon=True)
    _thread.start()
    LOG.info("weather scheduler started (interval %.0fs)", interval)
    return True


def stop(timeout: float = 5.0) -> None:
    """Stop the loop thread; returns once it has exited (or after timeout)."""
    _stop_evt.set()
    t = _thread
    if t is not None:
        t.join(timeout)


def job_available() -> bool:
    """True if no ingest job (scheduled or manual) is running right now."""
    return not _job_lock.locked()


def acquire_job(timeout: float = 0) -> bool:
    """Acquire the shared ingest job lock (manual ``POST /ingest`` path).

    ``timeout=0`` is a non-blocking attempt; a positive value waits at most
    that long.
    """
    return bool(_job_lock.acquire(timeout=timeout))


def release_job() -> None:
    if _job_lock.locked():
        _job_lock.release()


def status_payload() -> dict[str, Any]:
    """The readiness/status object the UI polls."""
    from . import service  # local import: service imports the scheduler lazily

    with _status_lock:
        s = dict(_status)
    with _box_lock:
        has_box = _active_box is not None
    active = active_box_key()
    # only datasets usable for the active route box are reported, so a
    # region switch inside one cycle cannot keep advertising the old box
    active_published = service.visible_metas(active)
    latest = active_published[0] if active_published else None
    current_box_usable = _box_usable_for(s.get("current_box"), active)
    fetched = (
        (s.get("fetched_at") if current_box_usable else None)
        or (latest.fetched_at if latest else None)
    )
    stale_block = staleness(time.time(), fetched) if fetched else None
    return {
        "enabled": os.environ.get("WEATHER_SCHEDULER_ENABLED", "1") != "0",
        "state": s["state"],
        "current_cycle": (
            (s["current_cycle"] or (latest.cycle_id if latest else None))
            if current_box_usable
            else (latest.cycle_id if latest else None)
        ),
        # DOCUMENTED SEMANTICS (follow-up #3 from PR #16 review):
        # ``current_box`` means "the box the UI should be requesting for the
        # ACTIVE route right now" — the active route's box when one is set
        # (``active_box_key()``), falling back to the last *completed*
        # ingest's box only when there is no active route (e.g. no saved
        # plan). It intentionally does NOT mean "the box of the dataset
        # actually served in this response" — that is reported separately,
        # per-offset, as each layer/route response's own ``box`` field (see
        # ``service.py``'s ``str(meta.box)``). A client that needs "what box
        # backs THIS payload" must read that per-response field, not status's
        # current_box.
        "current_box": str(active) if active is not None else s.get("current_box"),
        "in_progress_cycle": s["in_progress_cycle"],
        "in_progress_box": s.get("in_progress_box"),
        "latest_published": [m.cycle_id for m in active_published[:4]],
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
