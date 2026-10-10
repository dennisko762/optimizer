"""Operator-editable bridge settings, persisted to the gitignored ``.env``.

Why this module exists
----------------------
SimBrief (``SIMBRIEF_USER``) and Navigraph (``NAVIGRAPH_CLIENT_ID`` /
``NAVIGRAPH_CLIENT_SECRET``) were environment-only, so a pilot running the
bridge had no way to configure them: the EFB could only report "not
configured". This module adds the single write path, used by the EFB
Settings screen (``crew_platform/settings_routes.py``).

Rules this module enforces
--------------------------
- **Secret values never leave the process.** :func:`settings_state` reports
  booleans and the SimBrief *handle* only (a pilot username, not a
  credential). Client secrets and tokens are write-only: there is no code
  path that returns or logs them, not even masked.
- **The ``.env`` file is edited, not rewritten.** Unrelated keys, comments,
  blank lines, ordering and the file's newline style are preserved; only
  the assignment lines for the keys being set are replaced (and new keys
  appended). The write is atomic (temp file + ``os.replace``) and
  serialised by a process lock, so a crash or a concurrent save cannot
  truncate or interleave the file.
- **Values are validated before they are written**, with character sets
  narrow enough that a value can never break ``.env`` parsing (no
  newlines, quotes, ``#`` or backslashes) — which is also why no escaping
  scheme is needed, matching the deliberately simple stdlib loader in
  ``optimizer/api/app.py::_load_dotenv``.
- **``os.environ`` is updated in the same call**, so a saved setting takes
  effect without restarting the bridge, while the ``.env`` write is what
  makes it survive a restart.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: Repository root (…/crew_platform/settings.py -> …/).
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Serialises read-modify-write cycles on the env file within this process.
_WRITE_LOCK = threading.Lock()


class SettingsValidationError(ValueError):
    """One or more submitted settings are not acceptable.

    ``errors`` maps the submitted field name to an actionable message.
    Never carries a submitted value, so it is safe to surface and log.
    """

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = dict(errors)
        super().__init__("; ".join(f"{k}: {v}" for k, v in sorted(self.errors.items())))


class SettingsWriteError(RuntimeError):
    """The env file could not be written (permissions, read-only disk…)."""


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SettingField:
    """One operator-editable environment variable."""

    name: str
    env_key: str
    label: str
    #: True for credential material: write-only, never returned or logged.
    secret: bool
    max_len: int
    #: Allowed value shape. Deliberately excludes whitespace (except where
    #: ``allow_spaces``), quotes, ``#`` and backslashes so the written line
    #: can never confuse the ``.env`` loader.
    pattern: re.Pattern[str]
    hint: str
    allow_spaces: bool = False


_TOKEN = r"[A-Za-z0-9][A-Za-z0-9._~@-]*"

SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField(
        name="simbrief_user",
        env_key="SIMBRIEF_USER",
        label="SimBrief username or pilot ID",
        secret=False,
        max_len=64,
        pattern=re.compile(rf"^{_TOKEN}$"),
        hint=(
            "Your SimBrief account username (or the numeric SimBrief pilot ID). "
            "Letters, digits and . _ - @ ~ only."
        ),
    ),
    SettingField(
        name="navigraph_client_id",
        env_key="NAVIGRAPH_CLIENT_ID",
        label="Navigraph client ID",
        secret=False,
        max_len=128,
        pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]*$"),
        hint=(
            "Create an application at the Navigraph developer portal "
            "(https://developers.navigraph.com) and paste its client ID here."
        ),
    ),
    SettingField(
        name="navigraph_client_secret",
        env_key="NAVIGRAPH_CLIENT_SECRET",
        label="Navigraph client secret",
        secret=True,
        max_len=512,
        pattern=re.compile(r"^[A-Za-z0-9._~+/=:@-]+$"),
        hint=(
            "The client secret issued alongside the client ID. Stored in the "
            "bridge's gitignored .env and never shown again."
        ),
    ),
    SettingField(
        name="navigraph_scopes",
        env_key="NAVIGRAPH_SCOPES",
        label="Navigraph OAuth scopes",
        secret=False,
        max_len=200,
        pattern=re.compile(r"^[A-Za-z0-9_:-]+( [A-Za-z0-9_:-]+)*$"),
        hint="Space-separated scope list. Leave blank to use the default set.",
        allow_spaces=True,
    ),
)

FIELDS_BY_NAME: dict[str, SettingField] = {f.name: f for f in SETTING_FIELDS}

#: Field metadata for the UI — labels and hints only, never values.
def field_catalog() -> list[dict[str, Any]]:
    """Describe the editable fields (no values) for the Settings screen."""
    return [
        {
            "name": f.name,
            "env_key": f.env_key,
            "label": f.label,
            "secret": f.secret,
            "max_len": f.max_len,
            "hint": f.hint,
        }
        for f in SETTING_FIELDS
    ]


# ---------------------------------------------------------------------------
# Env file location
# ---------------------------------------------------------------------------


def env_file_path() -> Path:
    """The ``.env`` the bridge loads and this module edits.

    ``EFB_ENV_FILE`` wins (the same override ``_load_dotenv`` honours), so a
    packaged build or a test can point the pair at one file.
    """
    override = (os.environ.get("EFB_ENV_FILE") or "").strip()
    if override:
        return Path(override)
    return _REPO_ROOT / ".env"


def _env_file_writable(path: Path) -> bool:
    """Whether a save would succeed, without writing anything."""
    if path.exists():
        return os.access(path, os.W_OK)
    parent = path.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_settings(submitted: dict[str, Optional[str]]) -> dict[str, str]:
    """Validate submitted field values and return ``{env_key: value}``.

    ``None`` means "not submitted" and is skipped. Raises
    :class:`SettingsValidationError` listing every rejected field, so the
    UI can label all of them at once. Error messages never echo a value.
    """
    errors: dict[str, str] = {}
    updates: dict[str, str] = {}

    for name, raw in submitted.items():
        if raw is None:
            continue
        field = FIELDS_BY_NAME.get(name)
        if field is None:
            errors[name] = "Unknown setting."
            continue
        value = raw.strip()
        if not value:
            errors[name] = f"{field.label} must not be empty."
            continue
        if len(value) > field.max_len:
            errors[name] = (
                f"{field.label} is too long (maximum {field.max_len} characters)."
            )
            continue
        if not field.pattern.match(value):
            errors[name] = (
                f"{field.label} contains characters that are not allowed. {field.hint}"
            )
            continue
        updates[field.env_key] = value

    if errors:
        raise SettingsValidationError(errors)
    return updates


# ---------------------------------------------------------------------------
# .env read / modify / write
# ---------------------------------------------------------------------------


def _assignment_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")


def _needs_quoting(value: str) -> bool:
    return any(ch.isspace() for ch in value)


def _render_line(key: str, value: str) -> str:
    # Validation guarantees no quote, '#', backslash or newline is present,
    # so a plain double-quote wrapper round a value with spaces round-trips
    # through the loader's `.strip('"')` without any escaping scheme.
    return f'{key}="{value}"' if _needs_quoting(value) else f"{key}={value}"


def merge_env_text(original: str, updates: dict[str, str]) -> str:
    """Return ``original`` with ``updates`` applied, everything else intact.

    - An existing (uncommented) assignment for a key is replaced **in
      place**, preserving its position; a commented-out ``#KEY=`` line is
      left alone and the real assignment appended.
    - Unknown keys, comments, blank lines and ordering are untouched.
    - The file's dominant newline style (CRLF vs LF) and its trailing
      newline are preserved.
    """
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    remaining = dict(updates)

    for index, line in enumerate(lines):
        for key in list(remaining):
            if _assignment_re(key).match(line):
                lines[index] = _render_line(key, remaining.pop(key))
                break

    for key, value in remaining.items():
        lines.append(_render_line(key, value))

    if not lines:
        return ""
    return newline.join(lines) + newline


def _atomic_write(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` atomically, 0600 where supported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            # Best effort — Windows ACLs do not map onto POSIX modes.
            pass
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def apply_settings(submitted: dict[str, Optional[str]]) -> list[str]:
    """Validate, persist to ``.env`` and apply to ``os.environ``.

    Returns the environment key names that were written, in sorted order —
    names only, so the return value (and the log line derived from it) can
    never carry a secret. Raises :class:`SettingsValidationError` or
    :class:`SettingsWriteError`.
    """
    updates = validate_settings(submitted)
    if not updates:
        raise SettingsValidationError(
            {"__root__": "No settings were supplied."}
        )

    path = env_file_path()
    with _WRITE_LOCK:
        try:
            original = path.read_text(encoding="utf-8") if path.is_file() else ""
            _atomic_write(path, merge_env_text(original, updates))
        except OSError as exc:
            raise SettingsWriteError(
                f"Could not write the bridge settings file: {exc.strerror or exc}"
            ) from exc
        # Only after the durable write succeeds, so a failed save does not
        # leave the process configured differently from the file.
        os.environ.update(updates)

    written = sorted(updates)
    # Key names only. No value is logged at any level, by construction.
    logger.info("bridge settings updated: %s", ", ".join(written))
    return written


# ---------------------------------------------------------------------------
# State (safe to serialise)
# ---------------------------------------------------------------------------


def settings_state() -> dict[str, Any]:
    """Configuration state for the EFB Settings screen.

    Booleans plus the SimBrief handle only. ``NAVIGRAPH_CLIENT_ID`` is
    reported as a boolean rather than echoed: the UI only needs to know
    whether it is set, and a value that is never returned cannot leak.
    """
    from crew_platform.navigraph.config import DEFAULT_SCOPES

    simbrief_user = (os.environ.get("SIMBRIEF_USER") or "").strip()
    client_id = (os.environ.get("NAVIGRAPH_CLIENT_ID") or "").strip()
    client_secret = (os.environ.get("NAVIGRAPH_CLIENT_SECRET") or "").strip()
    access_token = (os.environ.get("NAVIGRAPH_ACCESS_TOKEN") or "").strip()
    scopes = (os.environ.get("NAVIGRAPH_SCOPES") or "").strip() or DEFAULT_SCOPES

    path = env_file_path()
    return {
        "simbrief": {
            "configured": bool(simbrief_user),
            # A SimBrief username is a public pilot handle, not a secret —
            # it is returned so the crew can see and correct what is set.
            "user": simbrief_user or None,
        },
        "navigraph": {
            "client_id_set": bool(client_id),
            "client_secret_set": bool(client_secret),
            "access_token_injected": bool(access_token),
            "configured": bool((client_id and client_secret) or access_token),
            "scopes": scopes.split(),
        },
        "env_file": {
            "exists": path.is_file(),
            "writable": _env_file_writable(path),
        },
        "fields": field_catalog(),
    }
