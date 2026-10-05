import { test } from "node:test";
import assert from "node:assert/strict";

// Minimal localStorage stub — crewCheckin must not touch the real global.
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};

const {
  parseCheckins,
  loadCheckins,
  saveCheckins,
  checkInFlight,
  isCheckedIn,
  getCheckin,
} = await import("./crewCheckin.js");

const KEY = "qr.crew.checkins.v1";

test("parseCheckins tolerates corrupt or wrong-shaped payloads", () => {
  assert.deepEqual(parseCheckins(null), {});
  assert.deepEqual(parseCheckins(""), {});
  assert.deepEqual(parseCheckins("{oops"), {});
  assert.deepEqual(parseCheckins("[1,2,3]"), {});
  assert.deepEqual(parseCheckins('"str"'), {});
  assert.deepEqual(parseCheckins("{}"), {});
});

test("parseCheckins keeps a valid map as-is", () => {
  const map = { f1: { at: 1, pilot: "QR-1" } };
  assert.deepEqual(parseCheckins(JSON.stringify(map)), map);
});

test("checkInFlight stores a record and reports ok", () => {
  const now = 1_700_000_000_000;
  const res = checkInFlight("QR815-DOH-LHR", { pilotId: "QR-1" }, now);
  assert.equal(res.ok, true);
  assert.deepEqual(res.record, { at: now, pilot: "QR-1" });
  assert.equal(isCheckedIn("QR815-DOH-LHR"), true);
  assert.deepEqual(getCheckin("QR815-DOH-LHR"), { at: now, pilot: "QR-1" });
});

test("checkInFlight rejects a missing flight id without writing", () => {
  store.clear();
  const res = checkInFlight(null, { pilotId: "QR-1" });
  assert.equal(res.ok, false);
  assert.equal(res.record, null);
  assert.ok(res.error);
  assert.deepEqual(loadCheckins(), {});
});

test("checkInFlight overwrites an earlier record for the same flight", () => {
  checkInFlight("f", null, 1);
  checkInFlight("f", null, 2);
  assert.deepEqual(getCheckin("f"), { at: 2, pilot: null });
});

test("pilot is recorded from the crew session when present", () => {
  checkInFlight("f", { pilotId: "  " }, 5); // blank pilot id
  assert.equal(getCheckin("f").pilot, null);
  checkInFlight("f", { pilotId: "QR-2" }, 6);
  assert.equal(getCheckin("f").pilot, "QR-2");
});

test("isCheckedIn / getCheckin are case-trimmed and null-safe", () => {
  checkInFlight(" f ", null, 7);
  assert.equal(isCheckedIn("f"), true);
  assert.equal(isCheckedIn(null), false);
  assert.equal(isCheckedIn("other"), false);
  assert.equal(getCheckin(null), null);
  assert.equal(getCheckin("other"), null);
});

test("saveCheckins / loadCheckins round-trip the whole map", () => {
  saveCheckins({ a: { at: 1, pilot: null }, b: { at: 2, pilot: "P" } });
  const map = loadCheckins();
  assert.deepEqual(map.a, { at: 1, pilot: null });
  assert.deepEqual(map.b, { at: 2, pilot: "P" });
});

test("a corrupt stored payload never breaks the UI (loadCheckins → {})", () => {
  store.set(KEY, "{{{");
  assert.deepEqual(loadCheckins(), {});
  // and a new check-in recovers cleanly over the corrupt data
  const res = checkInFlight("f", null, 9);
  assert.equal(res.ok, true);
  assert.deepEqual(loadCheckins(), { f: { at: 9, pilot: null } });
});
