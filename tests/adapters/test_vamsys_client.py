"""Tests for vamsys_client.py — token handling, data models, client logic."""

from __future__ import annotations

import time

import pytest

from src.adapters.lufthansa_virtual.vamsys_client import (
    BookedFlight,
    PIREPStatus,
    PilotProfile,
    VamsysAuthError,
    VamsysClient,
    VamsysTokens,
)


# ------------------------------------------------------------------
# Token model tests
# ------------------------------------------------------------------


class TestVamsysTokens:
    def test_not_expired(self):
        t = VamsysTokens(
            access_token="abc",
            expires_at=time.time() + 3600,
        )
        assert not t.expired

    def test_expired(self):
        t = VamsysTokens(
            access_token="abc",
            expires_at=time.time() - 10,
        )
        assert t.expired

    def test_grace_window(self):
        # Within the 30-second grace window → treated as expired
        t = VamsysTokens(
            access_token="abc",
            expires_at=time.time() + 15,
        )
        assert t.expired


# ------------------------------------------------------------------
# Data model construction tests
# ------------------------------------------------------------------


class TestDataModels:
    def test_pilot_profile(self):
        p = PilotProfile(
            pilot_id="42",
            callsign="DLH123",
            rank="Captain",
            hours_total=1500.0,
            airline_icao="DLH",
        )
        assert p.pilot_id == "42"
        assert p.rank == "Captain"

    def test_booked_flight(self):
        b = BookedFlight(
            booking_id="100",
            flight_number="DLH456",
            departure_icao="EDDF",
            arrival_icao="KJFK",
        )
        assert b.departure_icao == "EDDF"
        assert b.status is None  # not set

    def test_pirep_status(self):
        p = PIREPStatus(pirep_id="200", state="accepted")
        assert p.state == "accepted"
        assert p.fuel_used_kg is None


# ------------------------------------------------------------------
# Client construction / validation tests (no network)
# ------------------------------------------------------------------


class TestVamsysClient:
    def test_requires_credentials(self):
        client = VamsysClient(client_id="id", client_secret="secret")
        with pytest.raises(ValueError, match="username.*password.*refresh_token"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                client.authenticate()
            )

    def test_set_tokens(self):
        client = VamsysClient(client_id="id", client_secret="secret")
        tokens = VamsysTokens(
            access_token="tok",
            refresh_token="ref",
            expires_at=time.time() + 3600,
        )
        client.set_tokens(tokens)
        assert client._tokens is not None
        assert client._tokens.access_token == "tok"

    def test_unauthenticated_get_raises(self):
        client = VamsysClient(client_id="id", client_secret="secret")
        with pytest.raises(VamsysAuthError, match="Not authenticated"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                client._get("/test")
            )
