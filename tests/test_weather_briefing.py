"""Fixture-backed tests for crew_platform.weather.briefing adapters.

Every adapter is tested offline with monkeypatched httpx clients so the
parser/normalization/cache/error/staleness behavior is exercised
deterministically. Real (bounded, network) coverage for AviationWeather.gov
and EUMETSAT lives in tests/adapters/test_briefing_integration.py and skips
honestly when unreachable/unconfigured.
"""
from __future__ import annotations


import pytest

from crew_platform.weather.briefing.cache import TTLCache
from crew_platform.weather.briefing.models import Product, ProductState, to_utc_iso


# ---------------------------------------------------------------------
# models
# ---------------------------------------------------------------------


def test_to_utc_iso_handles_z_suffix():
    assert to_utc_iso("2026-10-06T18:00:00Z") == "2026-10-06T18:00:00Z"


def test_to_utc_iso_handles_milliseconds_z_suffix():
    assert to_utc_iso("2026-10-07T14:00:00.000Z") == "2026-10-07T14:00:00Z"


def test_to_utc_iso_handles_unix_seconds():
    assert to_utc_iso(0) == "1970-01-01T00:00:00Z"


def test_to_utc_iso_rejects_garbage():
    assert to_utc_iso("not-a-time") is None


def test_product_unavailable_never_has_raw():
    p = Product.unavailable("metar", "src", "METAR", "METAR", "no data")
    assert p.state == ProductState.UNAVAILABLE
    assert p.raw is None
    assert p.to_dict()["detail"] == "no data"


# ---------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------


def test_ttlcache_get_or_fetch_single_fetch_within_ttl():
    cache = TTLCache()
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return "value"

    assert cache.get_or_fetch("k", 100.0, fetch) == "value"
    assert cache.get_or_fetch("k", 100.0, fetch) == "value"
    assert calls["n"] == 1  # second call served from cache, no fan-out


def test_ttlcache_expires_and_refetches():
    cache = TTLCache()
    cache.set("k", "old", -1.0)  # already expired
    assert cache.get("k") is None
    assert cache.get_stale("k") == "old"


# ---------------------------------------------------------------------
# aviationweather.gov: METAR / TAF / SIGMET
# ---------------------------------------------------------------------


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


def test_fetch_metars_parses_fixture(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    fixture = [{
        "icaoId": "OTHH", "rawOb": "OTHH 091200Z 24008KT 9999 FEW025 32/18 Q1006",
        "reportTime": "2026-10-09T12:00:00Z", "lat": 25.27, "lon": 51.61,
    }]

    def responder(url, params):
        assert "metar" in url
        assert params["ids"] == "OTHH"
        return _FakeResponse(200, fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_metars(["othh"])
    assert len(out) == 1
    p = out[0]
    assert p.state == ProductState.OK
    assert p.kind == "metar"
    assert p.label == "METAR"
    assert p.station == "OTHH"
    assert p.raw.startswith("OTHH 091200Z")
    assert p.issued_utc == "2026-10-09T12:00:00Z"
    assert p.geometry == {"type": "Point", "coordinates": [51.61, 25.27]}


def test_fetch_metars_missing_station_is_unavailable_not_fabricated(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    def responder(url, params):
        return _FakeResponse(200, [])

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_metars(["ZZZZ"])
    assert len(out) == 1
    assert out[0].state == ProductState.UNAVAILABLE
    assert out[0].raw is None


def test_fetch_metars_http_error_without_cache_is_provider_error(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    def responder(url, params):
        return _FakeResponse(503, None)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_metars(["OTHH"])
    assert len(out) == 1
    assert out[0].state == ProductState.PROVIDER_ERROR
    assert out[0].raw is None


def test_fetch_metars_serves_stale_last_good_on_upstream_failure(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    good_fixture = [{
        "icaoId": "OTHH", "rawOb": "OTHH 091200Z 24008KT 9999 FEW025 32/18 Q1006",
        "reportTime": "2026-10-09T12:00:00Z",
    }]
    state = {"fail": False}

    def responder(url, params):
        if state["fail"]:
            return _FakeResponse(500, None)
        return _FakeResponse(200, good_fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    config = awc.AwcConfig(metar_taf_ttl_s=-1.0)  # force immediate expiry
    first = awc.fetch_metars(["OTHH"], config=config)
    assert first[0].state == ProductState.OK

    state["fail"] = True
    second = awc.fetch_metars(["OTHH"], config=config)
    assert second[0].state == ProductState.STALE
    assert second[0].stale is True
    assert second[0].raw == good_fixture[0]["rawOb"]  # last-good value, not fabricated


def test_fetch_tafs_parses_valid_window(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    fixture = [{
        "icaoId": "OTHH", "rawTAF": "TAF OTHH 091100Z 0912/1018 24010KT 9999 FEW025",
        "issueTime": "2026-10-09T11:00:00Z",
        "validTimeFrom": "2026-10-09T12:00:00Z", "validTimeTo": "2026-10-10T18:00:00Z",
    }]

    def responder(url, params):
        return _FakeResponse(200, fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_tafs(["OTHH"])
    assert out[0].state == ProductState.OK
    assert out[0].kind == "taf"
    assert out[0].valid_from_utc == "2026-10-09T12:00:00Z"
    assert out[0].valid_until_utc == "2026-10-10T18:00:00Z"


def test_fetch_sigmets_parses_polygon(monkeypatch):
    from crew_platform.weather.briefing import aviationweather as awc

    fixture = [{
        "firId": "OTHH", "hazard": "TURB",
        "rawSigmet": "WSQT31 OTHH 091200",
        "issueTime": "2026-10-09T12:00:00Z",
        "validTimeFrom": "2026-10-09T12:00:00Z", "validTimeTo": "2026-10-09T16:00:00Z",
        "coords": [{"lat": 25.0, "lon": 50.0}, {"lat": 26.0, "lon": 51.0}, {"lat": 25.0, "lon": 52.0}],
    }]

    def responder(url, params):
        return _FakeResponse(200, fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_sigmets(intl=True)
    assert out[0].state == ProductState.OK
    assert out[0].kind == "sigmet"
    assert "international" in out[0].label
    assert out[0].geometry["type"] == "Polygon"
    assert len(out[0].geometry["coordinates"][0]) == 3


def test_fetch_sigmets_is_a_faithful_proxy_no_dedup(monkeypatch):
    """DECISION (follow-up #2 from PR #16 review): duplicate upstream SIGMET
    records are passed through as-is, never silently dropped — dedup, if
    ever wanted, belongs in the UI mapper layer, not this adapter."""
    from crew_platform.weather.briefing import aviationweather as awc

    rec = {
        "firId": "OTHH", "hazard": "TURB",
        "rawSigmet": "WSQT31 OTHH 091200",
        "issueTime": "2026-10-09T12:00:00Z",
        "validTimeFrom": "2026-10-09T12:00:00Z", "validTimeTo": "2026-10-09T16:00:00Z",
        "coords": [{"lat": 25.0, "lon": 50.0}, {"lat": 26.0, "lon": 51.0}, {"lat": 25.0, "lon": 52.0}],
    }
    fixture = [dict(rec), dict(rec)]  # upstream repeats the exact same record

    def responder(url, params):
        return _FakeResponse(200, fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    out = awc.fetch_sigmets(intl=True)
    assert len(out) == 2, "the adapter must stay a transparent 1:1 proxy (no dedup)"


# ---------------------------------------------------------------------
# VATSIM ATIS: feature gate + label
# ---------------------------------------------------------------------


def test_vatsim_atis_disabled_by_default():
    from crew_platform.weather.briefing import vatsim_atis as va

    config = va.VatsimConfig(enabled=False)
    p = va.fetch_atis("OTHH", config=config)
    assert p.state == ProductState.DISABLED
    assert p.label == "VATSIM ATIS"
    assert p.raw is None


def test_vatsim_atis_enabled_parses_fixture(monkeypatch):
    from crew_platform.weather.briefing import vatsim_atis as va

    fixture = {"atis": [{
        "callsign": "OTHH_ATIS", "text_atis": ["OTHH ATIS INFO A", "RWY 34 IN USE"],
        "last_updated": "2026-10-09T12:00:00Z", "atis_code": "A", "frequency": "128.550",
    }]}

    def responder(url, params=None, headers=None):
        return _FakeResponse(200, fixture)

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    config = va.VatsimConfig(enabled=True)
    p = va.fetch_atis("OTHH", config=config)
    assert p.state == ProductState.OK
    assert p.label == "VATSIM ATIS"
    assert p.station == "OTHH_ATIS"
    assert "INFO A" in p.raw


def test_vatsim_atis_enabled_no_controller_online(monkeypatch):
    from crew_platform.weather.briefing import vatsim_atis as va

    def responder(url, params=None, headers=None):
        return _FakeResponse(200, {"atis": []})

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(responder))

    config = va.VatsimConfig(enabled=True)
    p = va.fetch_atis("OTHH", config=config)
    assert p.state == ProductState.UNAVAILABLE
    assert p.label == "VATSIM ATIS"


# ---------------------------------------------------------------------
# real-world D-ATIS: interface-only, never conflated with METAR
# ---------------------------------------------------------------------


def test_real_datis_unconfigured_falls_back_to_metar_briefing_label():
    from crew_platform.weather.briefing import real_datis as rd

    config = rd.DatisConfig(provider_name="")
    p = rd.fetch_datis("OTHH", config=config)
    assert p.state == ProductState.UNAVAILABLE
    assert p.label == "METAR briefing"
    assert p.kind == "real_world_datis"


def test_real_datis_configured_but_unregistered_provider():
    from crew_platform.weather.briefing import real_datis as rd

    config = rd.DatisConfig(provider_name="nonexistent-corp")
    p = rd.fetch_datis("OTHH", config=config)
    assert p.state == ProductState.UNAVAILABLE
    assert p.label == "METAR briefing"


def test_real_datis_registered_provider_label_enforced():
    from crew_platform.weather.briefing import real_datis as rd
    from crew_platform.weather.briefing.models import Product

    class FakeProvider:
        name = "fake-corp"

        def fetch(self, icao):
            return Product(
                kind="real_world_datis", source="fake-corp", product="D-ATIS",
                label="WRONG LABEL SHOULD BE OVERWRITTEN", state=ProductState.OK,
                raw="DOH ATIS INFO B", station=icao,
            )

    rd.register_provider(FakeProvider())
    try:
        config = rd.DatisConfig(provider_name="fake-corp")
        p = rd.fetch_datis("OTHH", config=config)
        assert p.state == ProductState.OK
        assert p.label == "Real-world D-ATIS — fake-corp"
        assert p.raw == "DOH ATIS INFO B"
    finally:
        rd._REGISTRY.pop("fake-corp", None)


# ---------------------------------------------------------------------
# EUMETSAT lightning: collection id, gating, never Level 1
# ---------------------------------------------------------------------


def test_lightning_disabled_by_default():
    from crew_platform.weather.briefing import eumetsat_lightning as eum

    config = eum.EumetsatConfig(enabled=False)
    p = eum.fetch_lightning((45.0, 55.0, 20.0, 30.0), config=config)
    assert p.state == ProductState.DISABLED
    assert eum.COLLECTION_ID == "EO:EUM:DAT:0691"
    assert "Level 2" in p.label


def test_lightning_enabled_without_credentials_is_unavailable():
    from crew_platform.weather.briefing import eumetsat_lightning as eum

    config = eum.EumetsatConfig(enabled=True, consumer_key="", consumer_secret="")
    p = eum.fetch_lightning((45.0, 55.0, 20.0, 30.0), config=config)
    assert p.state == ProductState.UNAVAILABLE
    assert "credentials" in (p.detail or "").lower() or "CONSUMER" in (p.detail or "")


def test_lightning_no_intensity_field_fabricated():
    """Never invent an 'intensity' value from a positional tuple — only the
    documented flash_count is surfaced, and geometry is the bounded box."""
    from crew_platform.weather.briefing import eumetsat_lightning as eum

    out = eum._to_product({"features": [{"a": 1}, {"b": 2}]}, (45.0, 55.0, 20.0, 30.0), "x", "y", stale=False, detail=None)
    assert "intensity" not in out.extra
    assert out.extra["flash_count"] == 2
    assert out.extra["collection"] == "EO:EUM:DAT:0691"


# ---------------------------------------------------------------------
# SIGWX/WAFS: never claims anonymous access
# ---------------------------------------------------------------------


def test_sigwx_disabled_by_default():
    from crew_platform.weather.briefing import sigwx_wafs as sw

    config = sw.SigwxConfig(enabled=False)
    p = sw.fetch_sigwx(config=config)
    assert p.state == ProductState.DISABLED


def test_sigwx_enabled_without_credentials_stays_unavailable():
    from crew_platform.weather.briefing import sigwx_wafs as sw

    config = sw.SigwxConfig(enabled=True, wifs_username="", wifs_password_set=False)
    p = sw.fetch_sigwx(config=config)
    assert p.state == ProductState.UNAVAILABLE
    assert "authorized" in (p.detail or "").lower()


def test_sigwx_never_returns_ok():
    """No live WIFS client is implemented — even with credentials configured
    this must not claim success (no fabricated SIGWX chart)."""
    from crew_platform.weather.briefing import sigwx_wafs as sw

    config = sw.SigwxConfig(enabled=True, wifs_username="user", wifs_password_set=True)
    p = sw.fetch_sigwx(config=config)
    assert p.state != ProductState.OK


# ---------------------------------------------------------------------
# OurAirports: metadata-only + non-authoritative attribution
# ---------------------------------------------------------------------


def test_ourairports_lookup_carries_non_authoritative_flag(monkeypatch):
    from crew_platform.weather.briefing import ourairports as oa

    csv_text = (
        "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,iata_code\n"
        "1,OTHH,large_airport,Hamad International Airport,25.273,51.608,13,QA,Doha,DOH\n"
    )

    class _Resp:
        status_code = 200
        text = csv_text

    def responder(url, params=None, headers=None):
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(lambda u, p: responder(u, p)))

    meta = oa.lookup("othh")
    assert meta["icao"] == "OTHH"
    assert meta["iata"] == "DOH"
    assert meta["authoritative"] is False
    assert "non-authoritative" in meta["attribution"].lower() or "OurAirports" in meta["attribution"]


def test_ourairports_unknown_ident_returns_none(monkeypatch):
    from crew_platform.weather.briefing import ourairports as oa

    class _Resp:
        status_code = 200
        text = "id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,iso_country,municipality,iata_code\n"

    def responder(url, params=None, headers=None):
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(lambda u, p: responder(u, p)))

    assert oa.lookup("ZZZZ") is None


# ---------------------------------------------------------------------
# aggregated atis_for_station resolution never conflates the 3 labels
# ---------------------------------------------------------------------


def test_atis_for_station_prefers_real_datis_then_vatsim_then_metar(monkeypatch):
    from crew_platform.weather.briefing import service as svc

    import httpx

    def metar_responder(url, params):
        if "metar" in url:
            return _FakeResponse(200, [{"icaoId": "OTHH", "rawOb": "OTHH 091200Z 24008KT 9999 32/18 Q1006"}])
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(metar_responder))

    out = svc.atis_for_station("OTHH")
    assert out["selected"] == "metar_briefing"
    assert out["real_world_datis"]["label"] == "METAR briefing"
    assert out["vatsim_atis"]["label"] == "VATSIM ATIS"
    assert out["metar_briefing"]["label"] == "METAR"
    # the three fields are never conflated: each carries its own distinct
    # label even though real_world_datis and metar_briefing share the same
    # fallback TEXT by design (the documented "METAR briefing" fallback) —
    # what matters is kind/state stay distinguishable per field.
    assert out["real_world_datis"]["kind"] == "real_world_datis"
    assert out["vatsim_atis"]["kind"] == "vatsim_atis"
    assert out["metar_briefing"]["kind"] == "metar"
