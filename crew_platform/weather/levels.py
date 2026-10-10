"""Flight-level ↔ pressure conversion (ISA standard atmosphere).

Pure-python, no scientific-stack import — unit-testable in isolation.

ISA reference atmosphere:
  Troposphere (0..11000 m):
      T(h)   = T0 - L·h,  T0 = 288.15 K, L = 0.0065 K/m
      p(h)   = 1013.25·(T/T0)^(g/(R·L))  hPa,  g/(R·L) = 5.25597
  Stratosphere (isothermal above the 11000 m / 216.65 K tropopause):
      p(h)   = p_t·exp(-g·(h - 11000)/(R·T_t))

Anchors the module reproduces: FL000 = 1013.25 hPa, FL100 ≈ 696.7,
FL300 ≈ 300.5, FL340 ≈ 250.0 (the 250 hPa surface is ~FL340),
FL450 ≈ 147.5 hPa.

The "pressure band" of a flight level is the pair of standard GFS pressure
levels that bracket the level's ISA altitude:
  above  = the standard level at the HIGHER altitude (smaller pressure)
  below  = the standard level at the LOWER altitude (larger pressure)
so that the level's own ISA pressure always lies in [above, below].
"""
from __future__ import annotations

import math
from typing import Sequence

G = 9.80665            # m/s^2
R = 287.05             # J/(kg K) specific gas constant (dry air)
L_LAPSE = 0.0065       # K/m
T0_K = 288.15
P0_HPA = 1013.25
H_TROPO_M = 11000.0
T_TROPO_K = 216.65
_TROPO_EXP = G / (R * L_LAPSE)          # 5.25597
_P_TROPO_HPA = P0_HPA * (T_TROPO_K / T0_K) ** _TROPO_EXP   # ≈ 226.4 hPa

FT_PER_M = 3.280840

#: Standard GFS pressure levels we ingest (descending pressure == ascending
#: altitude), in hPa.
STANDARD_LEVELS_HPA = (
    1000.0, 925.0, 850.0, 700.0, 500.0, 400.0, 300.0,
    250.0, 200.0, 150.0, 100.0, 50.0,
)


def fl_altitude_ft(fl: int) -> float:
    """Geometric altitude in ft for a flight level (FL050 -> 5000 ft)."""
    if fl < 0:
        raise ValueError("flight level must be >= 0")
    return fl * 100.0


def fl_to_pressure_hpa(fl: int) -> float:
    """ISA pressure (hPa) at the geometric altitude of flight level ``fl``."""
    h_m = fl_altitude_ft(fl) / FT_PER_M
    if h_m <= H_TROPO_M:
        t = T0_K - L_LAPSE * h_m
        return P0_HPA * (t / T0_K) ** _TROPO_EXP
    return _P_TROPO_HPA * math.exp(-G * (h_m - H_TROPO_M) / (R * T_TROPO_K))


def pressure_hpa_to_fl(p_hpa: float) -> float:
    """Inverse of :func:`fl_to_pressure_hpa` — flight level for an ISA pressure.

    Returns a (possibly fractional) FL; e.g. 250 hPa -> ~340.
    """
    if p_hpa <= 0:
        raise ValueError("pressure must be > 0")
    if p_hpa >= _P_TROPO_HPA:
        t = T0_K * (p_hpa / P0_HPA) ** (1.0 / _TROPO_EXP)
        h_m = (T0_K - t) / L_LAPSE
    else:
        h_m = H_TROPO_M + (R * T_TROPO_K / G) * math.log(_P_TROPO_HPA / p_hpa)
    return (h_m * FT_PER_M) / 100.0


def fl_pressure_band(fl: int) -> tuple[float, float]:
    """(p_upper, p_lower): the standard GFS levels bracketing the FL.

    ``p_upper`` is at the higher altitude (smaller pressure), ``p_lower`` at
    the lower altitude (larger pressure); the FL's ISA pressure always lies
    in [p_upper, p_lower]. When the ISA pressure sits within 0.25 hPa of a
    standard level the pair collapses to that level (fraction 0). Raises
    ``ValueError`` above the 50 hPa top of the stack; clamps FL000 (p >
    1000 hPa) to the 1000 hPa level.
    """
    p = fl_to_pressure_hpa(fl)
    if p < STANDARD_LEVELS_HPA[-1] - 0.5:
        raise ValueError(
            f"FL{fl:03d} (≈{p:.1f} hPa) is above the {STANDARD_LEVELS_HPA[-1]:.0f} hPa "
            "level stack"
        )
    if p > STANDARD_LEVELS_HPA[0]:
        # FL000 (sea level, 1013.25 hPa) is below the 1000 hPa surface.
        return (STANDARD_LEVELS_HPA[0], STANDARD_LEVELS_HPA[0])
    # Exactly on a standard level -> collapse to it.
    for lvl in STANDARD_LEVELS_HPA:
        if abs(p - lvl) <= 0.25:
            return (float(lvl), float(lvl))
    p_upper = max(level for level in STANDARD_LEVELS_HPA if level < p)   # higher altitude
    p_lower = min(level for level in STANDARD_LEVELS_HPA if level > p)   # lower altitude
    return (float(p_upper), float(p_lower))


def fl_fraction(fl: int) -> float:
    """Fraction of the FL's ISA pressure between its bracketing levels.

    ``t = 0`` at ``p_lower`` (the larger pressure) and ``t = 1`` at
    ``p_upper`` (the smaller pressure), so
    ``value ≈ (1-t)·value(p_lower) + t·value(p_upper)``. A FL sitting on a
    standard level returns 0.0 (sample that level). Clamped to [0, 1].
    """
    p_upper, p_lower = fl_pressure_band(fl)
    if p_upper == p_lower:
        return 0.0
    p = fl_to_pressure_hpa(fl)
    t = (p - p_lower) / (p_upper - p_lower)
    return min(1.0, max(0.0, t))


def lerp(lo: float, hi: float, t: float) -> float:
    """Linear blend: ``lo`` at t=0, ``hi`` at t=1."""
    return lo + (hi - lo) * t


def available_slabs(ingested_hpa: Sequence[float] | None = None) -> list[float]:
    """The FL slabs resolvable by a set of ingested pressure levels.

    With no argument, returns every supported FL band (50..450). Given the
    levels actually present in a dataset, returns the FLs whose ISA pressure
    is bracketed by two consecutive ingested levels (or sits exactly on one).
    """
    bands = (50, 100, 150, 200, 250, 300, 350, 400, 450)
    if ingested_hpa is None:
        return [float(b) for b in bands]
    lvl = sorted(float(x) for x in ingested_hpa)
    if not lvl:
        return []
    out: list[float] = []
    for fl in bands:
        p = fl_to_pressure_hpa(fl)
        # p must sit between two consecutive ingested levels (or on one)
        ok = False
        for i in range(len(lvl) - 1):
            lo, hi = lvl[i], lvl[i + 1]
            if lo - 0.5 <= p <= hi + 0.5:
                ok = True
                break
        if not ok and any(abs(p - x) <= 0.5 for x in lvl):
            ok = True
        if ok:
            out.append(float(fl))
    return out
