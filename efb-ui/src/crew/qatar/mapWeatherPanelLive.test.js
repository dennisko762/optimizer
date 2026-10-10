/**
 * M3 live route overlay on the ACTIVE MapLibre Route tab — regression.
 *
 * Independent review of PR #16 found the M3 live route behaviour
 * unreachable: the Route tab rendered only `MapWeatherPanel`, while the
 * SimConnect aircraft symbol and passed-waypoint marking lived in the
 * unreferenced `RouteScreen` SVG component. Nothing in the shipped UI
 * could show a live aircraft on the route again.
 *
 * Two layers of regression here:
 *
 * 1. behaviour — `mapLiveOverlay` derives the aircraft GeoJSON + passed fix
 *    keys + HUD numbers from telemetry, and stays empty when the sim is
 *    not connected (no invented position);
 * 2. reachability — the Route tab must pass the live telemetry props to
 *    `MapWeatherPanel`, and `MapWeatherPanel` must consume them and add
 *    the live layers. Asserted on the source (same approach as
 *    useSimTelemetry.test.js) because this project's frontend test suite
 *    has no DOM/renderer setup.
 */

import { test } from "vitest";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { mapLiveOverlay } from "./liveMappers.js";

const shell = readFileSync(
  fileURLToPath(new URL("./QatarShell.jsx", import.meta.url)), "utf8"
);
const panel = readFileSync(
  fileURLToPath(new URL("./MapWeatherPanel.jsx", import.meta.url)), "utf8"
);

// EDDF → a few fixes eastbound; aircraft just past the 2nd fix.
const POINTS = [
  { ident: "EDDF", lat: 50.0, lon: 8.5, occurrence: 0 },
  { ident: "ERNAS", lat: 50.0, lon: 12.0, occurrence: 0 },
  { ident: "TUVLU", lat: 50.0, lon: 16.0, occurrence: 0 },
  { ident: "LKPR", lat: 50.0, lon: 20.0, occurrence: 0 },
];
const TELEM = { rawSummary: { latitude: 50.0, longitude: 14.0 } };

/* ── behaviour ─────────────────────────────────────────────────────── */

test("mapLiveOverlay builds the aircraft symbol and passed state", () => {
  const o = mapLiveOverlay(POINTS, TELEM, true, { flightLevel: 360 });
  assert.equal(o.connected, true);
  assert.deepEqual(o.position, { lat: 50.0, lon: 14.0 });
  assert.equal(o.aircraftGeoJson.features.length, 1);
  assert.deepEqual(
    o.aircraftGeoJson.features[0].geometry.coordinates, [14.0, 50.0]
  );
  assert.equal(o.label, "FL360");
  assert.equal(o.aircraftGeoJson.features[0].properties.label, "FL360");
  // the fixes behind the aircraft are flagged passed, the ones ahead are not
  assert.deepEqual(o.passedIdents, ["EDDF"]);
  assert.deepEqual(o.passedKeys, ["EDDF#0"]);
  assert.ok(!o.passedIdents.includes("TUVLU"));
  assert.ok(o.remainingNm > 0 && o.remainingNm < o.totalNm);
});

test("mapLiveOverlay draws nothing without a live sim connection", () => {
  const off = mapLiveOverlay(POINTS, TELEM, false, { flightLevel: 360 });
  assert.equal(off.connected, false);
  assert.equal(off.position, null);
  assert.deepEqual(off.aircraftGeoJson, { type: "FeatureCollection", features: [] });
  assert.deepEqual(off.passedKeys, []);
  assert.equal(off.remainingNm, null);
});

test("mapLiveOverlay never fabricates a position from empty telemetry", () => {
  for (const t of [null, {}, { rawSummary: {} }, { rawSummary: { latitude: 50 } }]) {
    const o = mapLiveOverlay(POINTS, t, true, null);
    assert.equal(o.connected, false, `telemetry ${JSON.stringify(t)} must stay disconnected`);
    assert.equal(o.aircraftGeoJson.features.length, 0);
  }
});

test("mapLiveOverlay keys passed fixes per occurrence (repeated idents)", () => {
  const dup = [
    { ident: "AAA", lat: 50.0, lon: 8.0, occurrence: 0 },
    { ident: "BBB", lat: 50.0, lon: 12.0, occurrence: 0 },
    { ident: "AAA", lat: 50.0, lon: 16.0, occurrence: 1 },
    { ident: "CCC", lat: 50.0, lon: 20.0, occurrence: 0 },
  ];
  const o = mapLiveOverlay(dup, { rawSummary: { latitude: 50.0, longitude: 14.0 } }, true, null);
  assert.deepEqual(o.passedKeys, ["AAA#0"]);
  assert.ok(!o.passedKeys.includes("AAA#1"), "the second occurrence is still ahead");
  assert.equal(o.label, "LIVE", "no live FL yet -> generic LIVE label, never a guess");
});

test("mapLiveOverlay tolerates a missing/short route", () => {
  const o = mapLiveOverlay([], TELEM, true, null);
  assert.equal(o.connected, false);
  assert.deepEqual(o.position, { lat: 50.0, lon: 14.0 });
});

/* ── reachability (the reviewed regression) ────────────────────────── */

test("the active Route tab renders MapWeatherPanel WITH the live props", () => {
  const routeTab = /\{tab === "route" && \(\s*<MapWeatherPanel([\s\S]*?)\/>/.exec(shell);
  assert.ok(routeTab, "the Route tab must render MapWeatherPanel");
  const props = routeTab[1];
  for (const p of ["telemetry={telemetry}", "simConnected={simConnected}", "live={live}"]) {
    assert.ok(
      props.includes(p),
      `the Route tab must pass ${p} — without it the M3 live route is unreachable`
    );
  }
});

test("MapWeatherPanel consumes the live props", () => {
  assert.match(
    panel,
    /export default function MapWeatherPanel\(\{[^}]*telemetry[^}]*simConnected[^}]*live[^}]*\}\)/,
    "MapWeatherPanel must accept telemetry/simConnected/live"
  );
  assert.match(panel, /mapLiveOverlay\(\s*route\.points,\s*telemetry,\s*simConnected,\s*live\s*\)/);
  // behaviour of the layers/HUD themselves is asserted by rendering the real
  // component in MapWeatherPanel.test.jsx
});
