"""FastAPI routes for Navigraph data — all subscription-gated.

Contract with the UI: a route NEVER returns 5xx because of a missing key,
a missing subscription or an upstream outage. It returns 200 with a
declared ``status`` the EFB can render:

    ok             fresh live data
    stale          cached data, with age_seconds, upstream unavailable
    not_configured no credentials on this installation
    not_authenticated  pilot has not signed in / refresh failed
    not_subscribed this Navigraph account lacks the subscription
    rate_limited   local or upstream rate budget exhausted, no cache
    offline        network failure and no cache

Only genuinely bad requests (unknown tile layer, bad filename) return 4xx.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from crew_platform.navigraph.aero import (
    notam_summary,
    parse_nat_track_message,
    parse_notam_records,
    parse_risk_records,
)
from crew_platform.navigraph.auth import DeviceFlowState, NavigraphAuthError
from crew_platform.navigraph.client import (
    NavigraphUnavailable,
    get_client,
)

router = APIRouter(prefix="/api/crew/navigraph", tags=["navigraph"])

# Pending device-authorization flows, keyed by the public user_code.
_pending_flows: dict[str, DeviceFlowState] = {}
_flow_lock = asyncio.Lock()


def _degraded(status: str, detail: str, datatype: str) -> dict[str, Any]:
    """The uniform "no data, and here is why" body."""
    return {
        "status": status,
        "datatype": datatype,
        "data": None,
        "cached": False,
        "fresh": False,
        "age_seconds": None,
        "detail": detail,
        "source": "navigraph",
    }


def _guard(datatype: str):
    """Decorator-free helper: run a coroutine, map failures to a body."""

    async def run(coro) -> dict[str, Any]:
        try:
            return await coro
        except NavigraphUnavailable as exc:
            if exc.status == "bad_request":
                raise HTTPException(status_code=400, detail=exc.detail)
            return _degraded(exc.status, exc.detail, datatype)
        except NavigraphAuthError as exc:
            return _degraded("not_authenticated", str(exc), datatype)

    return run


# ---------------------------------------------------------------------------
# Status / subscription gate
# ---------------------------------------------------------------------------


@router.get("/status")
async def navigraph_status() -> dict[str, Any]:
    """Configuration, subscription, cache and rate-limit state.

    Reports booleans and claim names only — never a credential value.
    """
    client = get_client()
    await client.ensure_tokens()
    return client.status()


# ---------------------------------------------------------------------------
# Device authorization (pilot signs in with their own Navigraph account)
# ---------------------------------------------------------------------------


class DeviceStartOut(BaseModel):
    status: str
    user_code: Optional[str] = None
    verification_uri: Optional[str] = None
    verification_uri_complete: Optional[str] = None
    interval: Optional[int] = None
    expires_in: Optional[int] = None
    detail: str = ""


@router.post("/auth/device", response_model=DeviceStartOut)
async def start_device_auth() -> DeviceStartOut:
    client = get_client()
    if not client.config.configured:
        return DeviceStartOut(
            status="not_configured",
            detail="Navigraph client credentials are not configured "
            "(NAVIGRAPH_CLIENT_ID / NAVIGRAPH_CLIENT_SECRET)",
        )
    try:
        flow = await client.auth.start_device_flow()
    except NavigraphAuthError as exc:
        return DeviceStartOut(status="error", detail=str(exc))
    async with _flow_lock:
        _pending_flows[flow.user_code] = flow
    return DeviceStartOut(status="pending", **flow.public())


class DevicePollOut(BaseModel):
    status: str
    subscriptions: list[str] = []
    detail: str = ""


@router.post("/auth/device/poll", response_model=DevicePollOut)
async def poll_device_auth(user_code: str = Query(...)) -> DevicePollOut:
    client = get_client()
    async with _flow_lock:
        flow = _pending_flows.get(user_code)
    if flow is None:
        return DevicePollOut(status="unknown_flow", detail="No such pending sign-in")
    try:
        state, tokens = await client.auth.poll_device_token(flow)
    except NavigraphAuthError as exc:
        return DevicePollOut(status="error", detail=str(exc))
    if state == "authorized" and tokens is not None:
        client.set_tokens(tokens)
        async with _flow_lock:
            _pending_flows.pop(user_code, None)
        return DevicePollOut(status="authorized", subscriptions=tokens.subscriptions)
    if state in ("expired", "denied"):
        async with _flow_lock:
            _pending_flows.pop(user_code, None)
    return DevicePollOut(status=state)


@router.post("/auth/signout")
async def navigraph_signout() -> dict[str, Any]:
    """Forget the in-memory tokens (the cache is kept for offline use)."""
    client = get_client()
    client.set_tokens(None)
    async with _flow_lock:
        _pending_flows.clear()
    return {"status": "signed_out"}


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


@router.get("/airport/{icao}")
async def navigraph_airport(icao: str) -> dict[str, Any]:
    client = get_client()
    return await _guard("airport")(client.airport(icao))


@router.get("/charts/{icao}")
async def navigraph_charts(
    icao: str,
    version: str = Query("STD"),
    rules: str = Query("IFR"),
) -> dict[str, Any]:
    client = get_client()
    result = await _guard("charts_index")(client.charts_index(icao, version, rules))
    data = result.get("data")
    if isinstance(data, dict) and isinstance(data.get("charts"), list):
        result["chart_count"] = len(data["charts"])
    return result


@router.get("/charts/{icao}/{filename}")
async def navigraph_chart_image(icao: str, filename: str) -> Response:
    """Proxy one chart image, served from cache whenever possible.

    Proxying (rather than handing the UI a Navigraph URL) is required:
    the access token must not leave the backend, and the cache is what
    keeps the EFB inside the rate limits.
    """
    client = get_client()
    result = await _guard("chart_image")(client.chart_image(icao, filename))
    payload = result.get("data")
    if not isinstance(payload, (bytes, bytearray)):
        # Declared degradation — JSON body, not an image.
        return Response(
            content=json.dumps(
                {k: v for k, v in result.items() if k != "data"}
            ),
            media_type="application/json",
            status_code=200,
        )
    return Response(
        content=bytes(payload),
        media_type="image/png",
        headers={
            "X-Navigraph-Status": str(result.get("status")),
            "X-Navigraph-Age": str(result.get("age_seconds")),
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/tiles/{layer}/{z}/{x}/{y}")
async def navigraph_tile(
    layer: str, z: int, x: int, y: int, retina: bool = Query(False)
) -> Response:
    """Proxy one enroute bitmap tile (CloudFront cookies stay server-side)."""
    client = get_client()
    result = await _guard("tile")(client.enroute_tile(layer, z, x, y, retina))
    payload = result.get("data")
    if not isinstance(payload, (bytes, bytearray)):
        return Response(
            content=json.dumps(
                {k: v for k, v in result.items() if k != "data"}
            ),
            media_type="application/json",
            status_code=200,
        )
    return Response(
        content=bytes(payload),
        media_type="image/png",
        headers={
            "X-Navigraph-Status": str(result.get("status")),
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.get("/navdata")
async def navigraph_navdata(
    package_status: str = Query("current"),
) -> dict[str, Any]:
    """FMS-data (airspace/airway) package entitlement for this pilot.

    Reports the AIRAC cycle and package status only. The package archive
    itself is deliberately NOT downloaded here — see
    ``NavigraphClient.navdata_packages``.
    """
    client = get_client()
    result = await _guard("airspace")(client.navdata_packages(package_status))
    data = result.get("data")
    packages = data if isinstance(data, list) else []
    result["data"] = [
        {
            "package_id": p.get("package_id"),
            "cycle": p.get("cycle"),
            "revision": p.get("revision"),
            "package_status": p.get("package_status"),
            "format": p.get("format"),
            # signed_url is intentionally dropped: it is a short-lived
            # credential for a multi-megabyte archive.
            "files": [
                {"key": f.get("key"), "hash": f.get("hash")}
                for f in (p.get("files") or [])
                if isinstance(f, dict)
            ],
        }
        for p in packages
        if isinstance(p, dict)
    ]
    current = next(
        (p for p in result["data"] if p.get("package_status") == "current"), None
    )
    result["airac_cycle"] = (current or (result["data"][0] if result["data"] else {})).get(
        "cycle"
    )
    result["entitled_current"] = current is not None
    return result


# ---------------------------------------------------------------------------
# NOTAM / risk / NAT (operator feeds, normalised by aero.py)
# ---------------------------------------------------------------------------


@router.get("/notams")
async def navigraph_notams(
    icao: str = Query(..., description="Comma-separated ICAO codes"),
) -> dict[str, Any]:
    codes = [c for c in (s.strip().upper() for s in icao.split(",")) if c]
    if not codes:
        raise HTTPException(status_code=400, detail="at least one ICAO code required")
    client = get_client()
    result = await _guard("notam")(client.notams(codes))
    raw = result.get("data")
    # Deliberately NOT filtered by `codes`: the feed was asked for exactly
    # these stations, and it may answer in ICAO (OTHH) for an IATA request
    # (DOH). Dropping records on a code mismatch would silently hide real
    # NOTAMs. The requested set is reported separately instead.
    notams = parse_notam_records(raw) if raw is not None else []
    result["data"] = [n.as_dict() for n in notams]
    result["summary"] = notam_summary(notams)
    result["stations_requested"] = codes
    return result


@router.get("/risks")
async def navigraph_risks() -> dict[str, Any]:
    """Operational risk notices + NAT tracks for the EDTO/Risks screen."""
    client = get_client()
    result = await _guard("risk")(client.risk_bulletin())
    raw = result.get("data")

    risks = parse_risk_records(raw) if raw is not None else []
    nat: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        message = raw.get("nat_track_message") or raw.get("natTrackMessage")
        if isinstance(message, str):
            nat = [t.as_dict() for t in parse_nat_track_message(message)]
        elif isinstance(raw.get("nat_tracks"), list):
            nat = [t for t in raw["nat_tracks"] if isinstance(t, dict)]
    elif isinstance(raw, str):
        nat = [t.as_dict() for t in parse_nat_track_message(raw)]

    result["data"] = {
        "official_notices": [r.as_dict() for r in risks if r.kind == "airspace"],
        "operator_risks": [r.as_dict() for r in risks if r.kind == "operator"],
        "nat_tracks": nat,
    }
    return result
