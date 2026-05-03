"""
Boeing FCOM Performance Inflight data models.

Source: Boeing 777 FCOM Volume 2, Performance Inflight section.
Specifically structured for the format used in:
  - 777-300ER/GE90-115BL JAA Category B Brakes (December 15, 2017)

The format is identical across Boeing aircraft FCOMs (same publisher template).
This module is type-agnostic: Pydantic models can hold data for any Boeing
aircraft, as long as the parser populates the fields correctly.

UNITS CONVENTION:
- Weight:    kg (raw values × 1000)
- Altitude:  ft (raw FL values × 100)
- FF:        kg/h per engine (multiply by engine count for total)
- N1:        percent
- KIAS:      knots indicated airspeed
- TAT:       °C
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ─── Atomic data points ──────────────────────────────────────────────────────


class CruisePoint(BaseModel):
    """
    Single cruise data point at a specific weight and altitude.

    Empty/missing FL values for a given weight indicate the altitude is not
    reachable at that weight (above buffet boundary or thrust-limited).
    """

    altitude_ft: float
    mach: float
    kias: float
    ff_per_eng_kg_h: float
    n1_pct: float


class CruiseRow(BaseModel):
    """LRC cruise data for one weight value across all reachable altitudes."""

    weight_kg: float
    points: list[CruisePoint] = Field(default_factory=list)


class HoldingPoint(BaseModel):
    """Single holding data point at a specific weight and altitude."""

    altitude_ft: float
    n1_pct: float
    kias: float
    ff_per_eng_kg_h: float


class HoldingRow(BaseModel):
    weight_kg: float
    points: list[HoldingPoint] = Field(default_factory=list)


# ─── Optimum altitude & buffet boundary ───────────────────────────────────────


class BuffetMargins(BaseModel):
    """
    Maximum operating altitude at three load factor limits.

    Boeing FCOM convention:
    - 1.30 g (39° bank): maximum altitude with 30% margin to buffet
    - 1.40 g (44° bank): standard operational margin
    - 1.50 g (48° bank): aggressive operational limit

    At 1.40 g is the typical "max altitude" used in dispatch.
    """

    margin_1_30g_ft: float
    margin_1_40g_ft: float
    margin_1_50g_ft: float


class OptimumAltitudeEntry(BaseModel):
    """Optimum altitude at a given weight, plus buffet limits."""

    weight_kg: float
    optimum_altitude_ft: float
    tat_c: float
    buffet_margins: BuffetMargins


class OptimumAltitudeTable(BaseModel):
    """
    Optimum altitude + buffet boundary table.

    The same logical table is published for multiple ISA conditions and CG
    positions. Each (isa_condition, cg_position) combination has its own table.
    """

    isa_condition: Literal["ISA+10_AND_BELOW", "ISA+15", "ISA+20"]
    cg_position: Literal["FORWARD_7.5_MAC", "MID_30_MAC"]
    entries: list[OptimumAltitudeEntry] = Field(default_factory=list)


# ─── Descent profile ─────────────────────────────────────────────────────────


class DescentProfile(BaseModel):
    """Descent at standard speed schedule (e.g. .84M/310/250)."""

    speed_schedule: str
    by_initial_altitude: dict[int, dict[str, float]] = Field(default_factory=dict)
    """
    Keyed by FL (e.g. 350 = FL350 = 35000 ft initial altitude).
    Value is {distance_nm: float, time_min: float}.
    """


# ─── Wind-altitude trade ──────────────────────────────────────────────────────


class WindAltitudeTradeRow(BaseModel):
    """
    Wind factor table for break-even altitude change calculation.

    Per Boeing method:
    - Read wind factor at present altitude
    - Read wind factor at new altitude
    - Difference is the wind change needed for break-even
    """

    altitude_ft: float
    wind_factor_by_weight: dict[int, float] = Field(default_factory=dict)
    """Keyed by weight in 1000 kg (e.g. 220 = 220000 kg)."""


# ─── Top-level performance database ──────────────────────────────────────────


class BoeingFcomPerformance(BaseModel):
    """
    Complete Boeing FCOM Performance Inflight database for one
    aircraft/engine variant.
    """

    # Identification
    aircraft_type: str
    engine: str
    operator_variant: str
    """e.g. JAA Category B Brakes, FAA Category A Brakes"""
    fcom_revision: str
    """e.g. December 15, 2017"""

    # Cruise data — LRC only in this revision
    lrc_cruise_rows: list[CruiseRow] = Field(default_factory=list)

    # Optimum altitude tables — multiple per (isa_condition, cg_position)
    optimum_altitude_tables: list[OptimumAltitudeTable] = Field(default_factory=list)

    # Holding — separate tables for flap config
    holding_flaps_up: list[HoldingRow] = Field(default_factory=list)
    holding_flaps_1: list[HoldingRow] = Field(default_factory=list)

    # Descent
    descent_profile: DescentProfile | None = None

    # Wind-altitude trade for step climb optimization
    wind_altitude_trade: list[WindAltitudeTradeRow] = Field(default_factory=list)

    # Engine count (defaults to 2 for twin-engine aircraft like 777)
    engine_count: int = 2

    # Source notes
    notes: list[str] = Field(default_factory=list)


# ─── Lookup result ───────────────────────────────────────────────────────────


class CruiseQuery(BaseModel):
    """Result of a cruise data lookup at arbitrary (weight, altitude)."""

    weight_kg: float
    altitude_ft: float
    mach: float
    kias: float
    ff_per_eng_kg_h: float
    ff_total_kg_h: float
    n1_pct: float

    interpolated_in_weight: bool
    interpolated_in_altitude: bool

    out_of_envelope: bool = False
    """True if requested altitude exceeds any data point at this weight."""

    notes: list[str] = Field(default_factory=list)