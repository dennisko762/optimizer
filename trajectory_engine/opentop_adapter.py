from __future__ import annotations

import importlib.util
import math
from typing import Any

from trajectory_engine.trajectory_models import (
    CostGridPoint,
    TrajectoryOptimizeRequest,
    TrajectoryOptimizeResponse,
    TrajectoryPhase,
    TrajectoryPoint,
    TrajectorySummary,
    WindGridPoint,
)


def dependency_status() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(name) is not None
        for name in ("opentop", "casadi", "pandas", "fastmeteo", "xarray", "cfgrib")
    }


def run_trajectory_optimization(
    request: TrajectoryOptimizeRequest,
) -> TrajectoryOptimizeResponse:
    top = _import_opentop()
    pd = _import_pandas()

    warnings: list[str] = []
    if request.phase == TrajectoryPhase.MULTIPHASE and not hasattr(top, "MultiPhase"):
        warnings.append("Installed opentop exposes no MultiPhase class; using CompleteFlight fallback.")

    optimizer = _build_optimizer(top, request)

    if request.phase == TrajectoryPhase.CRUISE:
        _apply_cruise_constraints(optimizer, request)

    if request.wind_grid:
        optimizer.enable_wind(_wind_dataframe(pd, request.wind_grid))

    objective: str | tuple[str, ...] | Any = _objective_for_opentop(
        request.objective,
        multiphase_available=not (
            request.phase == TrajectoryPhase.MULTIPHASE and not hasattr(top, "MultiPhase")
        ),
    )
    trajectory_kwargs: dict[str, Any] = {}

    if request.cost_grid:
        interpolant = top.tools.interpolant_from_dataframe(_cost_dataframe(pd, request.cost_grid))
        trajectory_kwargs["interpolant"] = interpolant
        trajectory_kwargs["n_dim"] = request.cost_grid_dimensions
        objective = _grid_objective(
            optimizer=optimizer,
            n_dim=request.cost_grid_dimensions,
            grid_weight=request.grid_weight,
            fuel_weight=request.fuel_weight,
        )

    if request.multi_start and hasattr(optimizer, "multi_start_trajectory"):
        flight, _candidates = optimizer.multi_start_trajectory(
            objective=objective,
            n_starts=max(request.n_starts, 1),
            max_fuel=request.max_fuel_kg,
            **trajectory_kwargs,
        )
    else:
        flight = optimizer.trajectory(objective=objective, **trajectory_kwargs)

    return TrajectoryOptimizeResponse(
        summary=_build_summary(
            flight=flight,
            optimizer=optimizer,
            phase=request.phase,
            objective=request.objective,
        ),
        points=_sample_points(flight, max_samples=request.max_samples),
        warnings=warnings,
    )


def _import_opentop():
    try:
        import opentop
    except ImportError as exc:
        raise RuntimeError(
            "opentop is not installed. Install the advanced trajectory stack "
            "with `pip install opentop fastmeteo` or rebuild the EFB from the updated requirements."
        ) from exc
    return opentop


def _import_pandas():
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas is required by the trajectory optimizer.") from exc
    return pd


def _build_optimizer(top: Any, request: TrajectoryOptimizeRequest) -> Any:
    optimizer_cls = {
        TrajectoryPhase.COMPLETE: top.CompleteFlight,
        TrajectoryPhase.MULTIPHASE: getattr(top, "MultiPhase", top.CompleteFlight),
        TrajectoryPhase.CRUISE: top.Cruise,
        TrajectoryPhase.CLIMB: top.Climb,
        TrajectoryPhase.DESCENT: top.Descent,
    }[request.phase]

    kwargs: dict[str, Any] = {}
    if request.engine:
        kwargs["engine"] = request.engine

    try:
        return optimizer_cls(
            request.aircraft.upper(),
            request.origin.upper(),
            request.destination.upper(),
            m0=request.m0,
            **kwargs,
        )
    except TypeError:
        kwargs.pop("engine", None)
        return optimizer_cls(
            request.aircraft.upper(),
            request.origin.upper(),
            request.destination.upper(),
            m0=request.m0,
            **kwargs,
        )


def _objective_for_opentop(
    objective: str | list[str],
    *,
    multiphase_available: bool,
) -> str | tuple[str, ...]:
    if isinstance(objective, list):
        if not multiphase_available:
            return objective[0]
        return tuple(objective)
    return objective


def _objective_label(objective: str | list[str]) -> str:
    if isinstance(objective, list):
        return " / ".join(objective)
    return objective


def _apply_cruise_constraints(optimizer: Any, request: TrajectoryOptimizeRequest) -> None:
    if request.fix_cruise_altitude and hasattr(optimizer, "fix_cruise_altitude"):
        optimizer.fix_cruise_altitude()
    if request.fix_mach_number and hasattr(optimizer, "fix_mach_number"):
        optimizer.fix_mach_number()
    if request.fix_track_angle and hasattr(optimizer, "fix_track_angle"):
        optimizer.fix_track_angle()


def _wind_dataframe(pd: Any, points: list[WindGridPoint]):
    return pd.DataFrame(
        [
            {
                "ts": point.ts,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "h": point.h,
                "u": point.u,
                "v": point.v,
            }
            for point in points
        ]
    )


def _cost_dataframe(pd: Any, points: list[CostGridPoint]):
    rows = []
    for point in points:
        row = {
            "latitude": point.latitude,
            "longitude": point.longitude,
            "height": point.height,
            "cost": point.cost,
        }
        if point.ts is not None:
            row["ts"] = point.ts
        rows.append(row)
    return pd.DataFrame(rows)


def _grid_objective(
    *,
    optimizer: Any,
    n_dim: int,
    grid_weight: float,
    fuel_weight: float,
):
    def objective(x, u, dt, **kwargs):
        grid_cost = optimizer.obj_grid_cost(
            x,
            u,
            dt,
            n_dim=n_dim,
            time_dependent=n_dim == 4,
            **kwargs,
        )
        fuel_cost = optimizer.obj_fuel(x, u, dt, **kwargs)
        return grid_cost * grid_weight + fuel_cost * fuel_weight

    return objective


def _build_summary(
    *,
    flight: Any,
    optimizer: Any,
    phase: TrajectoryPhase,
    objective: str | list[str],
) -> TrajectorySummary:
    solver_stats = _solver_stats(optimizer)
    return TrajectorySummary(
        phase=phase,
        objective=_objective_label(objective),
        fuelKg=_series_sum(flight, "fuel_cost") or _mass_burn(flight),
        flightTimeMin=_max_value(flight, "ts", scale=1.0 / 60.0),
        maxAltitudeFt=_max_value(flight, "altitude"),
        finalMassKg=_last_value(flight, "mass"),
        solverStatus=solver_stats.get("return_status"),
        iterationCount=_to_int(solver_stats.get("iter_count")),
        objectiveValue=_finite_or_none(getattr(optimizer, "objective_value", None)),
    )


def _sample_points(flight: Any, *, max_samples: int) -> list[TrajectoryPoint]:
    if len(flight) <= 0:
        return []

    stride = max(1, math.ceil(len(flight) / max(max_samples, 1)))
    sample_indices = list(range(0, len(flight), stride))
    if sample_indices[-1] != len(flight) - 1:
        sample_indices.append(len(flight) - 1)
    sampled = flight.iloc[sample_indices]

    points: list[TrajectoryPoint] = []
    for _idx, row in sampled.iterrows():
        points.append(
            TrajectoryPoint(
                ts=_row_value(row, "ts"),
                latitude=_row_value(row, "latitude"),
                longitude=_row_value(row, "longitude"),
                altitudeFt=_row_value(row, "altitude"),
                mach=_row_value(row, "mach"),
                tasKt=_row_value(row, "tas"),
                verticalRateFpm=_row_value(row, "vertical_rate"),
                headingDeg=_row_value(row, "heading"),
                massKg=_row_value(row, "mass"),
                fuelFlowKgS=_row_value(row, "fuelflow"),
                fuelCostKg=_row_value(row, "fuel_cost"),
                gridCost=_row_value(row, "grid_cost"),
            )
        )

    return points


def _solver_stats(optimizer: Any) -> dict[str, Any]:
    solver = getattr(optimizer, "solver", None)
    if solver is None or not hasattr(solver, "stats"):
        return {}
    try:
        return dict(solver.stats())
    except Exception:
        return {}


def _series_sum(flight: Any, column: str) -> float | None:
    if column not in flight:
        return None
    return _finite_or_none(flight[column].sum())


def _mass_burn(flight: Any) -> float | None:
    first = _first_value(flight, "mass")
    last = _last_value(flight, "mass")
    if first is None or last is None:
        return None
    return round(first - last, 3)


def _max_value(flight: Any, column: str, *, scale: float = 1.0) -> float | None:
    if column not in flight:
        return None
    value = _finite_or_none(flight[column].max())
    return round(value * scale, 3) if value is not None else None


def _first_value(flight: Any, column: str) -> float | None:
    if column not in flight or len(flight) == 0:
        return None
    return _finite_or_none(flight[column].iloc[0])


def _last_value(flight: Any, column: str) -> float | None:
    if column not in flight or len(flight) == 0:
        return None
    return _finite_or_none(flight[column].iloc[-1])


def _row_value(row: Any, column: str) -> float | None:
    if column not in row:
        return None
    return _finite_or_none(row[column])


def _finite_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return round(result, 6)


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
