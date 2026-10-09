import test from "node:test";
import assert from "node:assert/strict";

import {
  NAVIGRAPH_STATUS_LABEL,
  annotateAirspace,
  mapNavigraphEnvelope,
  mapNavigraphStatus,
  mapNotamMessages,
  mapNotamSummary,
  mapRiskView,
  routeTileGrid,
  tileForLatLon,
  tileUrl,
} from "./navigraphMappers.js";

/* ── envelope ──────────────────────────────────────────────────────── */

test("envelope: ok status is available and labelled LIVE", () => {
  const env = mapNavigraphEnvelope({ status: "ok", data: { a: 1 }, age_seconds: 3 });
  assert.equal(env.available, true);
  assert.equal(env.label, "LIVE");
  assert.deepEqual(env.data, { a: 1 });
});

test("envelope: stale status keeps the data and reports its age", () => {
  const env = mapNavigraphEnvelope({ status: "stale", data: [1], age_seconds: 7200 });
  assert.equal(env.available, true);
  assert.equal(env.label, "CACHED");
  assert.equal(env.age, "2h old");
});

test("envelope: every degraded status maps to a readable label", () => {
  for (const status of [
    "not_configured",
    "not_authenticated",
    "not_subscribed",
    "rate_limited",
    "offline",
  ]) {
    const env = mapNavigraphEnvelope({ status, data: null });
    assert.equal(env.available, false, status);
    assert.equal(env.label, NAVIGRAPH_STATUS_LABEL[status]);
    assert.ok(env.badgeClass.startsWith("badge--"));
  }
});

test("envelope: a null/garbage response degrades instead of throwing", () => {
  assert.equal(mapNavigraphEnvelope(null).status, "offline");
  assert.equal(mapNavigraphEnvelope(undefined).available, false);
  assert.equal(mapNavigraphEnvelope("nope").status, "offline");
});

test("envelope: age formatting covers seconds, minutes, hours, days", () => {
  const age = (s) => mapNavigraphEnvelope({ status: "stale", age_seconds: s }).age;
  assert.equal(age(5), "5s old");
  assert.equal(age(300), "5m old");
  assert.equal(age(7200), "2h old");
  assert.equal(age(172800), "2d old");
  assert.equal(mapNavigraphEnvelope({ status: "ok" }).age, null);
});

/* ── status / subscription gate ─────────────────────────────────────── */

const STATUS_NO_KEY = {
  configured: false,
  authenticated: false,
  subscriptions: [],
  demo_airports: ["NZWN", "YBBN"],
  datatypes: {
    charts_index: {
      datatype: "charts_index",
      allowed: false,
      status: "not_configured",
      required_subscription: "charts",
      detail: "Navigraph credentials are not configured on this installation",
    },
    tile: { allowed: false, status: "not_configured", required_subscription: "tiles" },
    airspace: { allowed: false, status: "not_configured", required_subscription: "fmsdata" },
    notam: { allowed: false, status: "not_configured", detail: "No NOTAM feed configured" },
    risk: { allowed: false, status: "not_configured" },
    nat: { allowed: false, status: "not_configured" },
    chart_image: { allowed: false, status: "not_configured" },
    airport: { allowed: false, status: "not_configured" },
  },
};

test("status: without a key every row says NOT CONFIGURED", () => {
  const view = mapNavigraphStatus(STATUS_NO_KEY);
  assert.equal(view.configured, false);
  assert.equal(view.headline, "Navigraph not configured");
  assert.ok(view.rows.length >= 6);
  assert.ok(view.rows.every((r) => r.status === "not_configured" && !r.allowed));
});

test("status: signed in with charts only gates tiles and fmsdata", () => {
  const view = mapNavigraphStatus({
    configured: true,
    authenticated: true,
    subscriptions: ["charts"],
    datatypes: {
      charts_index: { allowed: true, status: "available", required_subscription: "charts" },
      tile: { allowed: false, status: "not_subscribed", required_subscription: "tiles" },
      airspace: { allowed: false, status: "not_subscribed", required_subscription: "fmsdata" },
    },
  });
  assert.equal(view.headline, "Navigraph signed in — charts");
  const byId = Object.fromEntries(view.rows.map((r) => [r.id, r]));
  assert.equal(byId.charts_index.allowed, true);
  assert.equal(byId.tile.statusLabel, "NOT SUBSCRIBED");
  assert.equal(byId.airspace.requires, "fmsdata");
});

test("status: signed in without any subscription says demo only", () => {
  const view = mapNavigraphStatus({
    configured: true,
    authenticated: true,
    subscriptions: [],
    datatypes: {},
  });
  assert.match(view.headline, /no active subscription/);
});

test("status: configured but not signed in asks for sign-in", () => {
  const view = mapNavigraphStatus({ configured: true, authenticated: false, datatypes: {} });
  assert.match(view.headline, /sign-in required/);
});

test("status: a broken response still yields a renderable view", () => {
  const view = mapNavigraphStatus(null);
  assert.deepEqual(view.rows, []);
  assert.equal(view.configured, false);
});

/* ── NOTAM ─────────────────────────────────────────────────────────── */

const NOTAM_OK = {
  status: "ok",
  age_seconds: 12,
  data: [
    {
      id: "A1234/26",
      icao: "OTHH",
      text: "RWY 16L/34R CLOSED DUE WIP",
      q_code: "QMRLC",
      start: "2026-10-08T06:00:00Z",
      end: "2026-10-08T18:00:00Z",
      permanent: false,
      severity: "critical",
    },
    {
      id: "A0002/26",
      icao: "EGLL",
      text: "WIP APRON 2",
      severity: "info",
      permanent: true,
    },
  ],
  summary: {
    total: 2,
    counts: { critical: 1, caution: 0, info: 1 },
    stations: ["EGLL", "OTHH"],
    station_count: 2,
  },
};

test("notam: records become NOTAM-badged inbox messages", () => {
  const messages = mapNotamMessages(NOTAM_OK);
  assert.equal(messages.length, 2);
  assert.equal(messages[0].kind, "NOTAM");
  assert.equal(messages[0].badge_class, "badge--notam");
  assert.match(messages[0].title, /OTHH A1234\/26/);
  assert.match(messages[0].body, /VALIDITY: 08OCT 0600Z – 08OCT 1800Z/);
  assert.match(messages[0].body, /Q-CODE: QMRLC/);
});

test("notam: critical NOTAMs sort before info", () => {
  const shuffled = { ...NOTAM_OK, data: [...NOTAM_OK.data].reverse() };
  assert.equal(mapNotamMessages(shuffled)[0].severity, "critical");
});

test("notam: a permanent NOTAM shows PERM validity", () => {
  const messages = mapNotamMessages(NOTAM_OK);
  assert.match(messages[1].body, /VALIDITY: PERM/);
});

test("notam: stale data labels the sender with the cache age", () => {
  const messages = mapNotamMessages({ ...NOTAM_OK, status: "stale", age_seconds: 900 });
  assert.match(messages[0].sender, /cached|15m old/);
});

test("notam: degraded statuses yield no messages (inbox keeps working)", () => {
  for (const status of ["not_configured", "not_subscribed", "offline", "rate_limited"]) {
    assert.deepEqual(mapNotamMessages({ status, data: null }), []);
  }
  assert.deepEqual(mapNotamMessages(null), []);
});

test("notam summary: counts stations and severities", () => {
  const summary = mapNotamSummary(NOTAM_OK);
  assert.equal(summary.available, true);
  assert.equal(summary.total, 2);
  assert.equal(summary.stations, 2);
  assert.match(summary.text, /2 NOTAMS • 2 stations • 1 critical/);
});

test("notam summary: no feed configured explains itself", () => {
  const summary = mapNotamSummary({
    status: "not_configured",
    detail: "No NOTAM feed configured (set NAVIGRAPH_NOTAM_URL)",
  });
  assert.equal(summary.available, false);
  assert.equal(summary.label, "NOT CONFIGURED");
  assert.match(summary.text, /NAVIGRAPH_NOTAM_URL/);
});

test("notam summary: nothing fetched yet says loading, not failure", () => {
  const summary = mapNotamSummary(null);
  assert.equal(summary.available, false);
  assert.equal(summary.label, "LOADING");
  assert.doesNotMatch(summary.text, /No response|unavailable/i);
});

test("notam summary: no flight selected is its own declared state", () => {
  const summary = mapNotamSummary({
    status: "no_stations",
    data: [],
    detail: "No flight selected — open a flight to load its NOTAMs.",
  });
  assert.equal(summary.label, "NO FLIGHT");
  assert.match(summary.text, /No flight selected/);
  assert.deepEqual(mapNotamMessages({ status: "no_stations", data: [] }), []);
});

/* ── EDTO risks / NAT ──────────────────────────────────────────────── */

const STATIC_VIEW = {
  official_notices: [{ region: "PERSIAN GULF & GULF OF OMAN", status: "ACTIVE" }],
  operator_risks: [{ country: "UAE", level: "LEVEL 3 CAUTION" }],
  nat_tracks: [{ name: "NAT A", direction: "WESTBOUND", valid: "x", track: "y" }],
  generated: "15:59Z",
};

test("risks: live bulletin replaces the static snapshot", () => {
  const view = mapRiskView(
    {
      status: "ok",
      age_seconds: 5,
      data: {
        official_notices: [
          { region: "PERSIAN GULF & GULF OF OMAN", status: "ACTIVE", level: 4 },
          { region: "EASTERN MEDITERRANEAN", status: "ADVISORY", level: 2 },
        ],
        operator_risks: [
          { region: "UAE", status: "LEVEL 3 CAUTION" },
          { region: "OMAN", status: "LEVEL 3 CAUTION" },
        ],
        nat_tracks: [
          { name: "NAT A", direction: "WESTBOUND", valid: "07/1130Z-07/1900Z", track: "VENIR 55/20 RAFIN", levels: ["FL350"], tmi: "280" },
        ],
      },
    },
    STATIC_VIEW
  );
  assert.equal(view.live, true);
  assert.equal(view.official_notices.length, 2);
  assert.equal(view.operator_risks[1].country, "OMAN");
  assert.equal(view.nat_tracks[0].tmi, "280");
  assert.match(view.sourceLabel, /live/);
});

test("risks: without a feed the static snapshot is kept and labelled", () => {
  const view = mapRiskView({ status: "not_configured", detail: "no feed" }, STATIC_VIEW);
  assert.equal(view.live, false);
  assert.equal(view.statusLabel, "NOT CONFIGURED");
  assert.deepEqual(view.official_notices, STATIC_VIEW.official_notices);
  assert.match(view.sourceLabel, /static/);
});

test("risks: stale bulletin is still live data, labelled as cached", () => {
  const view = mapRiskView(
    { status: "stale", age_seconds: 3600, data: { official_notices: [{ region: "X", status: "ACTIVE" }] } },
    STATIC_VIEW
  );
  assert.equal(view.live, true);
  assert.match(view.sourceLabel, /cached 1h old/);
});

test("risks: empty live sections stay empty and expose section state", () => {
  const view = mapRiskView(
    { status: "ok", data: { official_notices: [], operator_risks: [], nat_tracks: [] } },
    STATIC_VIEW
  );
  assert.deepEqual(view.official_notices, []);
  assert.deepEqual(view.operator_risks, []);
  assert.deepEqual(view.nat_tracks, []);
  assert.deepEqual(view.sectionState, {
    official_notices: "empty",
    operator_risks: "empty",
    nat_tracks: "empty",
  });
  assert.equal(view.sourceLabel, "operator risk feed • live");
});

/* ── tiles ─────────────────────────────────────────────────────────── */

test("tile: known lat/lon maps to the documented web-mercator tile", () => {
  assert.deepEqual(tileForLatLon(0, 0, 0), { z: 0, x: 0, y: 0 });
  assert.deepEqual(tileForLatLon(0, 0, 1), { z: 1, x: 1, y: 1 });
  // Doha (OTHH) at z=4 — northern hemisphere, eastern longitude.
  const doha = tileForLatLon(25.2731, 51.6081, 4);
  assert.equal(doha.z, 4);
  assert.ok(doha.x > 8 && doha.x < 12);
  assert.ok(doha.y >= 6 && doha.y <= 7);
});

test("tile: invalid coordinates return null rather than NaN tiles", () => {
  assert.equal(tileForLatLon(null, 10, 4), null);
  assert.equal(tileForLatLon(10, undefined, 4), null);
  assert.equal(tileForLatLon(NaN, NaN, 4), null);
});

test("tile: latitude is clamped to the mercator limit", () => {
  const tile = tileForLatLon(89.9, 0, 3);
  assert.ok(tile.y >= 0 && tile.y < 8);
});

test("route tile grid: stays within the tile budget", () => {
  const grid = routeTileGrid(
    [
      { lat: 25.2731, lon: 51.6081 },
      { lat: 40.0, lon: 25.0 },
      { lat: 51.47, lon: -0.4543 },
    ],
    { maxTiles: 12, maxZoom: 6 }
  );
  assert.ok(grid);
  assert.ok(grid.tiles.length <= 12, `got ${grid.tiles.length} tiles`);
  assert.equal(grid.tiles.length, grid.cols * grid.rows);
  assert.ok(grid.z <= 6);
});

test("route tile grid: a tiny budget still produces a valid grid", () => {
  const grid = routeTileGrid(
    [
      { lat: 25.2731, lon: 51.6081 },
      { lat: 51.47, lon: -0.4543 },
    ],
    { maxTiles: 2, maxZoom: 6 }
  );
  assert.ok(grid);
  assert.ok(grid.tiles.length <= 2);
});

test("route tile grid: fewer than two valid points yields null", () => {
  assert.equal(routeTileGrid([]), null);
  assert.equal(routeTileGrid([{ lat: 1, lon: 2 }]), null);
  assert.equal(routeTileGrid([{ lat: null, lon: null }, { lat: 1, lon: 2 }]), null);
});

test("tile url: points at the exact backend proxy origin, never Navigraph", () => {
  const url = tileUrl("", "ifr.hi.day", { z: 4, x: 9, y: 6 }, true);
  assert.equal(url, "/api/crew/navigraph/tiles/ifr.hi.day/4/9/6?retina=true");
  const parsed = new URL(url, "https://efb.local");
  assert.equal(parsed.protocol, "https:");
  assert.equal(parsed.hostname, "efb.local");
  assert.equal(parsed.pathname, "/api/crew/navigraph/tiles/ifr.hi.day/4/9/6");
  assert.equal(tileUrl("", "ifr.hi.day", null), null);
});

/* ── airspace annotation ───────────────────────────────────────────── */

test("airspace: waypoint rows are annotated with the AIRAC cycle", () => {
  const out = annotateAirspace(
    [{ ident: "ALSAD", airway: "M318" }, { ident: "DAVOL", airway: "M318" }],
    { status: "ok", data: [{ cycle: "2610" }], airac_cycle: "2610", entitled_current: true }
  );
  assert.equal(out.cycle, "2610");
  assert.equal(out.entitled, true);
  assert.equal(out.rows[0].airspaceSource, "AIRAC 2610");
  assert.equal(out.rows.length, 2);
});

test("airspace: an outdated package is labelled as unsubscribed", () => {
  const out = annotateAirspace([{ ident: "ALSAD" }], {
    status: "ok",
    data: [{ cycle: "2401", package_status: "outdated" }],
    airac_cycle: "2401",
    entitled_current: false,
  });
  assert.match(out.rows[0].airspaceSource, /outdated/);
});

test("airspace: no subscription leaves the rows untouched but labelled", () => {
  const out = annotateAirspace([{ ident: "ALSAD", airway: "M318" }], {
    status: "not_subscribed",
    data: null,
    detail: "no fmsdata",
  });
  assert.equal(out.available, false);
  assert.equal(out.rows[0].airway, "M318");
  assert.equal(out.rows[0].airspaceSource, null);
  assert.equal(out.statusLabel, "NOT SUBSCRIBED");
});

test("airspace: non-array rows do not throw", () => {
  assert.deepEqual(annotateAirspace(null, null).rows, []);
});
