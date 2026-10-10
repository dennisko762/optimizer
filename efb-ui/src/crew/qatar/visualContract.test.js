/**
 * Visual-contract and derivation tests for the convergence pass
 * (card t_a4119b80 / audit t_af01ae33).
 *
 * The geometry defects the audit measured in a browser (G1 wrapped tab rows
 * at negative Y, G8 sub-44 px hit boxes) are caused by a handful of CSS
 * declarations. Asserting those declarations here makes the regression
 * deterministic and cheap: if someone restores `flex-wrap: wrap` on the pill
 * strip or drops the 44 px minimum, this file fails without a browser.
 * The live measurement at all seven viewport widths is the E2E check
 * recorded on the PR.
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "vitest";

import {
  decodeNatTrack,
  formatInboxStamp,
  defaultInboxMessages,
  interpolateGreatCircle,
  mapOverviewCards,
  mapTimesRows,
  modelSimPlan,
  modelSimRoute,
  mapOfpHero,
} from "./qatarMappers.js";
import {
  tintBasemap,
  BASEMAP_TINT,
  mapRouteForMap,
  routeBounds,
} from "./weatherMappers.js";
import { mapRiskView } from "./navigraphMappers.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const qatarCss = fs.readFileSync(path.join(here, "qatar.css"), "utf8");

/**
 * Return a declaration block for `selector`. When `must` is given, the first
 * block for that selector containing it is returned (a selector can appear
 * more than once, e.g. inside a media query).
 */
function block(css, selector, must) {
  let from = 0;
  for (;;) {
    const idx = css.indexOf(selector, from);
    assert.notEqual(idx, -1, `selector "${selector}"${must ? ` with "${must}"` : ""} not found`);
    const open = css.indexOf("{", idx);
    const close = css.indexOf("}", open);
    const body = css.slice(open + 1, close);
    if (!must || body.includes(must)) return body;
    from = close;
  }
}

/* ── G1: the pill tab strip can never wrap out of the top bar ───────── */

test("the SmartOps/Crew Desk pill strip scrolls and never wraps", () => {
  const tabs = block(qatarCss, ".qr-shell .qr-tabs");
  assert.match(tabs, /flex-wrap:\s*nowrap/);
  assert.match(tabs, /overflow-x:\s*auto/);
  assert.doesNotMatch(tabs, /flex-wrap:\s*wrap/);

  const topbar = block(qatarCss, ".qr-shell .qr-topbar {");
  assert.match(topbar, /overflow:\s*hidden/);

  // The centre cell AND the strip wrapper must be allowed to shrink, or the
  // strip keeps its 650 px min-content width and overflows the bar.
  const center = block(qatarCss, ".qr-shell .qr-topbar__center");
  assert.match(center, /min-width:\s*0/);
  for (const side of [".qr-shell .qr-topbar__left", ".qr-shell .qr-topbar__right"]) {
    assert.match(block(qatarCss, side), /min-width:\s*0/);
  }
  const strip = block(qatarCss, ".qr-shell .qr-tabstrip {");
  assert.match(strip, /min-width:\s*0/);
  assert.match(tabs, /min-width:\s*0/);
});

/* ── G8: 44 px iPad touch targets ───────────────────────────────────── */

test("interactive controls declare the 44 px iPad minimum", () => {
  const tab = block(qatarCss, ".qr-shell .qr-tab {", "min-height");
  assert.match(tab, /min-height:\s*44px/);

  const buttons = block(qatarCss, ".qr-shell button,");
  assert.match(buttons, /min-height:\s*44px/);

  const iconOnly = block(qatarCss, ".qr-shell .wx-navbtn,");
  assert.match(iconOnly, /min-width:\s*44px/);
  assert.match(iconOnly, /min-height:\s*44px/);

  const link = block(qatarCss, ".qr-shell .qr-linkbtn {\n  min-height");
  assert.match(link, /min-height:\s*44px/);

  // Crew-entered ATO/ACT chips are real controls too.
  const chip = block(qatarCss, ".qr-shell .qr-wpt__chip {");
  assert.match(chip, /min-height:\s*44px/);
});

/* ── Settings screen: shell tokens, full-width fields, tablet stacking ─ */

test("the Settings screen reuses the shell panel tokens and stays responsive", () => {
  const section = block(qatarCss, ".qr-shell .qr-settings__section {");
  // Shell tokens, not a bolted-on palette: no hardcoded brand hex here.
  assert.match(section, /var\(--qr-border-soft\)/);
  assert.doesNotMatch(section, /#[0-9a-fA-F]{6}/);
  assert.match(section, /min-width:\s*0/);

  // Inputs fill their column at every width instead of overflowing it.
  const field = block(qatarCss, ".qr-shell .qr-settings__field input {");
  assert.match(field, /width:\s*100%/);
  assert.match(field, /box-sizing:\s*border-box/);

  // Action rows wrap rather than pushing the card wider (G1's failure mode).
  const actions = block(qatarCss, ".qr-shell .qr-settings__actions {");
  assert.match(actions, /flex-wrap:\s*wrap/);

  // Tablet portrait: the buttons grow to a usable width on one row each.
  const tablet = block(qatarCss, ".qr-shell .qr-settings__actions > button");
  assert.match(tablet, /flex:\s*1 1 160px/);
});

/* ── check 6: maroon basemap, not CARTO grey ────────────────────────── */
test("the basemap tint is the DESIGN.md maroon ramp", () => {
  assert.equal(BASEMAP_TINT.background, "#2a1019");
  assert.equal(BASEMAP_TINT.land, "#5c1a2e");

  const calls = [];
  const fakeMap = {
    getStyle: () => ({
      layers: [
        { id: "background", type: "background" },
        { id: "water", type: "fill" },
        { id: "landcover_wood", type: "fill" },
        { id: "wx-route", type: "line" },
        { id: "road_major", type: "line" },
      ],
    }),
    setPaintProperty: (id, prop, value) => calls.push([id, prop, value]),
  };
  const painted = tintBasemap(fakeMap);
  assert.equal(painted, 3);
  assert.deepEqual(calls, [
    ["background", "background-color", "#2a1019"],
    ["water", "fill-color", "#1a0a12"],
    ["landcover_wood", "fill-color", "#5c1a2e"],
  ]);
  // Route/hazard layers are never repainted.
  assert.equal(calls.filter(([id]) => id.startsWith("wx-")).length, 0);
});

test("tintBasemap is inert on a map without a usable style", () => {
  assert.equal(tintBasemap(null), 0);
  assert.equal(tintBasemap({}), 0);
  assert.equal(
    tintBasemap({
      getStyle: () => {
        throw new Error("style not ready");
      },
    }),
    0
  );
});

/* ── check 7: Route fix count == Flightplan waypoint count ─────────── */

test("the derived sim-planned route has one fix per Flightplan row", () => {
  const flight = {
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
  };
  const plan = modelSimPlan(flight);
  const route = modelSimRoute(flight);
  assert.ok(plan.rows.length > 2);
  assert.equal(route.point_count, plan.rows.length);
  assert.equal(route.points.length, plan.rows.length);

  // Endpoints are the real airports, in order, and every fix has a position.
  assert.equal(route.points[0].ident, "DOH");
  assert.equal(route.points.at(-1).ident, "LHR");
  assert.equal(route.points[0].stage, "DEP");
  assert.equal(route.points.at(-1).stage, "ARR");
  for (const p of route.points) {
    assert.ok(Number.isFinite(p.lat) && Number.isFinite(p.lon), `no position for ${p.ident}`);
  }
  // Cumulative distance is monotonic along the track.
  const cums = route.points.map((p) => p.cum_nm);
  for (let i = 1; i < cums.length; i += 1) assert.ok(cums[i] >= cums[i - 1]);
  // It is labelled as derived, never as a filed route.
  assert.equal(route.derived, true);
  assert.match(route.route, /derived/i);

  // And it maps cleanly through the same mapper the backend payload uses.
  const mapped = mapRouteForMap(route);
  assert.equal(mapped.pointCount, plan.rows.length);
  assert.equal(mapped.lines.length, 1);
});

test("modelSimRoute returns null when the ICAO pair cannot be resolved", () => {
  assert.equal(modelSimRoute({ departure_icao: "DOH" }), null);
  assert.equal(modelSimRoute({ departure_icao: "ZZZZ", arrival_icao: "YYYY" }), null);
});

test("great-circle interpolation hits both endpoints and the midpoint", () => {
  const [lat0, lon0] = interpolateGreatCircle(25, 51, 51, 0, 0);
  assert.ok(Math.abs(lat0 - 25) < 1e-6 && Math.abs(lon0 - 51) < 1e-6);
  const [lat1, lon1] = interpolateGreatCircle(25, 51, 51, 0, 1);
  assert.ok(Math.abs(lat1 - 51) < 1e-6 && Math.abs(lon1 - 0) < 1e-6);
  const [latM, lonM] = interpolateGreatCircle(25, 51, 51, 0, 0.5);
  assert.ok(latM > 25 && latM < 51, "midpoint latitude between endpoints");
  assert.ok(lonM > 0 && lonM < 51, "midpoint longitude between endpoints");
  // Degenerate input stays put instead of producing NaN.
  assert.deepEqual(interpolateGreatCircle(10, 10, 10, 10, 0.5), [10, 10]);
});

/* ── G12: real inbox timestamps ─────────────────────────────────────── */

test("inbox timestamps format as the qatar-02 DD MMM · HH:MMz", () => {
  assert.equal(formatInboxStamp(Date.UTC(2026, 9, 2, 17, 34) / 1000), "02 OCT · 17:34z");
  assert.equal(formatInboxStamp("2026-10-02T17:34:00Z"), "02 OCT · 17:34z");
  // Unknown values stay unknown — no invented time.
  assert.equal(formatInboxStamp(null), null);
  assert.equal(formatInboxStamp(""), null);
  assert.equal(formatInboxStamp("not-a-date"), null);
});

test("every seeded inbox message carries a real timestamp", () => {
  const now = new Date(Date.UTC(2026, 9, 2, 16, 0));
  const messages = defaultInboxMessages({ flight_number: "QR815" }, { now });
  assert.ok(messages.length >= 6);
  for (const m of messages) {
    assert.equal(typeof m.timestamp, "number", `${m.id} has no timestamp`);
    assert.match(formatInboxStamp(m.timestamp), /^02 OCT · \d{2}:\d{2}z$/);
    // Seed rows stay flagged as seed data.
    assert.equal(m.static, true);
  }
});

/* ── G10: NAT track decoding ────────────────────────────────────────── */

test("coded NAT tracks decode into the reference lat/long chain", () => {
  assert.equal(
    decodeNatTrack("VENIR 5330/20 51/30 RAFTN"),
    "VENIR 53°30'N 020°00'W 51°00'N 030°00'W RAFTN"
  );
  // Unknown tokens pass through untouched; nothing is invented.
  assert.equal(decodeNatTrack("DOGAL FOOBAR"), "DOGAL FOOBAR");
  assert.equal(decodeNatTrack(""), null);
  assert.equal(decodeNatTrack(null), null);
});

/* ── G2: the Overview / Times view models ───────────────────────────── */

test("Overview cards describe the plan source honestly", () => {
  const flight = { flight_number: "QR815", departure_icao: "DOH", arrival_icao: "LHR" };
  const hero = mapOfpHero(flight, null);
  const simPlan = modelSimPlan(flight);

  const planned = mapOverviewCards(hero, {
    ofpData: null,
    simPlan,
    distanceNm: simPlan.distance_nm,
    simConnected: false,
    waypointCount: simPlan.rows.length,
    live: {},
  });
  const byId = Object.fromEntries(planned.cards.map((c) => [c.id, c]));
  assert.equal(byId.status.value, "SIM PLANNED");
  assert.equal(byId.route.value, "DOH → LHR");
  assert.equal(byId.waypoints.value, String(simPlan.rows.length));
  assert.equal(byId.sim.value, "DISCONNECTED");
  assert.equal(planned.provenance, "sim-planned (no OFP)");

  const none = mapOverviewCards(hero, {});
  assert.equal(none.planned, false);
  assert.equal(none.provenance, "no flightplan source");
  assert.equal(none.cards.find((c) => c.id === "status").value, "NO PLAN");
});

test("Times rows never present planned values as actuals", () => {
  const hero = mapOfpHero(
    { flight_number: "QR815", departure_icao: "DOH", arrival_icao: "LHR" },
    null
  );
  const offline = mapTimesRows(hero, { simConnected: false, timing: { ete: "06:12" } });
  assert.equal(offline.connected, false);
  for (const row of offline.rows) assert.equal(row.actual, null);

  const livev = mapTimesRows(hero, {
    simConnected: true,
    timing: { ete: "06:12", deltaMin: -4 },
  });
  const byId = Object.fromEntries(livev.rows.map((r) => [r.id, r]));
  assert.equal(byId.eet.actual, "06:12");
  assert.equal(byId.delta.actual, "-4 min");
});

/* ── G10: live NAT tracks decode too (not only the static snapshot) ── */

test("live risk NAT tracks carry the decoded coordinate chain", () => {
  const view = mapRiskView(
    {
      status: "ok",
      data: {
        official_notices: [{ region: "GULF", status: "ACTIVE" }],
        operator_risks: [{ region: "IRAN", status: "LEVEL 3" }],
        nat_tracks: [
          {
            name: "NAT A",
            direction: "WESTBOUND",
            valid: "1130-1900Z",
            track: "DOGAL 5420/20 54/30 BURAK",
            levels: ["FL350", "FL360"],
            tmi: "283",
          },
        ],
      },
    },
    { nat_tracks: [], official_notices: [], operator_risks: [] }
  );
  assert.equal(view.live, true);
  assert.equal(view.nat_tracks.length, 1);
  assert.equal(
    view.nat_tracks[0].decoded,
    "DOGAL 54°20'N 020°00'W 54°00'N 030°00'W BURAK"
  );
  assert.deepEqual(view.nat_tracks[0].levels, ["FL350", "FL360"]);
});

/* ── map camera: fitBounds needs corner bounds, not a fix list ──────── */

test("routeBounds reduces the fix list to south-west / north-east corners", () => {
  const pts = [
    { lat: 25.27, lon: 51.17 },
    { lat: 40.0, lon: 30.0 },
    { lat: 51.47, lon: -0.45 },
  ];
  assert.deepEqual(routeBounds(pts), [[-0.45, 25.27], [51.17, 51.47]]);
  // Unusable input leaves the camera alone.
  assert.equal(routeBounds([]), null);
  assert.equal(routeBounds([{ lat: 1, lon: 1 }]), null);
  assert.equal(routeBounds([{ lat: 1, lon: 1 }, { lat: 1, lon: 1 }]), null);
  assert.equal(routeBounds([{ lat: null, lon: 2 }, { lat: 3, lon: null }]), null);
});
