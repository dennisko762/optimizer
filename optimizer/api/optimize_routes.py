from __future__ import annotations

from fastapi import APIRouter

from optimizer.api.api_models import OptimizeRequest, OptimizeResponse
from optimizer.app.ci_optimization_service import CiOptimizationService


router = APIRouter(prefix="/api", tags=["optimization"])

service = CiOptimizationService()


@router.post("/optimize", response_model=OptimizeResponse)
def optimize(request: OptimizeRequest) -> OptimizeResponse:
    return service.optimize(request)