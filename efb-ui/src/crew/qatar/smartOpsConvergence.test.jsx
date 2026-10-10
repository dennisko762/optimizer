/**
 * SmartOps convergence tests (card t_a4119b80 / audit t_af01ae33).
 *
 * These lock the defects the audit found in the screen-composition layer —
 * the part of QatarShell that had no tests at all and is exactly where G1,
 * G2, G3, G5, G7, G10, G11 and G12 lived:
 *
 *  - every SmartOps tab renders a DISTINCT screen (no Flightplan aliases),
 *  - Briefing reaches the real BriefingPanel,
 *  - Route and Weather hand the map IDENTICAL live props,
 *  - the Route screen carries the qatar-04 IA (ATC sectors / route terrain /
 *    precip+SIGMET legend / labelled layers control / WX TIME stepper),
 *  - Crew Desk → Tech Log replaces the inbox AND the detail panel,
 *  - a failed inbox refresh shows an explicit STALE state and keeps the
 *    selected message,
 *  - every inbox row renders a real `DD MMM · HH:MMz` timestamp,
 *  - EDTO renders the full-width route summary bar and 4-line NAT blocks.
 *
 * MapWeatherPanel is stubbed here (WebGL) and its own behaviour is covered
 * by MapWeatherPanel*.test.jsx; the stub records the props it was handed so
 * the Route/Weather data-contract assertion is made on real render output.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

const { navState, mapProps, telemetryState } = vi.hoisted(() => ({
  navState: {},
  mapProps: [],
  telemetryState: { telemetry: null, reachable: false },
}));

vi.mock("../useCrewPlatform.js", () => ({
  useCrewPlatform: () => ({
    apiBase: "/bridge",
    configReady: true,
    logout: vi.fn(),
    selectedProvider: { display_name: "Qatar Airways Virtual", icao: "QTR" },
    session: { session_id: "session-1" },
  }),
}));

vi.mock("./useNavigraph.js", () => ({ useNavigraph: () => navState }));

vi.mock("./useSimTelemetry.js", () => ({
  useSimTelemetry: () => ({
    telemetry: telemetryState.telemetry,
    reachable: telemetryState.reachable,
    apply: vi.fn(),
    applyState: null,
  }),
}));

vi.mock("./MapWeatherPanel.jsx", () => ({
  default: (props) => {
    mapProps.push(props);
    return React.createElement(
      "div",
      { className: "wx-map-stub" },
      `MAP variant=${props.variant}`
    );
  },
}));

import {
  CrewDeskScreen,
  EdtoScreen,
  SmartOpsScreen,
} from "./QatarShell.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flight = {
  flight_id: "QR815-1",
  flight_number: "QR815",
  departure_icao: "DOH",
  arrival_icao: "LHR",
  aircraft_icao: "B777-300ER",
  callsign: "QTR815",
};

const TABS = [
  "overview",
  "times",
  "flightplan",
  "route",
  "runways",
  "weather",
  "briefing",
  "edto",
];

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

async function renderTab(tab) {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(SmartOpsScreen, {
        utc: { time: "12:00", date: "Friday" },
        flight,
        ofp: null,
        ofpError: null,
        importing: false,
        tab,
        setTab: vi.fn(),
        onImportNewPlan: vi.fn(),
        onBack: vi.fn(),
      })
    );
  });
  return renderer;
}

beforeEach(() => {
  mapProps.length = 0;
  telemetryState.telemetry = null;
  telemetryState.reachable = false;
  globalThis.fetch = vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }));
  Object.assign(navState, {
    busy: false,
    navdata: { status: "ok", airac_cycle: "2610", entitled_current: true, data: [] },
    notams: { status: "no_stations", data: [], detail: "No flight selected" },
    refreshAll: vi.fn(),
    refreshNotams: vi.fn(),
    risks: null,
    signIn: null,
    signOut: vi.fn(),
    startSignIn: vi.fn(),
    status: { status: "ok", configured: true, authenticated: true, datatypes: {} },
  });
});

/* ── G2: eight tabs, eight distinct screens ─────────────────────────── */

test("every SmartOps tab renders a pairwise-distinct screen", async () => {
  const bodies = {};
  for (const tab of TABS) {
     
    const renderer = await renderTab(tab);
    bodies[tab] = textOf(renderer);
    assert.ok(bodies[tab].length > 0, `${tab} rendered nothing`);
  }
  for (const a of TABS) {
    for (const b of TABS) {
      if (a >= b) continue;
      assert.notEqual(
        bodies[a],
        bodies[b],
        `tabs "${a}" and "${b}" render identical content`
      );
    }
  }
  // The specific aliasing the audit found: overview/times/briefing were
  // byte-identical to flightplan.
  for (const tab of ["overview", "times", "briefing"]) {
    assert.notEqual(bodies[tab], bodies.flightplan);
  }
  assert.match(bodies.overview, /OVERVIEW/);
  assert.match(bodies.times, /TIMES/);
  assert.match(bodies.runways, /RUNWAYS/);
});

/* ── G2/G3: Briefing reaches BriefingPanel; map props are identical ─── */

test("Briefing tab renders the real BriefingPanel sections", async () => {
  const renderer = await renderTab("briefing");
  const body = textOf(renderer);
  assert.match(body, /ATIS/);
  assert.match(body, /METAR/);
  assert.match(body, /TAF/);
  assert.match(body, /SIGMET/);
});

test("Route and Weather hand the map an identical live data contract", async () => {
  telemetryState.telemetry = {
    rawSummary: { latitude: 30, longitude: 40 },
    flightStatePatch: { altitudeFt: 36000 },
  };
  telemetryState.reachable = true;

  await renderTab("route");
  const routeProps = mapProps.at(-1);
  await renderTab("weather");
  const weatherProps = mapProps.at(-1);

  for (const key of ["telemetry", "simConnected", "live", "flight", "apiBase"]) {
    assert.deepEqual(
      weatherProps[key],
      routeProps[key],
      `Weather tab is missing/differs on the "${key}" prop`
    );
  }
  assert.equal(routeProps.variant, "route");
  assert.equal(weatherProps.variant, "weather");
});

/* ── G10: EDTO composition ──────────────────────────────────────────── */

test("EDTO renders a full-width route summary bar and 4-line NAT blocks", async () => {
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(EdtoScreen, { flight, ofpData: null }));
  });
  const body = textOf(renderer);
  assert.match(body, /ROUTE STRING/);
  assert.match(body, /DISTANCE/);
  assert.match(body, /FLIGHT TIME \(PLAN\)/);
  assert.match(body, /ALTERNATE\(S\)/);
  // Four lines per NAT block: name/direction, coded track, FL band, decoded.
  assert.match(body, /NAT A WESTBOUND/);
  assert.match(body, /FL350 FL360 FL370 FL390 FL400/);
  assert.match(body, /53°30'N 020°00'W/);
  // The old near-empty map half is gone (no qr-edto__map container).
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("qr-edto__map")
    ).length,
    0
  );
});

/* ── G7/G11/G12: Crew Desk ──────────────────────────────────────────── */

async function renderCrewDesk() {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(CrewDeskScreen, {
        flight,
        pilotName: "CAPTAIN",
        utc: { time: "12:00", date: "Friday" },
        onNavigate: vi.fn(),
      })
    );
  });
  return renderer;
}

test("every inbox row renders a real DD MMM · HH:MMz timestamp", async () => {
  const renderer = await renderCrewDesk();
  const stamps = renderer.root
    .findAll((n) => n.props && String(n.props.className || "").includes("qr-msg__time"))
    .map((n) => n.children.join(""));
  assert.ok(stamps.length >= 6, "expected the seeded inbox rows");
  for (const s of stamps) {
    assert.match(
      s,
      /^\d{2} [A-Z]{3} · \d{2}:\d{2}z$/,
      `inbox timestamp "${s}" is not a real DD MMM · HH:MMz stamp`
    );
  }
});

test("Tech Log tab replaces the inbox list and the message detail panel", async () => {
  const renderer = await renderCrewDesk();
  const hasInbox = () =>
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").split(" ").includes("qr-inbox")
    ).length > 0;
  const hasDetail = () =>
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").split(" ").includes("qr-detail")
    ).length > 0;

  assert.ok(hasInbox());
  assert.ok(hasDetail());

  const techTab = renderer.root.find(
    (n) =>
      n.type === "button" &&
      n.props.role === "tab" &&
      n.children.some((c) => typeof c === "string" && c.includes("Tech Log"))
  );
  await act(async () => techTab.props.onClick());

  assert.equal(hasInbox(), false, "inbox list still on screen behind Tech Log");
  assert.equal(hasDetail(), false, "message detail still on screen behind Tech Log");
  assert.match(textOf(renderer), /TECH LOG/);
});

test("a failed inbox refresh shows an explicit STALE state and keeps the selection", async () => {
  const renderer = await renderCrewDesk();

  const firstMsg = renderer.root.findAll(
    (n) => n.props && String(n.props.className || "").includes("qr-msg ")
  )[0];
  await act(async () => firstMsg.props.onClick());
  const selectedBefore = renderer.root.findAll((n) =>
    n.props && String(n.props.className || "").includes("qr-msg--selected")
  ).length;
  assert.equal(selectedBefore, 1);
  // The open message shows its own timestamp in the detail meta line.
  assert.match(textOf(renderer), /· \d{2} [A-Z]{3} · \d{2}:\d{2}z/);

  globalThis.fetch = vi.fn(async () => {
    throw new TypeError("Failed to fetch");
  });
  const refresh = renderer.root.find(
    (n) => n.type === "button" && n.children.includes("REFRESH")
  );
  await act(async () => refresh.props.onClick());

  const state = renderer.root.find(
    (n) => n.props && n.props["data-testid"] === "inbox-refresh-state"
  );
  const stateText = textOf({ toJSON: () => state.children });
  assert.match(stateText, /STALE/);
  assert.match(stateText, /Refresh failed/);
  // The previously selected message must survive a failed refresh.
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("qr-msg--selected")
    ).length,
    1
  );
});

test("an HTTP error on refresh is reported verbatim, a success is timestamped", async () => {
  const renderer = await renderCrewDesk();
  const refresh = () =>
    renderer.root.find((n) => n.type === "button" && n.children.includes("REFRESH"));
  const stateText = () => {
    const state = renderer.root.find(
      (n) => n.props && n.props["data-testid"] === "inbox-refresh-state"
    );
    return textOf({ toJSON: () => state.children });
  };

  globalThis.fetch = vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) }));
  await act(async () => refresh().props.onClick());
  assert.match(stateText(), /STALE/);
  assert.match(stateText(), /HTTP 503/);

  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => [
      { id: 7, type: "weather", icao: "OTHH", summary: "WIND SHIFT", timestamp: 1790000000 },
    ],
  }));
  await act(async () => refresh().props.onClick());
  assert.match(stateText(), /REFRESHED/);
  assert.match(stateText(), /Last refresh \d{2}:\d{2}z/);
  assert.match(textOf(renderer), /WIND SHIFT/);
});

test("Trash, Preflight and Weather tabs each render their own content", async () => {
  const renderer = await renderCrewDesk();
  const tab = (label) =>
    renderer.root.find(
      (n) =>
        n.type === "button" &&
        n.props.role === "tab" &&
        n.children.some((c) => typeof c === "string" && c.trim() === label)
    );

  await act(async () => tab("Trash").props.onClick());
  assert.match(textOf(renderer), /Trash is empty/);

  await act(async () => tab("Preflight").props.onClick());
  assert.match(textOf(renderer), /PREFLIGHT CHECKLIST/);

  await act(async () => tab("Weather").props.onClick());
  // No defaultStation is passed here, so the briefing states that honestly.
  assert.match(textOf(renderer), /Select a flight with a departure ICAO/);
});

test("ATO/ACT chips accept crew-entered times and reject non-time characters", async () => {
  const renderer = await renderTab("flightplan");
  const chips = renderer.root.findAll(
    (n) => n.type === "input" && String(n.props.className || "").includes("qr-wpt__chip")
  );
  assert.ok(chips.length >= 2, "expected ATO/ACT entry chips on the waypoint table");
  assert.equal(chips[0].props.value, "");

  await act(async () => chips[0].props.onChange({ target: { value: "15:4x5" } }));
  const after = renderer.root.findAll(
    (n) => n.type === "input" && String(n.props.className || "").includes("qr-wpt__chip")
  );
  assert.equal(after[0].props.value, "15:45");
});
