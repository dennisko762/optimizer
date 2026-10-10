"""M4 Navigraph auth — credential-free coverage of the OIDC connector logic.

Every function in ``crew_platform.navigraph.auth`` that is pure logic
(PKCE helpers, token model, subscription/expiry decoding, cookie and token
payload parsing, device-flow state, refresh-token persistence) is tested
directly. The async ``NavigraphAuth`` transport methods are exercised by
monkeypatching the module's ``httpx.AsyncClient`` with a fake that returns
preset responses — so no live Navigraph call, credential, or secret is
required (the same data-integrity rule the rest of the M4 suite follows:
fixtures/mocks, never fabricated "live" data presented as real).

This file exists to satisfy the changed-code coverage gate for the newly
added ``crew_platform/navigraph/`` package (diff-cover >= 80%).
"""

from __future__ import annotations

import json
import os
import time

import pytest

import crew_platform.navigraph.auth as auth_mod
from crew_platform.navigraph.auth import (
    NavigraphAuth,
    NavigraphAuthError,
    NavigraphTokens,
    DeviceFlowState,
    decode_subscriptions,
    generate_code_challenge,
    generate_code_verifier,
    parse_set_cookies,
    token_expiry,
    _tokens_from_payload,
)
from crew_platform.navigraph.config import NavigraphConfig


# ---------------------------------------------------------------------------
# PKCE helpers
# ---------------------------------------------------------------------------


class TestPkcE:
    def test_verifier_default_length(self):
        assert 43 <= len(generate_code_verifier()) <= 128

    def test_verifier_invalid_length_raises(self):
        with pytest.raises(ValueError):
            generate_code_verifier(length=10)
        with pytest.raises(ValueError):
            generate_code_verifier(length=200)

    def test_challenge_is_s256_of_verifier(self):
        v = generate_code_verifier()
        c = generate_code_challenge(v)
        # S256 challenge is base64url of sha256(verifier), no padding.
        assert c == auth_mod.base64.urlsafe_b64encode(
            auth_mod.hashlib.sha256(v.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")


# ---------------------------------------------------------------------------
# Subscription / expiry decoding (unverified JWT hints)
# ---------------------------------------------------------------------------


class TestDecodeClaims:
    def _jwt(self, payload: dict, broken: bool = False) -> str:
        import base64

        seg = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        if broken:
            seg = "!!!not-base64!!!"
        return f"{seg}.{seg}.sig"

    def test_list_subscriptions(self):
        assert decode_subscriptions(self._jwt({"subscriptions": ["charts", "fmsdata"]})) == [
            "charts",
            "fmsdata",
        ]

    def test_string_subscription_coerced_to_list(self):
        assert decode_subscriptions(self._jwt({"subscriptions": "charts"})) == ["charts"]

    def test_missing_claim_yields_empty(self):
        assert decode_subscriptions(self._jwt({"sub": "x"})) == []

    def test_opaque_token_yields_empty(self):
        assert decode_subscriptions("opaque-no-dots") == []

    def test_none_yields_empty(self):
        assert decode_subscriptions(None) == []

    def test_malformed_payload_yields_empty(self):
        assert decode_subscriptions(self._jwt({}, broken=True)) == []

    def test_expiry_reads_exp(self):
        assert token_expiry(self._jwt({"exp": 12345.0})) == 12345.0

    def test_expiry_none_for_opaque(self):
        assert token_expiry("nope") is None

    def test_expiry_none_for_malformed(self):
        assert token_expiry(self._jwt({}, broken=True)) is None

    def test_expiry_none_when_no_exp(self):
        assert token_expiry(self._jwt({"sub": "x"})) is None


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


class TestTokenModel:
    def test_has_scope_empty_scope_is_permissive(self):
        t = NavigraphTokens(access_token="x", scopes=[])
        # Empty scope list means the identity server didn't echo scope;
        # the connector must not lock the pilot out.
        assert t.has_scope("charts") is True

    def test_has_scope_present_and_absent(self):
        t = NavigraphTokens(access_token="x", scopes=["charts", "tiles"])
        assert t.has_scope("charts") is True
        assert t.has_scope("fmsdata") is False

    def test_has_subscription(self):
        t = NavigraphTokens(access_token="x", subscriptions=["charts"])
        assert t.has_subscription("charts") is True
        assert t.has_subscription("tiles") is False

    def test_expired_when_past_margin(self):
        t = NavigraphTokens(access_token="x", expires_at=time.time() - 10)
        assert t.expired is True

    def test_not_expired_when_fresh(self):
        t = NavigraphTokens(access_token="x", expires_at=time.time() + 3600)
        assert t.expired is False

    def test_redacted_never_leaks_secrets(self):
        t = NavigraphTokens(
            access_token="topsecret-access",
            refresh_token="topsecret-refresh",
            expires_at=time.time() + 3600,
            scopes=["charts"],
            subscriptions=["charts"],
            tile_cookies={"CloudFront-Policy": "secret-policy"},
        )
        r = t.redacted()
        assert r["access_token_present"] is True
        assert r["refresh_token_present"] is True
        assert r["scopes"] == ["charts"]
        assert r["subscriptions"] == ["charts"]
        assert r["tile_cookies_present"] is True
        assert r["expires_in"] > 0
        joined = json.dumps(r)
        assert "topsecret" not in joined
        assert "secret-policy" not in joined


# ---------------------------------------------------------------------------
# Set-Cookie parsing (CloudFront tile cookies)
# ---------------------------------------------------------------------------


class TestParseSetCookies:
    def test_get_list_path(self):
        # Async-style response: headers.get_list("set-cookie") works.
        class Resp:
            class headers:
                @staticmethod
                def get_list(_k):
                    return [
                        "CloudFront-Policy=abc123; Path=/; Secure",
                        "CloudFront-Signature=def456; Path=/",
                        "unrelated=nope; Path=/",
                    ]

        out = parse_set_cookies(Resp())
        assert out["CloudFront-Policy"] == "abc123"
        assert out["CloudFront-Signature"] == "def456"
        assert "unrelated" in out  # parsed, but filtered by caller

    def test_raw_headers_fallback_path(self):
        # Sync-style response: no get_list -> AttributeError -> raw_headers.
        class Resp:
            raw_headers = [
                (b"set-cookie", b"CloudFront-Key-Pair-Id=K1; Path=/"),
                (b"other", b"x=y"),
            ]

        out = parse_set_cookies(Resp())
        assert out["CloudFront-Key-Pair-Id"] == "K1"


# ---------------------------------------------------------------------------
# Token payload parsing
# ---------------------------------------------------------------------------


class TestTokensFromPayload:
    def test_full_payload_with_tile_cookies(self):
        payload = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "scope": "openid charts tiles",
            "CloudFront-Policy": "p",
            "CloudFront-Signature": "s",
            "CloudFront-Key-Pair-Id": "k",
        }
        t = _tokens_from_payload(payload)
        assert t.access_token == "at"
        assert t.refresh_token == "rt"
        assert t.expires_at > time.time()
        assert t.scopes == ["openid", "charts", "tiles"]
        assert t.tile_cookies == {"CloudFront-Policy": "p", "CloudFront-Signature": "s", "CloudFront-Key-Pair-Id": "k"}

    def test_missing_access_token_raises(self):
        with pytest.raises(NavigraphAuthError):
            _tokens_from_payload({"refresh_token": "rt"})

    def test_bad_expires_in_falls_back(self):
        t = _tokens_from_payload({"access_token": "at", "expires_in": "not-a-number"})
        assert t.expires_at > time.time()  # fell back to +3600

    def test_no_expires_in_uses_token_or_default(self):
        t = _tokens_from_payload({"access_token": "opaque-token"})
        assert t.expires_at > time.time()


# ---------------------------------------------------------------------------
# Device-flow state
# ---------------------------------------------------------------------------


class TestDeviceFlowState:
    def _state(self, expires_delta: float) -> DeviceFlowState:
        return DeviceFlowState(
            device_code="dc",
            user_code="UC",
            verification_uri="https://verify",
            verification_uri_complete="https://verify?uc=UC",
            interval=5,
            expires_at=time.time() + expires_delta,
            code_verifier="v",
        )

    def test_expired_flag(self):
        assert self._state(-1).expired is True
        assert self._state(600).expired is False

    def test_public_hides_secrets(self):
        s = self._state(600)
        p = s.public()
        assert p["user_code"] == "UC"
        assert p["verification_uri"] == "https://verify"
        assert "device_code" not in p
        assert "code_verifier" not in p
        assert p["expires_in"] > 0


# ---------------------------------------------------------------------------
# Refresh-token persistence
# ---------------------------------------------------------------------------


class TestRefreshTokenPersistence:
    def _auth(self, token_store: str | None = None, env_refresh: str | None = None) -> NavigraphAuth:
        cfg = NavigraphConfig(
            client_id="cid", client_secret="sec", token_store=token_store, refresh_token=env_refresh
        )
        return NavigraphAuth(config=cfg)

    def test_env_refresh_wins_over_file(self, tmp_path):
        store = str(tmp_path / "tok.json")
        with open(store, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": "file-rt"}, fh)
        a = self._auth(token_store=store, env_refresh="env-rt")
        assert a.load_refresh_token() == "env-rt"

    def test_load_reads_file_when_no_env(self, tmp_path):
        store = str(tmp_path / "tok.json")
        with open(store, "w", encoding="utf-8") as fh:
            json.dump({"refresh_token": "  file-rt  "}, fh)
        a = self._auth(token_store=store)
        assert a.load_refresh_token() == "file-rt"

    def test_load_missing_file_returns_none(self, tmp_path):
        a = self._auth(token_store=str(tmp_path / "nope.json"))
        assert a.load_refresh_token() is None

    def test_load_corrupt_file_returns_none(self, tmp_path):
        store = str(tmp_path / "tok.json")
        with open(store, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        a = self._auth(token_store=store)
        assert a.load_refresh_token() is None

    def test_store_writes_file_with_0600(self, tmp_path):
        store = str(tmp_path / "sub" / "tok.json")
        a = self._auth(token_store=store)
        a.store_refresh_token("new-rt")
        assert os.path.isfile(store)
        with open(store, encoding="utf-8") as fh:
            assert json.load(fh) == {"refresh_token": "new-rt"}
        if os.name == "posix":
            # POSIX-only: the store is written 0600 (owner read/write).
            mode = os.stat(store).st_mode & 0o777
            assert mode == 0o600

    def test_store_noop_when_no_path(self, tmp_path):
        a = self._auth(token_store=None)
        # Must not raise.
        a.store_refresh_token("rt")

    def test_store_noop_when_no_token(self, tmp_path):
        a = self._auth(token_store=str(tmp_path / "tok.json"))
        a.store_refresh_token(None)
        assert not os.path.isfile(str(tmp_path / "tok.json"))


# ---------------------------------------------------------------------------
# Async transport methods (httpx.AsyncClient monkeypatched — no live call)
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int, body: dict | None = None, cookies: list[str] | None = None):
        self.status_code = status
        self._body = body or {}
        self._cookies = cookies or []

    def json(self):
        return self._body

    class _Headers:
        def __init__(self, cookies):
            self._c = cookies

        def get_list(self, _k):
            return self._c

    @property
    def headers(self):
        return self._Headers(self._cookies)


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient; returns queued responses."""

    def __init__(self, responses: list[_FakeResp]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _next(self):
        if not self._responses:
            raise AssertionError("more responses consumed than queued")
        return self._responses.pop(0)

    async def post(self, url, data=None, **_):
        self.calls.append(("POST", url, data or {}))
        return self._next()

    async def get(self, url, headers=None, **_):
        self.calls.append(("GET", url, headers or {}))
        return self._next()


@pytest.fixture
def patch_asyncclient(monkeypatch):
    holder: dict = {}

    def _factory(*a, **k) -> _FakeAsyncClient:
        # auth.py calls `httpx.AsyncClient(timeout=...)` then `async with` it.
        # Return the fake (which implements the async context manager).
        fake = _FakeAsyncClient(holder["responses"])
        holder["client"] = fake
        return fake

    def _install(responses: list[_FakeResp]):
        holder["responses"] = list(responses)
        monkeypatch.setattr(auth_mod.httpx, "AsyncClient", _factory)

    yield _install
    # restore real AsyncClient
    monkeypatch.undo()


class TestNavigraphAuthFlow:
    def test_start_device_flow_success(self, patch_asyncclient):
        patch_asyncclient([
            _FakeResp(200, {
                "device_code": "DC",
                "user_code": "UC",
                "verification_uri": "https://v",
                "verification_uri_complete": "https://v?uc=UC",
                "expires_in": 600,
                "interval": 5,
            })
        ])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        flow = _run_auth(a.start_device_flow())
        assert flow.device_code == "DC"
        assert flow.user_code == "UC"
        assert flow.interval == 5

    def test_start_device_flow_requires_credentials(self, patch_asyncclient):
        patch_asyncclient([])
        a = NavigraphAuth(NavigraphConfig(client_id=None, client_secret=None))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.start_device_flow())

    def test_start_device_flow_http_error(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(500, {"error": "boom"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.start_device_flow())

    def test_start_device_flow_incomplete_response(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(200, {"device_code": "DC"})])  # missing user_code
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.start_device_flow())

    def test_poll_authorized_returns_tokens(self, patch_asyncclient):
        patch_asyncclient([
            _FakeResp(200, {
                "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                "CloudFront-Policy": "p",
            }, cookies=["CloudFront-Signature=s; Path=/"])
        ])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec",
                                          token_store=None))
        flow = DeviceFlowState(device_code="dc", user_code="uc", verification_uri="",
                               verification_uri_complete=None, interval=5,
                               expires_at=time.time() + 600, code_verifier="v")
        status, tokens = _run_auth(a.poll_device_token(flow))
        assert status == "authorized"
        assert tokens is not None and tokens.access_token == "at"
        assert tokens.tile_cookies.get("CloudFront-Policy") == "p"
        assert tokens.tile_cookies.get("CloudFront-Signature") == "s"

    def test_poll_pending(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(400, {"error": "authorization_pending"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        flow = _fresh_flow()
        status, tokens = _run_auth(a.poll_device_token(flow))
        assert status == "pending" and tokens is None

    def test_poll_slow_down_lengthens_interval(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(400, {"error": "slow_down"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        flow = _fresh_flow()
        status, _ = _run_auth(a.poll_device_token(flow))
        assert status == "slow_down"
        assert flow.poll_interval > 5

    def test_poll_expired_short_circuits_without_http(self, patch_asyncclient):
        patch_asyncclient([])  # no HTTP should be made
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        flow = _fresh_flow(expires_delta=-1)
        status, _ = _run_auth(a.poll_device_token(flow))
        assert status == "expired"

    def test_poll_denied(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(400, {"error": "access_denied"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        status, _ = _run_auth(a.poll_device_token(_fresh_flow()))
        assert status == "denied"

    def test_refresh_success(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(200, {
            "access_token": "at2", "refresh_token": "rt2", "expires_in": 3600,
        })])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        tokens = _run_auth(a.refresh("rt1"))
        assert tokens.access_token == "at2"
        assert tokens.refresh_token == "rt2"

    def test_refresh_requires_token(self, patch_asyncclient):
        patch_asyncclient([])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.refresh(""))

    def test_refresh_http_error(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(401, {"error": "invalid_grant"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.refresh("rt"))

    def test_userinfo_success(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(200, {"preferred_username": "pilot"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        info = _run_auth(a.userinfo("at"))
        assert info["preferred_username"] == "pilot"

    def test_userinfo_http_error(self, patch_asyncclient):
        patch_asyncclient([_FakeResp(403, {"error": "forbidden"})])
        a = NavigraphAuth(NavigraphConfig(client_id="cid", client_secret="sec"))
        with pytest.raises(NavigraphAuthError):
            _run_auth(a.userinfo("at"))


def _fresh_flow(expires_delta: float = 600) -> DeviceFlowState:
    return DeviceFlowState(
        device_code="dc", user_code="uc", verification_uri="https://v",
        verification_uri_complete=None, interval=5,
        expires_at=time.time() + expires_delta, code_verifier="v",
    )


def _run_auth(coro):
    loop = __import__("asyncio").new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
