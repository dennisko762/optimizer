import { test } from "node:test";
import assert from "node:assert/strict";

// Minimal localStorage stub — crewAuth must not touch the real global.
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

const {
  validateCredentials,
  login,
  saveSession,
  loadSession,
  clearSession,
} = await import("./crewAuth.js");

test("validateCredentials rejects empty pilot id / password with hints", () => {
  const bothEmpty = validateCredentials({ pilotId: "", password: "" });
  assert.equal(bothEmpty.valid, false);
  assert.equal(bothEmpty.errors.length, 2);

  const onlyId = validateCredentials({ pilotId: "QR-1", password: "  " });
  assert.equal(onlyId.valid, false);
  assert.equal(onlyId.errors.length, 1);
  assert.match(onlyId.errors[0], /password/i);
});

test("validateCredentials accepts non-empty credentials", () => {
  const ok = validateCredentials({ pilotId: "QR-1", password: "x" });
  assert.equal(ok.valid, true);
  assert.deepEqual(ok.errors, []);
});

test("login accepts any non-empty credentials (mock auth)", () => {
  const now = 1_700_000_000_000;
  const res = login({ pilotId: "qr-42", password: "whatever" }, now);
  assert.equal(res.ok, true);
  assert.deepEqual(res.errors, []);
  assert.equal(res.session.pilotId, "qr-42");
  assert.equal(res.session.logged_in_at, now);
});

test("login rejects empty input and returns no session", () => {
  const res = login({ pilotId: "  ", password: "pw" });
  assert.equal(res.ok, false);
  assert.equal(res.session, null);
  assert.ok(res.errors.length > 0);
});

test("login trims whitespace from the stored pilot id", () => {
  const res = login({ pilotId: "  QR-7  ", password: "pw" });
  assert.equal(res.session.pilotId, "QR-7");
});

test("session round-trips through storage", () => {
  saveSession({ pilotId: "QR-9", logged_in_at: 123 });
  assert.deepEqual(loadSession(), { pilotId: "QR-9", logged_in_at: 123 });
});

test("loadSession returns null when absent or corrupt", () => {
  clearSession();
  assert.equal(loadSession(), null);

  store.set("qr.crew.session.v1", "{not json");
  assert.equal(loadSession(), null);

  store.set("qr.crew.session.v1", JSON.stringify({ logged_in_at: 1 }));
  assert.equal(loadSession(), null); // no pilotId → invalid
});

test("clearSession removes the persisted session", () => {
  saveSession({ pilotId: "QR-9" });
  clearSession();
  assert.equal(loadSession(), null);
});
