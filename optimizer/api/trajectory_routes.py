from __future__ import annotations

from fastapi import APIRouter, HTTPException

from trajectory_engine.opentop_adapter import dependency_status, run_trajectory_optimization
from trajectory_engine.trajectory_models import (
    TrajectoryOptimizeRequest,
    TrajectoryOptimizeResponse,
    TrajectoryPhase,
)


router = APIRouter(prefix="/api/trajectory", tags=["trajectory"])


@router.get("/status")
def trajectory_status() -> dict[str, object]:
    deps = dependency_status()
    return {
        "available": bool(deps.get("opentop") and deps.get("casadi") and deps.get("pandas")),
        "dependencies": deps,
    }


@router.post("/optimize", response_model=TrajectoryOptimizeResponse)
def optimize_trajectory(request: TrajectoryOptimizeRequest) -> TrajectoryOptimizeResponse:
    try:
        return run_trajectory_optimization(request)
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Trajectory optimization failed: {exc}") from exc


@router.post("/opticlimb", response_model=TrajectoryOptimizeResponse)
def opticlimb(request: TrajectoryOptimizeRequest) -> TrajectoryOptimizeResponse:
    return optimize_trajectory(_with_phase(request, TrajectoryPhase.CLIMB))


@router.post("/optcruise", response_model=TrajectoryOptimizeResponse)
def optcruise(request: TrajectoryOptimizeRequest) -> TrajectoryOptimizeResponse:
    return optimize_trajectory(_with_phase(request, TrajectoryPhase.CRUISE))


@router.post("/optdescend", response_model=TrajectoryOptimizeResponse)
def optdescend(request: TrajectoryOptimizeRequest) -> TrajectoryOptimizeResponse:
    return optimize_trajectory(_with_phase(request, TrajectoryPhase.DESCENT))


def _with_phase(
    request: TrajectoryOptimizeRequest,
    phase: TrajectoryPhase,
) -> TrajectoryOptimizeRequest:
    if hasattr(request, "model_copy"):
        return request.model_copy(update={"phase": phase})
    return request.copy(update={"phase": phase})

