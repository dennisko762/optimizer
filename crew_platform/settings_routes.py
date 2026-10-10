"""EFB Settings API — read configuration state, write ``.env`` settings.

Surface
-------
``GET  /api/crew/settings?session_id=…``  configuration STATE only
``PUT  /api/crew/settings``               write SimBrief / Navigraph settings

Security posture
----------------
1. **Existing crew gating.** Both endpoints require a live crew session
   from the same :class:`~crew_platform.sessions.SessionStore` the rest of
   the crew platform uses (``crew_platform.routes.get_session_store``). No
   new auth scheme is introduced.
2. **Writes are loopback-only by default.** A crew session can be minted
   by anyone who can reach ``POST /api/crew/session/local``, and the bridge
   binds ``0.0.0.0`` so a tablet can reach it — which would make session
   gating alone equivalent to an unauthenticated write for any host on the
   LAN. The write endpoint therefore additionally requires the request to
   originate from loopback unless the operator explicitly opts in with
   ``EFB_SETTINGS_ALLOW_REMOTE=1``. The refusal is a 403 that says exactly
   that, so configuring from a tablet is a deliberate choice, not a
   silent hole.
3. **No secret ever travels outward.** The response bodies are produced by
   :func:`crew_platform.settings.settings_state`, which emits booleans and
   the SimBrief pilot handle only. Submitted secrets are never echoed,
   never logged and never included in an error message.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from crew_platform.routes import get_session_store
from crew_platform.settings import (
    SettingsValidationError,
    SettingsWriteError,
    apply_settings,
    settings_state,
)

router = APIRouter(prefix="/api/crew/settings", tags=["crew-settings"])

#: Hosts treated as "this machine".
_LOOPBACK_HOSTS = frozenset(
    {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}
)

_REMOTE_WRITE_ENV = "EFB_SETTINGS_ALLOW_REMOTE"

_REMOTE_REFUSED = (
    "Settings can only be changed from the machine running the bridge. "
    "To configure the bridge from a tablet, set "
    f"{_REMOTE_WRITE_ENV}=1 in the bridge environment and restart it — "
    "that opens the write endpoint to every host that can reach this port."
)


class SettingsIn(BaseModel):
    """Submitted settings. Omitted fields are left unchanged."""

    session_id: str = Field(..., min_length=1)
    simbrief_user: Optional[str] = None
    navigraph_client_id: Optional[str] = None
    navigraph_client_secret: Optional[str] = None
    navigraph_scopes: Optional[str] = None

    def submitted(self) -> dict[str, Optional[str]]:
        return {
            "simbrief_user": self.simbrief_user,
            "navigraph_client_id": self.navigraph_client_id,
            "navigraph_client_secret": self.navigraph_client_secret,
            "navigraph_scopes": self.navigraph_scopes,
        }


def remote_writes_allowed() -> bool:
    """Whether non-loopback clients may write settings (opt-in)."""
    return (os.environ.get(_REMOTE_WRITE_ENV) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _require_session(session_id: str) -> None:
    """Reuse the crew platform's own session gate."""
    if get_session_store().get(session_id) is None:
        raise HTTPException(
            status_code=401, detail="Invalid or expired crew session."
        )


def _require_local_write(request: Request) -> None:
    if remote_writes_allowed():
        return
    host = (request.client.host if request.client else "") or ""
    if host not in _LOOPBACK_HOSTS:
        raise HTTPException(status_code=403, detail=_REMOTE_REFUSED)


@router.get("")
async def get_settings(session_id: str = Query(...)) -> dict[str, Any]:
    """Configuration state. Booleans plus the SimBrief handle only."""
    _require_session(session_id)
    state = settings_state()
    state["remote_writes_allowed"] = remote_writes_allowed()
    return state


@router.put("")
async def put_settings(body: SettingsIn, request: Request) -> dict[str, Any]:
    """Persist settings to the gitignored ``.env`` and apply them live."""
    _require_session(body.session_id)
    _require_local_write(request)

    try:
        written = apply_settings(body.submitted())
    except SettingsValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Some settings were rejected.",
                "errors": exc.errors,
            },
        ) from exc
    except SettingsWriteError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Navigraph configuration is read once when its client singleton is
    # built, so the singleton must be dropped for a new client id/secret to
    # take effect without a bridge restart.
    if any(key.startswith("NAVIGRAPH_") for key in written):
        from crew_platform.navigraph.client import reset_client

        reset_client()

    state = settings_state()
    state["remote_writes_allowed"] = remote_writes_allowed()
    state["updated"] = written
    return state
