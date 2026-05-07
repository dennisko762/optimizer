from __future__ import annotations

from performance_engine.ci_table.ci_mach_table_model import CiMachBand, CiMachTable


def cost_index_to_mach_from_table(
    cost_index: int | float,
    table: CiMachTable | None,
) -> float | None:
    band = find_band_for_cost_index(cost_index=cost_index, table=table)
    if band is None:
        return None
    return band.mach


def mach_to_cost_index_from_table(
    mach: float,
    table: CiMachTable | None,
) -> int | None:
    band = find_band_for_mach(mach=mach, table=table)
    if band is None:
        return None
    return representative_cost_index_for_band(band)


def find_band_for_cost_index(
    *,
    cost_index: int | float,
    table: CiMachTable | None,
) -> CiMachBand | None:
    if table is None:
        return None

    ci_value = int(round(float(cost_index)))
    for band in table.bands:
        upper_ci = band.upper_ci
        if upper_ci is None:
            if ci_value >= band.lower_ci:
                return band
            continue

        if band.lower_ci <= ci_value <= upper_ci:
            return band

    return None


def find_band_for_mach(
    *,
    mach: float,
    table: CiMachTable | None,
    tolerance: float = 0.0005,
) -> CiMachBand | None:
    if table is None or not table.bands:
        return None

    rounded = round(float(mach), 3)
    for band in table.bands:
        if abs(round(band.mach, 3) - rounded) <= tolerance:
            return band
    return None


def representative_cost_index_for_band(band: CiMachBand) -> int:
    if band.upper_ci is None:
        return int(band.lower_ci)
    return int(band.lower_ci + ((band.upper_ci - band.lower_ci) // 2))
