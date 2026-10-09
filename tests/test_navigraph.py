"""M4 Navigraph integration — connector, cache, rate limit, parsers, API.

The tests exercise the three states the EFB must survive:
no credentials, no subscription, and no network.

No live Navigraph call is made: all HTTP is served by an httpx MockTransport,
and the parser tests run against ICAO/Navigraph-shaped fixtures in
tests/fixtures/navigraph/.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crew_platform.navigraph.aero import (
    notam_summary,
    parse_icao_notam,
    parse_nat_track_message,
    parse_notam_records,
    parse_risk_records,
)
from crew_platform.navigraph.auth import (
    NavigraphTokens,
    decode_subscriptions,
    generate_code_challenge,
    generate_code_verifier,
)
from crew_platform.navigraph.cache import TtlCache
from crew_platform.navigraph.client import (
    NavigraphClient,
    NavigraphUnavailable,
    gate_for,
)
from crew_platform.navigraph.config import NavigraphConfig, load_config
from crew_platform.navigraph.ratelimit import RateLimiter, RateLimitExceeded

FIXTURES = Path(__file__).parent / "fixtures" / "navigraph"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _jwt(subscriptions: list[str], exp: float = 9999999999.0) -> str:
    """Unsigned JWT carrying a subscriptions claim (gating hint only)."""

    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    return f"{seg({'alg': 'none'})}.{seg({'subscriptions': subscriptions, 'exp': exp})}.sig"


def _tokens(subscriptions: list[str]) -> NavigraphTokens:
    token = _jwt(subscriptions)
    return NavigraphTokens(
        access_token=token,
        refresh_token="r1",
        expires_at=9999999999.0,
        scopes=["openid", "charts", "tiles", "fmsdata"],
        subscriptions=subscriptions,
        tile_cookies={"CloudFront-Policy": "p", "CloudFront-Signature": "s"},
    )


def _client(
    handler,
    *,
    subscriptions: list[str] | None = None,
    config: NavigraphConfig | None = None,
    cache: TtlCache | None = None,
    limiter: RateLimiter | None = None,
) -> NavigraphClient:
    cfg = config or NavigraphConfig(client_id="cid", client_secret="sec")
    client = NavigraphClient(
        config=cfg,
        cache=cache or TtlCache(),
        limiter=limiter or RateLimiter(rpm=600, burst=50),
        transport=httpx.MockTransport(handler),
    )
    if subscriptions is not None:
        client.set_tokens(_tokens(subscriptions))
    return client


# ---------------------------------------------------------------------------
# Configuration — environment only
# ---------------------------------------------------------------------------


class TestConfig:
    def test_unconfigured_env_is_not_an_error(self):
        cfg = load_config(env={})
        assert cfg.configured is False
        assert cfg.client_id is None

    def test_credentials_come_from_env(self):
        cfg = load_config(
            env={
                "NAVIGRAPH_CLIENT_ID": "cid",
                "NAVIGRAPH_CLIENT_SECRET": "sec",
                "NAVIGRAPH_RATE_LIMIT_RPM": "30",
            }
        )
        assert cfg.configured is True
        assert cfg.rate_limit_rpm == 30

    def test_redacted_status_never_leaks_secrets(self):
        cfg = load_config(
            env={"NAVIGRAPH_CLIENT_ID": "cid", "NAVIGRAPH_CLIENT_SECRET": "supersecret"}
        )
        blob = json.dumps(cfg.redacted())
        assert "supersecret" not in blob
        assert "cid" not in blob
        assert cfg.redacted()["client_secret_set"] is True

    def test_bad_rate_limit_falls_back_to_default(self):
        cfg = load_config(env={"NAVIGRAPH_RATE_LIMIT_RPM": "not-a-number"})
        assert cfg.rate_limit_rpm == 60

    def test_no_navigraph_secret_is_committed_to_the_repo(self):
        """Keys must live in ENV only — guard against a pasted credential."""
        root = Path(__file__).parent.parent
        offenders = []
        for path in sorted(root.glob("crew_platform/navigraph/*.py")):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                # An assignment of a literal to a credential name, or a
                # hardcoded JWT, would be a committed secret.
                if "CLIENT_SECRET = " in line or "Bearer eyJ" in line:
                    offenders.append((path.name, stripped[:60]))
        assert offenders == []


# ---------------------------------------------------------------------------
# PKCE / token claims
# ---------------------------------------------------------------------------


class TestAuthPrimitives:
    def test_code_verifier_and_challenge_are_well_formed(self):
        verifier = generate_code_verifier()
        assert 43 <= len(verifier) <= 128
        challenge = generate_code_challenge(verifier)
        assert "=" not in challenge and len(challenge) == 43

    def test_subscriptions_claim_is_decoded(self):
        assert decode_subscriptions(_jwt(["charts", "fmsdata"])) == ["charts", "fmsdata"]

    def test_opaque_token_yields_no_subscriptions(self):
        assert decode_subscriptions("opaque-token") == []
        assert decode_subscriptions(None) == []

    def test_expiry_margin_marks_tokens_expired_early(self):
        import time

        tokens = NavigraphTokens(access_token="a", expires_at=time.time() + 30)
        assert tokens.expired is True


# ---------------------------------------------------------------------------
# Subscription gate
# ---------------------------------------------------------------------------


class TestSubscriptionGate:
    def test_without_credentials_everything_is_not_configured(self):
        gate = gate_for("charts_index", NavigraphConfig(), None)
        assert (gate.allowed, gate.status) == (False, "not_configured")

    def test_configured_but_not_signed_in(self):
        cfg = NavigraphConfig(client_id="c", client_secret="s")
        gate = gate_for("charts_index", cfg, None)
        assert (gate.allowed, gate.status) == (False, "not_authenticated")

    def test_signed_in_without_charts_subscription(self):
        cfg = NavigraphConfig(client_id="c", client_secret="s")
        gate = gate_for("charts_index", cfg, _tokens(["fmsdata"]))
        assert (gate.allowed, gate.status) == (False, "not_subscribed")
        assert gate.required_subscription == "charts"
        assert "NZWN" in gate.detail  # demo airports named for the crew

    def test_signed_in_with_charts_subscription(self):
        cfg = NavigraphConfig(client_id="c", client_secret="s")
        gate = gate_for("charts_index", cfg, _tokens(["charts"]))
        assert (gate.allowed, gate.status) == (True, "available")

    def test_tiles_need_their_own_subscription(self):
        cfg = NavigraphConfig(client_id="c", client_secret="s")
        assert gate_for("tile", cfg, _tokens(["charts"])).status == "available"
        assert gate_for("tile", cfg, _tokens(["charts", "tiles"])).allowed is True

    def test_notam_gate_depends_on_operator_feed_not_navigraph(self):
        cfg = NavigraphConfig(client_id="c", client_secret="s")
        assert gate_for("notam", cfg, _tokens(["charts"])).status == "not_configured"
        cfg2 = NavigraphConfig(
            client_id="c", client_secret="s", notam_url="https://notam.example/api"
        )
        assert gate_for("notam", cfg2, None).allowed is True


# ---------------------------------------------------------------------------
# Cache + offline fallback
# ---------------------------------------------------------------------------


class TestCache:
    def test_fresh_then_expired(self):
        now = [1000.0]
        cache = TtlCache(clock=lambda: now[0])
        cache.put("k", "airport", {"a": 1}, ttl=60)
        assert cache.get("k").value == {"a": 1}
        now[0] += 61
        assert cache.get("k") is None

    def test_expired_entry_is_still_available_as_stale(self):
        now = [1000.0]
        cache = TtlCache(clock=lambda: now[0])
        cache.put("k", "notam", {"a": 1}, ttl=10)
        now[0] += 500
        stale = cache.get_stale("k")
        assert stale is not None and stale.age(now[0]) == 500

    def test_binary_payload_survives_a_restart_via_disk(self, tmp_path):
        directory = str(tmp_path / "ngcache")
        TtlCache(directory).put("tile:1", "airport", b"\x89PNG-data", ttl=600)
        reloaded = TtlCache(directory).get("tile:1")
        assert reloaded is not None and reloaded.value == b"\x89PNG-data"

    def test_unwritable_cache_dir_does_not_break_the_cache(self, tmp_path):
        blocker = tmp_path / "notadir"
        blocker.write_text("x", encoding="utf-8")
        cache = TtlCache(str(blocker / "sub"))
        cache.put("k", "notam", {"a": 1}, ttl=60)
        assert cache.get("k").value == {"a": 1}


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_burst_is_capped(self):
        limiter = RateLimiter(rpm=60, burst=3, clock=lambda: 0.0)
        for _ in range(3):
            limiter.acquire()
        with pytest.raises(RateLimitExceeded) as exc:
            limiter.acquire()
        assert exc.value.retry_after > 0
        assert exc.value.scope == "local"

    def test_bucket_refills_over_time(self):
        now = [0.0]
        limiter = RateLimiter(rpm=60, burst=1, clock=lambda: now[0])
        limiter.acquire()
        with pytest.raises(RateLimitExceeded):
            limiter.acquire()
        now[0] += 1.0  # 60 rpm == 1 token/s
        limiter.acquire()

    def test_upstream_429_blocks_every_request_for_retry_after(self):
        now = [0.0]
        limiter = RateLimiter(rpm=600, burst=50, clock=lambda: now[0])
        assert limiter.penalise("30") == 30.0
        with pytest.raises(RateLimitExceeded) as exc:
            limiter.acquire()
        assert exc.value.scope == "upstream"
        now[0] += 31
        limiter.acquire()

    def test_429_without_retry_after_uses_a_default_backoff(self):
        limiter = RateLimiter(rpm=600, burst=50)
        assert limiter.penalise(None) == 60.0
        assert limiter.status()["backing_off"] is True

    def test_client_honours_upstream_429_and_serves_cache(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(200, json={"airport": "OTHH"})
            return httpx.Response(429, headers={"Retry-After": "120"}, json={})

        cache = TtlCache(clock=lambda: 1000.0)
        client = _client(handler, subscriptions=["charts"], cache=cache)
        first = _run(client.airport("OTHH"))
        assert first["status"] == "ok"

        cache.invalidate("airport:OTHH")
        cache.put("airport:OTHH", "airport", {"airport": "OTHH"}, ttl=0)
        second = _run(client.airport("OTHH"))
        assert second["status"] == "stale"
        assert client.limiter.status()["backing_off"] is True

    def test_cached_reads_do_not_consume_rate_budget(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"airport": "OTHH"})

        limiter = RateLimiter(rpm=60, burst=1)
        client = _client(handler, subscriptions=["charts"], limiter=limiter)
        _run(client.airport("OTHH"))
        # Budget is now empty; a second call must come from the cache.
        for _ in range(5):
            assert _run(client.airport("OTHH"))["status"] == "ok"


# ---------------------------------------------------------------------------
# Client against Navigraph-shaped payloads
# ---------------------------------------------------------------------------


class TestNavigraphClient:
    def test_charts_index_hits_the_documented_endpoint(self):
        seen = {}
        fixture = json.loads((FIXTURES / "charts_othh.json").read_text("utf-8"))

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json=fixture)

        client = _client(handler, subscriptions=["charts"])
        result = _run(client.charts_index("othh", version="STD", rules="IFR"))
        assert result["status"] == "ok"
        assert len(result["data"]["charts"]) == 3
        assert seen["url"].startswith("https://api.navigraph.com/v2/charts/OTHH")
        assert "version=STD" in seen["url"] and "rules=IFR" in seen["url"]
        assert seen["auth"].startswith("Bearer ")

    def test_chart_image_is_returned_as_bytes_without_caching(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})

        client = _client(handler, subscriptions=["charts"])
        first = _run(client.chart_image("OTHH", "othh10-1_d.png"))
        second = _run(client.chart_image("OTHH", "othh10-1_d.png"))
        assert first["data"] == b"\x89PNG"
        assert second["status"] == "ok" and calls["n"] == 2

    @pytest.mark.parametrize(
        "filename",
        [
            "../../etc/passwd",
            "..%2f..%2fetc%2fpasswd",
            r"..\..\windows\win.ini",
            "chart.png?next=https://evil.example",
            "chart.png#@evil.example",
            "https:%2f%2fevil.example/chart.png",
        ],
    )
    def test_chart_filename_attack_is_rejected_before_any_request(self, filename):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("invalid chart filename must not reach the network")

        client = _client(handler, subscriptions=["charts"])
        with pytest.raises(NavigraphUnavailable) as exc:
            _run(client.chart_image("OTHH", filename))
        assert exc.value.status == "bad_request"

    @pytest.mark.parametrize(
        "icao",
        ["../x", "OT%2fHH", "OTHH?x=1", "OTHH#fragment", "evil.example@OTHH"],
    )
    def test_icao_attack_is_rejected_before_any_request(self, icao):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("invalid ICAO must not reach the network")

        client = _client(handler, subscriptions=["charts"])
        with pytest.raises(NavigraphUnavailable) as exc:
            _run(client.charts_index(icao))
        assert exc.value.status == "bad_request"

    def test_valid_identifiers_are_preserved(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"charts": []})

        client = _client(handler, subscriptions=["charts"])
        _run(client.charts_index("othh"))
        assert seen == ["https://api.navigraph.com/v2/charts/OTHH?version=STD&rules=IFR"]

    def test_outbound_sink_rejects_a_malicious_host(self):
        cfg = NavigraphConfig(
            client_id="c",
            client_secret="s",
            risk_url="https://risk.example/api",
        )
        client = _client(lambda r: httpx.Response(200), config=cfg)
        with pytest.raises(NavigraphUnavailable) as exc:
            _run(
                client.fetch(
                    "risk",
                    "risk:malicious",
                    "https://risk.example.evil.test/api?host=risk.example",
                    authenticated=False,
                )
            )
        assert exc.value.status == "bad_request"

    def test_loopback_operator_feed_is_allowed_for_fixtures(self):
        cfg = NavigraphConfig(notam_url="http://127.0.0.1:8765/notams")
        client = _client(
            lambda request: httpx.Response(200, json={"notams": []}),
            config=cfg,
        )
        result = _run(client.notams(["OTHH"]))
        assert result["status"] == "ok"

    def test_tile_uses_cloudfront_cookies_not_a_bearer_only(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["cookie"] = request.headers.get("Cookie", "")
            seen["url"] = str(request.url)
            return httpx.Response(200, content=b"tile")

        client = _client(handler, subscriptions=["charts", "tiles"])
        result = _run(client.enroute_tile("ifr.hi.day", 4, 9, 6, retina=True))
        assert result["status"] == "ok"
        assert "CloudFront-Policy" in seen["cookie"]
        assert seen["url"].endswith("/styles/ifr.hi.day/4/9/6@2x.png")

    def test_invalid_tile_coordinates_are_rejected_before_any_request(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not reach the network")

        client = _client(handler, subscriptions=["charts", "tiles"])
        for args in (("bogus.layer", 1, 0, 0), ("ifr.hi.day", 25, 0, 0), ("ifr.hi.day", 1, 9, 0)):
            with pytest.raises(NavigraphUnavailable) as exc:
                _run(client.enroute_tile(*args))
            assert exc.value.status == "bad_request"

    def test_navdata_packages_report_the_airac_cycle(self):
        fixture = json.loads((FIXTURES / "navdata_packages.json").read_text("utf-8"))
        client = _client(
            lambda r: httpx.Response(200, json=fixture), subscriptions=["fmsdata"]
        )
        result = _run(client.navdata_packages("current"))
        assert result["data"][0]["cycle"] == "2610"

    def test_without_subscription_no_request_is_made(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("gated datatype must not reach Navigraph")

        client = _client(handler, subscriptions=["fmsdata"])
        with pytest.raises(NavigraphUnavailable) as exc:
            _run(client.charts_index("OTHH"))
        assert exc.value.status == "not_subscribed"

    def test_network_failure_falls_back_to_the_last_cache(self):
        state = {"fail": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if state["fail"]:
                raise httpx.ConnectError("no route to host")
            return httpx.Response(200, json={"airport": "OTHH"})

        now = [1000.0]
        cache = TtlCache(clock=lambda: now[0])
        client = _client(handler, subscriptions=["charts"], cache=cache)
        _run(client.airport("OTHH"))
        now[0] += 10**6  # cache expires
        state["fail"] = True
        result = _run(client.airport("OTHH"))
        assert result["status"] == "stale"
        assert result["data"] == {"airport": "OTHH"}
        assert result["age_seconds"] > 0
        assert "offline" in result["note"]

    def test_network_failure_without_cache_is_a_declared_status(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        client = _client(handler, subscriptions=["charts"])
        with pytest.raises(NavigraphUnavailable) as exc:
            _run(client.charts_index("OTHH"))
        assert exc.value.status == "offline"

    def test_status_payload_is_secret_free(self):
        cfg = NavigraphConfig(client_id="cid", client_secret="topsecret")
        client = _client(lambda r: httpx.Response(200), subscriptions=["charts"], config=cfg)
        blob = json.dumps(client.status())
        assert "topsecret" not in blob
        assert client.status()["datatypes"]["charts_index"]["allowed"] is True


# ---------------------------------------------------------------------------
# NOTAM parsing
# ---------------------------------------------------------------------------


class TestNotamParsing:
    def test_single_icao_notam_fields(self):
        notam = parse_icao_notam(
            "A1234/26 NOTAMN\n"
            "Q) OTDF/QMRLC/IV/NBO/A/000/999/2516N05133E005\n"
            "A) OTHH B) 2610080600 C) 2610081800\n"
            "E) RWY 16L/34R CLOSED DUE WIP"
        )
        assert notam.id == "A1234/26"
        assert notam.icao == "OTHH"
        assert notam.q_code == "QMRLC"
        assert notam.start == "2026-10-08T06:00:00Z"
        assert notam.end == "2026-10-08T18:00:00Z"
        assert "RWY 16L/34R CLOSED" in notam.text
        assert notam.severity == "critical"

    def test_permanent_notam_has_no_end(self):
        notam = parse_icao_notam(
            "A1235/26 NOTAMN\nA) OTHH B) 2610080000 C) PERM\nE) NEW TWY M7 AVBL"
        )
        assert notam.permanent is True and notam.end is None

    def test_full_fixture_block_parses_and_sorts_by_severity(self):
        raw = (FIXTURES / "notams_icao.txt").read_text("utf-8")
        notams = parse_notam_records(raw)
        assert len(notams) == 4
        assert [n.severity for n in notams][0] == "critical"
        assert {n.icao for n in notams} == {"OTHH", "EGLL"}

    def test_icao_filter_is_applied(self):
        raw = (FIXTURES / "notams_icao.txt").read_text("utf-8")
        assert all(n.icao == "EGLL" for n in parse_notam_records(raw, ["EGLL"]))

    def test_structured_json_records_are_normalised(self):
        payload = {
            "notams": [
                {
                    "id": "A0001/26",
                    "icao": "othh",
                    "text": "TWY A CLSD",
                    "qCode": "qmxlc",
                    "start": "2026-10-08T06:00:00Z",
                },
                {"notamId": "A0002/26", "location": "EGLL", "body": "WIP APRON 2"},
            ]
        }
        notams = parse_notam_records(payload)
        assert {n.id for n in notams} == {"A0001/26", "A0002/26"}
        assert notams[0].icao == "OTHH" and notams[0].q_code == "QMXLC"

    def test_icao_keyed_map_shape_is_supported(self):
        payload = {"OTHH": "A1/26 NOTAMN\nE) RWY CLSD", "EGLL": []}
        notams = parse_notam_records(payload)
        assert len(notams) == 1 and notams[0].icao == "OTHH"

    def test_malformed_records_are_skipped_not_raised(self):
        assert parse_notam_records([None, {}, 42, "", {"nothing": 1}]) == []
        assert parse_icao_notam("") is None

    def test_summary_counts_stations_for_the_inbox_badge(self):
        raw = (FIXTURES / "notams_icao.txt").read_text("utf-8")
        summary = notam_summary(parse_notam_records(raw))
        assert summary["total"] == 4
        assert summary["station_count"] == 2
        assert summary["counts"]["critical"] >= 1

    def test_invalid_notam_timestamp_is_dropped_not_fatal(self):
        notam = parse_icao_notam("A9/26 NOTAMN\nA) OTHH B) 9999999999 C) 2610081800\nE) X")
        assert notam is not None and notam.start is None


# ---------------------------------------------------------------------------
# NAT tracks
# ---------------------------------------------------------------------------


class TestNatTracks:
    def test_real_nat_track_message_parses(self):
        message = (FIXTURES / "nat_track_message.txt").read_text("utf-8")
        tracks = parse_nat_track_message(message)
        names = [t.name for t in tracks]
        assert names == ["NAT A", "NAT B", "NAT C"]
        assert tracks[0].direction == "WESTBOUND"
        assert tracks[0].tmi == "280"
        assert tracks[0].track.startswith("VENIR 55/20")
        assert "RAFIN" in tracks[0].track

    def test_levels_are_split_out_of_the_track_string(self):
        tracks = parse_nat_track_message(
            "WESTBOUND\nA VENIR 55/20 RAFIN 350 360 370\n"
        )
        assert tracks[0].levels == ["FL350", "FL360", "FL370"]
        assert "350" not in tracks[0].track

    def test_validity_window_is_extracted(self):
        message = (FIXTURES / "nat_track_message.txt").read_text("utf-8")
        assert parse_nat_track_message(message)[0].valid == "07/1130Z-07/1900Z"

    def test_empty_or_noise_input_yields_no_tracks(self):
        assert parse_nat_track_message("") == []
        assert parse_nat_track_message("NO TRACKS TODAY") == []


# ---------------------------------------------------------------------------
# Operational risk
# ---------------------------------------------------------------------------


class TestRiskParsing:
    def test_fixture_bulletin_splits_airspace_and_operator(self):
        payload = json.loads((FIXTURES / "risk_bulletin.json").read_text("utf-8"))
        risks = parse_risk_records(payload)
        airspace = [r for r in risks if r.kind == "airspace"]
        operator = [r for r in risks if r.kind == "operator"]
        assert airspace[0].region == "PERSIAN GULF & GULF OF OMAN"
        assert airspace[0].status == "ACTIVE"
        assert len(operator) == 3

    def test_level_is_parsed_out_of_a_textual_label(self):
        risks = parse_risk_records({"operator": [{"country": "UAE", "level": "LEVEL 3 CAUTION"}]})
        assert risks[0].level == 3 and risks[0].status == "LEVEL 3 CAUTION"

    def test_records_without_a_region_are_dropped(self):
        assert parse_risk_records([{"status": "ACTIVE"}, None, 7]) == []


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------


def _app(client: NavigraphClient) -> TestClient:
    from crew_platform.navigraph import routes as navigraph_routes

    app = FastAPI()
    app.include_router(navigraph_routes.router)
    navigraph_routes.get_client.__globals__["_client"] = client
    return TestClient(app)


class TestApi:
    def test_status_endpoint_without_any_key(self):
        client = _client(lambda r: httpx.Response(200), config=NavigraphConfig())
        body = _app(client).get("/api/crew/navigraph/status").json()
        assert body["configured"] is False
        assert body["datatypes"]["charts_index"]["status"] == "not_configured"
        # Only the boolean readiness flag may appear, never a value.
        assert body["config"]["client_secret_set"] is False
        assert "secret\":" not in json.dumps(body).replace("_set\":", "")

    def test_charts_without_subscription_is_200_not_subscribed(self):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not call Navigraph")

        api = _app(_client(handler, subscriptions=["fmsdata"]))
        resp = api.get("/api/crew/navigraph/charts/OTHH")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_subscribed"
        assert body["data"] is None

    def test_charts_with_subscription_returns_the_index(self):
        fixture = json.loads((FIXTURES / "charts_othh.json").read_text("utf-8"))
        api = _app(
            _client(lambda r: httpx.Response(200, json=fixture), subscriptions=["charts"])
        )
        body = api.get("/api/crew/navigraph/charts/OTHH").json()
        assert body["status"] == "ok" and body["chart_count"] == 3

    def test_chart_image_is_proxied_as_png(self):
        api = _app(
            _client(
                lambda r: httpx.Response(200, content=b"\x89PNG"),
                subscriptions=["charts"],
            )
        )
        resp = api.get("/api/crew/navigraph/charts/OTHH/othh10-1_d.png")
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == b"\x89PNG"
        assert resp.headers["X-Navigraph-Status"] == "ok"

    def test_chart_image_degradation_is_json_not_a_broken_image(self):
        api = _app(_client(lambda r: httpx.Response(200), subscriptions=["fmsdata"]))
        resp = api.get("/api/crew/navigraph/charts/OTHH/othh10-1_d.png")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json()["status"] == "not_subscribed"

    def test_notams_endpoint_normalises_the_feed(self):
        raw = (FIXTURES / "notams_icao.txt").read_text("utf-8")
        cfg = NavigraphConfig(
            client_id="c", client_secret="s", notam_url="https://notam.example/api"
        )
        api = _app(
            _client(
                lambda r: httpx.Response(200, json={"notams": [raw]}),
                subscriptions=["charts"],
                config=cfg,
            )
        )
        body = api.get("/api/crew/navigraph/notams?icao=OTHH,EGLL").json()
        assert body["status"] == "ok"
        assert body["summary"]["total"] == 4
        assert body["stations_requested"] == ["OTHH", "EGLL"]
        assert body["data"][0]["severity"] == "critical"

    def test_notams_are_not_dropped_on_an_iata_icao_mismatch(self):
        """A DOH/LHR request must still surface the feed's OTHH/EGLL records."""
        raw = (FIXTURES / "notams_icao.txt").read_text("utf-8")
        cfg = NavigraphConfig(
            client_id="c", client_secret="s", notam_url="https://notam.example/api"
        )
        api = _app(
            _client(
                lambda r: httpx.Response(200, json={"notams": [raw]}),
                subscriptions=["charts"],
                config=cfg,
            )
        )
        body = api.get("/api/crew/navigraph/notams?icao=DOH,LHR").json()
        assert body["summary"]["total"] == 4
        assert body["stations_requested"] == ["DOH", "LHR"]

    def test_notams_without_a_feed_configured(self):
        api = _app(_client(lambda r: httpx.Response(200), subscriptions=["charts"]))
        body = api.get("/api/crew/navigraph/notams?icao=OTHH").json()
        assert body["status"] == "not_configured"
        assert body["data"] == []
        assert body["summary"]["total"] == 0

    def test_risks_endpoint_feeds_the_edto_screen(self):
        payload = json.loads((FIXTURES / "risk_bulletin.json").read_text("utf-8"))
        cfg = NavigraphConfig(
            client_id="c", client_secret="s", risk_url="https://risk.example/api"
        )
        api = _app(
            _client(
                lambda r: httpx.Response(200, json=payload),
                subscriptions=["charts"],
                config=cfg,
            )
        )
        body = api.get("/api/crew/navigraph/risks").json()
        assert body["status"] == "ok"
        assert body["data"]["official_notices"][0]["status"] == "ACTIVE"
        assert len(body["data"]["operator_risks"]) == 3
        assert body["data"]["nat_tracks"][0]["name"] == "NAT A"

    def test_navdata_endpoint_never_leaks_the_signed_url(self):
        fixture = json.loads((FIXTURES / "navdata_packages.json").read_text("utf-8"))
        api = _app(
            _client(lambda r: httpx.Response(200, json=fixture), subscriptions=["fmsdata"])
        )
        body = api.get("/api/crew/navigraph/navdata").json()
        assert body["airac_cycle"] == "2610"
        assert body["entitled_current"] is True
        assert "signed_url" not in json.dumps(body)

    def test_device_auth_without_credentials_is_declared_not_configured(self):
        api = _app(_client(lambda r: httpx.Response(200), config=NavigraphConfig()))
        body = api.post("/api/crew/navigraph/auth/device").json()
        assert body["status"] == "not_configured"

    def test_rest_of_the_app_still_works_without_navigraph(self):
        """Hard acceptance criterion: no key must not break the EFB."""
        from optimizer.api.app import create_app

        for key in ("NAVIGRAPH_CLIENT_ID", "NAVIGRAPH_CLIENT_SECRET", "NAVIGRAPH_ACCESS_TOKEN"):
            os.environ.pop(key, None)
        from crew_platform.navigraph.client import reset_client

        reset_client()
        with TestClient(create_app()) as app_client:
            assert app_client.get("/health").json() == {"status": "ok"}
            body = app_client.get("/api/crew/navigraph/status").json()
            assert body["configured"] is False
            assert app_client.get("/api/crew/providers").status_code == 200
