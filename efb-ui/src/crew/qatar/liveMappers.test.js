import test from "node:test";
import assert from "node:assert/strict";
import {
  mapSimStatus,
  mapLiveStrip,
  mapFuelDeviation,
  mapAltDeviation,
  mapLiveTiming,
  gcNm,
  mapRouteLive,
  mapApplyTargets,
  buildLiveOptimizeRequest,
  formatApiError,
} from "./liveMappers.js";

/* ── mapSimStatus ───────────────────────────────────────────────────── */

test("mapSimStatus connected chip with data age", () => {
  const s = mapSimStatus({ connected: true, dataAgeMs: 1500, flightStatePatch: {} });
  assert.equal(s.status, "connected");
  assert.equal(s.label, "SIM CONNECTED");
  assert.equal(s.sub, "data 2s old");
});

test("mapSimStatus disconnected shows the last error", () => {
  const s = mapSimStatus({ connected: false, lastError: "MSFS down" });
  assert.equal(s.status, "disconnected");
  assert.equal(s.label, "SIM DISCONNECTED");
  assert.equal(s.sub, "MSFS down");
});

test("mapSimStatus unknown when the EFB server itself is unreachable", () => {
  const s = mapSimStatus(null, { reachable: false });
  assert.equal(s.status, "unknown");
  assert.equal(s.label, "NO SERVER");
});

test("mapSimStatus disconnected without error text gets a hint", () => {
  const s = mapSimStatus({ connected: false });
  assert.equal(s.status, "disconnected");
  assert.equal(s.sub, "Start MSFS to go live");
});

/* ── mapLiveStrip ───────────────────────────────────────────────────── */

const LIVE_PATCH = {
  altitudeFt: 37000,
  mach: 0.84,
  groundSpeedKt: 480,
  windComponentKt: -12,
  fuelRemainingKg: 18500,
  fuelFlowKgH: 2900,
  fuelFlowSource: "FUEL_TOTAL_QUANTITY_WEIGHT_DELTA",
};

test("mapLiveStrip maps the connected telemetry patch 1:1 (no static fallback)", () => {
  const t = mapLiveStrip(
    { connected: true, dataAgeMs: 1000, flightStatePatch: LIVE_PATCH },
    { fuelLandingT: 16, cruiseAlt: 350 }
  );
  assert.equal(t.flightLevel, 370);
  assert.equal(t.mach, 0.84);
  assert.equal(t.groundSpeedKt, 480);
  assert.equal(t.windComponentKt, -12);
  assert.equal(t.fuelRemainingKg, 18500);
  assert.equal(t.fuelFlowKgH, 2900);
  assert.equal(t.fuelFlowSource, "FUEL_TOTAL_QUANTITY_WEIGHT_DELTA");
  assert.equal(t.deviation.kg, 2500); // 18500 - 16*1000
  assert.equal(t.deviation.t, 2.5);
  assert.equal(t.altDeviation, 20); // FL370 vs planned 350
});

test("mapLiveStrip disconnected: every value stays null (AGENTS.md: no static fallback)", () => {
  const t = mapLiveStrip(
    { connected: false, lastError: "offline", flightStatePatch: {} },
    { fuelLandingT: 16, cruiseAlt: 350 }
  );
  assert.equal(t.flightLevel, null);
  assert.equal(t.mach, null);
  assert.equal(t.fuelRemainingKg, null);
  assert.equal(t.fuelFlowKgH, null);
  assert.equal(t.deviation, null);
  assert.equal(t.altDeviation, null);
});

test("mapLiveStrip derives flight level from altitudeFt when FL missing", () => {
  const t = mapLiveStrip(
    { connected: true, flightStatePatch: { altitudeFt: 37950 } }
  );
  assert.equal(t.flightLevel, 380); // round(379.5) = 380
});

test("mapFuelDeviation null when either side is missing — never 0", () => {
  assert.equal(mapFuelDeviation({ actualKg: null, plannedKg: 16000 }), null);
  assert.equal(mapFuelDeviation({ actualKg: 18500, plannedKg: null }), null);
  assert.deepEqual(mapFuelDeviation({ actualKg: 15400, plannedKg: 16000 }), { kg: -600, t: -0.6 });
});

test("mapAltDeviation signed FL difference", () => {
  assert.equal(mapAltDeviation({ actualFl: 350, plannedFl: 380 }), -30);
  assert.equal(mapAltDeviation({ actualFl: null, plannedFl: 380 }), null);
});

/* ── mapLiveTiming ──────────────────────────────────────────────────── */

test("mapLiveTiming GPS ETE vs planned block time", () => {
  const t = mapLiveTiming(
    {
      connected: true,
      flightStatePatch: { gpsEteSeconds: 2580, groundSpeedKt: 480 },
    },
    { plannedBlockMin: 43 }
  );
  assert.equal(t.connected, true);
  assert.equal(t.ete, "00:43");
  assert.equal(t.eteMinutes, 43);
  assert.equal(t.eteSource, "GPS");
  assert.equal(t.plannedEte, "00:43");
  assert.equal(t.deltaMin, 0);
});

test("mapLiveTiming falls back to live distance + ground speed without GPS plan", () => {
  const t = mapLiveTiming(
    {
      connected: true,
      flightStatePatch: { gpsEteSeconds: null, remainingDistanceNm: 500, groundSpeedKt: 500 },
    },
    { plannedBlockMin: 45 }
  );
  assert.equal(t.eteSource, "LIVE_GS");
  assert.equal(t.eteMinutes, 60); // 500 nm / 500 kt = 1 h
  assert.equal(t.ete, "01:00");
  assert.equal(t.deltaMin, 15); // 60 - 45
});

test("mapLiveTiming offline: nothing derived, no invented time", () => {
  const t = mapLiveTiming({ connected: false, flightStatePatch: {} }, { plannedBlockMin: 45 });
  assert.equal(t.connected, false);
  assert.equal(t.ete, null);
  assert.equal(t.deltaMin, null);
  assert.equal(t.plannedEte, "00:45"); // plan is plan: still shown from OFP
});

test("mapLiveTiming derives planned block from an HH:MM STA string", () => {
  const t = mapLiveTiming(
    { connected: true, flightStatePatch: { gpsEteSeconds: 3000 } },
    { sta: "00:50" }
  );
  assert.equal(t.plannedEte, "00:50");
  assert.equal(t.ete, "00:50");
  assert.equal(t.deltaMin, 0); // 50 min ETE vs 50 planned
});

/* ── gcNm / mapRouteLive ────────────────────────────────────────────── */

test("gcNm equator 10° lon ≈ 600 NM; invalid inputs → null", () => {
  assert.ok(Math.abs(gcNm(0, 0, 0, 10) - 600.4) < 1, `got ${gcNm(0, 0, 0, 10)}`);
  assert.equal(gcNm(0, 0, 0, null), null);
});

test("mapRouteLive projects a mid-cruise aircraft onto the route", () => {
  const points = [
    { ident: "DOH", lat: 0, lon: 0, end: "dep" },
    { ident: "NAT1", lat: 0, lon: 10, end: null },
    { ident: "LHR", lat: 0, lon: 20, end: "arr" },
  ];
  const r = mapRouteLive(points, { latitude: 0, longitude: 15 });
  assert.equal(r.connected, true);
  assert.equal(r.progressIndex, 1); // on segment 2 → last passed index 1
  assert.deepEqual(r.passedIdents, ["DOH"]);
  assert.ok(Math.abs(r.remainingNm - 300) < 5, `got ${r.remainingNm}`);
  assert.ok(Math.abs(r.totalNm - 1201) < 5, `got ${r.totalNm}`);
});

test("mapRouteLive without a live position stays disconnected", () => {
  const r = mapRouteLive([{ ident: "A", lat: 0, lon: 0 }], null);
  assert.equal(r.connected, false);
  assert.equal(r.xy, null);
  assert.equal(r.progressIndex, null);
  assert.deepEqual(r.passedIdents, []);
});

/* ── mapApplyTargets ────────────────────────────────────────────────── */

test("mapApplyTargets camelCase best strategy → step-climb line + save summary", () => {
  const t = mapApplyTargets(
    {
      bestStrategy: {
        flightLevel: 380,
        mach: 0.84,
        costIndex: 3,
        deltaFuelKg: 120,
        deltaCruiseTimeMin: -4,
        allowed: true,
      },
      currentStrategy: { flightLevel: 350 },
      recommendation: "Step climb available",
      interpreted: { reasons: ["better altitude"], warnings: [] },
    },
    {}
  );
  assert.equal(t.applyable, true);
  assert.equal(t.line, "Step climb to FL380 · M0.840 · CI3");
  assert.equal(t.summary, "save 120 kg fuel · 4 min earlier");
  assert.deepEqual(t.targets, { flightLevel: 380, mach: 0.84, costIndex: 3 });
  assert.equal(t.recommendation, "Step climb available");
  assert.deepEqual(t.reasons, ["better altitude"]);
});

test("mapApplyTargets snake_case (Pydantic by_alias output) + not-allowed → not applyable", () => {
  const t = mapApplyTargets(
    {
      best_strategy: {
        flight_level: 370,
        delta_fuel_kg: -50,
        delta_cruise_time_min: 6,
        allowed: false,
      },
    },
    { liveFlightLevel: 350 }
  );
  assert.equal(t.applyable, false); // rejected strategy: no dead Apply button
  assert.equal(t.line, "Step climb to FL370");
  assert.equal(t.summary, "50 kg fuel cost · 6 min later");
  assert.equal(t.targets.flightLevel, 370);
});

test("mapApplyTargets descends when target FL is below current", () => {
  const t = mapApplyTargets(
    { bestStrategy: { flightLevel: 310, allowed: true } },
    { liveFlightLevel: 350 }
  );
  assert.equal(t.applyable, true);
  assert.equal(t.line, "Descend to FL310");
});

test("mapApplyTargets with no best strategy is not applyable and null-safe", () => {
  const t = mapApplyTargets(null);
  assert.equal(t.applyable, false);
  assert.equal(t.line, null);
  assert.equal(t.targets, null);
});

/* ── buildLiveOptimizeRequest ───────────────────────────────────────── */

test("buildLiveOptimizeRequest returns null when the sim is not connected", () => {
  assert.equal(buildLiveOptimizeRequest(null), null);
  assert.equal(buildLiveOptimizeRequest({ connected: false, flightStatePatch: {} }), null);
});

test("buildLiveOptimizeRequest sources every flight-state field from live telemetry", () => {
  const body = buildLiveOptimizeRequest(
    {
      connected: true,
      flightStatePatch: {
        aircraft: "B77W",
        aircraftConfig: "boeing_777_300er",
        altitudeFt: 36000,
        grossWeightKg: 240000,
        mach: 0.84,
        currentCostIndex: 60,
        remainingDistanceNm: 1800,
        windComponentKt: -22,
        isaDeviationC: 4,
        fuelRemainingKg: 51000,
        fuelFlowKgH: 7100,
        fuelFlowSource: "simconnect",
        groundSpeedKt: 470,
      },
      rawSummary: {},
    },
    { flight: { departure_icao: "OTHH", arrival_icao: "EGLL", flight_number: "QR3" } }
  );

  assert.equal(body.action, "NORMAL_RECALC");
  assert.equal(body.aircraftConfig, "boeing_777_300er");
  assert.equal(body.flightState.altitudeFt, 36000);
  assert.equal(body.flightState.fuelFlowKgH, 7100);
  assert.equal(body.flightState.fuelFlowSource, "simconnect");
  assert.equal(body.flightState.windComponentKt, -22);
  assert.equal(body.flightContext.origin, "OTHH");
  assert.equal(body.flightContext.destination, "EGLL");
  assert.equal(body.flightContext.flightNumber, "QR3");
  // Without an OFP the plan context stays null — never modelled.
  assert.equal(body.flightState.routeDistanceNm, null);
  assert.equal(body.flightContext.plannedBlockTimeMin, null);
});

test("buildLiveOptimizeRequest takes plan context from the OFP when present", () => {
  const telemetry = { connected: true, flightStatePatch: { altitudeFt: 35000 }, rawSummary: {} };

  const flatOfp = buildLiveOptimizeRequest(telemetry, {
    ofpData: { route_distance_nm: 3012, ete_min: 415 },
  });
  assert.equal(flatOfp.flightState.routeDistanceNm, 3012);
  assert.equal(flatOfp.flightContext.plannedBlockTimeMin, 415);

  const rawOfp = buildLiveOptimizeRequest(telemetry, {
    ofpData: { general: { route_distance: "3,012" } },
  });
  assert.equal(rawOfp.flightState.routeDistanceNm, 3012);
});

test("buildLiveOptimizeRequest never invents a fuel flow when SimConnect has none", () => {
  const body = buildLiveOptimizeRequest({
    connected: true,
    flightStatePatch: { altitudeFt: 35000, mach: 0.84 },
    rawSummary: {},
  });
  assert.equal(body.flightState.fuelFlowKgH, null);
  assert.equal(body.flightState.fuelFlowSource, null);
});

/* ── formatApiError (FastAPI envelopes must never render [object Object]) ── */

test("formatApiError renders a FastAPI 422 validation array as readable text", () => {
  const payload = {
    detail: [
      { type: "missing", loc: ["body", "action"], msg: "Field required" },
      { type: "missing", loc: ["body", "flightState"], msg: "Field required" },
    ],
  };
  const out = formatApiError(payload, 422);
  assert.ok(!out.includes("[object Object]"), "must never leak [object Object] into the UI");
  assert.match(out, /action: Field required/);
  assert.match(out, /flightState: Field required/);
});

test("formatApiError passes an HTTPException string detail through", () => {
  assert.equal(formatApiError({ detail: "Optimizer engine offline" }, 503), "Optimizer engine offline");
});

test("formatApiError falls back cleanly for an empty or odd body", () => {
  assert.equal(formatApiError({}, 500), "Optimizer unavailable (HTTP 500).");
  assert.equal(formatApiError(null, 500), "Optimizer unavailable (HTTP 500).");
  assert.ok(!formatApiError({ detail: [{}] }, 422).includes("[object Object]"));
});
