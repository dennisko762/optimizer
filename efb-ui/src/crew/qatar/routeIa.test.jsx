/**
 * qatar-04 Route information architecture (audit G4/G5, acceptance check 5).
 *
 * The M5-P MapLibre panel replaced the legacy SVG Route screen and dropped
 * the reference IA with it: the ATC SECTORS panel, the ROUTE TERRAIN card,
 * the PRECIP/SIGMET legend, the labelled "Layers & Overlays" control and the
 * -12h/+12h WX TIME steps all disappeared, and a sim-planned flight showed
 * "0 fixes". These tests assert the IA is present on the Route variant, that
 * the Weather variant is a different screen, and that the derived route
 * fallback is labelled.
 *
 * MapLibre is stubbed (WebGL); assertions are made on the real component's
 * render output.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

vi.mock("maplibre-gl", () => {
  class FakeMap {
    addControl() {}
    on() {}
    once() {}
    off() {}
    isStyleLoaded() { return true; }
    getStyle() { return { layers: [] }; }
    setPaintProperty() {}
    getSource() { return null; }
    addSource() {}
    getLayer() { return null; }
    addLayer() {}
    getLayoutProperty() { return "visible"; }
    setLayoutProperty() {}
    getCanvas() { return null; }
    fitBounds() {}
    project() { return { x: 10, y: 10 }; }
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

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flight = {
  flight_number: "QR815",
  departure_icao: "DOH",
  arrival_icao: "LHR",
  callsign: "QTR815",
};

function textOf(renderer) {
  const out = [];
  const walk = (node) => {
    if (node == null) return;
    if (typeof node === "string" || typeof node === "number") {
      out.push(String(node));
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    walk(node.children);
  };
  walk(renderer.toJSON());
  return out.join(" ").replace(/\s+/g, " ").trim();
}

function findByTestId(renderer, id) {
  return renderer.root.findAll((n) => n.props && n.props["data-testid"] === id);
}

function buttonWithText(renderer, label) {
  return renderer.root.findAll(
    (n) =>
      n.type === "button" &&
      n.children.some((c) => typeof c === "string" && c.trim() === label)
  );
}

async function render(variant) {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(MapWeatherPanel, {
        apiBase: "/bridge",
        utc: { time: "12:00", date: "Friday" },
        flight,
        telemetry: null,
        simConnected: false,
        live: {},
        variant,
      })
    );
  });
  return renderer;
}

beforeEach(() => {
  // Every backend call answers 404 (no SimBrief plan, no GFS cycle) — the
  // state the audit observed.
  globalThis.fetch = vi.fn(async () => ({
    ok: false,
    status: 404,
    json: async () => ({}),
  }));
});

test("the Route variant renders the full qatar-04 IA", async () => {
  const renderer = await render("route");
  const body = textOf(renderer);

  // Labelled layers control (was an unlabelled icon with a bare count).
  assert.match(body, /Layers & Overlays/);
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("wx-navbtn--labelled")
    ).length,
    1
  );

  // ATC SECTORS — present AND honest about the missing feed.
  assert.equal(findByTestId(renderer, "route-atc").length, 1);
  assert.match(body, /ATC SECTORS/);
  assert.match(body, /VATSIM/);
  assert.match(body, /NOT CONFIGURED/);
  assert.match(body, /DISPLAY ALTITUDE/);

  // ROUTE TERRAIN card.
  assert.equal(findByTestId(renderer, "route-terrain").length, 1);
  assert.match(body, /ROUTE TERRAIN/);

  // PRECIP + SIGMET legend strip.
  assert.equal(findByTestId(renderer, "route-legend").length, 1);
  for (const label of [
    "PRECIP", "None", "Light", "Moderate", "Heavy", "CB",
    "SIGMET", "Thunderstorm", "Turbulence", "Icing", "Volcanic Ash",
  ]) {
    assert.ok(body.includes(label), `legend is missing "${label}"`);
  }

  // WX TIME with PLAY / -12h / +12h / NOW.
  assert.match(body, /WX TIME/);
  assert.equal(buttonWithText(renderer, "-12h").length, 1);
  assert.equal(buttonWithText(renderer, "+12h").length, 1);
  assert.equal(buttonWithText(renderer, "NOW").length, 1);
  assert.equal(
    renderer.root.findAll((n) => n.type === "button" && n.props["aria-label"] === "Play").length,
    1
  );
});

test("the WX TIME steppers stay inside the published forecast horizon", async () => {
  const renderer = await render("route");
  const minus = () => buttonWithText(renderer, "-12h")[0];
  const plus = () => buttonWithText(renderer, "+12h")[0];

  // At NOW there is no past cycle behind the GFS proxy: the step back is
  // disabled rather than claiming a negative forecast hour.
  assert.equal(minus().props.disabled, true);
  assert.match(minus().props.title, /no past cycles/i);

  await act(async () => plus().props.onClick());
  assert.match(textOf(renderer), /T\+12h/);
  assert.equal(minus().props.disabled, false);

  await act(async () => plus().props.onClick());
  await act(async () => plus().props.onClick());
  assert.match(textOf(renderer), /T\+36h/);
  assert.equal(plus().props.disabled, true, "stepped past the T+36 horizon");

  await act(async () => minus().props.onClick());
  assert.match(textOf(renderer), /T\+24h/);
});

test("a sim-planned flight gets its fixes from the derived route, labelled DERIVED", async () => {
  const renderer = await render("route");
  const body = textOf(renderer);
  assert.doesNotMatch(body, /· 0 fixes/);
  assert.match(body, /· 13 fixes/);
  assert.match(body, /DERIVED/);
  assert.match(body, /not a filed route/);
});

test("the Weather variant is a different screen with the same map", async () => {
  const route = textOf(await render("route"));
  const weather = textOf(await render("weather"));
  assert.notEqual(route, weather);
  assert.match(weather, /FORECAST CYCLE/);
  assert.match(weather, /HAZARD PRODUCTS/);
  assert.doesNotMatch(weather, /ATC SECTORS/);
  // Both keep the honest GFS provenance disclaimer.
  for (const body of [route, weather]) {
    assert.match(body, /not official WAFS\/eWAS/);
  }
});

test("the ATC SECTORS display-altitude control follows the selected FL", async () => {
  const renderer = await render("route");
  const btn = (label) => buttonWithText(renderer, label)[0];

  // AUTO is the default and mirrors the selected flight level.
  assert.match(textOf(renderer), /DISPLAY ALTITUDE AUTO OFF FL\d{3}/);

  await act(async () => btn("OFF").props.onClick());
  assert.match(textOf(renderer), /DISPLAY ALTITUDE AUTO OFF —/);

  await act(async () => btn("AUTO").props.onClick());
  assert.match(textOf(renderer), /DISPLAY ALTITUDE AUTO OFF FL\d{3}/);
});

test("the Weather variant reports every hazard product's real state", async () => {
  // turbulence resolves with areas, icing is still downloading, jet is on
  // but unanswered, cape/fronts are switched off.
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/layer/turbulence")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          product: "turbulence",
          fl: 340,
          offset: 0,
          unit: "",
          thresholds: [1, 2, 3],
          features: [
            {
              tier: 1,
              geometry: { type: "Polygon", coordinates: [[[0, 0], [1, 0], [1, 1], [0, 0]]] },
            },
          ],
        }),
      };
    }
    if (u.includes("/layer/icing")) {
      return { ok: false, status: 503, json: async () => ({}) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });

  const renderer = await render("weather");
  const body = textOf(renderer);
  assert.match(body, /HAZARD PRODUCTS/);
  assert.match(body, /Turbulence \d+ areas/);
  assert.match(body, /Icing downloading/);
  assert.match(body, /CAPE off/);
  assert.match(body, /Fronts off/);
});

test("the live HUD only appears when SimConnect is actually connected", async () => {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(MapWeatherPanel, {
        apiBase: "/bridge",
        utc: { time: "12:00", date: "Friday" },
        flight,
        telemetry: {
          rawSummary: { latitude: 30, longitude: 40 },
          flightStatePatch: { altitudeFt: 36000 },
        },
        simConnected: true,
        live: { flightLevel: 360 },
        variant: "route",
      })
    );
  });
  const hud = renderer.root.findAll(
    (n) => n.props && n.props["data-testid"] === "wx-live-hud"
  );
  assert.equal(hud.length, 1);
  assert.match(textOf(renderer), /LIVE/);
  assert.match(textOf(renderer), /PASSED \d+ \/ 13/);
});
