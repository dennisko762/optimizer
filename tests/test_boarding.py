"""Tests for crew_platform.boarding — TDD, written before implementation.

Each test was written before the implementation it covers.
"""

from __future__ import annotations

import asyncio
import pytest

from crew_platform.boarding import (
    BoardingState,
    ring_percent,
    compute_weights,
    build_boarding_view_model,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ────────────────────────────────────────────────────────────────
# ring_percent
# ────────────────────────────────────────────────────────────────

class TestRingPercent:
    def test_zero_planned(self):
        assert ring_percent(50, 0) == 0.0

    def test_negative_planned(self):
        assert ring_percent(10, -5) == 0.0

    def test_normal(self):
        assert ring_percent(50, 200) == 25.0

    def test_clamp_over_100(self):
        assert ring_percent(150, 100) == 100.0

    def test_exact_match(self):
        assert ring_percent(142, 142) == 100.0

    def test_zero_current(self):
        assert ring_percent(0, 100) == 0.0


# ────────────────────────────────────────────────────────────────
# compute_weights
# ────────────────────────────────────────────────────────────────

class TestComputeWeights:
    def test_full_computation(self):
        result = compute_weights(
            oew_kg=40000,
            pax_count=142,
            pax_kg_each=84,
            bag_count=120,
            bag_kg_each=15,
            cargo_kg=500,
            fuel_kg=8000,
        )
        assert result["pax_kg"] == 142 * 84
        assert result["bag_kg"] == 120 * 15
        assert result["zfw_kg"] == 40000 + 142 * 84 + 120 * 15 + 500
        assert result["tow_kg"] == result["zfw_kg"] + 8000

    def test_all_zeros(self):
        result = compute_weights()
        assert result["pax_kg"] == 0
        assert result["tow_kg"] == 0


# ────────────────────────────────────────────────────────────────
# build_boarding_view_model
# ────────────────────────────────────────────────────────────────

class TestBuildBoardingViewModel:
    def test_full_model(self):
        flight = {
            "flight_number": "LH2024",
            "departure_icao": "EDDF",
            "arrival_icao": "LEPA",
            "sibt": "12:30",
            "sobt": "10:00",
            "block_time": 150,
        }
        ofp = {
            "pax_count": 142,
            "bag_count": 120,
            "oew_kg": 42000,
            "pax_kg_each": 84,
            "bag_kg_each": 15,
            "cargo_kg": 500,
            "fuel_kg": 8500,
        }
        state = BoardingState(flight_id="f1")
        vm = build_boarding_view_model(flight, ofp, state)

        assert vm["header"]["flight_number"] == "LH2024"
        assert vm["header"]["route"] == "EDDF → LEPA"
        assert vm["pax_ring"]["planned"] == 142
        assert vm["pax_ring"]["current"] == 0
        assert vm["pax_ring"]["percent"] == 0.0
        assert vm["bags_ring"]["planned"] == 120
        assert vm["weights"]["oew_kg"] == 42000

    def test_crew_override_causes_conflict(self):
        ofp = {"pax_count": 142, "bag_count": 120}
        state = BoardingState(flight_id="f1", pax_planned=130)
        vm = build_boarding_view_model(None, ofp, state)

        assert len(vm["conflicts"]) == 1
        assert vm["conflicts"][0]["field"] == "pax_planned"
        assert vm["conflicts"][0]["crew_value"] == 130
        assert vm["conflicts"][0]["simbrief_value"] == 142

    def test_no_conflict_without_crew_override(self):
        ofp = {"pax_count": 142}
        state = BoardingState(flight_id="f1")
        vm = build_boarding_view_model(None, ofp, state)
        assert len(vm["conflicts"]) == 0

    def test_null_inputs(self):
        state = BoardingState(flight_id="f1")
        vm = build_boarding_view_model(None, None, state)
        assert vm["header"]["flight_number"] == ""
        assert vm["header"]["route"] == ""
        assert vm["pax_ring"]["planned"] == 0

    def test_crew_actuals_flow_to_rings(self):
        state = BoardingState(flight_id="f1", pax_ate=118, pax_planned=142)
        vm = build_boarding_view_model(None, None, state)
        assert vm["pax_ring"]["current"] == 118
        assert vm["pax_ring"]["planned"] == 142
        assert vm["pax_ring"]["percent"] > 80


# ────────────────────────────────────────────────────────────────
# API route tests
# ────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """Create a FastAPI app with the crew platform router."""
    from fastapi import FastAPI
    from crew_platform.routes import router
    app = FastAPI()
    app.include_router(router)
    return app


class TestBoardingRoutes:
    def test_get_unknown_session_returns_404(self, app):
        from httpx import AsyncClient, ASGITransport
        from crew_platform.routes import _session_store

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get(
                    "/api/crew/boarding",
                    params={"session_id": "nonexistent", "flight_id": "f1"},
                )

        resp = _run(_do())
        assert resp.status_code == 404

    def test_get_valid_session_returns_200(self, app):
        from httpx import AsyncClient, ASGITransport
        from crew_platform.routes import _session_store

        session = _session_store.create(provider_id="test")

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.get(
                    "/api/crew/boarding",
                    params={"session_id": session.session_id, "flight_id": "f1"},
                )

        resp = _run(_do())
        assert resp.status_code == 200
        data = resp.json()
        assert "header" in data
        assert "pax_ring" in data
        assert "bags_ring" in data
        assert "weights" in data
        _session_store.remove(session.session_id)

    def test_post_update_persists_and_returns_rings(self, app):
        from httpx import AsyncClient, ASGITransport
        from crew_platform.routes import _session_store

        session = _session_store.create(provider_id="test")

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/api/crew/boarding/update",
                    json={
                        "session_id": session.session_id,
                        "flight_id": "f1",
                        "pax_ate": 118,
                        "pax_planned": 142,
                        "bags_loaded": 90,
                        "bags_expected": 120,
                    },
                )
                # Verify persistence
                resp2 = await c.get(
                    "/api/crew/boarding",
                    params={"session_id": session.session_id, "flight_id": "f1"},
                )
                return resp, resp2

        resp, resp2 = _run(_do())
        assert resp.status_code == 200
        data = resp.json()
        assert data["pax_ring"]["current"] == 118
        assert data["pax_ring"]["planned"] == 142
        assert data["bags_ring"]["current"] == 90

        data2 = resp2.json()
        assert data2["pax_ring"]["current"] == 118
        _session_store.remove(session.session_id)

    def test_negative_count_rejected_422(self, app):
        from httpx import AsyncClient, ASGITransport
        from crew_platform.routes import _session_store

        session = _session_store.create(provider_id="test")

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.post(
                    "/api/crew/boarding/update",
                    json={
                        "session_id": session.session_id,
                        "flight_id": "f1",
                        "pax_ate": -5,
                    },
                )

        resp = _run(_do())
        assert resp.status_code == 422
        _session_store.remove(session.session_id)

    def test_unknown_session_update_404(self, app):
        from httpx import AsyncClient, ASGITransport

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                return await c.post(
                    "/api/crew/boarding/update",
                    json={
                        "session_id": "nonexistent",
                        "flight_id": "f1",
                        "pax_ate": 10,
                    },
                )

        resp = _run(_do())
        assert resp.status_code == 404
