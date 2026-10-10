/**
 * MapWeatherPanel — M3 live route overlay regression (component level).
 *
 * Independent review of PR #16 found the M3 live route behaviour
 * unreachable: the Route tab rendered only `MapWeatherPanel`, while the
 * SimConnect aircraft symbol and passed-waypoint marking lived in the
 * unreferenced `RouteScreen` SVG component.
 *
 * This renders the REAL `MapWeatherPanel` (MapLibre stubbed, fetch stubbed)
 * and asserts the live state is actually produced and visible:
 * the aircraft source/layers are added to the map with the live position,
 * the already-passed fixes are flagged on the fix features, and the live
 * HUD chip is in the rendered output. With the sim disconnected nothing
 * live is drawn (no last-known or invented position).
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

const { mapCalls } = vi.hoisted(() => ({
  mapCalls: { sources: {}, layers: [], instances: 0 },
}));

// MapLibre is a WebGL/DOM library: stub it and record what the panel asks
// it to draw. react-test-renderer has no DOM, so the panel's own map-init
// guard (container ref) keeps the real map from ever being constructed —
// the stub only has to exist for the import.
vi.mock("maplibre-gl", () => {
  class FakeMap {
    constructor() {
      mapCalls.instances += 1;
    }
    addControl() {}
    on() {}
    once() {}
    off() {}
    isStyleLoaded() { return true; }
    getSource(id) { return mapCalls.sources[id] ? { setData: (d) => { mapCalls.sources[id] = d; } } : null; }
    addSource(id, spec) { mapCalls.sources[id] = spec.data; }
    getLayer(id) { return mapCalls.layers.find((l) => l.id === id) || null; }
    addLayer(spec) { mapCalls.layers.push(spec); }
    getLayoutProperty() { return "visible"; }
    setLayoutProperty() {}
    getCanvas() { return null; }
    fitBounds() {}
    project() { return { x: 0, y: 0 }; }
    remove() {}
    zoomIn() {}
    zoomOut() {}
    queryRenderedFeatures() { return []; }
  }
  return {
    Map: FakeMap,
    AttributionControl: class {},
    setWorkerUrl: () => {},
    default: { Map: FakeMap, AttributionControl: class {}, setWorkerUrl: () => {} },
  };
});

import MapWeatherPanel from "./MapWeatherPanel.jsx";
import { mapLiveOverlay } from "./liveMappers.js";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ROUTE_POINTS = [
  { ident: "EDDF", lat: 50.0, lon: 8.5, stage: "DEP", alt: 0 },
  { ident: "ERNAS", lat: 50.0, lon: 12.0, alt: 360 },
  { ident: "TUVLU", lat: 50.0, lon: 16.0, alt: 360 },
  { ident: "LKPR", lat: 50.0, lon: 20.0, stage: "ARR", alt: 0 },
];

const ROUTE_BODY = {
  origin: "EDDF",
  destination: "LKPR",
  cruise_fl: 360,
  points: ROUTE_POINTS,
  samples: { cycle: "20261005_18", fl: 360, offset: 0, points: [] },
};

// aircraft between ERNAS and TUVLU -> EDDF is behind it
const TELEMETRY = { rawSummary: { latitude: 50.0, longitude: 14.0 } };

function stubFetch() {
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/weather/status")) {
      return { ok: true, status: 200, json: async () => ({
        state: "idle", current_cycle: "20261005_18", last_good: "20261005_18",
        fetched_at: null, stale: false, enabled: true,
      }) };
    }
    if (u.includes("/weather/route")) {
      return { ok: true, status: 200, json: async () => ROUTE_BODY };
    }
    // hazard layers: honestly unavailable in this test
    return { ok: false, status: 503, json: async () => ({}) };
  });
}

async function render(props) {
  // react-test-renderer has no DOM: createNodeMock hands the panel a fake
  // laid-out container so its MapLibre init guard passes and the real
  // route/live layer effect runs against the stub map.
  const node = { clientWidth: 900, clientHeight: 600 };
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(MapWeatherPanel, {
        apiBase: "/bridge", utc: { time: "12:00" }, ...props,
      }),
      { createNodeMock: () => node }
    );
  });
  // let the route fetch promise + the rAF-deferred map init settle
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return renderer;
}

function text(renderer) {
  return JSON.stringify(renderer.toJSON());
}

beforeEach(() => {
  mapCalls.sources = {};
  mapCalls.layers = [];
  mapCalls.instances = 0;
  stubFetch();
  // node test environment: the panel defers map construction to a frame and
  // watches the container for layout
  globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
});

test("the live aircraft and passed fixes are rendered on the Route map", async () => {
  const renderer = await render({
    telemetry: TELEMETRY, simConnected: true, live: { flightLevel: 360 },
  });

  // the live HUD is in the DOM output (visible to the crew, not only canvas)
  const out = text(renderer);
  assert.match(out, /wx-live-hud/);
  assert.match(out, /LIVE/);
  assert.match(out, /FL360/);
  assert.match(out, /PASSED/);

  // the map actually received the live aircraft source + layers
  assert.ok(mapCalls.instances >= 1, "the MapLibre map must be constructed");
  const ac = mapCalls.sources["wx-live-ac"];
  assert.ok(ac, "wx-live-ac source must be added");
  assert.equal(ac.features.length, 1);
  assert.deepEqual(ac.features[0].geometry.coordinates, [14.0, 50.0]);
  assert.equal(ac.features[0].properties.label, "FL360");
  const layerIds = mapCalls.layers.map((l) => l.id);
  for (const id of ["wx-live-ac", "wx-live-ac-halo", "wx-live-ac-label"]) {
    assert.ok(layerIds.includes(id), `layer ${id} must be added`);
  }

  // the fixes behind the aircraft are flagged passed, the ones ahead are not
  const fixes = mapCalls.sources["wx-fixes"];
  const passed = fixes.features
    .filter((f) => f.properties.passed === "true")
    .map((f) => f.properties.ident);
  assert.deepEqual(passed, ["EDDF"]);
  assert.ok(fixes.features.some((f) => f.properties.ident === "TUVLU" && f.properties.passed === "false"));

  // the same derivation, asserted directly on the pure mapper
  const overlay = mapLiveOverlay(
    [
      { ident: "EDDF", lat: 50.0, lon: 8.5, occurrence: 0 },
      { ident: "ERNAS", lat: 50.0, lon: 12.0, occurrence: 0 },
      { ident: "TUVLU", lat: 50.0, lon: 16.0, occurrence: 0 },
      { ident: "LKPR", lat: 50.0, lon: 20.0, occurrence: 0 },
    ],
    TELEMETRY, true, { flightLevel: 360 }
  );
  assert.equal(overlay.connected, true);
  assert.deepEqual(overlay.passedKeys, ["EDDF#0"]);
});

test("nothing live is rendered while the sim is disconnected", async () => {
  const renderer = await render({
    telemetry: TELEMETRY, simConnected: false, live: { flightLevel: 360 },
  });
  const out = text(renderer);
  assert.ok(!out.includes("wx-live-hud"), "live HUD must not appear without a sim connection");
  // the aircraft source exists but is EMPTY — no last-known/invented position
  assert.deepEqual(mapCalls.sources["wx-live-ac"], { type: "FeatureCollection", features: [] });
  const fixes = mapCalls.sources["wx-fixes"];
  assert.ok(fixes.features.every((f) => f.properties.passed === "false"));
  // the rest of the eWAS map still renders (additive, no regression)
  assert.match(out, /wx-map__clickhint/);
  assert.match(out, /CYCLE 20261005_18/);
  assert.ok(mapCalls.layers.map((l) => l.id).includes("wx-route"));
});

test("the panel renders unchanged without any live props", async () => {
  const renderer = await render({});
  const out = text(renderer);
  assert.ok(!out.includes("wx-live-hud"));
  assert.match(out, /wx-map__canvas/);
  assert.match(out, /Tap a fix for detail/);
});
