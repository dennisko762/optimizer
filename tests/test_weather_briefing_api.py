"""API tests for /api/crew/weather/briefing — offline, httpx monkeypatched.

Proves the routes are thin passthroughs to the briefing adapters and that
disabled/unavailable states surface through the real FastAPI app (never a
500, never fabricated data).
"""
from __future__ import annotations

import pytest


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    def __init__(self, responder):
        self._responder = responder

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        return self._responder(url, params)


@pytest.fixture(autouse=True)
def _clear_cache():
    from crew_platform.weather.briefing.cache import CACHE
    CACHE.clear()
    yield
    CACHE.clear()


@pytest.fixture()
def client(monkeypatch):
    import httpx
    # Import TestClient (which subclasses httpx.Client at class-definition
    # time) BEFORE monkeypatching httpx.Client, or the subclass statement
    # itself breaks.
    from fastapi.testclient import TestClient
    from optimizer.api.app import create_app

    def responder(url, params=None):
        if "metar" in url:
            return _FakeResponse(200, [{"icaoId": "OTHH", "rawOb": "OTHH 091200Z 24008KT 9999 32/18 Q1006"}])
        if "taf" in url:
            return _FakeResponse(200, [{"icaoId": "OTHH", "rawTAF": "TAF OTHH 091100Z 0912/1018 24010KT"}])
        if "isigmet" in url:
            return _FakeResponse(200, [])
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))
    monkeypatch.setenv("WEATHER_SCHEDULER_ENABLED", "0")

    app = create_app()
    with TestClient(app) as c:
        yield c


def test_metar_route(client):
    r = client.get("/api/crew/weather/briefing/metar", params={"ids": "OTHH"})
    assert r.status_code == 200
    body = r.json()
    assert body["products"][0]["kind"] == "metar"
    assert body["products"][0]["label"] == "METAR"


def test_taf_route(client):
    r = client.get("/api/crew/weather/briefing/taf", params={"ids": "OTHH"})
    assert r.status_code == 200
    assert r.json()["products"][0]["kind"] == "taf"


def test_metar_route_requires_ids(client):
    r = client.get("/api/crew/weather/briefing/metar", params={"ids": ""})
    assert r.status_code == 422


def test_sigmet_route(client):
    r = client.get("/api/crew/weather/briefing/sigmet")
    assert r.status_code == 200
    assert r.json()["products"] == []


def test_atis_route_resolves_three_way_without_conflation(client):
    r = client.get("/api/crew/weather/briefing/atis/OTHH")
    assert r.status_code == 200
    body = r.json()
    assert body["selected"] == "metar_briefing"
    assert body["vatsim_atis"]["label"] == "VATSIM ATIS"
    assert body["vatsim_atis"]["state"] == "disabled"
    assert body["real_world_datis"]["label"] == "METAR briefing"
    assert body["metar_briefing"]["label"] == "METAR"


def test_sigwx_route_never_ok(client):
    r = client.get("/api/crew/weather/briefing/sigwx")
    assert r.status_code == 200
    assert r.json()["state"] != "ok"


def test_lightning_route_disabled_by_default(client):
    r = client.get(
        "/api/crew/weather/briefing/lightning",
        params={"leftlon": 45.0, "rightlon": 55.0, "bottomlat": 20.0, "toplat": 30.0},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "disabled"
    assert "EO:EUM:DAT:0691" in body.get("detail", "") or body.get("collection") == "EO:EUM:DAT:0691"


def test_airport_route_404_for_unknown(client, monkeypatch):
    import httpx

    class _Resp:
        status_code = 200
        text = "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,iata_code\n"

    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(lambda u, p: _Resp()))
    r = client.get("/api/crew/weather/briefing/airport/ZZZZ")
    assert r.status_code == 404
