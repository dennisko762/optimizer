"""Tests for operations notifications — weather-change event engine.

TDD: each test written before the implementation it covers.
"""

from __future__ import annotations

import asyncio
import time


from crew_platform.notifications import (
    WeatherSnapshot,
    WeatherProvider,
    NotificationEngine,
    NotificationEvent,
    NotificationFeed,
)


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Stub weather provider for deterministic testing
# ---------------------------------------------------------------------------


class StubWeatherProvider(WeatherProvider):
    """Injectable stub — returns canned snapshots keyed by ICAO."""

    def __init__(self, snapshots: dict[str, WeatherSnapshot] | None = None):
        self._snapshots = snapshots or {}

    def set(self, icao: str, snap: WeatherSnapshot) -> None:
        self._snapshots[icao] = snap

    async def fetch_weather(self, icao: str) -> WeatherSnapshot | None:
        return self._snapshots.get(icao)


def _snap(
    visibility_km: float = 10.0,
    wind_dir_deg: float = 270.0,
    wind_speed_kt: float = 10.0,
    temp_c: float = 15.0,
) -> WeatherSnapshot:
    return WeatherSnapshot(
        visibility_km=visibility_km,
        wind_dir_deg=wind_dir_deg,
        wind_speed_kt=wind_speed_kt,
        temp_c=temp_c,
    )


# ======================================================================
# Change detection
# ======================================================================


class TestChangeDetection:
    """AC-1: change detection emits exactly one event per changed (type, icao)."""

    def test_visibility_drop_emits_event(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))
        assert len(feed.events) == 0  # first poll = baseline

        provider.set("EDDF", _snap(visibility_km=8.0))  # 2 km drop >= 1 km
        _run(engine.poll("EDDF", feed))
        vis_events = [e for e in feed.events if e.type == "visibility" and e.icao == "EDDF"]
        assert len(vis_events) == 1
        assert "visibility" in vis_events[0].summary.lower()

    def test_wind_direction_shift_emits_event(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("KJFK", _snap(wind_dir_deg=180.0))
        _run(engine.poll("KJFK", feed))

        provider.set("KJFK", _snap(wind_dir_deg=215.0))  # 35° >= 30°
        _run(engine.poll("KJFK", feed))
        dir_events = [e for e in feed.events if e.type == "wind_direction"]
        assert len(dir_events) == 1

    def test_wind_speed_change_emits_event(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(wind_speed_kt=10.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(wind_speed_kt=21.0))  # 11 kt >= 10 kt
        _run(engine.poll("EDDF", feed))
        spd_events = [e for e in feed.events if e.type == "wind_speed"]
        assert len(spd_events) == 1

    def test_temperature_change_emits_event(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(temp_c=15.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(temp_c=19.0))  # 4°C >= 3°C
        _run(engine.poll("EDDF", feed))
        tmp_events = [e for e in feed.events if e.type == "temperature"]
        assert len(tmp_events) == 1

    def test_stable_snapshot_emits_nothing(self):
        """Identical snapshot emits nothing."""
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap())
        _run(engine.poll("EDDF", feed))
        _run(engine.poll("EDDF", feed))  # same snapshot
        assert len(feed.events) == 0

    def test_below_threshold_emits_nothing(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(visibility_km=9.5))  # 0.5 km < 1 km
        _run(engine.poll("EDDF", feed))
        assert len(feed.events) == 0

    def test_multiple_changes_emit_multiple_events(self):
        """Each changed parameter type emits its own event."""
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0, wind_speed_kt=10.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(visibility_km=5.0, wind_speed_kt=25.0))
        _run(engine.poll("EDDF", feed))
        types = {e.type for e in feed.events}
        assert "visibility" in types
        assert "wind_speed" in types


# ======================================================================
# Dedup
# ======================================================================


class TestDedup:
    """AC-1: dedup suppresses repeats within 15 min."""

    def test_dedup_suppresses_repeat_within_15min(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(visibility_km=5.0))
        _run(engine.poll("EDDF", feed))
        assert len(feed.events) == 1

        # Re-trigger same (type, icao) — should be suppressed
        provider.set("EDDF", _snap(visibility_km=3.0))
        _run(engine.poll("EDDF", feed))
        vis_events = [e for e in feed.events if e.type == "visibility" and e.icao == "EDDF"]
        assert len(vis_events) == 1  # still 1

    def test_dedup_allows_after_15min(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))

        provider.set("EDDF", _snap(visibility_km=5.0))
        _run(engine.poll("EDDF", feed))
        assert len(feed.events) == 1

        # Simulate time passing (> 15 min)
        engine._dedup_expiry[("visibility", "EDDF")] = time.time() - 1

        provider.set("EDDF", _snap(visibility_km=2.0))
        _run(engine.poll("EDDF", feed))
        vis_events = [e for e in feed.events if e.type == "visibility" and e.icao == "EDDF"]
        assert len(vis_events) == 2

    def test_dedup_different_icao_independent(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        provider.set("KJFK", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))
        _run(engine.poll("KJFK", feed))

        provider.set("EDDF", _snap(visibility_km=5.0))
        provider.set("KJFK", _snap(visibility_km=5.0))
        _run(engine.poll("EDDF", feed))
        _run(engine.poll("KJFK", feed))

        vis_events = [e for e in feed.events if e.type == "visibility"]
        assert len(vis_events) == 2  # one per ICAO


# ======================================================================
# Event structure
# ======================================================================


class TestEventStructure:
    def test_event_has_required_fields(self):
        provider = StubWeatherProvider()
        engine = NotificationEngine(provider=provider)
        feed = NotificationFeed()

        provider.set("EDDF", _snap(visibility_km=10.0))
        _run(engine.poll("EDDF", feed))
        provider.set("EDDF", _snap(visibility_km=5.0))
        _run(engine.poll("EDDF", feed))

        event = feed.events[0]
        assert event.id  # non-empty
        assert event.type == "visibility"
        assert event.icao == "EDDF"
        assert event.summary
        assert event.provenance
        assert event.timestamp > 0
        assert event.observed is not None


# ======================================================================
# NotificationFeed (API-level)
# ======================================================================


class TestNotificationFeed:
    """AC-2: feed ordering and clear."""

    def test_feed_returns_newest_first(self):
        feed = NotificationFeed()
        e1 = NotificationEvent(
            id="1", type="visibility", icao="EDDF",
            summary="vis drop", provenance="test", timestamp=100.0,
            observed={"visibility_km": 5.0},
        )
        e2 = NotificationEvent(
            id="2", type="wind_speed", icao="EDDF",
            summary="wind increase", provenance="test", timestamp=200.0,
            observed={"wind_speed_kt": 25.0},
        )
        feed.events.append(e1)
        feed.events.append(e2)
        ordered = feed.get_events()
        assert ordered[0].id == "2"
        assert ordered[1].id == "1"

    def test_clear_empties_feed(self):
        feed = NotificationFeed()
        feed.events.append(
            NotificationEvent(
                id="1", type="visibility", icao="EDDF",
                summary="vis drop", provenance="test", timestamp=100.0,
                observed={"visibility_km": 5.0},
            )
        )
        assert len(feed.events) == 1
        feed.clear()
        assert len(feed.events) == 0
        assert feed.get_events() == []


# ======================================================================
# API route tests (AC-2)
# ======================================================================


class TestNotificationRoutes:
    """AC-2: API tests for feed, ordering, clear, unknown session."""

    def test_feed_returns_events_with_provenance_and_ordering(self):
        """GET /api/crew/notifications returns events newest-first with provenance."""
        from crew_platform.routes import router, _session_store
        from crew_platform.notifications import get_or_create_feed
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport

        app = FastAPI()
        app.include_router(router)

        session = _session_store.create("lhvirtual")
        session.selected_flight_id = "f1"

        feed = get_or_create_feed(session.session_id)
        feed.events.append(
            NotificationEvent(
                id="a", type="visibility", icao="EDDF",
                summary="Vis drop to 5 km", provenance="open-meteo",
                timestamp=100.0, observed={"visibility_km": 5.0},
            )
        )
        feed.events.append(
            NotificationEvent(
                id="b", type="wind_speed", icao="KJFK",
                summary="Wind increase to 25 kt", provenance="open-meteo",
                timestamp=200.0, observed={"wind_speed_kt": 25.0},
            )
        )

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(
                    "/api/crew/notifications",
                    params={"session_id": session.session_id},
                )

        resp = _run(_do())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == "b"  # newest first
        assert data[1]["id"] == "a"
        assert data[0]["provenance"] == "open-meteo"
        assert "timestamp" in data[0]

        feed.clear()
        _session_store.remove(session.session_id)

    def test_clear_empties_feed_via_api(self):
        """POST /api/crew/notifications/clear empties the feed."""
        from crew_platform.routes import router, _session_store
        from crew_platform.notifications import get_or_create_feed
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport

        app = FastAPI()
        app.include_router(router)

        session = _session_store.create("lhvirtual")
        feed = get_or_create_feed(session.session_id)
        feed.events.append(
            NotificationEvent(
                id="c", type="temperature", icao="EDDF",
                summary="Temp change", provenance="open-meteo",
                timestamp=300.0, observed={"temp_c": 20.0},
            )
        )

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/crew/notifications/clear",
                    params={"session_id": session.session_id},
                )

        resp = _run(_do())
        assert resp.status_code == 200
        assert len(feed.events) == 0

        _session_store.remove(session.session_id)

    def test_unknown_session_returns_404(self):
        """Unknown session_id -> 404."""
        from crew_platform.routes import router
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport

        app = FastAPI()
        app.include_router(router)

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(
                    "/api/crew/notifications",
                    params={"session_id": "nonexistent"},
                )

        resp = _run(_do())
        assert resp.status_code == 404

    def test_clear_unknown_session_returns_404(self):
        from crew_platform.routes import router
        from fastapi import FastAPI
        from httpx import AsyncClient, ASGITransport

        app = FastAPI()
        app.include_router(router)

        async def _do():
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/crew/notifications/clear",
                    params={"session_id": "nonexistent"},
                )

        resp = _run(_do())
        assert resp.status_code == 404
