/**
 * BriefingPanel — component coverage for the aggregated aviation briefing
 * UI (ATIS / METAR / TAF / SIGMET / SIGWX).
 *
 * The panel must never make a gated, disabled or stale source look live:
 * every row keeps its own label, state badge and age, and the three
 * ATIS variants are listed separately. These tests render the REAL
 * component against a stubbed internal API (never upstream services).
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

import BriefingPanel from "./BriefingPanel.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const NOW_ISO = new Date(Date.now() - 6 * 60000).toISOString();

const ATIS_BODY = {
  station: "EDDF",
  selected: "vatsim_atis",
  real_world_datis: {
    label: "Real-world D-ATIS — FAA",
    state: "disabled",
    detail: "Provider not configured.",
  },
  vatsim_atis: {
    label: "VATSIM ATIS",
    state: "ok",
    raw: "EDDF ATIS A 1220Z 25008KT CAVOK",
    station: "EDDF",
    retrieved_utc: NOW_ISO,
  },
  metar_briefing: {
    label: "METAR briefing",
    state: "stale",
    raw: "EDDF 101220Z 25008KT CAVOK 18/09 Q1016",
    station: "EDDF",
    stale: true,
    issued_utc: NOW_ISO,
  },
};

const METAR_BODY = {
  products: [
    {
      label: "METAR",
      state: "ok",
      raw: "EDDF 101220Z 25008KT CAVOK 18/09 Q1016",
      station: "EDDF",
      retrieved_utc: NOW_ISO,
    },
  ],
};

const TAF_BODY = {
  products: [
    { label: "TAF", state: "ok", raw: "TAF EDDF 101100Z 1012/1112 25010KT", station: "EDDF" },
  ],
};

const SIGMET_BODY = {
  products: [
    {
      label: "SIGMET EDWW",
      state: "ok",
      raw: "EDWW SIGMET 1 VALID 101200/101600 SEV TURB FCST",
      geometry: { type: "Polygon", coordinates: [] },
      retrieved_utc: NOW_ISO,
    },
  ],
};

const SIGWX_BODY = {
  label: "SIGWX / WAFS",
  state: "ok",
  raw: "WAFS SIGWX chart T+12",
  flash_count: 12,
  collection: "EO:EUM:DAT:0691",
  retrieved_utc: NOW_ISO,
};

function stubFetch(mode = "ok") {
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (mode === "throw") throw new Error("briefing backend unreachable");
    if (mode === "down") return { ok: false, status: 503, json: async () => ({}) };
    const body = u.includes("/briefing/atis/")
      ? ATIS_BODY
      : u.includes("/briefing/metar")
        ? METAR_BODY
        : u.includes("/briefing/taf")
          ? TAF_BODY
          : u.includes("/briefing/sigmet")
            ? { ...SIGMET_BODY, products: mode === "empty" ? [] : SIGMET_BODY.products }
            : SIGWX_BODY;
    return { ok: true, status: 200, json: async () => body };
  });
}

async function render(props) {
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(BriefingPanel, { apiBase: "/bridge", ...props }));
  });
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return JSON.stringify(renderer.toJSON());
}

beforeEach(() => {
  stubFetch("ok");
});

test("every briefing product is rendered with its own label, state and age", async () => {
  const out = await render({ station: "EDDF" });

  assert.match(out, /AVIATION BRIEFING/);
  assert.match(out, /EDDF/);
  // the three ATIS variants stay separate rows with their own state
  assert.match(out, /VATSIM ATIS/);
  assert.match(out, /Real-world D-ATIS — FAA/);
  assert.match(out, /METAR briefing/);
  assert.match(out, /LIVE/);
  assert.match(out, /DISABLED/);
  // a stale product is disclosed as last-good, never as live
  assert.match(out, /last-good/);
  // a source without a raw bulletin shows its detail, not a blank row
  assert.match(out, /Provider not configured\./);
  // ages are rendered in minutes
  assert.match(out, /6/);
  assert.match(out, /min ago/);
  // METAR / TAF / SIGMET / SIGWX sections all populated
  assert.match(out, /TAF EDDF 101100Z/);
  assert.match(out, /SIGMET EDWW/);
  assert.match(out, /SIGWX \/ WAFS/);
  // lightning is route-box scoped and intentionally not duplicated here
  assert.match(out, /EO:EUM:DAT:0691/);
  assert.equal(globalThis.fetch.mock.calls.length, 5);
  assert.ok(
    globalThis.fetch.mock.calls.every((c) => String(c[0]).startsWith("/bridge/api/crew/weather/briefing/")),
    "the panel must only call internal briefing routes"
  );
});

test("an age-less product reads 'age unknown' and an empty SIGMET list is explicit", async () => {
  stubFetch("empty");
  const out = await render({ station: "EDDF" });
  assert.match(out, /age unknown/);
  assert.match(out, /No active SIGMETs\./);
});

test("unavailable upstream sources leave the panel honest, not empty", async () => {
  stubFetch("down");
  const out = await render({ station: "EDDF" });
  assert.match(out, /Loading ATIS…/);
  assert.match(out, /No active SIGMETs\./);
  assert.ok(!out.includes("LIVE"), "nothing may be badged LIVE when every source is down");
});

test("a fetch failure surfaces as an error instead of a silent blank briefing", async () => {
  stubFetch("throw");
  const out = await render({ station: "EDDF" });
  assert.match(out, /qr-error/);
  assert.match(out, /briefing backend unreachable/);
});

test("without a station the panel asks for a flight and fetches nothing", async () => {
  const out = await render({ station: null });
  assert.match(out, /Select a flight with a departure ICAO/);
  assert.equal(globalThis.fetch.mock.calls.length, 0);
});
