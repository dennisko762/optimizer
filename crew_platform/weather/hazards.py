"""Documented aviation hazard products from GFS fields.

Every product is a *derived proxy from public NOAA GFS model output*. None of
them is an official WAFS/eWAS product, and the API/UI must say so. Formulas
and thresholds are cited per product; where a threshold is empirical it is
labelled as such.

All functions are pure (no I/O, no scientific-stack import) so the formulas
and tier boundaries are unit-testable. Array math uses plain Python lists
where the caller passes scalars, or numpy arrays when the caller passes them
(callers pass 1-D flattened arrays to keep the functions dependency-light).
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

# ---------------------------------------------------------------------------
# Units and constants
# ---------------------------------------------------------------------------

# GFS pressure levels we ingest, in hPa (thinner first), paired with the
# index the level stack uses in the cycle dataset.
#: hPa -> index into the per-cycle ``levels`` order
LEVEL_INDEX = {
    850.0: 0,
    700.0: 1,
    500.0: 2,
    300.0: 3,
    250.0: 4,
    200.0: 5,
    150.0: 6,
    100.0: 7,
    50.0: 8,
}

#: Vertical distance (m) between adjacent pressure levels for shear estimates.
#: 850-700 ~ 1500 m, 700-500 ~ 2000 m, 500-300 ~ 3000 m, 300-250 ~ 1000 m,
#: 250-200 ~ 1200 m, 200-150 ~ 1500 m, 150-100 ~ 2300 m, 100-50 ~ 3500 m.
LEVEL_PAIR_SPACING_M = {
    (850.0, 700.0): 1500.0,
    (700.0, 500.0): 2000.0,
    (500.0, 300.0): 3000.0,
    (300.0, 250.0): 1000.0,
    (250.0, 200.0): 1200.0,
    (200.0, 150.0): 1500.0,
    (150.0, 100.0): 2300.0,
    (100.0, 50.0): 3500.0,
}

# ---------------------------------------------------------------------------
# Clear-Air Turbulence (CAT) — Ellrod / TITAN indices
# ---------------------------------------------------------------------------
#
# Ellrod & Knapp (1992), "An Objective Clear-Air Turbulence Forecasting
# Technique: Verification and Operational Use", Weather and Forecasting 7(1).
#
#   TI1 = S * DEF                       (Ellrod1 / NMC index)
#   TI2 = S * (DEF + CVG)               (Ellrod2 / AFGWC index)
#
# where S = |vertical wind shear| = sqrt( (du/dz)^2 + (dv/dz)^2 )   [1/s]
#       DEF = sqrt( (du/dx - dv/dy)^2 + (du/dx + dv/dy)^2 )         [1/s]
#       CVG = - (du/dx + dv/dy)                                          [1/s]
#
# Horizontal derivatives use the 0.25 deg grid (~28 km at mid-latitudes);
# vertical shear uses the pressure-level spacing above. The index is
# dimensionless; empirical tiers below follow the WAFS/TITAN practice of
# thresholding the index (proxy, not official WAFS).

TIER_NONE = 0
TIER_LIGHT = 1
TIER_MODERATE = 2
TIER_SEVERE = 3

# Empirical TI tier boundaries (index value). The stored index is the WMO
# TITAN-form value (see ti_from_grid: 1e6 x S x DEF), so the operational
# WMO/TITAN thresholds apply: <5 none, 5-15 light-moderate, 15-25 moderate,
# >=25 moderate-severe (WMO Code 306 / TITAN, proxy — not official WAFS).
TI_TIER_THRESHOLDS = (
    (0.0, TIER_NONE),
    (5.0, TIER_LIGHT),
    (15.0, TIER_MODERATE),
    (25.0, TIER_SEVERE),
)


def vertical_wind_shear(
    u_low: float, v_low: float, u_high: float, v_high: float, dz_m: float
) -> float:
    """|VWS| [1/s] between two pressure levels (scalar, one grid point)."""
    if dz_m <= 0:
        raise ValueError("dz_m must be > 0")
    du = (u_high - u_low) / dz_m
    dv = (v_high - v_low) / dz_m
    return math.hypot(du, dv)


def deformation(u_xx: float, v_yy: float, du_dy: float, dv_dx: float) -> float:
    """Total horizontal deformation [1/s] from wind derivatives.

    DEF = sqrt( (du/dx - dv/dy)^2 + (du/dy + dv/dx)^2 )
    — the sum of the stretching deformation (D1 - D2) and the shearing
    deformation (2 D12), as in Ellrod & Knapp (1992) / the ECMWF CAT memo.
    """
    stretch = u_xx - v_yy
    shear = du_dy + dv_dx
    return math.hypot(stretch, shear)


def convergence(u_xx, v_yy) -> float:
    """-CVG [1/s]; CVG = -(du/dx + dv/dy)."""
    return -(u_xx + v_yy)


def ti1(shear: float, defo: float) -> float:
    """Ellrod TI1 = S * DEF."""
    return shear * defo


def ti2(shear: float, defo: float, cvg: float) -> float:
    """Ellrod TI2 = S * (DEF + CVG)."""
    return shear * (defo + cvg)


def turbulence_tier(tiv: float) -> int:
    """Empirical TI tier from the index value (0..3)."""
    if tiv is None:
        return TIER_NONE
    tier = TIER_NONE
    for threshold, t in TI_TIER_THRESHOLDS:
        if tiv >= threshold:
            tier = t
    return tier


def ti_from_grid(
    u: list[list[float]], v: list[list[float]],
    u_hi: list[list[float]], v_hi: list[list[float]],
    dx_m: float, dz_m: float, dx_lat_m: Optional[float] = None,
) -> tuple[list[list[float]], list[list[float]]]:
    """TI1/TI2 2-D grids from wind components at two pressure levels.

    ``u``/``v`` are the lower level, ``u_hi``/``v_hi`` the upper level; each
    is a row-major ``[lat][lon]`` 2-D list (or numpy array) of the same
    shape. ``dx_m`` is the east-west cell size, ``dx_lat_m`` the north-south
    cell size (defaults to ``dx_m``), and ``dz_m`` the VERTICAL spacing
    between the two levels (m) used for the vertical wind shear.
    The longitude axis wraps (central difference across the antimeridian
    uses the circular neighbour); latitude uses one-sided edge differences.
    Returns ``(ti1, ti2)`` lists of the same shape.
    """
    rows = len(u)
    cols = len(u[0]) if rows else 0
    for name, arr in (("u", u), ("v", v), ("u_hi", u_hi), ("v_hi", v_hi)):
        if len(arr) != rows or any(len(r) != cols for r in arr):
            raise ValueError(f"wind array {name} shape mismatch")
    if dx_m <= 0 or dz_m <= 0:
        raise ValueError("dx_m and dz_m must be > 0")
    dy_m = dx_lat_m if dx_lat_m is not None else dx_m
    ti1_out: list[list[float]] = []
    ti2_out: list[list[float]] = []
    for i in range(rows):
        row1: list[float] = []
        row2: list[float] = []
        for j in range(cols):
            jw = (j - 1) % cols  # wraps east/west
            je = (j + 1) % cols
            du_dx = (u[i][je] - u[i][jw]) / (2.0 * dx_m)
            dv_dx = (v[i][je] - v[i][jw]) / (2.0 * dx_m)
            if i > 0 and i < rows - 1:
                du_dy = (u[i + 1][j] - u[i - 1][j]) / (2.0 * dy_m)
                dv_dy = (v[i + 1][j] - v[i - 1][j]) / (2.0 * dy_m)
            elif i == 0:
                du_dy = (u[1][j] - u[0][j]) / dy_m
                dv_dy = (v[1][j] - v[0][j]) / dy_m
            else:
                du_dy = (u[rows - 1][j] - u[rows - 2][j]) / dy_m
                dv_dy = (v[rows - 1][j] - v[rows - 2][j]) / dy_m
            s = vertical_wind_shear(u[i][j], v[i][j], u_hi[i][j], v_hi[i][j], dz_m)
            defo = deformation(du_dx, dv_dy, du_dy, dv_dx)
            cvg = convergence(du_dx, dv_dy)
            row1.append(ti1(s, defo))
            row2.append(ti2(s, defo, cvg))
        ti1_out.append(row1)
        ti2_out.append(row2)
    return ti1_out, ti2_out


# ---------------------------------------------------------------------------
# Icing (CIP-style) — proxy, NOT official WAFS
# ---------------------------------------------------------------------------
#
# CIP (Convective / In-Cloud Icing Proxy) is modelled on the classic icing
# criteria: supercooled water is possible when -20 C <= T <= 0 C, high RH
# (cloud), and a low-level lift / moist environment. We combine:
#
#   CIP = max(0, (RH/100)) * L(temp, w)
#
# where L is a lift/motion factor in 0..1 from vertical velocity ``w``
# (m/s): L = clamp(|w| / 2.0, 0, 1) — 2 m/s of lift saturates the factor.
#
#   CIP in 0..1; tiers:
#     0.00 < CIP <= 0.30  light
#     0.30 < CIP <= 0.60  moderate
#     0.60 < CIP          severe
#
# Labelled a proxy — it does not read cloud droplet content, only T/RH/w.

ICING_LIGHT_MAX = 0.30
ICING_MODERATE_MAX = 0.60


def icing_factor(temp_c: float, rh: float, w_m_s: float) -> float:
    """CIP-style icing proxy in 0..1 for one grid point.

    ``temp_c`` in Celsius, ``rh`` 0..100 (percent), ``w_m_s`` vertical
    velocity (m/s, sign-agnostic for icing — any strong lift counts).
    """
    if rh <= 0:
        return 0.0
    # supercooled water band: -20 C .. 0 C (below -20 C water is frozen/absent)
    if temp_c > 0.0 or temp_c < -20.0:
        return 0.0
    lift = min(1.0, abs(w_m_s) / 2.0)
    return min(1.0, (rh / 100.0) * lift)


def icing_tier(cip: float) -> int:
    """CIP tier 0..3 from the 0..1 proxy."""
    if cip is None:
        return TIER_NONE
    if cip <= 0.0:
        return TIER_NONE
    if cip <= ICING_LIGHT_MAX:
        return TIER_LIGHT
    if cip <= ICING_MODERATE_MAX:
        return TIER_MODERATE
    return TIER_SEVERE


# ---------------------------------------------------------------------------
# Jet stream — upper-level wind extent
# ---------------------------------------------------------------------------
#
# Standard jet-stream extent: wind speed >= 60 kt (or 30 m/s) marks the
# extent; the core is >= 100 kt (or 50 m/s). Computed on the 200 hPa (or the
# FL slab's upper level) wind. Proxy — not an official WAFS jet product.

JET_EXTENT_KT = 60.0
JET_CORE_KT = 100.0

KT_PER_MS = 1.94384


def jet_tier(speed_ms: float) -> int:
    """0 none, 1 extent (>=60 kt), 2 core (>=100 kt)."""
    if speed_ms is None:
        return TIER_NONE
    kt = speed_ms * KT_PER_MS
    if kt >= JET_CORE_KT:
        return 2
    if kt >= JET_EXTENT_KT:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Thermal fronts — Hewson & Järvi style, Bolton-1980 theta_e
# ---------------------------------------------------------------------------
#
# Hewson & Järvi (1995), "Front analysis and forecasting: A new
# methodology", Meteorological Applications 2(4): 341-356. Fronts are
# located by the magnitude of the gradient of an atmospheric "frontogenic"
# scalar. We use the *equivalent potential temperature* theta_e as the
# scalar, computed at 850 and 500 hPa from Bolton (1980):
#
#   Bolton (1980) "The computation of equivalent potential temperature",
#   Mon. Wea. Rev. 108: 1046-1053 (best approximation, as tabulated in the
#   equivalent-potential-temperature literature):
#
#     theta_e = theta_L * exp( ((3036/T_L) - 1.78) * r * (1 + 0.448 r) )
#     theta_L = T * (1000/(p - e))^kappa * (T/T_L)^(0.28 r)
#     T_L     = 1 / (1/(T - 56) + ln(T/T_d)/800) + 56
#
#   with kappa = 0.2854, e the Magnus/Tetens vapour pressure at the dew
#   point, and r the saturated mixing ratio at the LCL.
#
# Front strength is the horizontal gradient of (theta_e_850 - theta_e_500)
# or of theta_e_500; tiers (empirical, K per ~250 km):
#   >= 2.0  potential front
#   >= 3.5  strong front
# Warm vs cold sign from the vertical tilt of theta_e (theta_e_850 > theta_e_500
# on the warm side).

BOLTON_RD_OVER_CP = 0.2854
K = 273.15

FRONT_POTENTIAL_K = 2.0
FRONT_STRONG_K = 3.5


def dew_point_c(t_c: float, rh: float) -> float:
    """Dew point (C) from Magnus/Tetens: Td = (b*gamma)/(a-gamma),
    gamma = ln(RH/100) + a*T/(b+T), a=17.62, b=243.12 (Magnus 1965)."""
    if rh <= 0:
        return -100.0
    a, b = 17.62, 243.12
    rhc = min(100.0, max(1.0e-3, rh))
    gamma = math.log(rhc / 100.0) + a * t_c / (b + t_c)
    return (b * gamma) / (a - gamma)


def theta_e_k(temp_c: float, rh: float, p_hpa: float) -> float:
    """Equivalent potential temperature [K] — Bolton (1980) approximation.

        theta_e = theta_L * exp( ((3036/T_L) - 1.78) * r * (1 + 0.448 r) )
        theta_L = T * (1000/(p - e))^kappa * (T/T_L)^(0.28 r)
        T_L     = 1 / (1/(T - 56) + ln(T/T_d)/800) + 56        [LCL, K]

    with kappa = R_d/cp = 0.2854, e the vapour pressure at the dew point
    (Magnus/Tetens), and r the saturated mixing ratio at the LCL,
    r = 0.378 * e_s(T_L)/T_L. Inputs: temp_c (C), rh (0..100), p_hpa.
    Physically theta_e >= theta; for dry air it reduces to theta.
    """
    if p_hpa <= 0:
        raise ValueError("p_hpa must be > 0")
    t_k = temp_c + K
    if rh <= 0:
        # dry: theta_e == theta
        return t_k * (1000.0 / p_hpa) ** BOLTON_RD_OVER_CP
    if t_k <= 0:
        return float("nan")
    td_c = dew_point_c(temp_c, rh)
    e = 6.112 * math.exp((18.4 * td_c) / (td_c + 243.5))  # hPa
    p_eff = max(p_hpa - e, 1.0)
    t_l = 1.0 / (1.0 / (t_k - 56.0) + math.log(t_k / (td_c + K)) / 800.0) + 56.0
    t_l = max(min(t_l, t_k - 1e-3), 150.0)  # keep below T for the mixing ratio
    e_s = 6.112 * math.exp((17.67 * (t_l - K)) / (t_l - 29.65))
    r = 0.378 * (e_s / t_l)
    theta_l = t_k * (1000.0 / p_eff) ** BOLTON_RD_OVER_CP * (t_k / t_l) ** (0.28 * r)
    return theta_l * math.exp(((3036.0 / t_l) - 1.78) * r * (1.0 + 0.448 * r))


def front_gradient_k_per_250km(
    theta_e_i: float, theta_e_j: float, dx_m: float
) -> float:
    """|d(theta_e)/dx| scaled to K per ~250 km (scalar)."""
    if dx_m <= 0:
        raise ValueError("dx_m must be > 0")
    grad = abs(theta_e_i - theta_e_j) / dx_m  # K/m
    return grad * 250_000.0


def front_tier(grad_k: float) -> int:
    """0 none, 1 potential, 2 strong."""
    if grad_k is None:
        return TIER_NONE
    if grad_k >= FRONT_STRONG_K:
        return 2
    if grad_k >= FRONT_POTENTIAL_K:
        return 1
    return 0


# ---------------------------------------------------------------------------
# Vectorized (numpy) grid variants for the ingest path — same formulas as the
# scalar helpers above, no per-point Python loop.
# ---------------------------------------------------------------------------

def theta_e_grid(temp_c, rh, p_hpa: float):
    """Vectorized Bolton (1980) theta_e [K] over a (lat, lon) grid.

    ``temp_c`` in Celsius, ``rh`` 0..100, ``p_hpa`` the constant pressure of
    the level. Mirrors :func:`theta_e_k` point-for-point.
    """
    import numpy as np

    t = np.asarray(temp_c, dtype=np.float64)
    r = np.asarray(rh, dtype=np.float64)
    out = np.full(t.shape, np.nan, dtype=np.float64)
    dry = r <= 0.0
    out[dry] = (t[dry] + K) * (1000.0 / p_hpa) ** BOLTON_RD_OVER_CP
    wet = ~dry
    if wet.any():
        t_c = t[wet]                                  # Celsius
        tk = t_c + K                                  # Kelvin
        rc = np.clip(r[wet], 1.0e-3, 100.0)
        # Magnus/Tetens dew point — the gamma term uses °C (mirrors dew_point_c)
        gamma = np.log(rc / 100.0) + 17.62 * t_c / (243.12 + t_c)
        td_c = (243.12 * gamma) / (17.62 - gamma)
        e = 6.112 * np.exp((18.4 * td_c) / (td_c + 243.5))
        p_eff = np.maximum(p_hpa - e, 1.0)
        tl = 1.0 / (1.0 / (tk - 56.0) + np.log(tk / (td_c + K)) / 800.0) + 56.0
        tl = np.clip(tl, 150.0, tk - 1.0e-3)
        es = 6.112 * np.exp((17.67 * (tl - K)) / (tl - 29.65))
        rr = 0.378 * (es / tl)
        theta_l = tk * (1000.0 / p_eff) ** BOLTON_RD_OVER_CP * (tk / tl) ** (0.28 * rr)
        out[wet] = theta_l * np.exp(((3036.0 / tl) - 1.78) * rr * (1.0 + 0.448 * rr))
    return out


def icing_grid(temp_c, rh, w):
    """Vectorized CIP icing proxy 0..1 over a (lat, lon) grid.

    Mirrors :func:`icing_factor` point-for-point.
    """
    import numpy as np

    t = np.asarray(temp_c, dtype=np.float64)
    r = np.asarray(rh, dtype=np.float64)
    wv = np.asarray(w, dtype=np.float64)
    out = np.clip(r / 100.0 * np.minimum(1.0, np.abs(wv) / 2.0), 0.0, 1.0)
    out[(r <= 0.0) | (t > 0.0) | (t < -20.0)] = 0.0
    return out
