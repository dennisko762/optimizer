/**
 * MapWeatherPanel — interaction coverage for the eWAS route map controls.
 *
 * The M3 live-route regression is covered by MapWeatherPanel.test.jsx; this
 * file covers the surrounding crew controls so a change to them can't land
 * untested: the layer drawer (FL presets, per-product toggles, legend,
 * unavailable disclosure), the zoom/fit nav buttons, the T+0..36 time
 * slider with play/pause/NOW, the fix popover (incl. the honest "NO WX" /
 * ETA-flag provenance chips) and the route error states.
 *
 * MapLibre is stubbed (WebGL/DOM library); every assertion is made against
 * the real component's output or the draw calls it issues.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

const { mapCalls } = vi.hoisted(() => ({
  mapCalls: { sources: {}, layers: [], zoomIn: 0, zoomOut: 0, fitBounds: [], moveHandlers: 0 },
}));

vi.mock("maplibre-gl", () => {
  class FakeMap {
    addControl() {}
    on(ev) { if (ev === "move") mapCalls.moveHandlers += 1; }
    once() {}
    off() {}
    isStyleLoaded() { return true; }
    getSource(id) {
      return mapCalls.sources[id] ? { setData: (d) => { mapCalls.sources[id] = d; } } : null;
    }
    addSource(id, spec) { mapCalls.sources[id] = spec.data; }
    getLayer(id) { return mapCalls.layers.find((l) => l.id === id) || null; }
    addLayer(spec) { mapCalls.layers.push(spec); }
    getLayoutProperty() { return "visible"; }
    setLayoutProperty() {}
    getCanvas() { return null; }
    fitBounds(b) { mapCalls.fitBounds.push(b); }
    project() { return { x: 120, y: 80 }; }
    remove() {}
    zoomIn() { mapCalls.zoomIn += 1; }
    zoomOut() { mapCalls.zoomOut += 1; }
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

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SAMPLE = {
  ident: "ERNAS",
  occurrence: 0,
  wind_speed_kt: 65,
  wind_from_deg: 270,
  tailwind_kt: 42,
  oat_c: -52,
  turbulence_tier: 2,
  icing_tier: 1,
  jet_tier: 3,
  valid_at_utc: "2026-10-05T18:00:00+00:00",
  offset_served: 0,
  unavailable: [],
};

// TUVLU is sampled but outside the forecast horizon: the chip must say so
// instead of claiming the nominal valid time.
const SAMPLE_FLAGGED = {
  ident: "TUVLU",
  occurrence: 0,
  valid_at_utc: "2026-10-06T06:00:00+00:00",
  offset_served: 12,
  unavailable: ["eta beyond forecast horizon"],
};

const ROUTE_BODY = {
  origin: "EDDF",
  destination: "LKPR",
  cruise_fl: 360,
  points: [
    { ident: "EDDF", lat: 50.0, lon: 8.5, stage: "DEP", alt: 0, cum_nm: 0, ete: "00:00" },
    { ident: "ERNAS", lat: 50.0, lon: 12.0, alt: 360, cum_nm: 120, ete: "00:24", airway: "UL607", fir: "EDUU" },
    { ident: "TUVLU", lat: 50.0, lon: 16.0, alt: 360, cum_nm: 260, ete: "00:48" },
    { ident: "LKPR", lat: 50.0, lon: 20.0, stage: "ARR", alt: 0, cum_nm: 380, ete: "01:05" },
  ],
  samples: { cycle: "20261005_18", fl: 360, offset: 0, points: [SAMPLE, SAMPLE_FLAGGED] },
};

const TURB_LAYER = {
  label: "Turbulence",
  unit: "EDR",
  fl: 360,
  offset: 0,
  cycle: "20261005_18",
  thresholds: [0.15, 0.3, 0.45],
  source: "NOAA GFS proxy",
  features: [
    {
      type: "Feature",
      properties: { bucket: 1 },
      geometry: { type: "Polygon", coordinates: [[[10, 49], [14, 49], [14, 51], [10, 49]]] },
    },
  ],
};

function stubFetch({ routeStatus = 200 } = {}) {
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/weather/status")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          state: "ready",
          current_cycle: "20261005_18",
          last_good: "20261005_18",
          // the API reports fetched_at as epoch seconds
          fetched_at: Date.parse("2026-10-05T18:42:00Z") / 1000,
          stale: false,
          enabled: true,
          source: "NOAA GFS proxy",
        }),
      };
    }
    if (u.includes("/weather/route")) {
      if (routeStatus !== 200) return { ok: false, status: routeStatus, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ROUTE_BODY };
    }
    if (u.includes("/weather/layer/turbulence")) {
      return { ok: true, status: 200, json: async () => TURB_LAYER };
    }
    // honestly unavailable products (503 -> "unavailable" disclosure)
    return { ok: false, status: 503, json: async () => ({}) };
  });
}

function flatten(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  return flatten(node.children);
}

async function render(props = {}) {
  const node = { clientWidth: 900, clientHeight: 600 };
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(MapWeatherPanel, {
        apiBase: "/bridge", utc: { time: "18:42" }, ...props,
      }),
      { createNodeMock: () => node }
    );
  });
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  const text = () => {
    const json = renderer.toJSON();
    return `${flatten(json)}\n${JSON.stringify(json)}`;
  };
  return { renderer, text };
}

const byLabel = (renderer, label) =>
  renderer.root.findAll((n) => n.props && n.props["aria-label"] === label)[0];

async function click(el) {
  await act(async () => { el.props.onClick(); });
}

beforeEach(() => {
  mapCalls.sources = {};
  mapCalls.layers = [];
  mapCalls.zoomIn = 0;
  mapCalls.zoomOut = 0;
  mapCalls.fitBounds = [];
  mapCalls.moveHandlers = 0;
  stubFetch();
  globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(0), 0);
  globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
});

test("the status banner discloses cycle, validity and source", async () => {
  const { text } = await render();
  const out = text();
  assert.match(out, /CYCLE 20261005_18/);
  assert.match(out, /VALID/);
  assert.match(out, /FET 18:42Z/);
  assert.match(out, /NOAA GFS proxy/);
  // the planned route and the fixes are drawn
  assert.ok(mapCalls.layers.map((l) => l.id).includes("wx-route"));
  assert.equal(mapCalls.sources["wx-fixes"].features.length, 4);
});

test("the layer drawer selects an FL, toggles products and shows the legend", async () => {
  const { renderer, text } = await render();

  await click(byLabel(renderer, "Layers and overlays"));
  let out = text();
  assert.match(out, /LAYERS/);
  assert.match(out, /FLIGHT LEVEL/);
  assert.match(out, /Turbulence/);
  assert.match(out, /Jet Stream/);
  // only the products with features get a legend ramp; 503 products are
  // disclosed as unavailable instead of silently empty
  assert.match(out, /unavailable/);
  assert.ok(
    renderer.root.findAll((n) => n.props && n.props.className === "wx-legend__sw").length >= 3,
    "the turbulence legend must show one swatch per threshold"
  );

  // pick a different flight level -> route + layers refetch for that FL
  const fl300 = renderer.root.findAll(
    (n) => n.type === "button" && flatten(n.props.children) === "300"
  )[0];
  await click(fl300);
  assert.ok(
    globalThis.fetch.mock.calls.some((c) => String(c[0]).includes("fl=300")),
    "selecting FL300 must refetch the route at that level"
  );

  // turning turbulence off drops its legend and its fetch
  const turbBox = renderer.root.findAll((n) => n.type === "input" && n.props.type === "checkbox")[0];
  await act(async () => { turbBox.props.onChange({ target: { checked: false } }); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  out = text();
  assert.ok(!out.includes("wx-legend__sw"), "a disabled product must not keep a legend");

  // closing the drawer returns to the plain map
  await click(byLabel(renderer, "Layers and overlays"));
  assert.ok(!text().includes("FLIGHT LEVEL"));
});

test("the nav buttons drive zoom and fit-route on the real map", async () => {
  const { renderer } = await render();
  await click(byLabel(renderer, "Zoom in"));
  await click(byLabel(renderer, "Zoom out"));
  await click(byLabel(renderer, "Fit route"));
  assert.equal(mapCalls.zoomIn, 1);
  assert.equal(mapCalls.zoomOut, 1);
  assert.equal(mapCalls.fitBounds.length, 1);
  // MapLibre's fitBounds takes a 2-corner bounds, not a coordinate list
  // (passing every fix left the camera at the world view). The corners must
  // still enclose every fix on the route.
  const bounds = mapCalls.fitBounds[0];
  assert.equal(bounds.length, 2, "fitBounds takes [[w,s],[e,n]] corners");
  const [[w, s], [e, n]] = bounds;
  for (const p of ROUTE_BODY.points) {
    assert.ok(p.lon >= w && p.lon <= e, `fix ${p.ident} outside the fitted lon range`);
    assert.ok(p.lat >= s && p.lat <= n, `fix ${p.ident} outside the fitted lat range`);
  }
});

test("the time slider plays, scrubs and returns to NOW", async () => {
  const { renderer, text } = await render();
  assert.match(text(), /NOW/);

  await click(byLabel(renderer, "Play"));
  assert.ok(byLabel(renderer, "Pause"), "the control must flip to Pause while playing");

  const slider = renderer.root.findAll((n) => n.type === "input" && n.props.type === "range")[0];
  await act(async () => { slider.props.onChange({ target: { value: "9" } }); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  assert.match(text(), /T\+9h/);
  assert.ok(
    globalThis.fetch.mock.calls.some((c) => String(c[0]).includes("offset=9")),
    "scrubbing must refetch the route/layers at the new offset"
  );
  // scrubbing pauses playback
  assert.ok(byLabel(renderer, "Play"));

  const nowBtn = renderer.root.findAll(
    (n) => n.type === "button" && flatten(n.props.children) === "NOW"
  )[0];
  await click(nowBtn);
  assert.match(text(), /NOW/);
  // departure→arrival band derived from the OFP ETEs
  assert.match(text(), /DEP→ARR 1h/);
});

test("selecting a fix opens a popover with its sampled weather and provenance", async () => {
  const { renderer, text } = await render();
  const cross = renderer.root.findAll((n) => n.props && typeof n.props.onHover === "function")[0];

  await act(async () => { cross.props.onHover(1); });
  let out = text();
  assert.match(out, /ERNAS/);
  assert.match(out, /AWY UL607/);
  assert.match(out, /FIR EDUU/);
  assert.match(out, /WIND 65kt @270/);
  assert.match(out, /TAIL \+42kt/);
  assert.match(out, /OAT -52°C/);
  assert.match(out, /TURB T2/);
  assert.match(out, /ICE T1/);
  assert.match(out, /JET T3/);
  // sampled at the fix ETA -> the served valid time is shown
  assert.match(out, /VALID 18:00Z/);
  assert.ok(mapCalls.moveHandlers >= 1, "the popover must follow map movement");

  // a fix whose ETA could not be sampled shows the honest T+ flag, never a
  // valid-time label it doesn't have
  await act(async () => { cross.props.onHover(2); });
  out = text();
  assert.match(out, /TUVLU/);
  assert.match(out, /T\+12h/);

  // an unsampled fix says so instead of inventing values
  await act(async () => { cross.props.onHover(3); });
  out = text();
  assert.match(out, /NO WX/);
  assert.match(out, /WX sample unavailable/);

  // closing the popover clears the selection
  await click(byLabel(renderer, "Close"));
  assert.ok(!text().includes("WX sample unavailable"));

  // leaving the cross-section clears the hover without selecting anything
  await act(async () => { cross.props.onHover(null); });
  assert.ok(!text().includes("wx-pop__head"));
});

test("a missing flightplan and a downloading cycle are reported, not blanked", async () => {
  stubFetch({ routeStatus: 404 });
  let out = (await render()).text();
  assert.match(out, /No active SimBrief flightplan/);

  stubFetch({ routeStatus: 503 });
  out = (await render()).text();
  assert.match(out, /NOAA GFS cycle downloading/);

  stubFetch({ routeStatus: 500 });
  out = (await render()).text();
  assert.match(out, /Route unavailable \(HTTP 500\)/);
});
