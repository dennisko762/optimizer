/**
 * SettingsPanel component tests (card t_97dcde51).
 *
 * Both sections are exercised against a scripted bridge, including the
 * REAL `useNavigraph` device-authorization flow (start → poll at the
 * server-returned interval → authorized → status refresh), because the
 * polling path is where a "linked" claim could silently diverge from the
 * backend's actual state.
 *
 * The credential-shaped strings here are obvious placeholders.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { afterEach, beforeEach, test, vi } from "vitest";

import SettingsPanel from "./SettingsPanel.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PLACEHOLDER_ID = "aaaa-bbbb-cccc";
const PLACEHOLDER_SECRET = "zzzz-zzzz-zzzz-zzzz";

const DATATYPES_LOCKED = {
  airport: { status: "not_configured", allowed: false },
  charts_index: { status: "not_configured", allowed: false },
  chart_image: { status: "not_configured", allowed: false },
  tile: { status: "not_configured", allowed: false },
  airspace: { status: "not_configured", allowed: false },
  notam: { status: "not_configured", allowed: false },
  risk: { status: "not_configured", allowed: false },
  nat: { status: "not_configured", allowed: false },
};

const DATATYPES_LIVE = Object.fromEntries(
  Object.keys(DATATYPES_LOCKED).map((key) => [key, { status: "ok", allowed: true }])
);

/** A scripted bridge: mutable state plus a request log. */
function makeBridge() {
  const bridge = {
    calls: [],
    simbriefUser: null,
    clientIdSet: false,
    clientSecretSet: false,
    navAuthenticated: false,
    simbriefLive: { ok: false, status: 503, body: { detail: "SIMBRIEF_USER is not set." } },
    pollQueue: ["pending", "authorized"],
    deviceStart: {
      status: "pending",
      user_code: "ABCD-1234",
      verification_uri: "https://identity.api.navigraph.com/device",
      verification_uri_complete: "https://identity.api.navigraph.com/device?code=ABCD-1234",
      interval: 3,
      expires_in: 600,
    },
  };

  bridge.settingsState = () => ({
    simbrief: { configured: Boolean(bridge.simbriefUser), user: bridge.simbriefUser },
    navigraph: {
      client_id_set: bridge.clientIdSet,
      client_secret_set: bridge.clientSecretSet,
      access_token_injected: false,
      configured: bridge.clientIdSet && bridge.clientSecretSet,
      scopes: ["openid", "charts"],
    },
    env_file: { exists: true, writable: true },
    remote_writes_allowed: false,
  });

  bridge.navStatus = () => ({
    status: "ok",
    configured: bridge.clientIdSet && bridge.clientSecretSet,
    authenticated: bridge.navAuthenticated,
    subscriptions: bridge.navAuthenticated ? ["charts"] : [],
    datatypes: bridge.navAuthenticated ? DATATYPES_LIVE : DATATYPES_LOCKED,
  });

  bridge.fetch = vi.fn(async (url, init = {}) => {
    const u = String(url);
    const method = init.method || "GET";
    bridge.calls.push({ url: u, method, body: init.body ? JSON.parse(init.body) : null });

    if (u.includes("/api/crew/settings")) {
      if (method === "PUT") {
        const body = JSON.parse(init.body);
        if (body.simbrief_user !== undefined) bridge.simbriefUser = body.simbrief_user;
        if (body.navigraph_client_id !== undefined) bridge.clientIdSet = true;
        if (body.navigraph_client_secret !== undefined) bridge.clientSecretSet = true;
        return { ok: true, status: 200, json: async () => bridge.settingsState() };
      }
      return { ok: true, status: 200, json: async () => bridge.settingsState() };
    }
    if (u.includes("/api/simbrief/config/readiness")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ configured: Boolean(bridge.simbriefUser) }),
      };
    }
    if (u.includes("/api/simbrief/flightplan/live")) {
      const live = bridge.simbriefLive;
      return { ok: live.ok, status: live.status, json: async () => live.body };
    }
    if (u.includes("/api/crew/navigraph/status")) {
      return { ok: true, status: 200, json: async () => bridge.navStatus() };
    }
    if (u.includes("/api/crew/navigraph/auth/device/poll")) {
      const next = bridge.pollQueue.shift() || "expired";
      if (next === "authorized") bridge.navAuthenticated = true;
      return {
        ok: true,
        status: 200,
        json: async () => ({ status: next, subscriptions: next === "authorized" ? ["charts"] : [] }),
      };
    }
    if (u.includes("/api/crew/navigraph/auth/device")) {
      return { ok: true, status: 200, json: async () => bridge.deviceStart };
    }
    if (u.includes("/api/crew/navigraph/auth/signout")) {
      bridge.navAuthenticated = false;
      return { ok: true, status: 200, json: async () => ({ status: "signed_out" }) };
    }
    // navdata / risks / notams — declared, empty, not an error.
    return { ok: true, status: 200, json: async () => ({ status: "ok", data: [] }) };
  });

  return bridge;
}

let bridge;

beforeEach(() => {
  bridge = makeBridge();
  globalThis.fetch = bridge.fetch;
});

afterEach(() => {
  delete globalThis.fetch;
  vi.useRealTimers();
});

function byTestId(renderer, id) {
  return renderer.root.findAll((n) => n.props && n.props["data-testid"] === id)[0];
}

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

async function renderPanel(props = {}) {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(SettingsPanel, {
        apiBase: "/bridge",
        sessionId: "session-1",
        ...props,
      })
    );
  });
  return renderer;
}

async function type(renderer, testId, value) {
  await act(async () =>
    byTestId(renderer, testId).props.onChange({ target: { value } })
  );
}

async function click(renderer, testId) {
  await act(async () => byTestId(renderer, testId).props.onClick());
}

/* ── initial, honest, unconfigured state ─────────────────────────────── */

test("an unconfigured bridge is labelled as such in both sections", async () => {
  const renderer = await renderPanel();
  assert.equal(byTestId(renderer, "simbrief-pill").children.join(""), "NOT CONFIGURED");
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "NOT CONFIGURED");
  const body = textOf(renderer);
  assert.match(body, /SIMBRIEF/);
  assert.match(body, /NAVIGRAPH/);
  assert.match(body, /Navigraph not configured/);
  // Every capability row reports NOT CONFIGURED — nothing is faked.
  const rows = byTestId(renderer, "navigraph-capabilities");
  assert.ok(rows);
  assert.match(textOf(renderer), /NOT CONFIGURED/);
  // Without credentials there is no "link" action to mislead the crew.
  assert.equal(byTestId(renderer, "navigraph-link"), undefined);
});

test("a settings read failure is reported instead of a blank form", async () => {
  bridge.fetch = vi.fn(async (url) => {
    if (String(url).includes("/api/crew/settings")) {
      return { ok: false, status: 401, json: async () => ({ detail: "Invalid session." }) };
    }
    return { ok: true, status: 200, json: async () => ({ status: "ok", data: [] }) };
  });
  globalThis.fetch = bridge.fetch;
  const renderer = await renderPanel();
  assert.ok(byTestId(renderer, "settings-load-error"), "no load-error notice");
  assert.match(textOf(renderer), /Invalid session/);
});

test("an unwritable env file blocks silently-failing saves with a notice", async () => {
  const base = bridge.settingsState;
  bridge.settingsState = () => ({ ...base(), env_file: { exists: true, writable: false } });
  const renderer = await renderPanel();
  assert.match(textOf(renderer), /not writable/i);
});

/* ── SimBrief section ────────────────────────────────────────────────── */

test("saving a SimBrief handle PUTs it and flips the live pill", async () => {
  const onSimbriefConfigured = vi.fn();
  const renderer = await renderPanel({ onSimbriefConfigured });

  await type(renderer, "simbrief-input", "pilot123");
  await click(renderer, "simbrief-save");

  const put = bridge.calls.find((c) => c.method === "PUT");
  assert.deepEqual(put.body, { session_id: "session-1", simbrief_user: "pilot123" });
  // The pill is driven by a fresh readiness read, not by local optimism.
  assert.equal(byTestId(renderer, "simbrief-pill").children.join(""), "CONFIGURED");
  assert.match(textOf(renderer), /saved/i);
  // The shell is told to refetch the OFP so the Flightplan tile recovers
  // without a page reload.
  assert.equal(onSimbriefConfigured.mock.calls.length, 1);
});

test("a malformed handle is rejected client-side with no request", async () => {
  const renderer = await renderPanel();
  await type(renderer, "simbrief-input", "bad handle");
  await click(renderer, "simbrief-save");

  assert.equal(bridge.calls.some((c) => c.method === "PUT"), false);
  assert.match(textOf(renderer), /SimBrief username/);
});

test("a backend field error is surfaced on the SimBrief field", async () => {
  const original = bridge.fetch;
  globalThis.fetch = vi.fn(async (url, init) => {
    if (String(url).includes("/api/crew/settings") && init?.method === "PUT") {
      return {
        ok: false,
        status: 422,
        json: async () => ({
          detail: { message: "rejected", errors: { simbrief_user: "Handle not allowed." } },
        }),
      };
    }
    return original(url, init);
  });
  const renderer = await renderPanel();
  await type(renderer, "simbrief-input", "pilot123");
  await click(renderer, "simbrief-save");
  assert.match(textOf(renderer), /Handle not allowed/);
});

test("TEST performs a real fetch and reports a real failure", async () => {
  const renderer = await renderPanel();
  await click(renderer, "simbrief-test");
  assert.ok(
    bridge.calls.some((c) => c.url.includes("/api/simbrief/flightplan/live?refresh=true"))
  );
  assert.match(textOf(renderer), /SIMBRIEF_USER is not set/);
});

test("TEST reports a real success with the OFP it actually received", async () => {
  bridge.simbriefLive = {
    ok: true,
    status: 200,
    body: { origin: "OTHH", destination: "EGLL" },
  };
  const renderer = await renderPanel();
  await click(renderer, "simbrief-test");
  assert.match(textOf(renderer), /OTHH → EGLL/);
});

/* ── Navigraph section ───────────────────────────────────────────────── */

test("saving Navigraph credentials sends both fields and clears the secret", async () => {
  const renderer = await renderPanel();
  await type(renderer, "navigraph-client-id", PLACEHOLDER_ID);
  await type(renderer, "navigraph-client-secret", PLACEHOLDER_SECRET);
  await click(renderer, "navigraph-save");

  const put = bridge.calls.find((c) => c.method === "PUT");
  assert.equal(put.body.navigraph_client_id, PLACEHOLDER_ID);
  assert.equal(put.body.navigraph_client_secret, PLACEHOLDER_SECRET);
  // The secret is write-only: it is dropped from the form immediately.
  assert.equal(byTestId(renderer, "navigraph-client-secret").props.value, "");
  assert.equal(byTestId(renderer, "navigraph-client-id").props.value, "");
  // Credentials present but the pilot has not linked an account yet.
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "NOT LINKED");
  assert.ok(byTestId(renderer, "navigraph-link"));
});

test("a client id without a secret cannot pretend to be configured", async () => {
  const renderer = await renderPanel();
  await type(renderer, "navigraph-client-id", PLACEHOLDER_ID);
  await click(renderer, "navigraph-save");
  assert.equal(bridge.calls.some((c) => c.method === "PUT"), false);
  assert.match(textOf(renderer), /client secret is required/i);
});

test("a malformed client id is rejected client-side", async () => {
  const renderer = await renderPanel();
  await type(renderer, "navigraph-client-id", "bad id");
  await type(renderer, "navigraph-client-secret", PLACEHOLDER_SECRET);
  await click(renderer, "navigraph-save");
  assert.equal(bridge.calls.some((c) => c.method === "PUT"), false);
  assert.match(textOf(renderer), /client ID/);
});

test("pressing save with nothing changed says so instead of erroring", async () => {
  bridge.clientIdSet = true;
  bridge.clientSecretSet = true;
  const renderer = await renderPanel();
  await click(renderer, "navigraph-save");
  assert.equal(bridge.calls.some((c) => c.method === "PUT"), false);
  assert.match(textOf(renderer), /Nothing to save/);
});

test("a malformed secret is rejected client-side", async () => {
  bridge.clientIdSet = true;
  const renderer = await renderPanel();
  await type(renderer, "navigraph-client-secret", "has spaces");
  await click(renderer, "navigraph-save");
  assert.equal(bridge.calls.some((c) => c.method === "PUT"), false);
  assert.match(textOf(renderer), /client secret/);
});

/* ── device-authorization flow ───────────────────────────────────────── */

test("the device flow shows the code, polls at the server interval and flips the rows", async () => {
  vi.useFakeTimers();
  bridge.clientIdSet = true;
  bridge.clientSecretSet = true;

  const renderer = await renderPanel();
  await act(async () => byTestId(renderer, "navigraph-link").props.onClick());

  // The pilot-facing code and the one-click verification URL are rendered.
  const device = byTestId(renderer, "navigraph-device");
  assert.ok(device, "device-flow panel not rendered");
  const deviceText = textOf(renderer);
  assert.match(deviceText, /ABCD-1234/);
  const link = renderer.root.findAll(
    (n) => n.type === "a" && String(n.props.href || "").includes("code=ABCD-1234")
  )[0];
  assert.ok(link, "verification_uri_complete is not a clickable link");

  // Nothing is polled before the server-declared interval elapses.
  const pollsAt = () =>
    bridge.calls.filter((c) => c.url.includes("/auth/device/poll")).length;
  assert.equal(pollsAt(), 0);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  assert.equal(pollsAt(), 0, "polled before the 3 s interval");

  // First tick: still pending, so the panel keeps waiting.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1100);
  });
  assert.equal(pollsAt(), 1);
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "NOT LINKED");

  // Second tick: authorized → status refreshed → capabilities flip.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });
  assert.equal(pollsAt(), 2);
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "LINKED");
  assert.match(textOf(renderer), /Airport data LIVE/);
  assert.match(textOf(renderer), /NAT tracks LIVE/);
  assert.match(textOf(renderer), /Navigraph signed in/);

  // Polling stopped once the flow resolved.
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  assert.equal(pollsAt(), 2, "kept polling after authorization");
});

test("an expired device flow is reported, not retried forever", async () => {
  vi.useFakeTimers();
  bridge.clientIdSet = true;
  bridge.clientSecretSet = true;
  bridge.pollQueue = ["expired"];

  const renderer = await renderPanel();
  await act(async () => byTestId(renderer, "navigraph-link").props.onClick());
  await act(async () => {
    await vi.advanceTimersByTimeAsync(3100);
  });

  assert.match(textOf(renderer), /expired/);
  const polls = bridge.calls.filter((c) => c.url.includes("/auth/device/poll")).length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(10000);
  });
  assert.equal(
    bridge.calls.filter((c) => c.url.includes("/auth/device/poll")).length,
    polls
  );
});

test("signing out calls the existing signout endpoint and drops the link", async () => {
  bridge.clientIdSet = true;
  bridge.clientSecretSet = true;
  bridge.navAuthenticated = true;

  const renderer = await renderPanel();
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "LINKED");
  await click(renderer, "navigraph-signout");

  assert.ok(bridge.calls.some((c) => c.url.includes("/auth/signout") && c.method === "POST"));
  assert.equal(byTestId(renderer, "navigraph-pill").children.join(""), "NOT LINKED");
});
