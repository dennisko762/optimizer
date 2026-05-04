from __future__ import annotations

from fastapi import APIRouter

from optimizer.api.api_models import OptimizeRequest, OptimizeResponse
from optimizer.api.route_profile_cache import get_latest_cruise_segments


router = APIRouter(prefix="/api", tags=["optimization"])

_service = None


@router.post("/optimize", response_model=OptimizeResponse)
def optimize(request: OptimizeRequest) -> OptimizeResponse:
    if not request.flight_state.cruise_segments:
        segments = get_latest_cruise_segments(
            remaining_distance_nm=request.flight_state.remaining_distance_nm
        )
        if segments:
            request = _with_cruise_segments(request, segments)

    return _get_service().optimize(request)


def _with_cruise_segments(request: OptimizeRequest, segments: list[dict[str, float]]) -> OptimizeRequest:
    if hasattr(request, "model_dump"):
        data = request.model_dump(by_alias=True)
    else:
        data = request.dict(by_alias=True)

    data.setdefault("flightState", {})["cruiseSegments"] = segments

    if hasattr(OptimizeRequest, "model_validate"):
        return OptimizeRequest.model_validate(data)

    return OptimizeRequest.parse_obj(data)


def _get_service():
    global _service
    if _service is None:
        from optimizer.app.ci_optimization_service import CiOptimizationService

        _service = CiOptimizationService()
    return _service
