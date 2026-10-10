import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

const { navState } = vi.hoisted(() => ({ navState: {} }));

vi.mock("../useCrewPlatform.js", () => ({
  useCrewPlatform: () => ({
    apiBase: "/bridge",
    configReady: true,
    logout: vi.fn(),
    selectedProvider: { name: "Qatar Virtual", icao: "QTR" },
    session: { session_id: "session-1" },
  }),
}));

vi.mock("./useNavigraph.js", () => ({
  useNavigraph: () => navState,
}));

import {
  CrewDeskScreen,
  EdtoScreen,
  ProfileScreen,
  RouteScreen,
} from "./QatarShell.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const flight = {
  flight_id: "QR815-1",
  flight_number: "QR815",
  departure_icao: "DOH",
  arrival_icao: "LHR",
  route: "DCT DOH LHR",
};

function text(renderer) {
  return JSON.stringify(renderer.toJSON());
}

beforeEach(() => {
  Object.assign(navState, {
    busy: false,
    navdata: {
      status: "ok",
      airac_cycle: "2610",
      entitled_current: true,
      data: [],
    },
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

test("Crew Desk renders live critical NOTAMs and reload action", async () => {
  navState.notams = {
    status: "ok",
    data: [{ id: "A1234/26", icao: "OTHH", text: "RWY CLOSED", severity: "critical" }],
    summary: { total: 1, counts: { critical: 1 }, stations: ["OTHH"], station_count: 1 },
  };
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

  const output = text(renderer);
  assert.match(output, /OTHH A1234\/26/);
  assert.match(output, /qr-msg--critical/);
  assert.match(output, /1 NOTAM/);
  const reload = renderer.root.find((node) => node.type === "button" && node.children.includes("RELOAD"));
  act(() => reload.props.onClick());
  assert.equal(navState.refreshNotams.mock.calls.length, 1);
});

test("Profile renders subscription gates, device flow, and actions", async () => {
  navState.status = {
    configured: true,
    authenticated: false,
    subscriptions: [],
    datatypes: {
      tile: { allowed: false, status: "not_subscribed", required_subscription: "tiles" },
    },
    rate_limit: { rpm: 30, backing_off: true, backoff_seconds_remaining: 4 },
    cache: { entries: 2 },
  };
  navState.signIn = {
    status: "pending",
    user_code: "ABCD-EFGH",
    verification_uri: "https://identity.example/device",
  };
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(ProfileScreen, {
        crewSession: { pilotId: "CAPTAIN" },
        utc: { time: "12:00", date: "Friday" },
        onBack: vi.fn(),
        onLogout: vi.fn(),
        onOpenOptimizer: vi.fn(),
      })
    );
  });

  const output = text(renderer);
  assert.match(output, /pilot sign-in required/);
  assert.match(output, /ABCD-EFGH/);
  assert.match(output, /RATE /);
  assert.match(output, /30/);
  const signIn = renderer.root.find(
    (node) =>
      node.type === "button" &&
      node.children.some((child) => typeof child === "string" && child.includes("SIGN IN TO NAVIGRAPH"))
  );
  act(() => signIn.props.onClick());
  assert.equal(navState.startSignIn.mock.calls.length, 1);
});

test("Route renders AIRAC state and switches to bounded proxied chart tiles", async () => {
  navState.status = {
    status: "ok",
    datatypes: { tile: { allowed: true, status: "ok" } },
  };
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(RouteScreen, {
        flight,
        ofpData: null,
        distanceNm: 2814,
        utc: { time: "12:00", date: "Friday" },
        telemetry: null,
        simConnected: false,
        live: {},
        timing: {},
      })
    );
  });

  assert.match(text(renderer), /AIRAC 2610/);
  const layer = renderer.root.find(
    (node) => node.type === "button" && node.props.title === "Navigraph ENR HI chart"
  );
  await act(async () => layer.props.onClick());
  const output = text(renderer);
  assert.match(output, /\/bridge\/api\/crew\/navigraph\/tiles\/ifr\.hi\.day\//);
  assert.match(output, /qr-svg-route/);
});

test("EDTO renders live risk/NAT provenance and TMI details", async () => {
  navState.risks = {
    status: "ok",
    data: {
      official_notices: [{ region: "GULF", status: "ACTIVE", detail: "Monitor" }],
      operator_risks: [{ region: "IRAN", status: "LEVEL 3" }],
      nat_tracks: [{ name: "NAT A", direction: "WEST", valid: "1200-1900Z", track: "DOGAL 54/20", levels: ["FL330"], tmi: "283" }],
    },
  };
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(EdtoScreen, { flight, ofpData: null }));
  });

  const output = text(renderer);
  assert.match(output, /operator risk feed • live/);
  assert.match(output, /TMI /);
  assert.match(output, /283/);
  assert.match(output, /FL330/);
});
