/**
 * settingsApi unit tests (card t_97dcde51).
 *
 * The validators mirror the backend rules, so a bad value is labelled
 * before a round trip; the mappers must never report "configured" for an
 * absent or failed response.
 *
 * The placeholders below are obvious non-credentials.
 */

import assert from "node:assert/strict";
import { afterEach, test, vi } from "vitest";

import {
  NAVIGRAPH_PORTAL_URL,
  describeSaveFailure,
  fetchSettings,
  fetchSimbriefReadiness,
  mapSettingsView,
  mapSimbriefPill,
  saveSettings,
  testSimbrief,
  validateNavigraphClientId,
  validateNavigraphClientSecret,
  validateSimbriefUser,
} from "./settingsApi.js";

const PLACEHOLDER_ID = "aaaa-bbbb-cccc";
const PLACEHOLDER_SECRET = "zzzz-zzzz-zzzz-zzzz";

afterEach(() => {
  delete globalThis.fetch;
});

/* ── validators ──────────────────────────────────────────────────────── */

test("a valid SimBrief handle is accepted and trimmed", () => {
  const check = validateSimbriefUser("  pilot.handle  ");
  assert.equal(check.valid, true);
  assert.equal(check.value, "pilot.handle");
  assert.equal(check.error, null);
});

test("empty, spaced, quoted and over-long SimBrief handles are rejected", () => {
  for (const bad of ["", "   ", null, undefined, "has space", 'q"uote', "x#y", "-lead", "x".repeat(65)]) {
    const check = validateSimbriefUser(bad);
    assert.equal(check.valid, false, `expected ${JSON.stringify(bad)} to be rejected`);
    assert.ok(check.error && check.error.length > 0);
  }
  assert.match(validateSimbriefUser("").error, /required/i);
  assert.match(validateSimbriefUser("x".repeat(65)).error, /too long/i);
});

test("Navigraph client id and secret have their own shapes", () => {
  assert.equal(validateNavigraphClientId(PLACEHOLDER_ID).valid, true);
  assert.equal(validateNavigraphClientId("bad id").valid, false);
  assert.equal(validateNavigraphClientId("x".repeat(129)).valid, false);

  assert.equal(validateNavigraphClientSecret(PLACEHOLDER_SECRET).valid, true);
  assert.equal(validateNavigraphClientSecret("has space").valid, false);
  assert.equal(validateNavigraphClientSecret("").valid, false);
  assert.equal(validateNavigraphClientSecret("x".repeat(513)).valid, false);
});

test("the portal hint points at Navigraph's developer site", () => {
  assert.match(NAVIGRAPH_PORTAL_URL, /^https:\/\/developers\.navigraph\.com(\/|$)/);
});

/* ── mappers ─────────────────────────────────────────────────────────── */

test("an absent settings body maps to an honest unavailable view", () => {
  for (const bad of [null, undefined, "nope", 42]) {
    const view = mapSettingsView(bad);
    assert.equal(view.available, false);
    assert.equal(view.simbrief.configured, false);
    assert.equal(view.navigraph.configured, false);
    assert.match(view.notice, /unavailable/i);
  }
});

test("a real settings body maps to booleans and the pilot handle", () => {
  const view = mapSettingsView({
    simbrief: { configured: true, user: "pilot123" },
    navigraph: {
      client_id_set: true,
      client_secret_set: true,
      access_token_injected: false,
      configured: true,
      scopes: ["openid", "charts"],
    },
    env_file: { exists: true, writable: true },
    remote_writes_allowed: true,
  });
  assert.equal(view.available, true);
  assert.deepEqual(view.simbrief, { configured: true, user: "pilot123" });
  assert.equal(view.navigraph.clientSecretSet, true);
  assert.deepEqual(view.navigraph.scopes, ["openid", "charts"]);
  assert.equal(view.remoteWritesAllowed, true);
  assert.equal(view.notice, null);
});

test("an unwritable env file is surfaced as a blocking notice", () => {
  const view = mapSettingsView({ env_file: { exists: true, writable: false } });
  assert.match(view.notice, /not writable/i);
  assert.deepEqual(view.navigraph.scopes, []);
});

test("the SimBrief pill never claims configured without a live answer", () => {
  assert.deepEqual(mapSimbriefPill(null), {
    label: "UNKNOWN",
    badgeClass: "badge--dispatch",
    configured: false,
  });
  assert.equal(mapSimbriefPill({ configured: false }).label, "NOT CONFIGURED");
  assert.equal(mapSimbriefPill({ configured: true }).label, "CONFIGURED");
});

test("describeSaveFailure surfaces per-field errors and plain details", () => {
  const fielded = describeSaveFailure(422, {
    detail: { message: "Some settings were rejected.", errors: { simbrief_user: "bad" } },
  });
  assert.equal(fielded.fieldErrors.simbrief_user, "bad");

  const plain = describeSaveFailure(403, { detail: "loopback only" });
  assert.equal(plain.message, "loopback only");
  assert.deepEqual(plain.fieldErrors, {});

  const unknown = describeSaveFailure(500, null);
  assert.match(unknown.message, /HTTP 500/);
});

/* ── network wrappers ────────────────────────────────────────────────── */

test("fetchSettings refuses without a session and returns the body with one", async () => {
  const none = await fetchSettings("/bridge", null);
  assert.equal(none.ok, false);
  assert.match(none.error, /session/i);

  globalThis.fetch = vi.fn(async (url) => {
    assert.match(String(url), /\/api\/crew\/settings\?session_id=s1/);
    return { ok: true, status: 200, json: async () => ({ simbrief: { configured: true } }) };
  });
  const got = await fetchSettings("/bridge", "s1");
  assert.equal(got.ok, true);
  assert.equal(got.state.simbrief.configured, true);
});

test("fetchSettings reports an HTTP failure and a thrown fetch honestly", async () => {
  globalThis.fetch = vi.fn(async () => ({
    ok: false,
    status: 401,
    json: async () => ({ detail: "Invalid or expired crew session." }),
  }));
  const denied = await fetchSettings("", "s1");
  assert.equal(denied.ok, false);
  assert.match(denied.error, /expired crew session/);

  globalThis.fetch = vi.fn(async () => {
    throw new Error("bridge unreachable");
  });
  const offline = await fetchSettings("", "s1");
  assert.equal(offline.ok, false);
  assert.match(offline.error, /unreachable/);
});

test("saveSettings PUTs only the submitted keys alongside the session id", async () => {
  let sent = null;
  globalThis.fetch = vi.fn(async (url, init) => {
    assert.equal(init.method, "PUT");
    sent = JSON.parse(init.body);
    return { ok: true, status: 200, json: async () => ({ updated: ["SIMBRIEF_USER"] }) };
  });
  const result = await saveSettings("/bridge", "s1", { simbrief_user: "pilot123" });
  assert.equal(result.ok, true);
  assert.deepEqual(sent, { session_id: "s1", simbrief_user: "pilot123" });
  assert.equal(Object.hasOwn(sent, "navigraph_client_secret"), false);
});

test("saveSettings maps a 422 into per-field errors", async () => {
  globalThis.fetch = vi.fn(async () => ({
    ok: false,
    status: 422,
    json: async () => ({
      detail: { message: "Some settings were rejected.", errors: { simbrief_user: "empty" } },
    }),
  }));
  const result = await saveSettings("", "s1", { simbrief_user: " " });
  assert.equal(result.ok, false);
  assert.equal(result.fieldErrors.simbrief_user, "empty");
});

test("saveSettings needs a session and survives a network error", async () => {
  const none = await saveSettings("", null, { simbrief_user: "x" });
  assert.equal(none.ok, false);

  globalThis.fetch = vi.fn(async () => {
    throw new Error("socket closed");
  });
  const failed = await saveSettings("", "s1", { simbrief_user: "x" });
  assert.equal(failed.ok, false);
  assert.match(failed.message, /socket closed/);
});

test("fetchSimbriefReadiness returns null instead of guessing", async () => {
  globalThis.fetch = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }));
  assert.equal(await fetchSimbriefReadiness(""), null);

  globalThis.fetch = vi.fn(async () => {
    throw new Error("down");
  });
  assert.equal(await fetchSimbriefReadiness(""), null);

  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ configured: true }),
  }));
  assert.deepEqual(await fetchSimbriefReadiness(""), { configured: true });
});

test("testSimbrief performs a real refresh fetch and reports the outcome", async () => {
  globalThis.fetch = vi.fn(async (url) => {
    assert.match(String(url), /\/api\/simbrief\/flightplan\/live\?refresh=true/);
    return {
      ok: true,
      status: 200,
      json: async () => ({ origin: "OTHH", destination: "EGLL" }),
    };
  });
  const ok = await testSimbrief("/bridge");
  assert.equal(ok.ok, true);
  assert.match(ok.message, /OTHH → EGLL/);
});

test("a failing SimBrief test is never dressed up as a success", async () => {
  globalThis.fetch = vi.fn(async () => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: "SIMBRIEF_USER is not set." }),
  }));
  const failed = await testSimbrief("");
  assert.equal(failed.ok, false);
  assert.match(failed.message, /SIMBRIEF_USER is not set/);

  globalThis.fetch = vi.fn(async () => ({ ok: false, status: 502, json: async () => null }));
  const bare = await testSimbrief("");
  assert.equal(bare.ok, false);
  assert.match(bare.message, /HTTP 502/);

  globalThis.fetch = vi.fn(async () => {
    throw new Error("no route to host");
  });
  const thrown = await testSimbrief("");
  assert.equal(thrown.ok, false);
  assert.match(thrown.message, /no route to host/);
});

test("a successful test without route fields still reports success", async () => {
  globalThis.fetch = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({}) }));
  const ok = await testSimbrief("");
  assert.equal(ok.ok, true);
  assert.match(ok.message, /real OFP\./);
});
