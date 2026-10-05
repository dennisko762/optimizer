"""The crew platform routes must be served by the main API app.

The PWA shell calls /api/crew/* on the same origin as the FastAPI bridge,
so create_app() must register the crew router. Without this the launcher
shell renders but every crew API call 404s.
"""

from fastapi.testclient import TestClient

from optimizer.api.app import app


def test_crew_provider_listing_served_by_main_app():
    client = TestClient(app)
    resp = client.get("/api/crew/providers")
    assert resp.status_code == 200
    providers = resp.json()
    assert isinstance(providers, list)
    ids = {p["id"] for p in providers}
    assert {"lhvirtual", "emiratesvirtual", "etihadvirtual"} <= ids
    lh = next(p for p in providers if p["id"] == "lhvirtual")
    assert lh["theme"], "provider must expose theme data for the theme engine"


def test_crew_config_readiness_served_by_main_app():
    client = TestClient(app)
    resp = client.get("/api/crew/config/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert "ready" in body, "readiness must report whether the bridge is configured"
    # Readiness must never leak credential material.
    raw = resp.text.lower()
    for forbidden in ("client_secret", "password", "token=***"):
        assert forbidden not in raw
