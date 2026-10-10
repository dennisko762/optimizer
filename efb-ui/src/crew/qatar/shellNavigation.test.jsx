/**
 * Shell navigation coverage (card t_a4119b80, acceptance check 15).
 *
 * The audit's root-cause evidence was coverage: the screen-composition
 * layer of QatarShell — login gate, Home, the sidebar, Crew Desk, Profile
 * and the flight → SmartOps handoff — was effectively untested, which is
 * exactly where the layout and aliasing defects lived. This file walks the
 * real navigation path through the real components.
 *
 * MapWeatherPanel (WebGL) is stubbed; everything else is the real shell.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

const { navState, sessionBox } = vi.hoisted(() => ({
  navState: {},
  sessionBox: { session: { pilotId: "CAPTAIN", at: 0 } },
}));

vi.mock("../useCrewPlatform.js", () => ({
  useCrewPlatform: () => ({
    apiBase: "/bridge",
    configReady: { ready: false },
    loading: false,
    error: null,
    logout: vi.fn(),
    startAuth: vi.fn(),
    selectedProvider: { id: "qatarvirtual", display_name: "Qatar Airways Virtual", icao: "QTR" },
    session: { session_id: "session-1", authenticated: true, display_name: "Local Pilot" },
  }),
}));

vi.mock("../crewAuth.js", () => ({
  loadSession: () => sessionBox.session,
  clearSession: () => {
    sessionBox.session = null;
  },
  saveSession: vi.fn(),
}));

vi.mock("../crewCheckin.js", () => ({
  checkInFlight: () => ({ ok: true, record: { at: 0 } }),
  getCheckin: () => null,
  isCheckedIn: () => false,
}));

vi.mock("./useNavigraph.js", () => ({ useNavigraph: () => navState }));

vi.mock("./useSimTelemetry.js", () => ({
  useSimTelemetry: () => ({ telemetry: null, reachable: false, apply: vi.fn(), applyState: null }),
}));

vi.mock("./MapWeatherPanel.jsx", () => ({
  default: (props) =>
    React.createElement("div", { className: "wx-map-stub" }, `MAP ${props.variant}`),
}));

import QatarShell from "./QatarShell.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const ROSTER = [
  {
    flight_id: "QR815-1",
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
    aircraft_icao: "B777-300ER",
    callsign: "QTR815",
  },
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

function sideItem(renderer, label) {
  return renderer.root.findAll(
    (n) =>
      n.type === "button" &&
      String(n.props.className || "").includes("qr-side-item") &&
      n.children.some((c) => typeof c === "string" && c.trim() === label)
  )[0];
}

beforeEach(() => {
  sessionBox.session = { pilotId: "CAPTAIN", at: 0 };
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
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/flights")) {
      return { ok: true, status: 200, json: async () => ROSTER };
    }
    if (u.includes("/api/simbrief/flightplans")) {
      return { ok: true, status: 200, json: async () => ({ plans: [], last_plan: null }) };
    }
    if (u.includes("/api/simbrief/flightplan/live")) {
      return { ok: false, status: 503, json: async () => ({}) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
});

async function renderShell() {
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(QatarShell, { onOpenOptimizer: vi.fn() }));
  });
  return renderer;
}

test("without a crew session the shell renders the login gate only", async () => {
  sessionBox.session = null;
  const renderer = await renderShell();
  const body = textOf(renderer);
  assert.match(body, /QATAR/);
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("qr-sidebar")
    ).length,
    0
  );
});

test("Home lists the roster, company news and the saved-plan panel", async () => {
  const renderer = await renderShell();
  const body = textOf(renderer);
  assert.match(body, /HOME/);
  assert.match(body, /COMPANY NEWS/);
  assert.match(body, /QR815/);
  // The sidebar is present on Home.
  assert.ok(sideItem(renderer, "Crew Desk"));
});

test("the sidebar navigates Home → Crew Desk → Profile", async () => {
  const renderer = await renderShell();

  await act(async () => sideItem(renderer, "Crew Desk").props.onClick());
  assert.match(textOf(renderer), /INBOX/);

  await act(async () => sideItem(renderer, "Profile").props.onClick());
  const profile = textOf(renderer);
  assert.match(profile, /PROFILE/);
  assert.match(profile, /NAVIGRAPH/);
  // G9: the Profile states the airline AND the rendered interface.
  assert.match(profile, /Qatar Airways Virtual/);
  assert.match(profile, /QR SmartOps \(Qatar Airways reference shell\)/);
});

test("opening a flight hands off to SmartOps and every tab is reachable", async () => {
  const renderer = await renderShell();

  const open = renderer.root.findAll(
    (n) =>
      n.type === "button" &&
      n.children.some((c) => typeof c === "string" && /OPEN|FLIGHTPLAN/i.test(c))
  );
  assert.ok(open.length > 0, "no flight open action on Home");
  await act(async () => open[0].props.onClick());

  // SmartOps is up with the pill tab strip.
  const tabs = renderer.root.findAll(
    (n) => n.type === "button" && n.props.role === "tab"
  );
  assert.equal(tabs.length, 8, "expected the eight SmartOps tabs");

  const seen = new Set();
  for (const label of ["Overview", "Times", "Route", "Runways", "Weather", "Briefing", "EDTO"]) {
    const tab = renderer.root.findAll(
      (n) =>
        n.type === "button" &&
        n.props.role === "tab" &&
        n.children.some((c) => typeof c === "string" && c.trim() === label)
    )[0];
    assert.ok(tab, `tab ${label} missing`);
     
    await act(async () => tab.props.onClick());
    const body = textOf(renderer);
    assert.ok(body.length > 0);
    assert.equal(seen.has(body), false, `tab ${label} duplicates another screen`);
    seen.add(body);
  }

  // ← HOME returns to Home.
  const back = renderer.root.findAll(
    (n) => n.type === "button" && n.children.some((c) => typeof c === "string" && c.includes("HOME"))
  )[0];
  await act(async () => back.props.onClick());
  assert.match(textOf(renderer), /COMPANY NEWS/);
});
