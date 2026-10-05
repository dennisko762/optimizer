"""Tests for the crew platform — providers, PKCE, eDesk check-in, sessions."""

from __future__ import annotations

import time
import urllib.parse

import pytest

from crew_platform.providers import (
    EMIRATES_VIRTUAL,
    ETIHAD_VIRTUAL,
    LH_VIRTUAL,
    ProviderProfile,
    ThemeColors,
    get_provider,
    list_providers,
    provider_theme_css_vars,
)
from crew_platform.vamsys_pilot_auth import (
    PKCEError,
    PKCEFlowState,
    PilotIdentity,
    PilotTokens,
    VamsysPilotAuth,
    VamsysPilotClient,
    generate_code_challenge,
    generate_code_verifier,
    generate_state,
)
from crew_platform.edesk import (
    CheckInRecord,
    CheckInValidationResult,
    create_checkin,
    validate_checkin,
)
from crew_platform.sessions import CrewSession, SessionStore
from crew_platform.vamsys_pilot_auth import PilotFlight


# ======================================================================
# Provider / Theme tests
# ======================================================================


class TestProviderRegistry:
    def test_list_providers_returns_three(self):
        providers = list_providers()
        assert len(providers) == 3
        ids = {p.id for p in providers}
        assert ids == {"lhvirtual", "emiratesvirtual", "etihadvirtual"}

    def test_get_provider_lhvirtual(self):
        p = get_provider("lhvirtual")
        assert p.display_name == "Lufthansa Virtual"
        assert p.icao == "DLH"
        assert p.short_code == "LHV"

    def test_get_provider_emiratesvirtual(self):
        p = get_provider("emiratesvirtual")
        assert p.display_name == "Emirates Virtual"
        assert p.icao == "UAE"

    def test_get_provider_etihadvirtual(self):
        p = get_provider("etihadvirtual")
        assert p.display_name == "Etihad Virtual"
        assert p.icao == "ETD"

    def test_get_provider_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown provider"):
            get_provider("unknownairline")

    def test_providers_are_frozen(self):
        p = get_provider("lhvirtual")
        with pytest.raises(AttributeError):
            p.display_name = "Something Else"  # type: ignore

    def test_no_remote_checkin_support(self):
        for p in list_providers():
            assert p.supports_remote_checkin is False


class TestThemeOutput:
    def test_theme_css_vars_complete(self):
        css = provider_theme_css_vars(LH_VIRTUAL)
        expected_keys = {
            "--airline-primary",
            "--airline-accent",
            "--airline-bg",
            "--airline-surface",
            "--airline-text",
            "--airline-text-secondary",
        }
        assert set(css.keys()) == expected_keys

    def test_theme_values_are_color_strings(self):
        for provider in list_providers():
            css = provider_theme_css_vars(provider)
            for key, val in css.items():
                assert isinstance(val, str), f"{key} is not a string"
                assert val.startswith("#"), f"{key}={val} is not a hex color"

    def test_different_providers_have_different_primary(self):
        lh = provider_theme_css_vars(LH_VIRTUAL)
        ek = provider_theme_css_vars(EMIRATES_VIRTUAL)
        ey = provider_theme_css_vars(ETIHAD_VIRTUAL)
        primaries = {lh["--airline-primary"], ek["--airline-primary"], ey["--airline-primary"]}
        assert len(primaries) == 3, "Providers should have distinct primary colors"


# ======================================================================
# PKCE tests
# ======================================================================


class TestPKCEHelpers:
    def test_code_verifier_length(self):
        v = generate_code_verifier(64)
        assert len(v) == 64

    def test_code_verifier_url_safe(self):
        v = generate_code_verifier()
        # URL-safe chars only
        import re
        assert re.match(r"^[A-Za-z0-9_-]+$", v)

    def test_code_verifier_min_length(self):
        with pytest.raises(ValueError, match="43-128"):
            generate_code_verifier(42)

    def test_code_verifier_max_length(self):
        with pytest.raises(ValueError, match="43-128"):
            generate_code_verifier(129)

    def test_code_challenge_is_deterministic(self):
        v = "test_verifier_value_that_is_long_enough_for_spec"
        c1 = generate_code_challenge(v)
        c2 = generate_code_challenge(v)
        assert c1 == c2

    def test_code_challenge_differs_from_verifier(self):
        v = generate_code_verifier()
        c = generate_code_challenge(v)
        assert c != v

    def test_code_challenge_no_padding(self):
        v = generate_code_verifier()
        c = generate_code_challenge(v)
        assert "=" not in c

    def test_state_is_unique(self):
        states = {generate_state() for _ in range(100)}
        assert len(states) == 100

    def test_state_is_url_safe(self):
        import re
        s = generate_state()
        assert re.match(r"^[A-Za-z0-9_-]+$", s)


class TestPKCEFlowState:
    def test_flow_state_creation(self):
        flow = PKCEFlowState(
            state="test_state",
            code_verifier="test_verifier",
            redirect_uri="https://localhost/callback",
        )
        assert flow.state == "test_state"
        assert flow.created_at > 0

    def test_validate_callback_success(self):
        auth = VamsysPilotAuth(
            client_id="test",
            redirect_uri="https://localhost/callback",
        )
        flow = PKCEFlowState(
            state="matching_state",
            code_verifier="test_verifier",
            redirect_uri="https://localhost/callback",
        )
        # Should not raise
        auth.validate_callback(received_state="matching_state", expected_flow=flow)

    def test_validate_callback_mismatch_raises(self):
        auth = VamsysPilotAuth(
            client_id="test",
            redirect_uri="https://localhost/callback",
        )
        flow = PKCEFlowState(
            state="expected_state",
            code_verifier="test_verifier",
            redirect_uri="https://localhost/callback",
        )
        with pytest.raises(PKCEError, match="State mismatch"):
            auth.validate_callback(received_state="wrong_state", expected_flow=flow)


class TestPilotTokens:
    def test_not_expired(self):
        t = PilotTokens(access_token="abc", expires_at=time.time() + 3600)
        assert not t.expired

    def test_expired(self):
        t = PilotTokens(access_token="abc", expires_at=time.time() - 10)
        assert t.expired

    def test_grace_window(self):
        t = PilotTokens(access_token="abc", expires_at=time.time() + 15)
        assert t.expired  # within 30s grace


class TestAuthorizeURL:
    def test_build_authorize_url_contains_pkce_params(self):
        auth = VamsysPilotAuth(
            client_id="12345",
            redirect_uri="https://localhost/callback",
            scopes=["identity:basic"],
        )
        url, flow = auth.build_authorize_url()
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert "state=" in url
        assert "client_id=12345" in url
        assert "response_type=code" in url
        assert flow.code_verifier
        assert flow.state

    def test_default_scopes_are_v3_spec_scopes(self):
        auth = VamsysPilotAuth(
            client_id="12345",
            redirect_uri="https://localhost/callback",
        )
        url, _ = auth.build_authorize_url()
        # v3 spec requires identity:basic at minimum; our default adds
        # the read scopes the crew platform needs.
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        scopes = parsed["scope"][0].split()
        assert "identity:basic" in scopes
        assert "pilot:read" in scopes
        assert "flights:read" in scopes
        # v1-era invented scopes must not leak into the request
        assert "pilot:profile" not in scopes
        assert "pilot:flights" not in scopes

    def test_no_password_in_authorize_url(self):
        auth = VamsysPilotAuth(
            client_id="test",
            redirect_uri="https://localhost/callback",
        )
        url, _ = auth.build_authorize_url()
        assert "password" not in url.lower()
        assert "secret" not in url.lower()


class TestV3ClientContract:
    def test_base_url_is_v3(self):
        assert VamsysPilotClient.BASE_URL == "https://vamsys.io/api/v3/pilot"

    def test_replace_flight_icaos_from_ofp(self):
        from crew_platform.vamsys_pilot_auth import _replace_flight_icaos

        flight = PilotFlight(flight_id="1", callsign="DLH2024")
        ofp = {
            "ofp_data": {
                "departure": {"icao": "EDDF"},
                "arrival": {"icao": "KJFK"},
                "aircraft": {"icao": "B77W"},
            },
            "pdf_url": None,
            "created_at": "2026-09-25T08:00:00Z",
        }
        out = _replace_flight_icaos(flight, ofp)
        assert out.departure_icao == "EDDF"
        assert out.arrival_icao == "KJFK"
        assert out.aircraft_icao == "B77W"
        assert out.has_ofp is True

    def test_replace_flight_icaos_tolerates_missing_ofp(self):
        from crew_platform.vamsys_pilot_auth import _replace_flight_icaos

        flight = PilotFlight(flight_id="1", callsign="DLH2024")
        out = _replace_flight_icaos(flight, {})
        assert out.departure_icao is None
        assert out.has_ofp is True


# ======================================================================
# eDesk check-in validation tests
# ======================================================================


class TestCheckInValidation:
    @pytest.fixture
    def pilot(self):
        return PilotIdentity(
            pilot_id="42",
            crew_id="LHV042",
            callsign="DLH456",
            airline_icao="DLH",
        )

    @pytest.fixture
    def flight(self):
        return PilotFlight(
            flight_id="100",
            flight_number="DLH456",
            departure_icao="EDDF",
            arrival_icao="KJFK",
            aircraft_icao="B77W",
            callsign="DLH456",
        )

    def test_valid_checkin(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_departure="EDDF",
            simbrief_arrival="KJFK",
            simbrief_callsign="DLH456",
            simbrief_aircraft="B77W",
        )
        assert result.valid
        assert len(result.errors) == 0

    def test_airline_mismatch_fails(self, flight):
        pilot_wrong = PilotIdentity(
            pilot_id="42", airline_icao="UAE"
        )
        result = validate_checkin(
            flight=flight,
            pilot=pilot_wrong,
            provider=LH_VIRTUAL,
        )
        assert not result.valid
        assert any("airline" in e.lower() for e in result.errors)

    def test_callsign_prefix_mismatch_fails(self, pilot):
        flight_wrong = PilotFlight(
            flight_id="100",
            callsign="UAE123",
        )
        result = validate_checkin(
            flight=flight_wrong,
            pilot=pilot,
            provider=LH_VIRTUAL,
        )
        assert not result.valid
        assert any("callsign" in e.lower() for e in result.errors)

    def test_route_mismatch_fails(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_departure="EDDM",  # Wrong departure
            simbrief_arrival="KJFK",
        )
        assert not result.valid
        assert any("departure" in e.lower() for e in result.errors)

    def test_arrival_mismatch_fails(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_departure="EDDF",
            simbrief_arrival="EGLL",  # Wrong arrival
        )
        assert not result.valid
        assert any("arrival" in e.lower() for e in result.errors)

    def test_past_date_fails(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_flight_date="2020-01-01",
        )
        assert not result.valid
        assert any("past" in e.lower() for e in result.errors)

    def test_callsign_warning_on_mismatch(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_callsign="DLH999",  # Different from flight callsign
        )
        # Callsign cross-check is a warning, not an error
        assert result.valid
        assert any("callsign" in w.lower() for w in result.warnings)

    def test_aircraft_warning_on_mismatch(self, pilot, flight):
        result = validate_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            simbrief_aircraft="A333",  # Different from flight
        )
        assert result.valid
        assert any("aircraft" in w.lower() for w in result.warnings)

    def test_create_checkin_success(self, pilot, flight):
        validation = CheckInValidationResult(valid=True)
        record = create_checkin(
            flight=flight,
            pilot=pilot,
            provider=LH_VIRTUAL,
            validation=validation,
        )
        assert record.flight_id == "100"
        assert record.pilot_id == "42"
        assert record.provider_id == "lhvirtual"
        assert record.remote_checkin_status == "dispatch_url"
        assert "dispatch-url" in record.remote_checkin_reason.lower()

    def test_create_checkin_with_errors_raises(self, pilot, flight):
        validation = CheckInValidationResult(
            valid=False,
            errors=["airline mismatch"],
        )
        with pytest.raises(ValueError, match="validation errors"):
            create_checkin(
                flight=flight,
                pilot=pilot,
                provider=LH_VIRTUAL,
                validation=validation,
            )


class TestRemoteCheckinPath:
    """Remote check-in uses the documented vAMSYS Pilot API v3 write path.

    v3 documents POST /dispatch-url (Phoenix dispatch) — the pilot opens the
    returned URL to complete the dispatch form. Local validation still
    happens in eDesk; the remote write is the dispatch flow.
    """

    def test_checkin_record_remote_status(self):
        record = CheckInRecord(
            flight_id="1",
            flight_number="DLH1",
            departure_icao="EDDF",
            arrival_icao="KJFK",
            aircraft_icao="B77W",
            callsign="DLH1",
            pilot_id="42",
            provider_id="lhvirtual",
            checked_in_at_utc="2025-01-01T00:00:00+00:00",
            validation=CheckInValidationResult(valid=True),
        )
        assert record.remote_checkin_status == "dispatch_url"
        assert "dispatch-url" in record.remote_checkin_reason.lower()

    def test_no_provider_has_provider_level_remote_checkin(self):
        """Provider-level flag stays False: dispatch-url is a vAMSYS
        platform endpoint, not a per-provider integration."""
        for p in list_providers():
            assert p.supports_remote_checkin is False, (
                f"Provider {p.id} should not claim provider-level remote check-in"
            )


# ======================================================================
# Session tests
# ======================================================================


class TestSessionStore:
    def test_create_and_get(self):
        store = SessionStore()
        session = store.create("lhvirtual")
        assert session.provider_id == "lhvirtual"
        assert session.session_id

        retrieved = store.get(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_returns_none(self):
        store = SessionStore()
        assert store.get("nonexistent") is None

    def test_remove_session(self):
        store = SessionStore()
        session = store.create("emiratesvirtual")
        assert store.remove(session.session_id) is True
        assert store.get(session.session_id) is None

    def test_expired_session_removed(self):
        store = SessionStore(session_timeout_seconds=0.001)
        session = store.create("etihadvirtual")
        time.sleep(0.01)
        assert store.get(session.session_id) is None

    def test_session_not_authenticated_without_tokens(self):
        store = SessionStore()
        session = store.create("lhvirtual")
        assert not session.is_authenticated

    def test_session_authenticated_with_valid_tokens(self):
        store = SessionStore()
        session = store.create("lhvirtual")
        session.tokens = PilotTokens(
            access_token="valid",
            expires_at=time.time() + 3600,
        )
        assert session.is_authenticated

    def test_session_not_authenticated_with_expired_tokens(self):
        store = SessionStore()
        session = store.create("lhvirtual")
        session.tokens = PilotTokens(
            access_token="expired",
            expires_at=time.time() - 100,
        )
        assert not session.is_authenticated

    def test_cleanup_expired(self):
        store = SessionStore(session_timeout_seconds=0.001)
        store.create("lhvirtual")
        store.create("emiratesvirtual")
        time.sleep(0.01)
        removed = store.cleanup_expired()
        assert removed == 2


# ======================================================================
# Authorization failure tests
# ======================================================================


class TestAuthorizationFailures:
    def test_unauthenticated_pilot_client_raises(self):
        from crew_platform.vamsys_pilot_auth import VamsysPilotClient, VamsysPilotAuthError
        expired_tokens = PilotTokens(
            access_token="expired",
            expires_at=time.time() - 100,
        )
        client = VamsysPilotClient(expired_tokens)
        with pytest.raises(VamsysPilotAuthError, match="expired"):
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                client.get_pilot_identity()
            )

    def test_pkce_state_mismatch_is_csrf_protection(self):
        auth = VamsysPilotAuth(
            client_id="test",
            redirect_uri="https://localhost/callback",
        )
        _, flow = auth.build_authorize_url()
        with pytest.raises(PKCEError, match="State mismatch"):
            auth.validate_callback(
                received_state="attacker_state",
                expected_flow=flow,
            )
