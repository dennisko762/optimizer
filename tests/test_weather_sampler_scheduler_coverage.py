"""Focused coverage for weather sampling and scheduler loop behavior."""
from __future__ import annotations

import logging

import numpy as np
import pytest

from crew_platform.weather import sampler, scheduler
from crew_platform.weather.cycle import GfsConfig


def test_bilinear_periodic_antimeridian_wrap():
    cols = 72
    step = 5.0
    lons = [(175.0 + i * step + 180.0) % 360.0 - 180.0 for i in range(cols)]
    grid = [[float(column) for column in range(cols)] for _ in range(2)]

    assert sampler.bilinear(grid, [0.0, 10.0], lons, 5.0, 175.0) == 0.0
    assert sampler.bilinear(grid, [0.0, 10.0], lons, 5.0, -177.5) == pytest.approx(1.5)
    assert sampler.bilinear(grid, [0.0, 10.0], lons, 5.0, -175.0) == pytest.approx(2.0)
    assert sampler.bilinear(grid, [0.0, 10.0], lons, 5.0, 179.9) == pytest.approx(0.98)


def test_bilinear_collapses_array_cells_to_first_value():
    cells = [
        [np.array([3.0, 99.0]), np.array([5.0, 99.0])],
        [np.array([7.0, 99.0]), np.array([9.0, 99.0])],
    ]

    assert sampler.bilinear(cells, [0.0, 1.0], [0.0, 1.0], 0.5, 0.5) == pytest.approx(6.0)


def test_pressure_bracket_outside_grid_returns_none():
    grids = {
        400.0: [[10.0, 10.0], [10.0, 10.0]],
        300.0: [[20.0, 20.0], [20.0, 20.0]],
    }

    assert sampler.sample_at_fl(
        grids, [300.0, 400.0], [0.0, 10.0], [0.0, 10.0], 5.0, 5.0, 300
    ) is not None
    assert sampler.sample_at_fl(
        grids, [300.0, 400.0], [0.0, 10.0], [0.0, 10.0], 20.0, 5.0, 300
    ) is None


def test_scheduler_loop_logs_tick_exception_and_stops(monkeypatch):
    calls = {"tick": 0, "wait": 0}

    def fake_tick(_config):
        calls["tick"] += 1
        if calls["tick"] == 1:
            raise RuntimeError("tick exploded")

    class FakeStopEvent:
        def is_set(self):
            return calls["tick"] >= 2

        def wait(self, interval):
            calls["wait"] += 1
            assert interval == pytest.approx(180.0)
            return False

    monkeypatch.setattr(scheduler, "_tick", fake_tick)
    monkeypatch.setattr(scheduler, "_stop_evt", FakeStopEvent())
    monkeypatch.setenv("WEATHER_CHECK_INTERVAL_S", "180")
    scheduler._running = True

    log = scheduler.LOG
    captured: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = captured.append
    previous = (log.disabled, log.level, log.propagate)
    log.addHandler(handler)
    log.setLevel(logging.ERROR)
    log.propagate = False
    log.disabled = False
    try:
        scheduler._loop(GfsConfig.from_env())
    finally:
        log.disabled, log.level, log.propagate = previous
        log.removeHandler(handler)

    assert calls == {"tick": 2, "wait": 2}
    assert scheduler._running is False
    assert any(
        record.getMessage() == "unexpected scheduler error"
        and isinstance(record.exc_info[1], RuntimeError)
        for record in captured
    )
