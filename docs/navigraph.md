# Navigraph integration (M4)

Subscription-gated Jeppesen charts, enroute tiles, FMS-data entitlement,
NOTAMs and operational-risk/NAT data for the Qatar EFB.

## Hard rules

1. **Credentials live only in the environment.** Nothing in this repo reads
   or writes a Navigraph credential file inside the working tree, and the
   status API reports booleans and claim names only — never a value.
2. **No key must never break the EFB.** Every Navigraph route answers
   `200` with a declared `status` the UI renders as a readable state. The
   rest of the app is unaffected.
3. **Navigraph's ToS forbids bulk retrieval.** Every request is cached and
   rate-limited client-side, an upstream `429` is honoured with its
   `Retry-After`, and the route chart underlay is capped at 12 tiles.

## Environment variables

| Variable | Required | Meaning |
| --- | --- | --- |
| `NAVIGRAPH_CLIENT_ID` | yes | Client id issued by Navigraph (`dev@navigraph.com`) |
| `NAVIGRAPH_CLIENT_SECRET` | yes | Client secret issued by Navigraph |
| `NAVIGRAPH_SCOPES` | no | Default `openid offline_access charts tiles fmsdata` |
| `NAVIGRAPH_ACCESS_TOKEN` | no | Inject a token instead of running the device flow |
| `NAVIGRAPH_REFRESH_TOKEN` | no | Matching refresh token |
| `NAVIGRAPH_TOKEN_STORE` | no | Absolute path for the rotated refresh token (written `0600`, **must sit outside the repo**). Unset = memory only |
| `NAVIGRAPH_RATE_LIMIT_RPM` | no | Local request ceiling, default `60` |
| `NAVIGRAPH_RATE_LIMIT_BURST` | no | Token-bucket burst, default `10` |
| `NAVIGRAPH_CACHE_DIR` | no | On-disk offline cache. Unset = memory only |
| `NAVIGRAPH_NOTAM_URL` | no | Operator NOTAM feed (see "Scope" below) |
| `NAVIGRAPH_RISK_URL` | no | Operator risk / NAT-track bulletin feed |

The backend also reads a gitignored `.env` at the repo root (see
`optimizer/api/app.py::_load_dotenv`); real environment variables always win.

## Scope — what Navigraph does and does not provide

Navigraph's public API covers **charts** (Jeppesen airport charts + enroute
bitmap tiles) and **FMS data** (AIRAC navdata packages). It does **not**
serve NOTAMs, SIGMETs, operational-risk bulletins or the NAT track message.

Those come from an operator-supplied feed configured through
`NAVIGRAPH_NOTAM_URL` / `NAVIGRAPH_RISK_URL`. The parsers in
`crew_platform/navigraph/aero.py` accept both raw ICAO-format text and
structured JSON, so most NOTAM providers plug in without code changes.
Without a feed the UI shows `NOT CONFIGURED` and the EDTO screen keeps its
static briefing snapshot, clearly labelled as such.

Airspace/airway data is delivered by Navigraph as an AIRAC **package** (a
signed-URL zip per cycle), not as per-waypoint REST queries. `/navdata`
therefore reports the entitled cycle and status and deliberately does not
download the archive — the route screen labels the waypoint table with that
AIRAC cycle instead of inventing per-waypoint values.

## Subscription gating

Entitlement comes from the `subscriptions` claim inside the access token:

| Datatype | Requires | Without it |
| --- | --- | --- |
| Airport data, charts index, chart images | `charts` | Demo airports only (`NZWN`, `YBBN`) |
| Enroute bitmap tiles | `tiles` | Layer buttons disabled |
| Airspace / airways (navdata) | `fmsdata` | An `outdated` package, labelled as such |
| NOTAM / risk / NAT | operator feed | `NOT CONFIGURED` |

Statuses the UI renders: `ok`, `stale` (cached, with age), `not_configured`,
`not_authenticated`, `not_subscribed`, `rate_limited`, `offline`.

## Pilot sign-in

Device Authorization Flow with PKCE (RFC 8628) — the flow Navigraph
recommends for in-simulator add-ons. The pilot opens the verification URI
shown on the Profile screen and enters the displayed user code; no password
ever reaches this process. Refresh tokens are **single-use**: each refresh
stores the new one.

## Caching and offline behaviour

TTL per datatype (`config.py::DATA_TTLS`), from 10 minutes for NOTAMs to
7 days for chart images. An expired entry is still served when upstream is
unreachable, labelled `stale` with its age — a live-flight EFB must not go
blank because an API call failed.

## API surface

```
GET  /api/crew/navigraph/status                     config + subscription + cache + rate state
POST /api/crew/navigraph/auth/device                start device sign-in
POST /api/crew/navigraph/auth/device/poll           poll for authorization
POST /api/crew/navigraph/auth/signout               forget tokens (cache kept)
GET  /api/crew/navigraph/airport/{icao}
GET  /api/crew/navigraph/charts/{icao}              ?version=STD|CAO&rules=IFR|VFR|ANY
GET  /api/crew/navigraph/charts/{icao}/{filename}   PNG proxy (token stays server-side)
GET  /api/crew/navigraph/tiles/{layer}/{z}/{x}/{y}  PNG proxy (CloudFront cookies stay server-side)
GET  /api/crew/navigraph/navdata                    AIRAC cycle + entitlement
GET  /api/crew/navigraph/notams?icao=A,B            normalised NOTAMs + summary
GET  /api/crew/navigraph/risks                      risk notices + NAT tracks
```

## Local verification without a subscription

`scripts/serve_navigraph_fixtures.py` serves the repo's NOTAM/risk fixtures
over HTTP so the "with data" path can be exercised end-to-end:

```
python scripts/serve_navigraph_fixtures.py 8099
NAVIGRAPH_NOTAM_URL=http://127.0.0.1:8099/notams \
NAVIGRAPH_RISK_URL=http://127.0.0.1:8099/risks \
  python -m uvicorn optimizer.api.app:app --port 8003
```

Chart and tile paths still require a real Navigraph client id/secret and a
subscribed account; without them those datatypes report `not_configured`
and the UI disables them rather than erroring.
