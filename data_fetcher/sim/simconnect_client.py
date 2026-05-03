from __future__ import annotations

import asyncio
from typing import Any

from SimConnect import SimConnect, AircraftRequests

from data_fetcher.sim.sim_client import SimClient, SimClientError
from data_fetcher.sim.sim_models import LiveSimState, RawSimState
from data_fetcher.sim.sim_normalizer import normalize_raw_sim_state


class SimConnectClientError(SimClientError):
    pass


class SimConnectClient(SimClient):
    def __init__(self, cache_ms: int = 200):
        self.cache_ms = cache_ms
        self._simconnect: SimConnect | None = None
        self._aircraft_requests: AircraftRequests | None = None

    def connect(self) -> None:
        if self._simconnect is not None and self._aircraft_requests is not None:
            return

        try:
            self._simconnect = SimConnect()
            self._aircraft_requests = AircraftRequests(
                self._simconnect,
                _time=self.cache_ms,
            )
        except Exception as exc:
            raise SimConnectClientError(
                "Could not connect to MSFS via SimConnect. "
                "Make sure MSFS is running and a flight is loaded."
            ) from exc

    def close(self) -> None:
        if self._simconnect is not None:
            try:
                self._simconnect.exit()
            except Exception:
                pass

        self._simconnect = None
        self._aircraft_requests = None

    async def get_live_state(self) -> LiveSimState:
        return await asyncio.to_thread(self._get_live_state_sync)

    def _get_live_state_sync(self) -> LiveSimState:
        raw = self.get_raw_state_sync()
        return normalize_raw_sim_state(raw)

    def get_raw_state_sync(self) -> RawSimState:
        self.connect()

        if self._aircraft_requests is None:
            raise SimConnectClientError("SimConnect AircraftRequests not initialized.")

        aq = self._aircraft_requests

        return RawSimState(
            aircraft_title=self._safe_get(aq, "TITLE"),

            indicated_altitude_ft=self._safe_get_float(aq, "INDICATED_ALTITUDE"),
            true_altitude_ft=self._safe_get_float(aq, "PLANE_ALTITUDE"),
            pressure_altitude_m=self._safe_get_float(aq, "PRESSURE_ALTITUDE"),
            latitude=self._safe_get_float(aq, "PLANE_LATITUDE"),
            longitude=self._safe_get_float(aq, "PLANE_LONGITUDE"),

            mach=self._safe_get_float(aq, "AIRSPEED_MACH"),
            true_airspeed_kt=self._safe_get_float(aq, "AIRSPEED_TRUE"),
            ground_speed_kt=self._safe_get_float(aq, "GROUND_VELOCITY"),
            vertical_speed_fps=self._safe_get_float(aq, "VERTICAL_SPEED"),

            gross_weight_lb=self._safe_get_float(aq, "TOTAL_WEIGHT"),
            fuel_remaining_lb=self._safe_get_float(aq, "FUEL_TOTAL_QUANTITY_WEIGHT"),
            fuel_remaining_lb_ex1=self._safe_get_float(aq, "FUEL_TOTAL_QUANTITY_WEIGHT_EX1"),

            wind_velocity_kt=self._safe_get_float(aq, "AMBIENT_WIND_VELOCITY"),
            wind_direction_deg=self._safe_get_float(aq, "AMBIENT_WIND_DIRECTION"),
            ambient_temperature_c=self._safe_get_float(aq, "AMBIENT_TEMPERATURE"),

            # AIRCRAFT WIND X: longitudinal wind component along aircraft axis.
            # Positive = tailwind, negative = headwind. Direct MSFS SimVar,
            # no trigonometry needed. More reliable than ambient + track calc.
            wind_x_kt=self._safe_get_float(aq, "AIRCRAFT_WIND_X"),

            on_ground=self._safe_get_bool(aq, "SIM_ON_GROUND"),
        )

    @staticmethod
    def _safe_get(aq: AircraftRequests, name: str) -> Any | None:
        try:
            return aq.get(name)
        except Exception:
            return None

    @classmethod
    def _safe_get_float(cls, aq: AircraftRequests, name: str) -> float | None:
        value = cls._safe_get(aq, name)

        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _safe_get_bool(cls, aq: AircraftRequests, name: str) -> bool | None:
        value = cls._safe_get(aq, name)

        if value is None:
            return None

        if isinstance(value, bool):
            return value

        try:
            return bool(int(value))
        except (TypeError, ValueError):
            return None
