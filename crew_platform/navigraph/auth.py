"""Navigraph OIDC authentication — Device Authorization Flow with PKCE.

Why the device flow: the EFB backend is an in-process flight-simulator
add-on, which is exactly the case Navigraph documents the device flow for
(RFC 8628). The pilot gets a short verification URI, authorises in a
browser, and the backend polls for the token — no password ever reaches
this process.

Endpoints (https://identity.api.navigraph.com):
    POST /connect/deviceauthorization   -> device_code + verification_uri
    POST /connect/token                 -> access/refresh token (poll)
    POST /connect/token (refresh_token) -> rotated token pair
    GET  /connect/userinfo              -> preferred_username

Subscription entitlement is NOT a separate API call: it is the
``subscriptions`` claim inside the access token. ``charts`` in that array
means the user may read real charts; absent it, Navigraph serves demo
imagery for two demo airports only (NZWN, YBBN). The connector surfaces
that distinction instead of silently showing demo data as real.

Refresh tokens are single-use: every refresh must store the NEW refresh
token or the session is lost.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from crew_platform.navigraph.config import IDENTITY_BASE, NavigraphConfig

#: Navigraph demo airports available without a charts subscription.
DEMO_AIRPORTS = ("NZWN", "YBBN")


class NavigraphAuthError(RuntimeError):
    """Raised when Navigraph authentication fails irrecoverably."""


# ---------------------------------------------------------------------------
# PKCE helpers (S256, 43-char verifier per Navigraph docs)
# ---------------------------------------------------------------------------


def generate_code_verifier(length: int = 43) -> str:
    if not (43 <= length <= 128):
        raise ValueError("code_verifier length must be 43-128")
    return secrets.token_urlsafe(96)[:length]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def decode_subscriptions(access_token: Optional[str]) -> list[str]:
    """Extract the ``subscriptions`` claim from a JWT access token.

    The signature is NOT verified here: this is a UI-gating hint only.
    Authorisation is always enforced by Navigraph's servers. An opaque or
    malformed token yields an empty list, which the gate treats as
    "no subscription" — the safe direction.
    """
    if not access_token or access_token.count(".") != 2:
        return []
    payload_b64 = access_token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        claims = json.loads(raw)
    except (ValueError, TypeError):
        return []
    subs = claims.get("subscriptions")
    if isinstance(subs, str):
        return [subs]
    if isinstance(subs, list):
        return [str(s) for s in subs if s]
    return []


def token_expiry(access_token: Optional[str]) -> Optional[float]:
    """Unverified ``exp`` claim of a JWT, or None."""
    if not access_token or access_token.count(".") != 2:
        return None
    payload_b64 = access_token.split(".")[1]
    padding = "=" * (-len(payload_b64) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return float(claims["exp"])
    except (ValueError, TypeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclass
class NavigraphTokens:
    """An access/refresh pair plus the tile-server CloudFront cookies.

    Enroute tiles are not bearer-authenticated: the token endpoint returns
    signed CloudFront cookies alongside the token when the ``tiles`` scope
    is requested. They expire with the access token.
    """

    access_token: str
    refresh_token: Optional[str] = None
    expires_at: float = 0.0
    scopes: list[str] = field(default_factory=list)
    subscriptions: list[str] = field(default_factory=list)
    tile_cookies: dict[str, str] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        # 60 s safety margin — a token that dies mid-request is a 401.
        return self.expires_at > 0 and time.time() >= (self.expires_at - 60)

    def has_subscription(self, name: str) -> bool:
        return name in self.subscriptions

    def has_scope(self, name: str) -> bool:
        """True when the token was granted this OAuth scope.

        An empty scope list means the identity server did not echo
        ``scope`` back; the requested scopes are then unknown, so this
        returns True rather than locking the pilot out of data they may
        well be entitled to. Navigraph's own 401/403 remains the
        authoritative check.
        """
        if not self.scopes:
            return True
        return name in self.scopes

    def redacted(self) -> dict[str, Any]:
        return {
            "access_token_present": bool(self.access_token),
            "refresh_token_present": bool(self.refresh_token),
            "expires_in": max(0, int(self.expires_at - time.time()))
            if self.expires_at
            else None,
            "scopes": list(self.scopes),
            "subscriptions": list(self.subscriptions),
            "tile_cookies_present": bool(self.tile_cookies),
        }


_TILE_COOKIE_NAMES = (
    "CloudFront-Policy",
    "CloudFront-Signature",
    "CloudFront-Key-Pair-Id",
)

def parse_set_cookies(response) -> dict[str, str]:
    """Parse every Set-Cookie for use in Navigraph tile requests."""
    result = {}
    # httpx: response.headers can have repeated keys; use get_list in async, or .raw
    try:
        for cookie in response.headers.get_list("set-cookie"):
            field, _, _ = cookie.partition(";")
            if "=" in field:
                name, value = field.split("=", 1)
                result[name.strip()] = value.strip()
    except AttributeError:
        # fallback for sync response (.raw)
        for key, value in getattr(response, 'raw_headers', []):
            if key.lower() == b'set-cookie':
                cookie = value.decode()
                field, _, _ = cookie.partition(";")
                if "=" in field:
                    name, value = field.split("=", 1)
                    result[name.strip()] = value.strip()
    return result


def _tokens_from_payload(payload: dict[str, Any]) -> NavigraphTokens:
    access = payload.get("access_token")
    if not access:
        raise NavigraphAuthError("token response contained no access_token")
    expires_in = payload.get("expires_in")
    if expires_in:
        try:
            expires_at = time.time() + float(expires_in)
        except (TypeError, ValueError):
            expires_at = time.time() + 3600
    else:
        expires_at = token_expiry(access) or (time.time() + 3600)
    scope_raw = payload.get("scope") or ""
    cookies = {
        name: str(payload[name])
        for name in _TILE_COOKIE_NAMES
        if payload.get(name)
    }
    return NavigraphTokens(
        access_token=access,
        refresh_token=payload.get("refresh_token"),
        expires_at=expires_at,
        scopes=[s for s in str(scope_raw).split() if s],
        subscriptions=decode_subscriptions(access),
        tile_cookies=cookies,
    )


# ---------------------------------------------------------------------------
# Device authorization flow
# ---------------------------------------------------------------------------


@dataclass
class DeviceFlowState:
    """In-flight device authorization, as handed to the pilot."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: Optional[str]
    interval: int
    expires_at: float
    code_verifier: str
    next_poll_at: float = 0.0
    poll_interval: int = 5

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def public(self) -> dict[str, Any]:
        """Fields safe to hand to the UI (no device_code / verifier)."""
        return {
            "user_code": self.user_code,
            "verification_uri": self.verification_uri,
            "verification_uri_complete": self.verification_uri_complete,
            "interval": self.interval,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class NavigraphAuth:
    """Device-flow client for the Navigraph identity server."""

    DEVICE_AUTH_URL = f"{IDENTITY_BASE}/connect/deviceauthorization"
    TOKEN_URL = f"{IDENTITY_BASE}/connect/token"
    USERINFO_URL = f"{IDENTITY_BASE}/connect/userinfo"

    def __init__(self, config: NavigraphConfig, timeout: float = 15.0):
        self.config = config
        self._timeout = timeout
        self._poll_lock = asyncio.Lock()
        self._tile_cookies: dict[str, str] = {}

    # -- flow -------------------------------------------------------------

    async def start_device_flow(self) -> DeviceFlowState:
        if not (self.config.client_id and self.config.client_secret):
            raise NavigraphAuthError(
                "Navigraph client credentials are not configured "
                "(NAVIGRAPH_CLIENT_ID / NAVIGRAPH_CLIENT_SECRET)"
            )
        verifier = generate_code_verifier()
        form = {
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code_challenge": generate_code_challenge(verifier),
            "code_challenge_method": "S256",
            "scope": self.config.scopes,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.DEVICE_AUTH_URL, data=form)
        if resp.status_code >= 400:
            raise NavigraphAuthError(
                f"device authorization failed (HTTP {resp.status_code})"
            )
        payload = resp.json()
        device_code = payload.get("device_code")
        user_code = payload.get("user_code")
        if not device_code or not user_code:
            raise NavigraphAuthError("device authorization response incomplete")
        try:
            expires_in = float(payload.get("expires_in") or 600)
        except (TypeError, ValueError):
            expires_in = 600.0
        try:
            interval = int(payload.get("interval") or 5)
        except (TypeError, ValueError):
            interval = 5
        return DeviceFlowState(
            device_code=device_code,
            user_code=user_code,
            verification_uri=payload.get("verification_uri") or "",
            verification_uri_complete=payload.get("verification_uri_complete"),
            interval=max(1, interval),
            expires_at=time.time() + expires_in,
            code_verifier=verifier,
        )

    async def poll_device_token(
        self, flow: DeviceFlowState
    ) -> tuple[str, Optional[NavigraphTokens]]:
        """Poll the token endpoint once.

        Returns ``(status, tokens)`` where status is one of
        ``authorized`` | ``pending`` | ``slow_down`` | ``expired`` |
        ``denied``. Only ``authorized`` carries tokens. Callers must
        respect ``flow.interval`` between polls (and lengthen it on
        ``slow_down``) — Navigraph rate-limits the token endpoint too.
        """
        async with self._poll_lock:
            if flow.expired:
                return "expired", None
            now = time.time()
            if now < flow.next_poll_at:
                return "pending", None
            flow.next_poll_at = now + max(1, flow.poll_interval)
        form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": flow.device_code,
            "code_verifier": flow.code_verifier,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "scope": self.config.scopes,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.TOKEN_URL, data=form)
        if resp.status_code == 200:
            tokens = _tokens_from_payload(resp.json())
            # If tile cookies delivered via set-cookie, merge those in
            cookies = parse_set_cookies(resp)
            if cookies:
                self._tile_cookies.update({k: v for k, v in cookies.items() if k in _TILE_COOKIE_NAMES})
            self._tile_cookies.update(tokens.tile_cookies)
            tokens.tile_cookies.update(self._tile_cookies)
            self.store_refresh_token(tokens.refresh_token)
            return "authorized", tokens
        try:
            error = str(resp.json().get("error") or "")
        except ValueError:
            error = ""
        if error == "authorization_pending":
            return "pending", None
        if error == "slow_down":
            flow.poll_interval += 5
            flow.next_poll_at = time.time() + flow.poll_interval
            return "slow_down", None
        if error == "expired_token":
            return "expired", None
        if error == "access_denied":
            return "denied", None
        raise NavigraphAuthError(
            f"token exchange failed (HTTP {resp.status_code}"
            f"{', ' + error if error else ''})"
        )

    async def refresh(self, refresh_token: str) -> NavigraphTokens:
        """Exchange a refresh token. The new refresh token is persisted."""
        if not refresh_token:
            raise NavigraphAuthError("no refresh token available")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(self.TOKEN_URL, data=form)
        if resp.status_code >= 400:
            raise NavigraphAuthError(
                f"refresh failed (HTTP {resp.status_code}) — pilot must sign in again"
            )
        tokens = _tokens_from_payload(resp.json())
        # Merge tile cookies from Set-Cookie too, if present
        cookies = parse_set_cookies(resp)
        if cookies:
            self._tile_cookies.update({k: v for k, v in cookies.items() if k in _TILE_COOKIE_NAMES})
        self._tile_cookies.update(tokens.tile_cookies)
        tokens.tile_cookies.update(self._tile_cookies)
        self.store_refresh_token(tokens.refresh_token)
        return tokens

    async def userinfo(self, access_token: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code >= 400:
            raise NavigraphAuthError(f"userinfo failed (HTTP {resp.status_code})")
        return resp.json()

    # -- refresh-token persistence ---------------------------------------

    def load_refresh_token(self) -> Optional[str]:
        """Read the stored refresh token (env wins over the file)."""
        if self.config.refresh_token:
            return self.config.refresh_token
        path = self.config.token_store
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = (json.load(fh).get("refresh_token") or "").strip()
        except (OSError, ValueError, AttributeError):
            return None
        return value or None

    def store_refresh_token(self, refresh_token: Optional[str]) -> None:
        """Persist a rotated refresh token with 0600 permissions.

        No-op when NAVIGRAPH_TOKEN_STORE is unset (memory-only session).
        The store path is operator-chosen and must sit outside the repo;
        nothing here ever writes into the working tree by default.
        """
        path = self.config.token_store
        if not path or not refresh_token:
            return
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = f"{path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"refresh_token": refresh_token}, fh)
            try:
                os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            os.replace(tmp, path)
        except OSError:
            # Losing persistence is survivable; the in-memory token works
            # for the rest of the session.
            pass
