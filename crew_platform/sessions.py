"""Session management for the crew platform.

The bridge holds auth tokens; browser clients get only an opaque
session id. Sessions are in-memory and ephemeral — they are not
persisted across bridge restarts.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Optional

from crew_platform.boarding import BoardingState
from crew_platform.edesk import CheckInRecord
from crew_platform.vamsys_pilot_auth import PilotIdentity, PilotTokens


@dataclass
class CrewSession:
    """An authenticated crew session."""

    session_id: str
    provider_id: str
    pilot: Optional[PilotIdentity] = None
    tokens: Optional[PilotTokens] = None
    checkin: Optional[CheckInRecord] = None
    selected_flight_id: Optional[str] = None
    boarding: Optional[BoardingState] = None
    local: bool = False  # local (offline) session: no vAMSYS OAuth
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    @property
    def is_authenticated(self) -> bool:
        if self.local:
            return True
        return self.tokens is not None and not self.tokens.expired

    def touch(self) -> None:
        self.last_activity = time.time()


class SessionStore:
    """In-memory session store. Browser clients see only session_id."""

    def __init__(self, session_timeout_seconds: float = 3600 * 4) -> None:
        self._sessions: dict[str, CrewSession] = {}
        self._timeout = session_timeout_seconds

    def create(self, provider_id: str) -> CrewSession:
        """Create a new session for the given provider."""
        session_id = secrets.token_urlsafe(32)
        session = CrewSession(session_id=session_id, provider_id=provider_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[CrewSession]:
        """Get a session by id, or None if expired/missing."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() - session.last_activity > self._timeout:
            del self._sessions[session_id]
            return None
        session.touch()
        return session

    def remove(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        return self._sessions.pop(session_id, None) is not None

    def cleanup_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        now = time.time()
        expired = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_activity > self._timeout
        ]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)
