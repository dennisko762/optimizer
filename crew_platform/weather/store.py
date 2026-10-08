"""Disk cache + in-memory LRU for ingested GFS subsets.

Design (task: "cache cycle/status/validity and atomically swap completed
cycles"; "never commit NOAA blobs"):

* Raw subset GRIB2 files live under an env-selected cache dir (default
  ``.cache/crew-weather`` in the repo root; gitignored). Never committed.
* A cycle is only *published* (its ``meta.json`` swapped in via
  ``os.replace``) once every required forecast-hour file is on disk — a
  half-finished cycle is invisible to readers (atomic swap).
* Parsed :mod:`xarray` datasets are opened lazily and held in a small
  process-wide LRU so repeated map loads are sub-second without keeping the
  whole 37-step horizon in RAM.

The cache dir resolution follows the repo's env-only convention
(``WEATHER_CACHE_DIR`` > ``EFB_DATA_DIR`` > ``<repo>/.cache/crew-weather``).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from collections import OrderedDict

#: how long a dataset stays hot in the LRU (offsets per (cycle,box)).
_MAX_LRU_DATASETS = 4
_STALE_AFTER_H = 7.0  # a GFS cycle older than the next 6-hour run + 1 h margin


def cache_root() -> Path:
    """Resolve the weather cache directory (env-only)."""
    env_dir = os.environ.get("WEATHER_CACHE_DIR", "").strip()
    if not env_dir:
        efb = os.environ.get("EFB_DATA_DIR", "").strip()
        if efb:
            env_dir = str(Path(efb) / "crew-weather")
    if env_dir:
        root = Path(env_dir)
    else:
        root = Path(__file__).resolve().parents[2] / ".cache" / "crew-weather"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cycle_dir(cycle_id: str) -> Path:
    if not re.match(r"^[0-9]{8}_[0-9]{2}$", cycle_id or ""):
        raise ValueError(f"bad cycle id: {cycle_id!r}")
    return cache_root() / cycle_id


@dataclass
class BoxKey:
    """Region box that a subset was fetched for (degrees)."""

    leftlon: float
    rightlon: float
    toplat: float
    bottomlat: float

    def __str__(self) -> str:
        # underscore separator: '/' is a path separator on Windows and would
        # turn the meta filename into a nested (non-existent) directory.
        return (
            f"{self.leftlon:.2f}_{self.rightlon:.2f}_"
            f"{self.bottomlat:.2f}_{self.toplat:.2f}"
        )


@dataclass
class CycleMeta:
    """Published metadata for one (cycle, box) dataset."""

    cycle_id: str          # YYYYMMDD_HH (date + run)
    run_date: str          # YYYYMMDD
    run_hour: str          # HH
    box: BoxKey
    offsets: list[int]     # forecast-hour offsets present, ascending
    fields: list[str]      # available field stems (u,v,t,r,w,cape,gh)
    n_lat: int
    n_lon: int
    fetched_at: float      # unix time when the cycle completed
    source: str = "NOAA NOMADS GFS 0.25 deg (g2sub)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "run_date": self.run_date,
            "run_hour": self.run_hour,
            "box": str(self.box),
            "offsets": self.offsets,
            "fields": self.fields,
            "n_lat": self.n_lat,
            "n_lon": self.n_lon,
            "fetched_at": self.fetched_at,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CycleMeta":
        box = str(d["box"]).split("_")
        return cls(
            cycle_id=d["cycle_id"],
            run_date=d["run_date"],
            run_hour=d["run_hour"],
            box=BoxKey(*[float(x) for x in box]),
            offsets=list(d["offsets"]),
            fields=list(d["fields"]),
            n_lat=int(d["n_lat"]),
            n_lon=int(d["n_lon"]),
            fetched_at=float(d["fetched_at"]),
            source=d.get("source", ""),
        )


def _meta_path(cycle_id: str, box: BoxKey) -> Path:
    return _cycle_dir(cycle_id) / f"{box}.meta.json"


def _file_path(cycle_id: str, offset: int) -> Path:
    return _cycle_dir(cycle_id) / f"f{offset:03d}.grb2"


class CycleStore:
    """Atomic publish + lazy read of completed (cycle, box) datasets."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ds_cache: "OrderedDict[str, Any]" = {}

    # -- publish -----------------------------------------------------------

    def write_raw(self, cycle_id: str, offset: int, data: bytes) -> Path:
        """Stage one forecast-hour subset on disk (not yet visible)."""
        path = _file_path(cycle_id, offset)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f"f{offset:03d}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return path

    def publish(self, meta: CycleMeta) -> None:
        """Atomically make a completed cycle visible.

        Refuses to publish while any required file is missing (a completed
        cycle must have every declared offset on disk).
        """
        for off in meta.offsets:
            p = _file_path(meta.cycle_id, off)
            if not p.is_file() or p.stat().st_size == 0:
                raise ValueError(f"offset f{off:03d} missing for {meta.cycle_id}")
        with self._lock:
            path = _meta_path(meta.cycle_id, meta.box)
            fd, tmp = tempfile.mkstemp(
                dir=str(path.parent), prefix=str(meta.box) + ".", suffix=".meta.tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(meta.to_dict(), fh, indent=1)
                os.replace(tmp, path)
            except BaseException:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise

    # -- read --------------------------------------------------------------

    def load_meta(self, cycle_id: str, box: BoxKey) -> Optional[CycleMeta]:
        path = _meta_path(cycle_id, box)
        if not path.is_file():
            return None
        try:
            return CycleMeta.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError):
            return None

    def list_published(self) -> list[CycleMeta]:
        out: list[CycleMeta] = []
        root = cache_root()
        if not root.is_dir():
            return out
        for cyc in sorted(root.iterdir(), reverse=True):
            if not cyc.is_dir() or not re.match(r"^\d{8}_\d{2}$", cyc.name):
                continue
            for m in cyc.glob("*.meta.json"):
                try:
                    out.append(CycleMeta.from_dict(json.loads(m.read_text(encoding="utf-8"))))
                except (OSError, ValueError, KeyError, json.JSONDecodeError):
                    continue
        out.sort(key=lambda c: (c.run_date, c.run_hour, c.box.leftlon), reverse=True)
        return out

    def dataset(self, cycle_id: str, box: BoxKey, offset: int) -> Optional[Any]:
        """Parsed xarray Dataset for one forecast hour (LRU-cached).

        Returns None when the (cycle, box) is not published or the offset is
        absent. The dataset keeps the cfgrib ``time``/``valid_time`` coords;
        variable names are ``u v t r w cape gh`` where present.
        """
        key = f"{cycle_id}|{box}|{offset}"
        with self._lock:
            if key in self._ds_cache:
                self._ds_cache.move_to_end(key)
                return self._ds_cache[key]
        path = _file_path(cycle_id, offset)
        if not path.is_file():
            return None
        try:
            import xarray as xr

            ds = xr.open_dataset(str(path), engine="cfgrib")
            # force-load so the on-disk file can be rotated without locks
            ds.load()
            ds.close()
        except Exception:
            return None
        with self._lock:
            self._ds_cache[key] = ds
            while len(self._ds_cache) > _MAX_LRU_DATASETS:
                self._ds_cache.popitem(last=False)
        return ds


#: process-wide store instance (shared by routes + scheduler)
STORE = CycleStore()


def staleness(now: float, fetched_at: float) -> dict[str, Any]:
    """Staleness flags for a published cycle (6-hour cadence)."""
    age_h = (now - fetched_at) / 3600.0
    return {
        "age_hours": round(age_h, 2),
        "stale": age_h > _STALE_AFTER_H,
        "cadence": "6-hour model cycles, hourly forecast steps",
    }
