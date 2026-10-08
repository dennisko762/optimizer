import test from "node:test";
import assert from "node:assert/strict";
import {
  stateBadge,
  ageMinutes,
  mapProduct,
  mapAtisResolution,
  mapMetarTafList,
  mapSigmetList,
  mapLightning,
} from "./briefingMappers.js";

test("stateBadge maps every known state to an uppercase label", () => {
  assert.equal(stateBadge("ok"), "LIVE");
  assert.equal(stateBadge("stale"), "STALE");
  assert.equal(stateBadge("disabled"), "DISABLED");
  assert.equal(stateBadge("provider_error"), "ERROR");
  assert.equal(stateBadge("unavailable"), "UNAVAILABLE");
});

test("stateBadge falls back to the raw state for unknown values", () => {
  assert.equal(stateBadge("mystery"), "MYSTERY");
});

test("ageMinutes computes from issued_utc relative to now", () => {
  const now = Date.parse("2026-10-09T12:30:00Z");
  const mins = ageMinutes({ issued_utc: "2026-10-09T12:00:00Z" }, now);
  assert.equal(mins, 30);
});

test("ageMinutes returns null when no timestamp present", () => {
  assert.equal(ageMinutes({}), null);
  assert.equal(ageMinutes(null), null);
});

test("mapProduct defaults to unavailable for a missing product", () => {
  const p = mapProduct(null);
  assert.equal(p.state, "unavailable");
  assert.equal(p.badge, "UNAVAILABLE");
  assert.equal(p.raw, null);
});

test("mapProduct carries label/state/stale/raw through untouched", () => {
  const p = mapProduct({
    label: "METAR", state: "ok", raw: "OTHH 091200Z ...",
    issued_utc: "2026-10-09T12:00:00Z", stale: false, station: "OTHH",
  });
  assert.equal(p.label, "METAR");
  assert.equal(p.badge, "LIVE");
  assert.equal(p.raw, "OTHH 091200Z ...");
  assert.equal(p.station, "OTHH");
});

test("mapAtisResolution keeps the three candidates distinct, never conflated", () => {
  const body = {
    station: "OTHH", selected: "metar_briefing",
    real_world_datis: { label: "METAR briefing", state: "unavailable" },
    vatsim_atis: { label: "VATSIM ATIS", state: "disabled" },
    metar_briefing: { label: "METAR", state: "ok", raw: "OTHH ..." },
  };
  const out = mapAtisResolution(body);
  assert.equal(out.selected, "metar_briefing");
  assert.equal(out.candidates.length, 3);
  const byKey = Object.fromEntries(out.candidates.map((c) => [c.key, c]));
  assert.equal(byKey.real_world_datis.label, "METAR briefing");
  assert.equal(byKey.vatsim_atis.label, "VATSIM ATIS");
  assert.equal(byKey.vatsim_atis.state, "disabled");
  assert.equal(byKey.metar_briefing.label, "METAR");
  assert.equal(byKey.metar_briefing.state, "ok");
});

test("mapAtisResolution tolerates a missing/garbage body", () => {
  const out = mapAtisResolution(null);
  assert.deepEqual(out, { station: null, selected: null, candidates: [] });
});

test("mapMetarTafList maps every entry, tolerating a non-array products field", () => {
  const list = mapMetarTafList({ products: [{ label: "METAR", state: "ok" }] });
  assert.equal(list.length, 1);
  assert.equal(list[0].label, "METAR");
  assert.deepEqual(mapMetarTafList({}), []);
});

test("mapSigmetList preserves geometry", () => {
  const list = mapSigmetList({
    products: [{ label: "SIGMET (international)", state: "ok", geometry: { type: "Polygon", coordinates: [[[0, 0]]] } }],
  });
  assert.equal(list[0].geometry.type, "Polygon");
});

test("mapLightning surfaces flash_count and collection id, no invented intensity", () => {
  const out = mapLightning({
    label: "MTG-LI Level 2 Lightning Flashes (LFL)", state: "disabled",
    flash_count: 4, collection: "EO:EUM:DAT:0691",
  });
  assert.equal(out.flashCount, 4);
  assert.equal(out.collection, "EO:EUM:DAT:0691");
  assert.equal(out.state, "disabled");
  assert.ok(!("intensity" in out));
});
