from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from data_fetcher.sim.cpp.fmc_adapter_bundle.optimizer.adapters import (
    FmcAdapterRegistry,
)
from data_fetcher.sim.fmc_models import FmcTelemetrySnapshotData


DEFAULT_STALE_AFTER_S = 10.0
WINDOWS_NO_WINDOW = 0x08000000
_PMDG_777_TITLE_RE = re.compile(r"pmdg.*777|777.*pmdg", re.IGNORECASE)
_PMDG_CDU_HEADER_RE = re.compile(r"^=+\s*CDU\s+(\d+)\s*=+\s*$", re.IGNORECASE)
_PMDG_CDU_FOOTER = "======================================"


@dataclass(frozen=True)
class FmcBridgeSpec:
    adapter_key: str
    bridge_kind: str
    executable_env_var: str | None
    default_executable: Path | None


_PMDG_777_SPEC = FmcBridgeSpec(
    adapter_key="PMDG_777",
    bridge_kind="pmdg_text_blocks",
    executable_env_var="PMDG_777_CDU_LISTENER_EXE",
    default_executable=(
        Path(__file__).resolve().parent
        / "cpp"
        / "build"
        / "Release"
        / "pmdg777_cdu_listener.exe"
    ),
)


class _PmdgTextBlockParser:
    def __init__(self) -> None:
        self._cdu_index: int | None = None
        self._lines: list[str] = []

    def feed_line(self, raw_line: str) -> list[tuple[int, list[str]]]:
        events: list[tuple[int, list[str]]] = []
        line = raw_line.rstrip("\r\n")
        header = _PMDG_CDU_HEADER_RE.match(line.strip())
        if header:
            self._cdu_index = int(header.group(1))
            self._lines = []
            return events

        if self._cdu_index is None:
            return events

        if line.strip() == _PMDG_CDU_FOOTER:
            if self._lines:
                events.append((self._cdu_index, self._lines[:]))
            self._cdu_index = None
            self._lines = []
            return events

        self._lines.append(line)
        return events


class FmcBridgeManager:
    """
    Aircraft-specific FMC bridge supervisor.

    It starts a matching bridge only for supported aircraft and keeps the latest
    parsed snapshot in memory for use by telemetry and optimization layers.
    """

    def __init__(self) -> None:
        self._registry = FmcAdapterRegistry()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._latest_snapshot_by_cdu: dict[int, FmcTelemetrySnapshotData] = {}
        self._last_update_monotonic: float | None = None
        self._status = "inactive"
        self._last_error: str | None = None
        self._active_spec: FmcBridgeSpec | None = None
        self._active_aircraft_title: str | None = None

    def update_aircraft_context(self, aircraft_title: str | None) -> None:
        normalized_title = (aircraft_title or "").strip()
        target_spec = _resolve_fmc_bridge_spec(normalized_title)
        executable = _resolve_bridge_executable(target_spec)

        with self._lock:
            current_executable = _resolve_bridge_executable(self._active_spec)
            process_running = (
                self._proc is not None
                and self._proc.poll() is None
            )
            if (
                target_spec == self._active_spec
                and normalized_title == self._active_aircraft_title
                and current_executable == executable
            ):
                if target_spec is None:
                    return
                if process_running:
                    return
                if executable is None or not executable.exists():
                    return

            self._stop_locked(clear_snapshots=True)
            self._active_spec = target_spec
            self._active_aircraft_title = normalized_title or None

            if target_spec is None:
                self._status = "inactive"
                self._last_error = None
                return

            if executable is None or not executable.exists():
                self._status = "unavailable"
                self._last_error = (
                    f"FMC adapter bridge executable not found for {target_spec.adapter_key}."
                )
                return

            self._start_locked(target_spec, executable)

    def get_latest_snapshot(
        self,
        *,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> tuple[FmcTelemetrySnapshotData | None, str, str | None]:
        with self._lock:
            snapshot = self._preferred_snapshot_locked(stale_after_s=stale_after_s)
            return snapshot, self._status, self._last_error

    def close(self) -> None:
        with self._lock:
            self._stop_locked(clear_snapshots=True)
            self._status = "inactive"
            self._last_error = None
            self._active_spec = None
            self._active_aircraft_title = None

    def _start_locked(self, spec: FmcBridgeSpec, executable: Path) -> None:
        creationflags = WINDOWS_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                [str(executable)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._status = "offline"
            self._last_error = f"Could not start FMC adapter bridge: {exc}"
            return

        self._stop_event = threading.Event()
        self._proc = proc
        self._status = "starting"
        self._last_error = None
        self._latest_snapshot_by_cdu = {}
        self._last_update_monotonic = None
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            args=(spec, proc, self._stop_event),
            daemon=True,
            name=f"fmc-bridge-{spec.adapter_key.lower()}",
        )
        self._reader_thread.start()

    def _stop_locked(self, *, clear_snapshots: bool) -> None:
        proc = self._proc
        reader_thread = self._reader_thread
        stop_event = self._stop_event
        self._proc = None
        self._reader_thread = None
        self._stop_event = threading.Event()

        if stop_event is not None:
            stop_event.set()

        if proc is not None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        if clear_snapshots:
            self._latest_snapshot_by_cdu = {}
            self._last_update_monotonic = None

    def _reader_loop(
        self,
        spec: FmcBridgeSpec,
        proc: subprocess.Popen[str],
        stop_event: threading.Event,
    ) -> None:
        parser = _build_stdout_parser(spec)
        stdout = proc.stdout
        if stdout is None:
            with self._lock:
                self._status = "offline"
                self._last_error = "FMC adapter bridge did not expose stdout."
            return

        try:
            for raw_line in stdout:
                if stop_event.is_set():
                    break

                for cdu_index, lines in parser.feed_line(raw_line):
                    parsed = self._registry.parse_first(lines, cdu_index=cdu_index)
                    if parsed is None:
                        continue

                    snapshot = FmcTelemetrySnapshotData.model_validate(
                        {
                            **parsed.to_dict(),
                            "adapterKey": spec.adapter_key,
                        }
                    )
                    with self._lock:
                        self._latest_snapshot_by_cdu[cdu_index] = snapshot
                        self._last_update_monotonic = time.monotonic()
                        self._status = "connected"
                        self._last_error = None
        except Exception as exc:
            with self._lock:
                self._status = "offline"
                self._last_error = f"FMC adapter bridge reader failed: {exc}"
            return
        finally:
            try:
                return_code = proc.poll()
            except Exception:
                return_code = None

        with self._lock:
            if stop_event.is_set():
                if self._status != "inactive":
                    self._status = "stopped"
                return

            self._status = "offline"
            if self._last_error is None:
                self._last_error = (
                    f"FMC adapter bridge exited with code {return_code}."
                )

    def _preferred_snapshot_locked(
        self,
        *,
        stale_after_s: float,
    ) -> FmcTelemetrySnapshotData | None:
        if not self._latest_snapshot_by_cdu:
            return None

        if self._last_update_monotonic is None:
            return None

        if (time.monotonic() - self._last_update_monotonic) > stale_after_s:
            return None

        return (
            self._latest_snapshot_by_cdu.get(0)
            or self._latest_snapshot_by_cdu.get(1)
            or next(iter(self._latest_snapshot_by_cdu.values()))
        )


def _resolve_fmc_bridge_spec(aircraft_title: str | None) -> FmcBridgeSpec | None:
    if aircraft_title and _PMDG_777_TITLE_RE.search(aircraft_title):
        return _PMDG_777_SPEC
    return None


def _resolve_bridge_executable(spec: FmcBridgeSpec | None) -> Path | None:
    if spec is None:
        return None

    env_value = (
        os.environ.get(spec.executable_env_var)
        if spec.executable_env_var is not None
        else None
    )
    if env_value:
        return Path(env_value).expanduser()

    return spec.default_executable


def _build_stdout_parser(spec: FmcBridgeSpec) -> _PmdgTextBlockParser:
    if spec.bridge_kind == "pmdg_text_blocks":
        return _PmdgTextBlockParser()
    raise ValueError(f"Unsupported FMC bridge kind: {spec.bridge_kind}")


_fmc_bridge_manager = FmcBridgeManager()


def get_fmc_bridge_manager() -> FmcBridgeManager:
    return _fmc_bridge_manager
