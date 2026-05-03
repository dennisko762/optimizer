from __future__ import annotations

from dataclasses import dataclass

from performance_engine.database.boeing_fcom_model import (
    BoeingFcomPerformance,
    CruisePoint,
    CruiseQuery,
    CruiseRow,
)


@dataclass(frozen=True)
class _Bracket:
    lower: CruiseRow
    upper: CruiseRow
    ratio: float
    interpolated: bool


def query_lrc_cruise(
    performance: BoeingFcomPerformance,
    *,
    weight_kg: float,
    altitude_ft: float,
) -> CruiseQuery:
    """
    Bilinear lookup in the Boeing FCOM Long Range Cruise table.

    The Boeing source is tabulated by weight and reachable altitude. We interpolate
    in altitude inside each weight row, then interpolate between the two bracketing
    weight rows.

    If the requested altitude is above the highest reachable point for a row, the
    lookup clamps to the highest point and marks the query as out-of-envelope.
    """

    rows = sorted(performance.lrc_cruise_rows, key=lambda row: row.weight_kg)
    if not rows:
        raise ValueError("Boeing FCOM dataset has no LRC cruise rows.")

    bracket = _bracket_rows(rows=rows, weight_kg=weight_kg)
    lower = _query_row(row=bracket.lower, altitude_ft=altitude_ft, engine_count=performance.engine_count)

    if not bracket.interpolated:
        return lower

    upper = _query_row(row=bracket.upper, altitude_ft=altitude_ft, engine_count=performance.engine_count)

    return CruiseQuery(
        weight_kg=round(weight_kg, 2),
        altitude_ft=round(altitude_ft, 2),
        mach=round(_lerp(lower.mach, upper.mach, bracket.ratio), 4),
        kias=round(_lerp(lower.kias, upper.kias, bracket.ratio), 2),
        ff_per_eng_kg_h=round(_lerp(lower.ff_per_eng_kg_h, upper.ff_per_eng_kg_h, bracket.ratio), 2),
        ff_total_kg_h=round(_lerp(lower.ff_total_kg_h, upper.ff_total_kg_h, bracket.ratio), 2),
        n1_pct=round(_lerp(lower.n1_pct, upper.n1_pct, bracket.ratio), 2),
        interpolated_in_weight=True,
        interpolated_in_altitude=lower.interpolated_in_altitude or upper.interpolated_in_altitude,
        out_of_envelope=lower.out_of_envelope or upper.out_of_envelope,
        notes=_merge_notes(lower.notes, upper.notes),
    )


def _bracket_rows(*, rows: list[CruiseRow], weight_kg: float) -> _Bracket:
    if weight_kg <= rows[0].weight_kg:
        return _Bracket(lower=rows[0], upper=rows[0], ratio=0.0, interpolated=False)

    if weight_kg >= rows[-1].weight_kg:
        return _Bracket(lower=rows[-1], upper=rows[-1], ratio=0.0, interpolated=False)

    for lower, upper in zip(rows, rows[1:]):
        if lower.weight_kg <= weight_kg <= upper.weight_kg:
            span = upper.weight_kg - lower.weight_kg
            ratio = 0.0 if span <= 1e-9 else (weight_kg - lower.weight_kg) / span
            return _Bracket(lower=lower, upper=upper, ratio=ratio, interpolated=True)

    return _Bracket(lower=rows[-1], upper=rows[-1], ratio=0.0, interpolated=False)


def _query_row(*, row: CruiseRow, altitude_ft: float, engine_count: int) -> CruiseQuery:
    points = sorted(row.points, key=lambda point: point.altitude_ft)
    if not points:
        raise ValueError(f"Boeing FCOM cruise row {row.weight_kg:.0f} kg has no points.")

    if altitude_ft <= points[0].altitude_ft:
        point = points[0]
        return _query_point(
            point=point,
            weight_kg=row.weight_kg,
            altitude_ft=altitude_ft,
            engine_count=engine_count,
            interpolated_in_altitude=False,
            out_of_envelope=False,
            notes=(
                [
                    f"Requested altitude {altitude_ft:.0f} ft is below lowest FCOM row point "
                    f"{point.altitude_ft:.0f} ft; using lowest available point."
                ]
                if altitude_ft < point.altitude_ft
                else []
            ),
        )

    if altitude_ft >= points[-1].altitude_ft:
        point = points[-1]
        return _query_point(
            point=point,
            weight_kg=row.weight_kg,
            altitude_ft=altitude_ft,
            engine_count=engine_count,
            interpolated_in_altitude=False,
            out_of_envelope=altitude_ft > point.altitude_ft,
            notes=(
                [
                    f"Requested altitude {altitude_ft:.0f} ft exceeds highest reachable FCOM point "
                    f"{point.altitude_ft:.0f} ft at {row.weight_kg:.0f} kg; clamping to highest point."
                ]
                if altitude_ft > point.altitude_ft
                else []
            ),
        )

    for lower, upper in zip(points, points[1:]):
        if lower.altitude_ft <= altitude_ft <= upper.altitude_ft:
            span = upper.altitude_ft - lower.altitude_ft
            ratio = 0.0 if span <= 1e-9 else (altitude_ft - lower.altitude_ft) / span

            return CruiseQuery(
                weight_kg=round(row.weight_kg, 2),
                altitude_ft=round(altitude_ft, 2),
                mach=round(_lerp(lower.mach, upper.mach, ratio), 4),
                kias=round(_lerp(lower.kias, upper.kias, ratio), 2),
                ff_per_eng_kg_h=round(_lerp(lower.ff_per_eng_kg_h, upper.ff_per_eng_kg_h, ratio), 2),
                ff_total_kg_h=round(_lerp(lower.ff_per_eng_kg_h, upper.ff_per_eng_kg_h, ratio) * engine_count, 2),
                n1_pct=round(_lerp(lower.n1_pct, upper.n1_pct, ratio), 2),
                interpolated_in_weight=False,
                interpolated_in_altitude=True,
                out_of_envelope=False,
                notes=[],
            )

    point = points[-1]
    return _query_point(
        point=point,
        weight_kg=row.weight_kg,
        altitude_ft=altitude_ft,
        engine_count=engine_count,
        interpolated_in_altitude=False,
        out_of_envelope=False,
        notes=["FCOM lookup fell through unexpectedly; using highest point as fallback."],
    )


def _query_point(
    *,
    point: CruisePoint,
    weight_kg: float,
    altitude_ft: float,
    engine_count: int,
    interpolated_in_altitude: bool,
    out_of_envelope: bool,
    notes: list[str],
) -> CruiseQuery:
    return CruiseQuery(
        weight_kg=round(weight_kg, 2),
        altitude_ft=round(altitude_ft, 2),
        mach=round(point.mach, 4),
        kias=round(point.kias, 2),
        ff_per_eng_kg_h=round(point.ff_per_eng_kg_h, 2),
        ff_total_kg_h=round(point.ff_per_eng_kg_h * engine_count, 2),
        n1_pct=round(point.n1_pct, 2),
        interpolated_in_weight=False,
        interpolated_in_altitude=interpolated_in_altitude,
        out_of_envelope=out_of_envelope,
        notes=notes,
    )


def _lerp(a: float, b: float, ratio: float) -> float:
    return a + (b - a) * ratio


def _merge_notes(*note_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for notes in note_lists:
        for note in notes:
            if note not in merged:
                merged.append(note)
    return merged
