"""Bounded real integration tests: AviationWeather.gov (always publicly
reachable, no credentials) and EUMETSAT (skips when not configured).

Task requirement: "bounded real integration tests for publicly reachable
AviationWeather.gov data and, when configured, official EUMETSAT; tests
must skip honestly when credentials are absent."
"""
from __future__ import annotations

import os

import pytest

from crew_platform.weather.briefing.models import ProductState


def _awc_reachable() -> bool:
    try:
        import httpx
        with httpx.Client(timeout=5) as c:
            r = c.get("https://aviationweather.gov/api/data/metar", params={"ids": "KJFK", "format": "json"})
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _awc_reachable(), reason="AviationWeather.gov not reachable from this host")
def test_real_metar_kjfk():
    from crew_platform.weather.briefing import aviationweather as awc

    out = awc.fetch_metars(["KJFK"])
    assert len(out) == 1
    p = out[0]
    assert p.state in (ProductState.OK, ProductState.UNAVAILABLE)  # station may be temporarily absent
    if p.state == ProductState.OK:
        assert p.station == "KJFK"
        assert p.raw and "KJFK" in p.raw
        assert p.issued_utc is not None


@pytest.mark.skipif(not _awc_reachable(), reason="AviationWeather.gov not reachable from this host")
def test_real_sigmet_intl_bounded():
    from crew_platform.weather.briefing import aviationweather as awc

    out = awc.fetch_sigmets(intl=True)
    # may legitimately be empty (no active SIGMETs) — assert no crash + correct labelling
    assert isinstance(out, list)
    for p in out:
        assert p.kind == "sigmet"
        assert "SIGMET" in p.label


@pytest.mark.skipif(
    not (os.environ.get("EUMETSAT_CONSUMER_KEY") and os.environ.get("EUMETSAT_CONSUMER_SECRET")
         and os.environ.get("EUMETSAT_LIGHTNING_ENABLED") == "1"),
    reason="EUMETSAT credentials/feature-gate not configured — skipping honestly",
)
def test_real_eumetsat_lightning_bounded():
    from crew_platform.weather.briefing import eumetsat_lightning as eum

    # small bounded box over the Gulf region, short lookback (default 30 min)
    p = eum.fetch_lightning((45.0, 55.0, 20.0, 30.0))
    assert p.state in (ProductState.OK, ProductState.STALE, ProductState.PROVIDER_ERROR)
    assert p.extra.get("collection") == "EO:EUM:DAT:0691"
