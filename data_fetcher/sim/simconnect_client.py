from __future__ import annotations

import asyncio
import time
from typing import Any

from SimConnect import SimConnect, AircraftRequests

from data_fetcher.sim.sim_client import SimClient, SimClientError
from data_fetcher.sim.sim_models import LiveSimState, RawSimState
from data_fetcher.sim.sim_normalizer import normalize_raw_sim_state


LB_TO_KG = 0.45359237
DEFAULT_FUEL_WEIGHT_PER_GALLON_LB = 6.7
MAX_FUEL_FLOW_ENGINE_COUNT = 4
MAX_REASONABLE_FUEL_FLOW_KG_H = 100000.0
DELTA_FUEL_FLOW_SOURCE = "FUEL_TOTAL_QUANTITY_WEIGHT_DELTA"


class SimConnectClientError(SimClientError):
    pass


class SimConnectClient(SimClient):
    def __init__(self, cache_ms: int = 200):
        self.cache_ms = cache_ms
        self._simconnect: SimConnect | None = None
        self._aircraft_requests: AircraftRequests | None = None
        self._last_fuel_remaining_lb: float | None = None
        self._last_fuel_sample_monotonic: float | None = None

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
        self._last_fuel_remaining_lb = None
        self._last_fuel_sample_monotonic = None

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
        fuel_remaining_lb = self._safe_get_float(aq, "FUEL_TOTAL_QUANTITY_WEIGHT")
        fuel_remaining_lb_ex1 = self._safe_get_float(aq, "FUEL_TOTAL_QUANTITY_WEIGHT_EX1")
        effective_fuel_remaining_lb = self._first_not_none(
            fuel_remaining_lb,
            fuel_remaining_lb_ex1,
        )
        engine_count = self._safe_get_int(aq, "NUMBER_OF_ENGINES")
        engine_type = self._safe_get_int(aq, "ENGINE_TYPE")
        fuel_weight_per_gallon_lb = self._safe_get_float(aq, "FUEL_WEIGHT_PER_GALLON")
        fuel_flow_kg_h, fuel_flow_source = self._resolve_fuel_flow_measurement(
            aq,
            engine_count=engine_count,
            engine_type=engine_type,
            fuel_weight_per_gallon_lb=fuel_weight_per_gallon_lb,
            fuel_remaining_lb=effective_fuel_remaining_lb,
        )

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
            fuel_remaining_lb=fuel_remaining_lb,
            fuel_remaining_lb_ex1=fuel_remaining_lb_ex1,
            fuel_weight_per_gallon_lb=fuel_weight_per_gallon_lb,
            fuel_flow_kg_h=fuel_flow_kg_h,
            fuel_flow_source=fuel_flow_source,
            engine_count=engine_count,
            engine_type=engine_type,

            wind_velocity_kt=self._safe_get_float(aq, "AMBIENT_WIND_VELOCITY"),
            wind_direction_deg=self._safe_get_float(aq, "AMBIENT_WIND_DIRECTION"),
            ambient_temperature_c=self._safe_get_float(aq, "AMBIENT_TEMPERATURE"),

            # AIRCRAFT WIND X = lateral body-axis wind component.
            wind_x_kt=self._safe_get_float(aq, "AIRCRAFT_WIND_X"),
            # AIRCRAFT WIND Z = longitudinal body-axis wind component.
            wind_z_kt=self._safe_get_float(aq, "AIRCRAFT_WIND_Z"),

            on_ground=self._safe_get_bool(aq, "SIM_ON_GROUND"),

            gps_is_active_flight_plan=self._safe_get_bool(aq, "GPS_IS_ACTIVE_FLIGHT_PLAN"),
            gps_ete_seconds=self._safe_get_float(aq, "GPS_ETE"),
            gps_eta_seconds=self._safe_get_float(aq, "GPS_ETA"),
            gps_target_distance_m=self._safe_get_float(aq, "GPS_TARGET_DISTANCE"),
            gps_wp_distance_m=self._safe_get_float(aq, "GPS_WP_DISTANCE"),
            gps_ground_speed_m_s=self._safe_get_float(aq, "GPS_GROUND_SPEED"),
        )

    def _resolve_fuel_flow_measurement(
        self,
        aq: AircraftRequests,
        *,
        engine_count: int | None,
        engine_type: int | None,
        fuel_weight_per_gallon_lb: float | None,
        fuel_remaining_lb: float | None,
    ) -> tuple[float | None, str | None]:
        derived_fuel_flow_kg_h = self._estimate_fuel_flow_from_total_fuel_lb(
            fuel_remaining_lb
        )

        for simvar_name, unit in self._fuel_flow_source_candidates(engine_type):
            fuel_flow_kg_h = self._indexed_fuel_flow_kg_h(
                aq,
                simvar_name=simvar_name,
                unit=unit,
                engine_count=engine_count,
                fuel_weight_per_gallon_lb=fuel_weight_per_gallon_lb,
            )
            if fuel_flow_kg_h is not None:
                return fuel_flow_kg_h, simvar_name

        if derived_fuel_flow_kg_h is not None:
            return derived_fuel_flow_kg_h, DELTA_FUEL_FLOW_SOURCE

        return None, None

    @staticmethod
    def _fuel_flow_source_candidates(
        engine_type: int | None,
    ) -> tuple[tuple[str, str], ...]:
        piston_candidates = (
            ("RECIP_ENG_FUEL_FLOW", "pph"),
            ("ENG_FUEL_FLOW_PPH", "pph"),
            ("ENG_FUEL_FLOW_GPH", "gph"),
            ("TURB_ENG_FUEL_FLOW_PPH", "pph"),
            ("TURB_ENG_CORRECTED_FF", "pph"),
        )
        turbine_candidates = (
            ("TURB_ENG_FUEL_FLOW_PPH", "pph"),
            ("ENG_FUEL_FLOW_PPH", "pph"),
            ("ENG_FUEL_FLOW_GPH", "gph"),
            ("TURB_ENG_CORRECTED_FF", "pph"),
            ("RECIP_ENG_FUEL_FLOW", "pph"),
        )
        generic_candidates = (
            ("TURB_ENG_FUEL_FLOW_PPH", "pph"),
            ("ENG_FUEL_FLOW_PPH", "pph"),
            ("RECIP_ENG_FUEL_FLOW", "pph"),
            ("ENG_FUEL_FLOW_GPH", "gph"),
            ("TURB_ENG_CORRECTED_FF", "pph"),
        )

        if engine_type == 0:
            return piston_candidates
        if engine_type in {1, 3, 5}:
            return turbine_candidates
        return generic_candidates

    def _indexed_fuel_flow_kg_h(
        self,
        aq: AircraftRequests,
        *,
        simvar_name: str,
        unit: str,
        engine_count: int | None,
        fuel_weight_per_gallon_lb: float | None,
    ) -> float | None:
        if engine_count is None or engine_count <= 0:
            engine_indices = range(1, MAX_FUEL_FLOW_ENGINE_COUNT + 1)
        else:
            engine_indices = range(
                1,
                min(int(engine_count), MAX_FUEL_FLOW_ENGINE_COUNT) + 1,
            )
        total_fuel_flow_kg_h = 0.0
        positive_engine_count = 0

        for engine_index in engine_indices:
            raw_value = self._safe_get_float(aq, f"{simvar_name}:{engine_index}")
            if raw_value is None:
                continue

            if unit == "gph":
                fuel_weight_lb = (
                    fuel_weight_per_gallon_lb
                    if fuel_weight_per_gallon_lb is not None
                    and fuel_weight_per_gallon_lb > 0
                    else DEFAULT_FUEL_WEIGHT_PER_GALLON_LB
                )
                fuel_flow_kg_h = raw_value * fuel_weight_lb * LB_TO_KG
            else:
                fuel_flow_kg_h = raw_value * LB_TO_KG

            fuel_flow_kg_h = self._sanitize_fuel_flow_kg_h(fuel_flow_kg_h)
            if fuel_flow_kg_h is None:
                continue

            total_fuel_flow_kg_h += fuel_flow_kg_h
            if fuel_flow_kg_h > 0:
                positive_engine_count += 1

        if positive_engine_count <= 0:
            return None

        return round(total_fuel_flow_kg_h, 2)

    def _estimate_fuel_flow_from_total_fuel_lb(
        self,
        fuel_remaining_lb: float | None,
    ) -> float | None:
        now_monotonic = time.monotonic()
        previous_fuel_remaining_lb = self._last_fuel_remaining_lb
        previous_sample_monotonic = self._last_fuel_sample_monotonic

        self._last_fuel_remaining_lb = fuel_remaining_lb
        self._last_fuel_sample_monotonic = now_monotonic

        if (
            fuel_remaining_lb is None
            or previous_fuel_remaining_lb is None
            or previous_sample_monotonic is None
        ):
            return None

        elapsed_seconds = now_monotonic - previous_sample_monotonic
        if elapsed_seconds <= 0.5:
            return None

        burned_fuel_lb = previous_fuel_remaining_lb - fuel_remaining_lb
        if burned_fuel_lb <= 0:
            return None

        fuel_flow_kg_h = burned_fuel_lb * LB_TO_KG * (3600.0 / elapsed_seconds)
        return self._sanitize_fuel_flow_kg_h(fuel_flow_kg_h)

    @staticmethod
    def _sanitize_fuel_flow_kg_h(value: float | None) -> float | None:
        if value is None or value <= 0 or value > MAX_REASONABLE_FUEL_FLOW_KG_H:
            return None
        return float(value)

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
    def _safe_get_int(cls, aq: AircraftRequests, name: str) -> int | None:
        value = cls._safe_get(aq, name)

        if value is None:
            return None

        try:
            return int(value)
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

    @staticmethod
    def _first_not_none(*values: float | None) -> float | None:
        for value in values:
            if value is not None:
                return value
        return None
