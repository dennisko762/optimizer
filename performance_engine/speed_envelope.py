"""
Speed envelope: ICAO standard atmosphere and VMO/MMO Mach ceiling.

All altitudes in feet, speeds in knots.
Uses the precise ICAO compressible-flow formula (not the simplified EAS
approximation) — the difference at M0.80 and FL250 is about 0.5%, which
matters when the optimizer is choosing between M0.78 and M0.80.
"""
from __future__ import annotations

import math

# ── ISA constants ──────────────────────────────────────────────────────────────
_T0_K: float = 288.15        # sea-level ISA temperature (K)
_P0_HPA: float = 1013.25     # sea-level ISA pressure (hPa)
_A0_KT: float = 661.479      # sea-level speed of sound (KTAS)
_TROPOPAUSE_FT: float = 36089.0

# ── ISA atmosphere ─────────────────────────────────────────────────────────────

def isa_temperature_k(altitude_ft: float) -> float:
    """Static air temperature in ISA [K] at altitude_ft."""
    if altitude_ft <= _TROPOPAUSE_FT:
        return _T0_K * (1.0 - 6.8755856e-6 * altitude_ft)
    return 216.65


def isa_pressure_ratio(altitude_ft: float) -> float:
    """Pressure ratio δ = P/P0 in ISA."""
    t = isa_temperature_k(altitude_ft)
    theta = t / _T0_K
    if altitude_ft <= _TROPOPAUSE_FT:
        return theta ** 5.2561
    # Stratosphere: isothermal layer
    delta_trop = isa_pressure_ratio(_TROPOPAUSE_FT)
    return delta_trop * math.exp(-0.0000481 * (altitude_ft - _TROPOPAUSE_FT))


def isa_speed_of_sound_ktas(altitude_ft: float) -> float:
    """Speed of sound [KTAS] in ISA at altitude_ft."""
    return _A0_KT * math.sqrt(isa_temperature_k(altitude_ft) / _T0_K)


# ── CAS ↔ Mach (ICAO compressible-flow formula) ───────────────────────────────

def cas_to_mach(cas_kt: float, altitude_ft: float) -> float:
    """
    Convert CAS (≈ IAS) in knots to Mach at altitude_ft.

    ICAO Doc 8643 / BADA formula — compressible subsonic flow:
      qc = P0 × [(1 + 0.2 × (CAS/a0)²)^3.5 − 1]
      M  = √(5 × [(qc/P + 1)^(2/7) − 1])
    """
    if cas_kt <= 0:
        return 0.0

    # Impact pressure at sea level from CAS
    qc_hpa = _P0_HPA * ((1.0 + 0.2 * (cas_kt / _A0_KT) ** 2) ** 3.5 - 1.0)

    # Pressure at altitude
    p_hpa = _P0_HPA * isa_pressure_ratio(altitude_ft)

    mach_sq = 5.0 * ((qc_hpa / p_hpa + 1.0) ** (2.0 / 7.0) - 1.0)
    return math.sqrt(max(mach_sq, 0.0))


def mach_to_cas(mach: float, altitude_ft: float) -> float:
    """Inverse of cas_to_mach — returns CAS [kt] for a given Mach at altitude."""
    p_hpa = _P0_HPA * isa_pressure_ratio(altitude_ft)
    qc_hpa = p_hpa * ((1.0 + 0.2 * mach ** 2) ** 3.5 - 1.0)
    cas_sq = 5.0 * _A0_KT ** 2 * ((qc_hpa / _P0_HPA + 1.0) ** (2.0 / 7.0) - 1.0)
    return math.sqrt(max(cas_sq, 0.0))


# ── Speed-envelope ceiling ─────────────────────────────────────────────────────

def max_mach_at_altitude(
    altitude_ft: float,
    *,
    vmo_kt: float,
    mmo: float,
) -> float:
    """
    Maximum achievable Mach at altitude_ft, limited by the lower of:
      • VMO (structural limit expressed as CAS in knots)
      • MMO (compressibility / buffet limit expressed as Mach)

    Both limits are real aircraft certification values; the crossover FL is
    where their curves intersect (typically FL290–FL320 for most transports).
    """
    mach_from_vmo = cas_to_mach(vmo_kt, altitude_ft)
    return min(mach_from_vmo, mmo)


def min_altitude_for_mach(
    target_mach: float,
    *,
    vmo_kt: float,
    mmo: float,
    step_ft: float = 500.0,
) -> float | None:
    """
    Return the lowest altitude (ft) at which target_mach is within the
    VMO/MMO envelope, or None if target_mach > MMO (always impossible).

    Searches upward from 10,000 ft in step_ft increments.
    """
    if target_mach > mmo:
        return None

    for alt in range(10_000, 450_000, int(step_ft)):
        if max_mach_at_altitude(float(alt), vmo_kt=vmo_kt, mmo=mmo) >= target_mach:
            return float(alt)
    return None


def climb_advisory(
    current_altitude_ft: float,
    target_mach: float,
    *,
    vmo_kt: float,
    mmo: float,
) -> str | None:
    """
    If target_mach is not achievable at current_altitude_ft, return a
    human-readable advisory string (e.g. "Climb to FL280 for M0.82").
    Returns None when no climb is needed.
    """
    if max_mach_at_altitude(current_altitude_ft, vmo_kt=vmo_kt, mmo=mmo) >= target_mach:
        return None

    needed_ft = min_altitude_for_mach(target_mach, vmo_kt=vmo_kt, mmo=mmo)
    if needed_ft is None:
        return f"M{target_mach:.3f} exceeds MMO ({mmo:.2f}) and is not achievable."
    fl = int(round(needed_ft / 100))
    return f"Climb to FL{fl:03d} to achieve M{target_mach:.2f} (VMO-limited at current altitude)."
