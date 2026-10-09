/**
 * useSimTelemetry — guard regression tests.
 *
 * The M3 live layer silently did nothing in the DEFAULT configuration:
 * CrewPlatformContext sets apiBase = import.meta.env.VITE_API_URL ?? "", and
 * "" (same-origin) is falsy, so `if (!apiBase) return;` short-circuited every
 * fetch. The endpoint reported connected:true while the header chip stayed
 * "SIM DISCONNECTED" forever.
 *
 * These tests pin the contract on the pure guard: same-origin "" must be
 * treated as a usable base; only null/undefined may disable the live layer.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = readFileSync(fileURLToPath(new URL("./useSimTelemetry.js", import.meta.url)), "utf8");

test("same-origin apiBase '' is not rejected by a truthiness guard", () => {
  assert.ok(
    !/if \(!apiBase\) return/.test(source),
    'a bare `if (!apiBase) return` disables the live layer for same-origin ("")'
  );
  assert.ok(
    !/if \(!enabled \|\| !apiBase\)/.test(source),
    'a bare `!apiBase` in the polling effect stops all telemetry polling for same-origin ("")'
  );
  assert.ok(
    !/if \(!apiBase\) return \{ ok: false/.test(source),
    'a bare `!apiBase` in apply() makes the Apply button dead for same-origin ("")'
  );
});

test("guards use an explicit null/undefined check", () => {
  assert.match(
    source,
    /const hasApiBase = \(apiBase\) => apiBase != null;/,
    "expected an explicit null/undefined predicate for the API base"
  );
  const guardCount = (source.match(/hasApiBase\(apiBase\)/g) || []).length;
  assert.equal(guardCount, 3, "fetchTelemetry, the polling effect and apply() must all use the predicate");
});

test("the predicate accepts '' and a real base, rejects null/undefined", () => {
  const hasApiBase = (apiBase) => apiBase != null;
  assert.equal(hasApiBase(""), true, "same-origin must be usable");
  assert.equal(hasApiBase("http://127.0.0.1:8002"), true);
  assert.equal(hasApiBase(null), false);
  assert.equal(hasApiBase(undefined), false);
});
