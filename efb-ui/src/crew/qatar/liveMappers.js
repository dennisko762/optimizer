/**
 * liveMappers — pure view-model mappers for the M3 SimConnect live layer.
 *
 * No React, no DOM, no fetch. Input: the raw JSON returned by
 * GET /api/simconnect/telemetry (see data_fetcher/sim/simconnect_routes.py)
 * plus the OFP/flightplan view already built by qatarMappers. Output: the
 * display structures the Qatar screens render for LIVE values:
 *
 * - mapSimStatus      → SIM CONNECTED / DISCONNECTED header chip state
 * - mapLiveStrip      → FL / speed / fuel / fuel-flow / wind live row (nulls
 *                       preserved — the UI shows "—" and never falls back to
 *                       static values)
 * - mapLiveTiming     → ETE/ETA live vs plan (planned vs flown deltas)
 * - mapFuelDeviation  → planned vs actual fuel deviation cell
 * - mapRouteLive      → aircraft position + passed-waypoint set for the
 *                       route overlay (great-circle progress along the plan)
 * - mapApplyTargets   → optimizer best-strategy → sim apply targets
 *                       (flight level / mach) + the "save Xkg" summary line
 *
 * Convention: every value that could not be derived from the source data is
 * null — the UI renders "—". Live data is only live: there is no static
 * fallback anywhere in this module (AGENTS.md rule).
 */

import { parseNumber } from "./qatarMappers.js";

/* ── Sim status ─────────────────────────────────────────────────────── */

/**
 * Map the telemetry envelope into the header sim-status chip.
 * status: "connected" | "disconnected" | "unknown" (server unreachable).
 */
export function mapSimStatus(telemetry, { reachable = true } = {}) {
  if (!reachable) {
    return {
      status: "unknown",
      label: "NO SERVER",
      sub: "EFB server not reachable",
    };
  }
  if (telemetry?.connected && telemetry.flightStatePatch) {
    const age = telemetry.dataAgeMs ?? null;
    return {
      status: "connected",
      label: "SIM CONNECTED",
      sub: age != null ? `data ${Math.max(0, Math.round(age / 1000))}s old` : "live",
    };
  }
  const error =
    telemetry?.lastError ||
    (Array.isArray(telemetry?.warnings) ? telemetry.warnings[0] : null) ||
    null;
  return {
    status: "disconnected",
    label: "SIM DISCONNECTED",
    sub: error || "Start MSFS to go live",
  };
}

/* ── Live strip (FL / speed / fuel / fuel flow / wind) ──────────────── */

/**
 * Map a telemetry response + optional planned values into the live strip
 * shown on the Flightplan screen. All values come from SimConnect only;
 * null stays null — no static fallback.
 */
export function mapLiveStrip(telemetry, planned = {}) {
  const t = telemetry;
  const p = t?.connected && t.flightStatePatch ? t.flightStatePatch : {};

  const live = {
    flightLevel:
      p.flightLevel ?? (p.altitudeFt != null ? Math.round(p.altitudeFt / 100) : null),
    altitudeFt: p.altitudeFt ?? null,
    mach: p.mach ?? null,
    groundSpeedKt: p.groundSpeedKt ?? null,
    windComponentKt: p.windComponentKt ?? null,
    fuelRemainingKg: p.fuelRemainingKg ?? null,
    fuelFlowKgH: p.fuelFlowKgH ?? null,
    fuelFlowSource: p.fuelFlowSource ?? null,
    onGround: p.onGround ?? null,
    dataAgeMs: t?.dataAgeMs ?? null,
  };

  // Planned values come from the OFP (M2), never from a static model here.
  const plannedFuelT = planned.fuelLandingT ?? null;
  const plannedAlt = planned.cruiseAlt ?? null;

  live.deviation = mapFuelDeviation({
    actualKg: live.fuelRemainingKg,
    plannedKg: plannedFuelT != null ? plannedFuelT * 1000 : null,
  });
  live.altDeviation = mapAltDeviation({
    actualFl: live.flightLevel,
    plannedFl: plannedAlt,
  });

  return live;
}

/**
 * Fuel deviation: actual remaining vs planned landing fuel (kg, + over /
 * − under). Null when either side is missing — UI shows "—", never 0.
 */
export function mapFuelDeviation({ actualKg, plannedKg }) {
  const a = parseNumber(actualKg);
  const b = parseNumber(plannedKg);
  if (a == null || b == null) return null;
  return {
    kg: Math.round(a - b),
    t: Number(((a - b) / 1000).toFixed(1)),
  };
}

/**
 * Altitude deviation: actual FL vs planned cruise FL (±FL units).
 */
export function mapAltDeviation({ actualFl, plannedFl }) {
  const a = parseNumber(actualFl);
  const b = parseNumber(plannedFl);
  if (a == null || b == null) return null;
  return Math.round(a - b);
}

/* ── Live timing (ETE/ETA vs plan) ──────────────────────────────────── */

function pad2(n) {
  return String(Math.max(0, Math.floor(n))).padStart(2, "0");
}

/**
 * Map live GPS ETE/ETA (seconds) against the planned block time from the
 * OFP. Returns HH:MM strings + the delta in minutes (flown − planned).
 * Anything missing → null (UI shows "—"), never an invented time.
 */
export function mapLiveTiming(telemetry, { plannedBlockMin = null, sta = null } = {}) {
  const t = telemetry;
  const p = t?.connected && t.flightStatePatch ? t.flightStatePatch : {};
  const patch = p;

  const gpsEte =
    patch.gpsEteSeconds != null && patch.gpsEteSeconds > 0 ? patch.gpsEteSeconds : null;
  const remainingNm =
    patch.remainingDistanceNm != null && patch.remainingDistanceNm > 0
      ? patch.remainingDistanceNm
      : null;
  const gsKt =
    patch.groundSpeedKt != null && patch.groundSpeedKt > 0 ? patch.groundSpeedKt : null;

  // Fallback: derive ETE from live remaining distance + ground speed when
  // GPS has no flightplan active.
  let eteSec = gpsEte;
  let eteSource = eteSec != null ? "GPS" : null;
  if (eteSec == null && remainingNm != null && gsKt != null) {
    eteSec = (remainingNm / gsKt) * 3600;
    eteSource = "LIVE_GS";
  }

  let planned = parseNumber(plannedBlockMin);
  if (planned == null && sta != null && typeof sta === "string") {
    const m = String(sta).trim().match(/^(\d{1,2}):(\d{2})$/);
    if (m) planned = Number(m[1]) * 60 + Number(m[2]);
  }

  const eteMin = eteSec != null ? eteSec / 60 : null;
  const deltaMin =
    eteMin != null && planned != null ? Math.round(eteMin - planned) : null;

  return {
    connected: Boolean(t?.connected),
    ete: eteMin != null ? `${pad2(eteMin / 60)}:${pad2(eteMin % 60)}` : null,
    eteMinutes: eteMin != null ? Math.round(eteMin) : null,
    eteSource,
    plannedEte: planned != null ? `${pad2(planned / 60)}:${pad2(planned % 60)}` : null,
    deltaMin,
    remainingNm,
    groundSpeedKt: gsKt,
    fuelFlowKgH: patch.fuelFlowKgH ?? null,
  };
}

/* ── Route live overlay ─────────────────────────────────────────────── */

/**
 * Great-circle distance in NM (mirrors qatarMappers' projection world).
 */
export function gcNm(lat1, lon1, lat2, lon2) {
  const a = [lat1, lon1, lat2, lon2].map(parseNumber);
  if (a.some((v) => v == null)) return null;
  const R = 3440.065;
  const r1 = (lat1 * Math.PI) / 180;
  const r2 = (lat2 * Math.PI) / 180;
  const dlat = ((lat2 - lat1) * Math.PI) / 180;
  const dlon = ((lon2 - lon1) * Math.PI) / 180;
  const h =
    Math.sin(dlat / 2) ** 2 + Math.cos(r1) * Math.cos(r2) * Math.sin(dlon / 2) ** 2;
  return R * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

/**
 * Project the live aircraft position onto the planned route points and
 * derive: progress index (last passed waypoint), remaining NM, and the
 * map coordinates for the aircraft symbol.
 *
 * routePoints: [{ident, lat, lon, end}] — same shape as qatarMappers
 * mapRouteView(...).points. live: {latitude, longitude} from telemetry.
 */
export function mapRouteLive(routePoints, live) {
  const pts = Array.isArray(routePoints) ? routePoints.filter((p) => p) : [];
  if (!live || live.latitude == null || live.longitude == null || pts.length === 0) {
    return { connected: false, xy: null, progressIndex: null, remainingNm: null, passedIdents: [] };
  }

  // Segment walk: find the segment the aircraft is nearest to and the
  // accumulated distance along the route to that segment's start.
  let best = { index: 0, along: 0, perp: Infinity, acc: 0 };
  let acc = 0;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const a = pts[i];
    const b = pts[i + 1];
    const dab = gcNm(a.lat, a.lon, b.lat, b.lon) ?? 0;
    const dabA = gcNm(a.lat, a.lon, live.latitude, live.longitude);
    const dabB = gcNm(b.lat, b.lon, live.latitude, live.longitude);
    // Cross-track approximation: |dAB - (dAP + dBP)| measures how far off
    // the segment the aircraft is (0 when on it).
    const perp = Math.abs(dabA + dabB - dab);
    if (perp < best.perp) {
      best = { index: i, along: (dabA / Math.max(dab, 1e-9)) * dab, perp, acc };
    }
    acc += dab;
  }

  const progressIndex = Math.min(best.index, pts.length - 1);
  const totalNm = acc;
  const doneNm = best.acc + Math.min(best.along, totalNm - best.acc);
  const remainingNm = Math.max(0, totalNm - doneNm);

  const passedIdents = pts
    .slice(0, progressIndex)
    .map((p) => p.ident)
    .filter(Boolean);

  return {
    connected: true,
    progressIndex,
    remainingNm: remainingNm != null ? Math.round(remainingNm) : null,
    totalNm: Math.round(totalNm),
    passedIdents,
    // The symbol is drawn at the live lat/lon by the caller (projectMap).
  };
}

/* ── Optimizer recommendation → sim apply targets ───────────────────── */

/**
 * Build an /api/optimize request body from LIVE telemetry + the selected
 * flight. Everything is sourced from the running sim (no static fallback);
 * when the telemetry is not connected (or the patch is empty) the function
 * returns null — the UI then does not fire a recommendation, rather than
 * optimizing against invented values.
 *
 * The returned shape mirrors the CI Optimizer panel's request (App.jsx
 * buildOptimizeRequest) but trimmed to the NORMAL_RECALC action, since that is
 * the only thing meaningful to re-run mid-flight from live state.
 */
export function buildLiveOptimizeRequest(telemetry, { flight = null, ofpData = null } = {}) {
  const t = telemetry;
  const p = t?.connected && t.flightStatePatch ? t.flightStatePatch : null;
  if (!p) return null;
  const rs = t.rawSummary || {};

  const num = (v) => (v == null ? null : parseNumber(v));

  // Plan context from the OFP (M2) — never modelled here: absent fields stay
  // null so the optimizer works from live state only.
  const ofp = ofpData || {};
  const routeDistanceNm =
    num(ofp.route_distance_nm) ?? num(ofp.general?.route_distance) ?? null;
  const plannedBlockTimeMin = num(ofp.ete_min) ?? null;

  return {
    action: "NORMAL_RECALC",
    aircraftConfig: p.aircraftConfig || rs.aircraft_config || null,
    flightState: {
      aircraft: p.aircraft || rs.aircraft_title || null,
      engineVariant: null,
      aircraftRegistration: null,
      altitudeFt: num(p.altitudeFt ?? rs.altitude_ft),
      grossWeightKg: num(p.grossWeightKg ?? rs.gross_weight_kg),
      mach: num(p.mach ?? rs.mach),
      currentCostIndex: num(p.currentCostIndex ?? rs.cost_index),
      remainingDistanceNm: num(p.remainingDistanceNm ?? rs.gps_remaining_distance_nm),
      routeDistanceNm,
      windComponentKt: num(p.windComponentKt ?? rs.wind_component_kt),
      isaDeviationC: num(p.isaDeviationC ?? rs.isa_deviation_c),
      fuelRemainingKg: num(p.fuelRemainingKg ?? rs.fuel_remaining_kg),
      fuelFlowKgH: num(p.fuelFlowKgH ?? rs.fuel_flow_kg_h),
      fuelFlowSource: p.fuelFlowSource ?? rs.fuel_flow_source ?? null,
      groundSpeedKt: num(p.groundSpeedKt ?? rs.ground_speed_kt),
      paxCount: null,
    },
    flightContext: {
      origin: flight?.departure_icao || null,
      destination: flight?.arrival_icao || null,
      plannedBlockTimeMin,
      flightNumber: flight?.flight_number || null,
      airline: "Qatar Airways",
      sibtUtc: null,
      sobtUtc: null,
    },
    remainingRouteProfile: null,
    payload: {},
  };
}

/**
 * Map an /api/optimize response (OptimizeResponse shape) into the UI
 * recommendation card: the "what + why" line, the apply targets, and the
 * delta summary. Only the BEST strategy is actionable; everything is
 * null-safe (a rejected best strategy → applyable=false).
 */
export function mapApplyTargets(optimizeResponse, { liveFlightLevel = null } = {}) {
  const raw = optimizeResponse || {};
  const best = raw.bestStrategy || raw.best_strategy || null;
  const current = raw.currentStrategy || raw.current_strategy || null;
  if (!best) {
    return { applyable: false, line: null, targets: null, summary: null };
  }

  const fl = parseNumber(best.flightLevel ?? best.flight_level);
  const mach = parseNumber(best.mach);
  const ci = parseNumber(best.costIndex ?? best.cost_index);

  const deltaFuelKg = parseNumber(best.deltaFuelKg ?? best.delta_fuel_kg);
  const deltaTimeMin = parseNumber(best.deltaCruiseTimeMin ?? best.delta_cruise_time_min);
  const currentFl = parseNumber(current?.flightLevel ?? current?.flight_level) ?? parseNumber(liveFlightLevel);

  // "Step climb to FL380 at XYZ — save 120kg" style line.
  const verb =
    fl != null && currentFl != null && fl > currentFl
      ? "Step climb to"
      : fl != null && currentFl != null && fl < currentFl
        ? "Descend to"
        : "Fly at";
  const parts = [];
  if (fl != null) parts.push(`${verb} FL${Math.round(fl)}`);
  if (mach != null) parts.push(`M${mach.toFixed(3)}`);
  if (ci != null) parts.push(`CI${Math.round(ci)}`);
  const line = parts.join(" · ") || (best.label ? String(best.label) : null);

  const summaryBits = [];
  if (deltaFuelKg != null) {
    summaryBits.push(
      deltaFuelKg >= 0 ? `save ${Math.round(deltaFuelKg)} kg fuel` : `${Math.round(-deltaFuelKg)} kg fuel cost`
    );
  }
  if (deltaTimeMin != null) {
    summaryBits.push(
      deltaTimeMin <= 0 ? `${Math.round(Math.abs(deltaTimeMin))} min earlier` : `${Math.round(deltaTimeMin)} min later`
    );
  }

  return {
    applyable: Boolean(best.allowed) && (fl != null || mach != null),
    line,
    summary: summaryBits.join(" · ") || null,
    recommendation: raw.recommendation || null,
    targets: {
      flightLevel: fl != null ? Math.round(fl) : null,
      mach: mach != null ? Number(mach.toFixed(3)) : null,
      costIndex: ci != null ? Math.round(ci) : null,
    },
    reasons: Array.isArray(raw.interpreted?.reasons) ? raw.interpreted.reasons : [],
    warnings: Array.isArray(raw.interpreted?.warnings) ? raw.interpreted.warnings : [],
  };
}

/* ── Live fuel cell (Flightplan fuel row DEVIATION) ─────────────────── */

export const LIVE_STRIP_LABELS = {
  flightLevel: "FL",
  mach: "MACH",
  groundSpeedKt: "GS",
  windComponentKt: "WIND",
  fuelRemainingKg: "FUEL",
  fuelFlowKgH: "FLOW",
};
