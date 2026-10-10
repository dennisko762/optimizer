"""Focused edge-case coverage for the weather cache and service layers."""
from __future__ import annotations

import json
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

from crew_platform.weather import sampler, scheduler, service
from crew_platform.weather.cycle import GfsConfig
from crew_platform.weather.store import BoxKey, CycleMeta, CycleStore, cache_root, cycle_epoch


BOX = BoxKey(25.0, 75.0, 65.0, 10.0)
CYCLE = "20261005_18"


def _meta(*, offsets=(0,), fetched_at=None):
    return CycleMeta(
        cycle_id=CYCLE,
        run_date="20261005",
        run_hour="18",
        box=BOX,
        offsets=list(offsets),
        fields=["u", "v", "t"],
        n_lat=2,
        n_lon=2,
        fetched_at=time.time() if fetched_at is None else fetched_at,
    )


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    return CycleStore()


def test_cache_root_uses_efb_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("WEATHER_CACHE_DIR", raising=False)
    monkeypatch.setenv("EFB_DATA_DIR", str(tmp_path))
    assert cache_root() == tmp_path / "crew-weather"


def test_store_rejects_bad_identifiers_and_metadata(store, tmp_path):
    with pytest.raises(ValueError, match="bad cycle id"):
        store.write_raw("not-a-cycle", BOX, 0, b"x")
    with pytest.raises(ValueError, match="bad box key"):
        BoxKey.parse("1_2_3")
    with pytest.raises(ValueError, match="bad cycle id"):
        cycle_epoch("bad")

    malformed = tmp_path / CYCLE / str(BOX) / "meta.json"
    malformed.parent.mkdir(parents=True)
    malformed.write_text("{broken", encoding="utf-8")
    assert store.load_meta(CYCLE, BOX) is None
    assert store.list_published() == []

    malformed.write_text(json.dumps({"cycle_id": CYCLE}), encoding="utf-8")
    assert store.load_meta(CYCLE, BOX) is None
    assert store.list_published() == []


def test_store_atomic_writes_clean_up_failed_temp_files(store, monkeypatch, tmp_path):
    def fail_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr("crew_platform.weather.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.write_raw(CYCLE, BOX, 0, b"data")
    assert list(tmp_path.rglob("*.tmp")) == []


def test_store_publish_cleans_up_failed_metadata_swap(store, monkeypatch, tmp_path):
    store.write_raw(CYCLE, BOX, 0, b"data")

    def fail_replace(_src, _dst):
        raise OSError("replace failed")

    monkeypatch.setattr("crew_platform.weather.store.os.replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        store.publish(_meta())
    assert list(tmp_path.rglob("*.tmp")) == []


def test_store_dataset_missing_failure_success_and_lru(store, monkeypatch):
    assert store.dataset(CYCLE, BOX, 0) is None
    path = store.write_raw(CYCLE, BOX, 0, b"fake-grib")

    monkeypatch.setitem(
        sys.modules,
        "xarray",
        SimpleNamespace(open_dataset=lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad grib"))),
    )
    assert store.dataset(CYCLE, BOX, 0) is None

    class FakeDataset:
        def __init__(self, name):
            self.name = name
            self.loaded = False
            self.closed = False

        def load(self):
            self.loaded = True

        def close(self):
            self.closed = True

    opened = []

    def open_dataset(name, **_kwargs):
        ds = FakeDataset(name)
        opened.append(ds)
        return ds

    monkeypatch.setitem(sys.modules, "xarray", SimpleNamespace(open_dataset=open_dataset))
    first = store.dataset(CYCLE, BOX, 0)
    assert first is not None and first.loaded and first.closed
    assert store.dataset(CYCLE, BOX, 0) is first

    for offset in range(1, 6):
        path.with_name(f"f{offset:03d}.grb2").write_bytes(b"fake")
        assert store.dataset(CYCLE, BOX, offset) is not None
    assert len(store._ds_cache) == 4
    assert f"{CYCLE}|{BOX}|0" not in store._ds_cache


def test_store_legacy_file_and_prune_disabled(store, monkeypatch, tmp_path):
    legacy = tmp_path / CYCLE / "f003.grb2"
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"legacy")

    class FakeDataset:
        def load(self):
            return None

        def close(self):
            return None

    monkeypatch.setitem(sys.modules, "xarray", SimpleNamespace(open_dataset=lambda name, **kwargs: FakeDataset()))
    assert store.dataset(CYCLE, BOX, 3) is not None
    monkeypatch.setenv("WEATHER_CACHE_MAX_MB", "0")
    assert store.prune() == []


def test_service_bundle_loading_region_legacy_and_corrupt(monkeypatch, tmp_path):
    monkeypatch.setenv("WEATHER_CACHE_DIR", str(tmp_path))
    region = tmp_path / CYCLE / str(BOX)
    region.mkdir(parents=True)
    np.savez(region / "f000.npz", lat=np.array([1.0, 2.0]))
    assert service._load_bundle(CYCLE, BOX, 0)["lat"].tolist() == [1.0, 2.0]

    (region / "f000.npz").write_bytes(b"broken")
    assert service._load_bundle(CYCLE, BOX, 0) is None
    legacy = tmp_path / CYCLE / "f001.npz"
    np.savez(legacy, lon=np.array([3.0, 4.0]))
    assert service._load_bundle(CYCLE, BOX, 1)["lon"].tolist() == [3.0, 4.0]
    legacy.write_bytes(b"broken")
    assert service._load_bundle(CYCLE, BOX, 1) is None
    assert service._load_bundle(CYCLE, BOX, 2) is None


def test_service_helpers_cover_unavailable_values(monkeypatch):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [])
    assert service._box_key(CYCLE) is None
    assert service._nearest_offset([], 4) is None
    assert service._nearest_offset([0, 6], 4) == 6
    assert service._jet_grid({"u_300": np.ones((2, 2))}, (300,), 300) is None
    assert service._valid_at_iso("bad", 0) is None
    assert service._hazard_grid_at_fl({}, "cape", (0,), 300) is None
    assert service._hazard_grid_at_fl({}, "ti", (300, 200), 300) is None


def test_hazard_layer_errors_and_unavailable_paths(monkeypatch):
    with pytest.raises(KeyError, match="unknown hazard"):
        service.hazard_layer(CYCLE, "missing", 300, 0)

    monkeypatch.setattr(service.STORE, "list_published", lambda: [])
    with pytest.raises(LookupError, match="no published"):
        service.hazard_layer(CYCLE, "cape", 300, 0)

    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0, 6))])
    missing_hour = service.hazard_layer(CYCLE, "cape", 300, 12)
    assert missing_hour["offset"] is None
    assert "T+12h" in missing_hour["unavailable"]

    monkeypatch.setattr(service, "_load_bundle", lambda *_args: None)
    with pytest.raises(LookupError, match="bundle"):
        service.hazard_layer(CYCLE, "cape", 300, 0)

    monkeypatch.setattr(service, "_load_bundle", lambda *_args: {"lat": np.arange(2), "lon": np.arange(2)})
    unavailable = service.hazard_layer(CYCLE, "turbulence", 450, 0)
    assert unavailable["features"] == []
    assert unavailable["valid_at_utc"] == "2026-10-05T18:00:00Z"


def test_route_samples_rejects_missing_cycle_and_bad_points(monkeypatch):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [])
    with pytest.raises(LookupError, match="no published"):
        service.route_samples({"waypoints": []}, CYCLE, 300, 0)

    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: None)
    view = {
        "std_utc": "not-a-date",
        "waypoints": [
            "not-a-row",
            {"ident": "BAD", "lat": "x", "lon": 20, "ete": True},
            {"ident": "NAN", "lat": float("nan"), "lon": 20, "ete": "bad"},
            {"ident": "OK", "lat": 50, "lon": 30, "ete": 15},
        ],
    }
    result = service.route_samples(view, CYCLE, 300, 0)
    assert [point["ident"] for point in result["points"]] == ["OK"]
    assert result["points"][0]["unavailable"] == ["bundle missing on disk"]


def test_route_samples_returns_interpolated_weather_and_provenance(monkeypatch):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    grid = np.full((2, 2), 10.0)
    bundle = {
        "lat": np.array([0.0, 1.0]),
        "lon": np.array([0.0, 1.0]),
        "u_500": grid,
        "u_700": grid,
        "v_500": np.zeros((2, 2)),
        "v_700": np.zeros((2, 2)),
        "t_500": np.full((2, 2), 273.15),
        "t_700": np.full((2, 2), 273.15),
        "ti_300": np.full((2, 2), 6.0),
        "ice_500": np.full((2, 2), 0.2),
        "ice_700": np.full((2, 2), 0.2),
    }
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: bundle)

    result = service.route_samples(
        {
            "std_utc": "2026-10-05 18:00",
            "waypoints": [
                {"ident": "A", "lat": 0.25, "lon": 0.25, "ete": "0:00"},
                {"ident": "B", "lat": 0.75, "lon": 0.75, "ete": 0},
            ],
        },
        CYCLE,
        100,
        0,
    )

    first, second = result["points"]
    assert result["served_offsets"] == [0]
    assert first["wind_speed_kt"] == 19.4
    assert first["tailwind_kt"] is not None
    assert first["oat_c"] == 0.0
    assert first["turbulence_tier"] == 1
    assert first["icing_tier"] == 1
    assert first["valid_at_utc"] == "2026-10-05T18:00:00Z"
    assert second["tailwind_kt"] is None
    assert first["unavailable"] == second["unavailable"] == []


def test_store_listing_usage_and_prune_boundaries(store, monkeypatch, tmp_path):
    (tmp_path / "README.txt").write_text("not a cycle", encoding="utf-8")
    (tmp_path / "invalid-cycle").mkdir()
    assert store.list_published() == []

    good = tmp_path / "data.bin"
    skipped = tmp_path / "vanished.bin"
    good.write_bytes(b"1234")
    skipped.write_bytes(b"123456")

    from crew_platform.weather import store as store_module

    real_getsize = store_module.os.path.getsize

    def flaky_getsize(path):
        if path == str(skipped):
            raise OSError("file disappeared")
        return real_getsize(path)

    monkeypatch.setattr(store_module.os.path, "getsize", flaky_getsize)
    assert store.disk_usage_bytes() == len("not a cycle") + 4

    skipped.unlink()
    good.unlink()
    for cycle in ("20261001_00", "20261002_00", "20261003_00"):
        cycle_dir = tmp_path / cycle
        cycle_dir.mkdir()
        (cycle_dir / "payload").write_bytes(b"x" * 400)
    monkeypatch.setenv("WEATHER_CACHE_MAX_MB", str(900 / (1024 * 1024)))
    assert store.prune() == ["20261001_00"]
    assert (tmp_path / "20261002_00").is_dir()
    assert (tmp_path / "20261003_00").is_dir()


def test_service_box_key_returns_first_matching_published_box(monkeypatch):
    other = _meta()
    other.cycle_id = "20261005_12"
    monkeypatch.setattr(service.STORE, "list_published", lambda: [other, _meta()])
    assert service._box_key(CYCLE) == BOX


@pytest.mark.parametrize(
    "std_utc",
    [
        "2026-10-05 18:00",
        "2026-10-05 18:00:00",
        "2026-10-05T18:00:00",
        "2026-10-05T18:00",
        "2026-10-05T18:00:00Z",
        "2026-10-05T18:00:00z",
    ],
)
def test_route_samples_accepts_supported_std_formats(monkeypatch, std_utc):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: None)
    result = service.route_samples(
        {
            "std_utc": std_utc,
            "waypoints": [{"ident": "FIX", "lat": 0, "lon": 0, "ete": 0}],
        },
        CYCLE,
        300,
        0,
    )
    assert result["points"][0]["offset_served"] == 0


def test_route_samples_marks_missing_display_hour_unavailable(monkeypatch):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    result = service.route_samples(
        {"waypoints": [{"ident": "FIX", "lat": 0, "lon": 0, "ete": 15}]},
        CYCLE,
        300,
        6,
    )
    point = result["points"][0]
    assert point["offset"] is None
    assert point["offset_served"] is None
    assert point["valid_at_utc"] is None
    assert point["unavailable"] == [
        f"no weather at T+6h for cycle {CYCLE} (published T+0..T+0h)"
    ]


def test_route_samples_falls_back_for_invalid_std_values(monkeypatch):
    class UnprintableTime:
        def __str__(self):
            raise RuntimeError("cannot stringify")

    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: None)
    waypoint = {"ident": "FIX", "lat": 0, "lon": 0, "ete": 15}

    for std_utc in ("not-a-date", UnprintableTime()):
        result = service.route_samples(
            {"std_utc": std_utc, "waypoints": [waypoint]},
            CYCLE,
            300,
            0,
        )
        assert result["points"][0]["offset_served"] == 0
        assert result["points"][0]["unavailable"] == ["bundle missing on disk"]


def test_route_samples_handles_invalid_next_fix_and_sparse_bundle(monkeypatch):
    monkeypatch.setattr(service.STORE, "list_published", lambda: [_meta(offsets=(0,))])
    grid = np.full((2, 2), 10.0)
    wind_bundle = {
        "lat": np.array([0.0, 1.0]),
        "lon": np.array([0.0, 1.0]),
        "u_500": grid,
        "u_700": grid,
        "v_500": np.zeros((2, 2)),
        "v_700": np.zeros((2, 2)),
    }
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: wind_bundle)
    bad_next = service.route_samples(
        {
            "std_utc": "2026-10-05 18:00",
            "waypoints": [
                {"ident": "A", "lat": 0.25, "lon": 0.25, "ete": 0},
                {"ident": "BAD", "lat": "invalid", "lon": 0.75, "ete": 0},
            ],
        },
        CYCLE,
        100,
        0,
    )
    assert bad_next["points"][0]["tailwind_kt"] is None

    sparse = {"lat": np.array([0.0, 1.0]), "lon": np.array([0.0, 1.0])}
    monkeypatch.setattr(service, "_load_bundle", lambda *_args: sparse)
    missing = service.route_samples(
        {
            "std_utc": "2026-10-05 18:00",
            "waypoints": [{"ident": "A", "lat": 0.25, "lon": 0.25, "ete": 0}],
        },
        CYCLE,
        100,
        0,
    )
    assert missing["points"][0]["unavailable"] == [
        "wind",
        "oat",
        "turbulence",
        "icing",
    ]


def test_sampler_degenerate_and_invalid_values():
    assert sampler.bilinear([], [], [], 0, 0) is None
    assert sampler.bilinear([[1, 2], [3, 4]], [0, 1], [5, 5], 0.5, 5) is None
    assert sampler.bilinear([[1, None], [3, 4]], [0, 1], [0, 1], 0.5, 0.5) is None
    assert sampler.bilinear([[object(), 2], [3, 4]], [0, 1], [0, 1], 0.5, 0.5) is None
    assert sampler.sample_at_fl({}, [], [0, 1], [0, 1], 0.5, 0.5, 300) is None
    assert sampler.split_around_antimeridian([]) == []


def test_sampler_direct_level_and_missing_bracket():
    grid = [[1.0, 1.0], [1.0, 1.0]]
    assert sampler.sample_at_fl({300.0: grid}, [300.0], [0, 1], [0, 1], 0.5, 0.5, 340) == 1.0
    assert sampler.sample_at_fl({500.0: grid}, [500.0], [0, 1], [0, 1], 0.5, 0.5, 450) is None


def test_scheduler_discovery_empty_current_and_internal_error(monkeypatch):
    scheduler._active_box = (25.0, 75.0, 65.0, 10.0)
    with scheduler._status_lock:
        scheduler._status.update(current_cycle=None, in_progress_cycle=None, last_error=None)

    monkeypatch.setattr(scheduler.gfs, "list_cycles", lambda _config: [])
    scheduler._tick(GfsConfig.from_env())
    assert scheduler._status["last_error"] == "no GFS cycle directories found"

    monkeypatch.setattr(scheduler.gfs, "list_cycles", lambda _config: [{"date": "20261005"}])
    monkeypatch.setattr(scheduler.gfs, "list_cycle_hours", lambda *_args: [])
    scheduler._tick(GfsConfig.from_env())
    assert scheduler._status["last_error"] == "no run hours found"

    monkeypatch.setattr(scheduler.gfs, "list_cycle_hours", lambda *_args: ["18"])
    with scheduler._status_lock:
        scheduler._status["current_cycle"] = CYCLE
    scheduler._tick(GfsConfig.from_env())
    assert scheduler._status["state"] == "idle"

    monkeypatch.setattr(scheduler.gfs, "list_cycles", lambda _config: (_ for _ in ()).throw(RuntimeError("boom")))
    scheduler._tick(GfsConfig.from_env())
    assert scheduler._status["last_error"] == "internal: boom"


def test_scheduler_disabled_release_and_event_timeout(monkeypatch):
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "0")
    scheduler._running = False
    assert scheduler.start() is False
    scheduler.release_job()
    with scheduler._events_cond:
        scheduler._events.clear()
    assert scheduler.wait_for_event(0.001, time.time()) is None
