from __future__ import annotations

from fastapi import APIRouter

from optimizer.api.api_models import OptimizeRequest, OptimizeResponse


router = APIRouter(prefix="/api", tags=["optimization"])

_service = None


@router.post("/optimize", response_model=OptimizeResponse)
def optimize(request: OptimizeRequest) -> OptimizeResponse:
    return _get_service().optimize(request)


def _get_service():
    global _service
    if _service is None:
        from optimizer.app.ci_optimization_service import CiOptimizationService

        _service = CiOptimizationService()
    return _service
