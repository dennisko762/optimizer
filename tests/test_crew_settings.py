"""Tests for the EFB settings read/write API (card t_97dcde51).

Covers the three things that make this feature safe rather than merely
working:

- ``.env`` is *edited*: unrelated keys, comments, blank lines, ordering and
  the newline style survive a save, and the write is atomic.
- validation rejects anything that could break ``.env`` parsing or that is
  obviously not a SimBrief handle, with actionable per-field errors.
- no secret value is ever returned to a client or written to a log record,
  and the write endpoint is not reachable unauthenticated.

No real credential appears anywhere in this file: the "secrets" below are
obvious local placeholders.
"""

from __future__ import annotations

import logging
import os

import pytest
from fastapi.testclient import TestClient

from crew_platform import settings as settings_module
from crew_platform.settings import (
    SettingsValidationError,
    apply_settings,
    merge_env_text,
    settings_state,
    validate_settings,
)
from optimizer.api.app import app

# Obvious placeholders — not credentials.
PLACEHOLDER_CLIENT_ID = "efb-test-client-id"
PLACEHOLDER_CLIENT_SECRET = "not-a-real-secret-0000"

SETTING_ENV_KEYS = (
    "SIMBRIEF_USER",
    "NAVIGRAPH_CLIENT_ID",
    "NAVIGRAPH_CLIENT_SECRET",
    "NAVIGRAPH_SCOPES",
    "NAVIGRAPH_ACCESS_TOKEN",
    "EFB_SETTINGS_ALLOW_REMOTE",
)


@pytest.fixture
def env_file(tmp_path):
    """Point the settings module at a throwaway .env and isolate the env.

    ``apply_settings`` writes to ``os.environ`` by design, so the affected
    keys are saved and restored explicitly here — otherwise a saved
    ``SIMBRIEF_USER`` would leak into unrelated tests in the same process.
    """
    from crew_platform.navigraph.client import reset_client

    path = tmp_path / ".env"
    tracked = SETTING_ENV_KEYS + ("EFB_ENV_FILE",)
    saved = {key: os.environ.get(key) for key in tracked}
    for key in SETTING_ENV_KEYS:
        os.environ.pop(key, None)
    os.environ["EFB_ENV_FILE"] = str(path)
    try:
        yield path
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        reset_client()


@pytest.fixture
def client(env_file, monkeypatch):
    """TestClient with a real local crew session and remote writes allowed.

    ``TestClient`` reports a non-loopback client host ("testclient"), so the
    loopback guard has to be opted out of explicitly — which is also what
    proves the guard exists (see the deny test below, which does not).
    """
    monkeypatch.setenv("EFB_SETTINGS_ALLOW_REMOTE", "1")
    return TestClient(app)


def _session_id(client: TestClient) -> str:
    resp = client.post(
        "/api/crew/session/local",
        json={"provider_id": "qatarvirtual", "display_name": "Local Pilot"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


# ======================================================================
# .env merging
# ======================================================================


class TestEnvMerge:
    def test_existing_key_replaced_in_place_and_others_preserved(self):
        original = (
            "# bridge configuration\n"
            "CREW_PLATFORM_SESSION_SECRET=keep-me\n"
            "\n"
            "# vAMSYS\n"
            "VAMSYS_PILOT_CLIENT_ID=abc\n"
            "SIMBRIEF_USER=oldhandle\n"
            "VAMSYS_REDIRECT_URI=https://example.invalid/cb\n"
        )
        merged = merge_env_text(original, {"SIMBRIEF_USER": "newhandle"})
        assert merged == original.replace("oldhandle", "newhandle")
        # Position preserved, nothing else touched.
        assert merged.splitlines()[0] == "# bridge configuration"
        assert "CREW_PLATFORM_SESSION_SECRET=keep-me" in merged
        assert merged.splitlines()[-1].startswith("VAMSYS_REDIRECT_URI=")

    def test_new_keys_appended_without_disturbing_the_file(self):
        original = "VAMSYS_PILOT_CLIENT_ID=abc\n"
        merged = merge_env_text(
            original,
            {
                "NAVIGRAPH_CLIENT_ID": PLACEHOLDER_CLIENT_ID,
                "NAVIGRAPH_CLIENT_SECRET": PLACEHOLDER_CLIENT_SECRET,
            },
        )
        lines = merged.splitlines()
        assert lines[0] == "VAMSYS_PILOT_CLIENT_ID=abc"
        assert f"NAVIGRAPH_CLIENT_ID={PLACEHOLDER_CLIENT_ID}" in lines
        assert f"NAVIGRAPH_CLIENT_SECRET={PLACEHOLDER_CLIENT_SECRET}" in lines
        assert merged.endswith("\n")

    def test_commented_assignment_is_left_alone(self):
        original = "#SIMBRIEF_USER=disabled\n"
        merged = merge_env_text(original, {"SIMBRIEF_USER": "handle"})
        assert "#SIMBRIEF_USER=disabled" in merged
        assert "SIMBRIEF_USER=handle" in merged.splitlines()

    def test_export_prefixed_assignment_is_replaced(self):
        merged = merge_env_text("export SIMBRIEF_USER=old\n", {"SIMBRIEF_USER": "new"})
        assert merged == "SIMBRIEF_USER=new\n"

    def test_crlf_newlines_are_preserved(self):
        original = "A=1\r\nSIMBRIEF_USER=old\r\n"
        merged = merge_env_text(original, {"SIMBRIEF_USER": "new"})
        assert merged == "A=1\r\nSIMBRIEF_USER=new\r\n"

    def test_empty_file_and_no_updates_are_both_safe(self):
        assert merge_env_text("", {}) == ""
        assert merge_env_text("", {"SIMBRIEF_USER": "x"}) == "SIMBRIEF_USER=x\n"

    def test_space_bearing_values_are_quoted_for_the_loader(self):
        merged = merge_env_text("", {"NAVIGRAPH_SCOPES": "openid charts"})
        assert merged == 'NAVIGRAPH_SCOPES="openid charts"\n'

    def test_quoted_value_round_trips_through_the_bridge_loader(self, env_file):
        """What this module writes is what ``_load_dotenv`` reads back."""
        from optimizer.api.app import _load_dotenv

        env_file.write_text(
            merge_env_text("", {"NAVIGRAPH_SCOPES": "openid charts tiles"}),
            encoding="utf-8",
        )
        _load_dotenv()
        assert os.environ["NAVIGRAPH_SCOPES"] == "openid charts tiles"


# ======================================================================
# Validation
# ======================================================================


class TestValidation:
    def test_omitted_fields_are_skipped(self):
        assert validate_settings({"simbrief_user": None}) == {}

    def test_valid_values_map_to_env_keys(self):
        updates = validate_settings(
            {
                "simbrief_user": "  pilot.handle  ",
                "navigraph_client_id": PLACEHOLDER_CLIENT_ID,
                "navigraph_scopes": "openid charts",
            }
        )
        assert updates == {
            "SIMBRIEF_USER": "pilot.handle",
            "NAVIGRAPH_CLIENT_ID": PLACEHOLDER_CLIENT_ID,
            "NAVIGRAPH_SCOPES": "openid charts",
        }

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "has space",
            'quote"inside',
            "hash#inside",
            "new\nline",
            "back\\slash",
            "-leading-dash",
            "x" * 65,
        ],
    )
    def test_malformed_simbrief_handles_are_rejected(self, value):
        with pytest.raises(SettingsValidationError) as excinfo:
            validate_settings({"simbrief_user": value})
        errors = excinfo.value.errors
        assert set(errors) == {"simbrief_user"}
        # The error explains the problem without echoing the value.
        assert value.strip() not in errors["simbrief_user"] or not value.strip()

    def test_unknown_setting_is_rejected(self):
        with pytest.raises(SettingsValidationError) as excinfo:
            validate_settings({"session_secret": "x"})
        assert excinfo.value.errors == {"session_secret": "Unknown setting."}

    def test_every_rejected_field_is_reported_together(self):
        with pytest.raises(SettingsValidationError) as excinfo:
            validate_settings({"simbrief_user": "", "navigraph_client_id": "a b"})
        assert set(excinfo.value.errors) == {"simbrief_user", "navigraph_client_id"}

    def test_error_message_lists_fields(self):
        exc = SettingsValidationError({"simbrief_user": "bad"})
        assert "simbrief_user: bad" in str(exc)


# ======================================================================
# apply_settings
# ======================================================================


class TestApplySettings:
    def test_write_updates_process_env_and_file(self, env_file):
        written = apply_settings({"simbrief_user": "pilot123"})
        assert written == ["SIMBRIEF_USER"]
        assert os.environ["SIMBRIEF_USER"] == "pilot123"
        assert "SIMBRIEF_USER=pilot123" in env_file.read_text(encoding="utf-8")

    def test_write_creates_a_missing_env_file(self, env_file):
        assert not env_file.exists()
        apply_settings({"navigraph_client_id": PLACEHOLDER_CLIENT_ID})
        assert env_file.is_file()

    def test_second_write_preserves_the_first(self, env_file):
        apply_settings({"simbrief_user": "pilot123"})
        apply_settings({"navigraph_client_id": PLACEHOLDER_CLIENT_ID})
        text = env_file.read_text(encoding="utf-8")
        assert "SIMBRIEF_USER=pilot123" in text
        assert f"NAVIGRAPH_CLIENT_ID={PLACEHOLDER_CLIENT_ID}" in text

    def test_nothing_submitted_is_a_validation_error(self, env_file):
        with pytest.raises(SettingsValidationError):
            apply_settings({"simbrief_user": None})
        assert not env_file.exists()

    def test_failed_write_leaves_process_env_untouched(self, env_file, monkeypatch):
        def boom(*_args, **_kwargs):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(settings_module, "_atomic_write", boom)
        with pytest.raises(settings_module.SettingsWriteError):
            apply_settings({"simbrief_user": "pilot123"})
        assert "SIMBRIEF_USER" not in os.environ

    def test_atomic_write_leaves_no_temp_file_on_failure(
        self, env_file, monkeypatch
    ):
        """A failed rename must not litter half-written .env.* files."""

        def boom(*_args, **_kwargs):
            raise OSError(5, "I/O error")

        monkeypatch.setattr(settings_module.os, "replace", boom)
        with pytest.raises(OSError):
            settings_module._atomic_write(env_file, "SIMBRIEF_USER=pilot123\n")
        assert list(env_file.parent.glob(".env.*.tmp")) == []
        assert not env_file.exists()

    def test_secret_values_are_never_logged(self, env_file, caplog):
        with caplog.at_level(logging.DEBUG):
            apply_settings(
                {
                    "navigraph_client_id": PLACEHOLDER_CLIENT_ID,
                    "navigraph_client_secret": PLACEHOLDER_CLIENT_SECRET,
                }
            )
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert PLACEHOLDER_CLIENT_SECRET not in emitted
        # The key name IS logged, so an operator can audit what changed.
        assert "NAVIGRAPH_CLIENT_SECRET" in emitted


# ======================================================================
# settings_state
# ======================================================================


class TestSettingsState:
    def test_unconfigured_state_is_honest(self, env_file):
        state = settings_state()
        assert state["simbrief"] == {"configured": False, "user": None}
        assert state["navigraph"]["client_id_set"] is False
        assert state["navigraph"]["client_secret_set"] is False
        assert state["navigraph"]["configured"] is False
        assert state["navigraph"]["scopes"]  # the default scope list
        assert state["env_file"]["exists"] is False
        assert state["env_file"]["writable"] is True

    def test_configured_state_reports_booleans_not_values(
        self, env_file, monkeypatch
    ):
        monkeypatch.setenv("SIMBRIEF_USER", "pilot123")
        monkeypatch.setenv("NAVIGRAPH_CLIENT_ID", PLACEHOLDER_CLIENT_ID)
        monkeypatch.setenv("NAVIGRAPH_CLIENT_SECRET", PLACEHOLDER_CLIENT_SECRET)
        state = settings_state()
        assert state["simbrief"] == {"configured": True, "user": "pilot123"}
        assert state["navigraph"]["configured"] is True
        # The client id and secret are reported as booleans only.
        assert PLACEHOLDER_CLIENT_ID not in repr(state)
        assert PLACEHOLDER_CLIENT_SECRET not in repr(state)

    def test_injected_access_token_counts_as_configured_but_is_not_returned(
        self, env_file, monkeypatch
    ):
        monkeypatch.setenv("NAVIGRAPH_ACCESS_TOKEN", "injected-token-placeholder")
        state = settings_state()
        assert state["navigraph"]["access_token_injected"] is True
        assert state["navigraph"]["configured"] is True
        assert "injected-token-placeholder" not in repr(state)

    def test_field_catalog_carries_labels_and_hints_only(self, env_file):
        fields = {f["name"]: f for f in settings_state()["fields"]}
        assert fields["navigraph_client_id"]["env_key"] == "NAVIGRAPH_CLIENT_ID"
        assert "developers.navigraph.com" in fields["navigraph_client_id"]["hint"]
        assert fields["navigraph_client_secret"]["secret"] is True
        assert fields["simbrief_user"]["secret"] is False
        for field in fields.values():
            assert "value" not in field

    def test_unwritable_env_file_is_reported(self, env_file, monkeypatch):
        monkeypatch.setattr(settings_module.os, "access", lambda *_: False)
        assert settings_state()["env_file"]["writable"] is False

    def test_env_file_override_is_honoured(self, tmp_path, monkeypatch):
        target = tmp_path / "custom.env"
        monkeypatch.setenv("EFB_ENV_FILE", str(target))
        assert settings_module.env_file_path() == target
        monkeypatch.delenv("EFB_ENV_FILE")
        assert settings_module.env_file_path().name == ".env"


# ======================================================================
# HTTP surface
# ======================================================================


class TestSettingsApi:
    def test_get_requires_a_valid_session(self, client):
        assert client.get("/api/crew/settings?session_id=nope").status_code == 401

    def test_put_requires_a_valid_session(self, client):
        resp = client.put(
            "/api/crew/settings",
            json={"session_id": "nope", "simbrief_user": "pilot123"},
        )
        assert resp.status_code == 401

    def test_get_returns_state_only(self, client):
        session_id = _session_id(client)
        resp = client.get(f"/api/crew/settings?session_id={session_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["simbrief"]["configured"] is False
        assert body["navigraph"]["client_secret_set"] is False
        assert "remote_writes_allowed" in body

    def test_put_persists_and_applies_without_a_restart(self, client, env_file):
        session_id = _session_id(client)
        resp = client.put(
            "/api/crew/settings",
            json={"session_id": session_id, "simbrief_user": "pilot123"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["simbrief"] == {"configured": True, "user": "pilot123"}
        assert resp.json()["updated"] == ["SIMBRIEF_USER"]

        # In-process effect: the SimBrief readiness endpoint flips without a
        # restart, which is what makes the "not configured" error disappear.
        readiness = client.get("/api/simbrief/config/readiness")
        assert readiness.json() == {"configured": True}
        # Durable effect: the value is in the gitignored .env.
        assert "SIMBRIEF_USER=pilot123" in env_file.read_text(encoding="utf-8")

    def test_put_rejects_a_malformed_handle_with_an_actionable_error(self, client):
        session_id = _session_id(client)
        resp = client.put(
            "/api/crew/settings",
            json={"session_id": session_id, "simbrief_user": "   "},
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "simbrief_user" in detail["errors"]
        assert "empty" in detail["errors"]["simbrief_user"].lower()

    def test_put_with_no_settings_is_rejected(self, client):
        session_id = _session_id(client)
        resp = client.put("/api/crew/settings", json={"session_id": session_id})
        assert resp.status_code == 422

    def test_put_reports_a_write_failure_honestly(self, client, monkeypatch):
        session_id = _session_id(client)

        def boom(*_args, **_kwargs):
            raise OSError(13, "Permission denied")

        monkeypatch.setattr(settings_module, "_atomic_write", boom)
        resp = client.put(
            "/api/crew/settings",
            json={"session_id": session_id, "simbrief_user": "pilot123"},
        )
        assert resp.status_code == 500
        assert "Permission denied" in resp.json()["detail"]

    def test_navigraph_write_rebuilds_the_client_so_status_flips(
        self, client, env_file
    ):
        from crew_platform.navigraph.client import get_client, reset_client

        reset_client()
        assert get_client().config.configured is False

        session_id = _session_id(client)
        resp = client.put(
            "/api/crew/settings",
            json={
                "session_id": session_id,
                "navigraph_client_id": PLACEHOLDER_CLIENT_ID,
                "navigraph_client_secret": PLACEHOLDER_CLIENT_SECRET,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["navigraph"]["configured"] is True
        # The singleton was dropped, so the new credentials are live.
        assert get_client().config.configured is True
        reset_client()

    def test_no_secret_is_ever_returned_by_the_settings_endpoints(
        self, client, env_file
    ):
        session_id = _session_id(client)
        put = client.put(
            "/api/crew/settings",
            json={
                "session_id": session_id,
                "simbrief_user": "pilot123",
                "navigraph_client_id": PLACEHOLDER_CLIENT_ID,
                "navigraph_client_secret": PLACEHOLDER_CLIENT_SECRET,
                "navigraph_scopes": "openid charts",
            },
        )
        get = client.get(f"/api/crew/settings?session_id={session_id}")
        for resp in (put, get):
            raw = resp.text
            assert PLACEHOLDER_CLIENT_SECRET not in raw
            assert PLACEHOLDER_CLIENT_ID not in raw
            assert "client_secret\":" not in raw.replace(" ", "")
        # Only the boolean survives.
        assert get.json()["navigraph"]["client_secret_set"] is True

    def test_writes_do_not_log_secret_values(self, client, env_file, caplog):
        session_id = _session_id(client)
        with caplog.at_level(logging.DEBUG):
            client.put(
                "/api/crew/settings",
                json={
                    "session_id": session_id,
                    "navigraph_client_secret": PLACEHOLDER_CLIENT_SECRET,
                },
            )
        emitted = "\n".join(record.getMessage() for record in caplog.records)
        assert PLACEHOLDER_CLIENT_SECRET not in emitted


class TestRemoteWriteGuard:
    """The bridge binds 0.0.0.0, so a LAN host must not be able to write."""

    def test_non_loopback_write_is_refused_by_default(self, env_file):
        local = TestClient(app)  # no EFB_SETTINGS_ALLOW_REMOTE
        session_id = _session_id(local)
        resp = local.put(
            "/api/crew/settings",
            json={"session_id": session_id, "simbrief_user": "pilot123"},
        )
        assert resp.status_code == 403
        assert "EFB_SETTINGS_ALLOW_REMOTE" in resp.json()["detail"]
        assert not env_file.exists()

    def test_loopback_write_is_allowed_without_the_opt_in(self, env_file):
        local = TestClient(app, client=("127.0.0.1", 50000))
        session_id = _session_id(local)
        resp = local.put(
            "/api/crew/settings",
            json={"session_id": session_id, "simbrief_user": "pilot123"},
        )
        assert resp.status_code == 200, resp.text

    @pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
    def test_opt_in_values(self, env_file, monkeypatch, value):
        from crew_platform.settings_routes import remote_writes_allowed

        monkeypatch.setenv("EFB_SETTINGS_ALLOW_REMOTE", value)
        assert remote_writes_allowed() is True

    @pytest.mark.parametrize("value", ["", "0", "no", "maybe"])
    def test_non_opt_in_values(self, env_file, monkeypatch, value):
        from crew_platform.settings_routes import remote_writes_allowed

        monkeypatch.setenv("EFB_SETTINGS_ALLOW_REMOTE", value)
        assert remote_writes_allowed() is False

    def test_reads_are_not_loopback_restricted(self, env_file):
        local = TestClient(app)
        session_id = _session_id(local)
        resp = local.get(f"/api/crew/settings?session_id={session_id}")
        assert resp.status_code == 200
        assert resp.json()["remote_writes_allowed"] is False


def test_env_file_is_gitignored():
    """A saved setting must never become a commit."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignored]
