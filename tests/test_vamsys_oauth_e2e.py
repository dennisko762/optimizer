"""End-to-end vAMSYS browser OAuth flow.

Drives the real FastAPI crew routes (including the browser-facing
GET /crew/auth/callback and the catch-all SPA mount ordering) through a
complete authorization-code + PKCE cycle against an in-process fake vAMSYS
provider. The fake provider is wired in by swapping httpx.AsyncClient for a
MockTransport-based client — the app's own request code is otherwise
untouched (real PKCE validation, real token exchange, real redirect).
"""
from __future__ import annotations

import urllib.parse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from crew_platform.routes import callback_router, router as crew_router
from crew_platform import vamsys_pilot_auth as vpa
from optimizer.api.app import app as real_app


FAKE_CLIENT_ID = "999000"
FAKE_REDIRECT = "https://crew.example/crew/auth/callback"


async def _fake_vamsys_handler(request: httpx.Request) -> httpx.Response:
    """In-process fake vAMSYS: authorize / token / pilot API."""
    url = urllib.parse.urlparse(str(request.url))
    path = url.path

    if path == "/oauth/token":
        body = dict(urllib.parse.parse_qsl(request.content.decode()))
        if body.get("grant_type") == "authorization_code":
            # Real PKCE check: re-derive the challenge from the verifier.
            verifier = body.get("code_verifier", "")
            challenge = vpa.generate_code_challenge(verifier)
            if challenge != _state_store.get("expected_challenge"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            if body.get("code") != "FAKE_CODE":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": "FAKE_ACCESS",
                    "refresh_token": "FAKE_REFRESH",
                    "expires_in": 3600,
                    "scope": " ".join(vpa.DEFAULT_SCOPES),
                    "token_type": "Bearer",
                },
            )
        if body.get("grant_type") == "refresh_token":
            if body.get("refresh_token") != "FAKE_REFRESH":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": "FAKE_ACCESS_2",
                    "refresh_token": "FAKE_REFRESH",
                    "expires_in": 3600,
                    "scope": " ".join(vpa.DEFAULT_SCOPES),
                },
            )
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    if path == "/api/v3/pilot/user":
        if request.headers.get("Authorization") != "Bearer FAKE_ACCESS":
            return httpx.Response(401, json={"detail": "Unauthorized"})
        return httpx.Response(
            200,
            json={
                "data": {
                    "id": 4242,
                    "email": "pilot@example.com",
                    "first_name": "Dennis",
                    "last_name": "Korolevych",
                    "pilot": {
                        "id": 777,
                        "username": "LK042",
                        "airline_id": 5,
                    },
                }
            },
        )

    return httpx.Response(404, json={"detail": "not found"})


_state_store: dict = {}


from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture
def crew_app(monkeypatch):
    """Mirror of optimizer.api.app.create_app() router ordering:
    API routers first, then the catch-all SPA static mount."""
    monkeypatch.setenv("VAMSYS_PILOT_CLIENT_ID", FAKE_CLIENT_ID)
    monkeypatch.setenv("VAMSYS_REDIRECT_URI", FAKE_REDIRECT)

    class _MockAsyncClient(httpx.AsyncClient):
        async def send(self, request, **kwargs):
            # Intercept only requests headed for vAMSYS; the TestClient's
            # own traffic (http://testserver/…) passes through untouched.
            if request.url.host == "vamsys.io":
                transport = httpx.MockTransport(_fake_vamsys_handler)
                return await transport.handle_async_request(request)
            return await super().send(request, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _MockAsyncClient)

    app = FastAPI()
    app.include_router(crew_router)
    app.include_router(callback_router)

    # Catch-all mount AFTER the routers, exactly like create_app().
    import tempfile
    import pathlib

    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        (d / "index.html").write_text("<!doctype html><title>spa</title>", encoding="utf-8")
        app.mount("/", StaticFiles(directory=d, html=True), name="ui")

    with TestClient(app) as client:
        yield client


class TestBrowserOAuthFlow:
    def test_full_redirect_cycle(self, crew_app):
        # 1. Start the flow
        r = crew_app.post("/api/crew/auth/start", params={"provider_id": "lhvirtual"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["redirect_uri"] == FAKE_REDIRECT
        assert data["session_id"]

        # Authorize URL points at vAMSYS with the v3 params
        url = data["authorize_url"]
        parsed = urllib.parse.urlparse(url)
        assert parsed.path == "/oauth/authorize"
        q = dict(urllib.parse.parse_qs(parsed.query))
        assert q["client_id"] == [FAKE_CLIENT_ID]
        assert q["response_type"] == ["code"]
        assert q["code_challenge_method"] == ["S256"]
        challenge = q["code_challenge"][0]
        _state_store["expected_challenge"] = challenge
        state = q["state"][0]

        # 2. Pilot "signs in" at vAMSYS → vAMSYS redirects to our callback.
        #    Hit the callback on the app that serves the SPA (follow the
        #    final 302 into the app is NOT done here — we assert the target).
        cb = crew_app.get(
            "/crew/auth/callback",
            params={"code": "FAKE_CODE", "state": state},
            follow_redirects=False,
        )
        assert cb.status_code == 302, cb.text
        loc = urllib.parse.urlparse(cb.headers["location"])
        loc_q = dict(urllib.parse.parse_qs(loc.query))
        assert loc_q.get("crew_session", [None])[0] == data["session_id"]
        assert loc_q.get("provider", [None])[0] == "lhvirtual"

        # 3. The SPA re-adopts the session — it is authenticated with the
        #    identity fetched from the fake /user.
        s = crew_app.get(f"/api/crew/session/{data['session_id']}")
        assert s.status_code == 200, s.text
        body = s.json()
        assert body["authenticated"] is True
        assert body["local"] is False
        assert body["callsign"] == "LK042"
        assert body["crew_id"] == "777"
        assert body["display_name"] == "Dennis Korolevych"

    def test_callback_route_wins_over_spa_catch_all(self, crew_app):
        """Without a code/state the route must 422 (not serve index.html)."""
        r = crew_app.get("/crew/auth/callback")
        assert r.status_code == 422, (
            f"callback route shadowed by static mount: {r.status_code}"
        )

    def test_bad_code_shows_error_page(self, crew_app):
        r = crew_app.post("/api/crew/auth/start", params={"provider_id": "etihadvirtual"})
        data = r.json()
        parsed = urllib.parse.urlparse(data["authorize_url"])
        q = dict(urllib.parse.parse_qs(parsed.query))
        _state_store["expected_challenge"] = q["code_challenge"][0]

        r = crew_app.get(
            "/crew/auth/callback",
            params={"code": "WRONG_CODE", "state": q["state"][0]},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "Sign-in problem" in r.text

    def test_state_mismatch_is_rejected(self, crew_app):
        r = crew_app.post("/api/crew/auth/start", params={"provider_id": "lhvirtual"})
        data = r.json()
        parsed = urllib.parse.urlparse(data["authorize_url"])
        q = dict(urllib.parse.parse_qs(parsed.query))
        _state_store["expected_challenge"] = q["code_challenge"][0]

        r = crew_app.get(
            "/crew/auth/callback",
            params={"code": "FAKE_CODE", "state": "attacker-state"},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "Unknown or expired auth state" in r.text


class TestRealAppWiring:
    def test_callback_route_registered_before_static_mount(self):
        """The real app must route /crew/auth/callback to the OAuth handler
        (422 on missing params), not to the SPA index."""
        with TestClient(real_app, follow_redirects=False) as client:
            r = client.get("/crew/auth/callback")
            assert r.status_code == 422, r.status_code
