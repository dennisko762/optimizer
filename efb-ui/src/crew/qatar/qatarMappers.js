/**
 * qatarMappers — pure view-model mappers for the Qatar "QR SmartOps" UI.
 *
 * No React, no DOM. Takes raw data (SimBrief OFP payloads, flight objects,
 * notification payloads) and produces the display structures the Qatar
 * screens render: flight hero, fuel row, waypoint table, route map data,
 * EDTO risk/NAT content and Crew Desk inbox messages.
 *
 * Convention: every value that could not be derived from the source data is
 * null — the UI renders "—" for null. Derived (modelled) values are always
 * flagged via a `derived` flag on the containing view so the UI can mark
 * them as "SIM PLANNED" instead of presenting them as real OFP numbers.
 */

const EARTH_RADIUS_NM = 3440.065;

/* ── number / string helpers ───────────────────────────────────────── */

export function parseNumber(value) {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const n = parseFloat(String(value).replace(/[,\s]/g, ""));
  return Number.isFinite(n) ? n : null;
}

export function parsePm(value) {
  if (value == null) return null;
  const text = String(value).trim().toUpperCase();
  if (!text) return null;
  if (text.startsWith("P")) return parseNumber(text.slice(1));
  if (text.startsWith("M")) {
    const n = parseNumber(text.slice(1));
    return n == null ? null : -n;
  }
  return parseNumber(text);
}

export function fmt(value, { digits = 1, suffix = "" } = {}) {
  if (value == null) return null;
  const n = parseNumber(value);
  if (n == null) return null;
  return `${Number(n.toFixed(digits))}${suffix}`;
}

export function kgToT(kg, digits = 1) {
  if (kg == null) return null;
  const n = parseNumber(kg);
  return n == null ? null : Number((n / 1000).toFixed(digits));
}

export function hhmm(value) {
  if (value == null) return null;
  if (typeof value === "string" && /^\d{1,2}:\d{2}$/.test(value.trim())) {
    return value.trim().padStart(5, "0");
  }
  const n = parseNumber(value);
  if (n == null) return null;
  if (n > 86400) {
    // Unix timestamp (seconds).
    const d = new Date(n * 1000);
    const h = String(d.getUTCHours()).padStart(2, "0");
    const m = String(d.getUTCMinutes()).padStart(2, "0");
    return `${h}:${m}`;
  }
  // Seconds since midnight.
  const h = Math.floor(n / 3600);
  const m = Math.floor((n % 3600) / 60);
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

export function durHhmm(minutes) {
  const n = parseNumber(minutes);
  if (n == null) return null;
  const h = Math.floor(n / 60);
  const m = Math.round(n % 60);
  return `${h}:${String(m).padStart(2, "0")}`;
}

function toMinutes(value) {
  if (value == null) return null;
  if (typeof value === "string" && value.includes(":")) {
    const parts = value.split(":").map(parseNumber);
    if (parts.some((p) => p == null)) return null;
    if (parts.length === 3) return parts[0] * 60 + parts[1] + parts[2] / 60;
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return null;
  }
  const n = parseNumber(value);
  if (n == null) return null;
  // A bare small number is hours; a large one is minutes.
  return n < 24 ? n * 60 : n;
}

/* ── geography ──────────────────────────────────────────────────────── */

const ICAO_LATLON = {
  DOH: [25.2731, 51.1671],
  OTHH: [25.2731, 51.1671],
  LHR: [51.47, -0.4543],
  EGTT: [51.47, -0.4543],
  LGW: [51.1481, -0.1903],
  SYD: [-33.9391, 151.1755],
  YSYD: [-33.9391, 151.1755],
};

export const ICAO_NAMES = {
  DOH: "Doha",
  OTHH: "Doha",
  LHR: "London Heathrow",
  EGTT: "London Heathrow",
  LGW: "London Gatwick",
  SYD: "Sydney Kingsford Smith",
  YSYD: "Sydney Kingsford Smith",
};

export function icaoLatlon(icao) {
  if (!icao) return null;
  return ICAO_LATLON[String(icao).trim().toUpperCase()] || null;
}

export function haversineNm(lat1, lon1, lat2, lon2) {
  const a = [lat1, lon1, lat2, lon2].map(parseNumber);
  if (a.some((v) => v == null)) return null;
  const r1 = (lat1 * Math.PI) / 180;
  const r2 = (lat2 * Math.PI) / 180;
  const dlat = ((lat2 - lat1) * Math.PI) / 180;
  const dlon = ((lon2 - lon1) * Math.PI) / 180;
  const h =
    Math.sin(dlat / 2) ** 2 + Math.cos(r1) * Math.cos(r2) * Math.sin(dlon / 2) ** 2;
  return EARTH_RADIUS_NM * 2 * Math.asin(Math.min(1, Math.sqrt(h)));
}

export function greatCircleInterpolate(lat1, lon1, lat2, lon2, t) {
  const r1 = (lat1 * Math.PI) / 180;
  const r2 = (lat2 * Math.PI) / 180;
  const l1 = (lon1 * Math.PI) / 180;
  const l2 = (lon2 * Math.PI) / 180;
  const d = 2 * Math.asin(
    Math.sqrt(
      Math.sin((r2 - r1) / 2) ** 2 +
        Math.cos(r1) * Math.cos(r2) * Math.sin((l2 - l1) / 2) ** 2
    )
  );
  if (d < 1e-9) return [lat1, lon1];
  const a = Math.sin((1 - t) * d) / Math.sin(d);
  const b = Math.sin(t * d) / Math.sin(d);
  const x = a * Math.cos(r1) * Math.cos(l1) + b * Math.cos(r2) * Math.cos(l2);
  const y = a * Math.cos(r1) * Math.sin(l1) + b * Math.cos(r2) * Math.sin(l2);
  const z = a * Math.sin(r1) + b * Math.sin(r2);
  const lat = (Math.atan2(z, Math.sqrt(x * x + y * y)) * 180) / Math.PI;
  const lon = (Math.atan2(y, x) * 180) / Math.PI;
  return [lat, lon];
}

/** Equirectangular projection into a 1000x560 viewBox (map units). */
export function projectMap(lat, lon, width = 1000, height = 560) {
  const x = ((lon + 180) / 360) * width;
  const y = ((90 - lat) / 180) * height;
  return { x: Math.round(x * 10) / 10, y: Math.round(y * 10) / 10 };
}

/* ── OFP view: hero + fuel row ──────────────────────────────────────── */

function section(data, key) {
  const v = data?.[key];
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

function firstPresent(obj, ...keys) {
  for (const k of keys) {
    const v = obj?.[k];
    if (v != null && v !== "") return v;
  }
  return null;
}

function firstNum(obj, ...keys) {
  for (const k of keys) {
    const v = firstPresent(obj, k);
    if (v == null) continue;
    const n = parseNumber(v);
    if (n != null) return n;
  }
  return null;
}

function flightNum(v) {
  if (v == null) return null;
  return String(v).trim().toUpperCase() || null;
}

/**
 * Map a SimBrief OFP payload (any known v1/v2 shape) + booking flight into
 * the Qatar flight-plan view: hero block + fuel row + OFP banner facts.
 */
export function mapOfpHero(flight, ofpData) {
  const d = ofpData || {};
  const general = section(d, "general");
  const times = section(d, "times");
  const aircraft = section(d, "aircraft");
  const origin = section(d, "origin");
  const destination = section(d, "destination");
  const alternate = section(d, "alternate");
  const weights = section(d, "weights");
  const fuel = section(d, "fuel");

  const departure = firstPresent(origin, "icao_code", "icao") || flight?.departure_icao || null;
  const arrival =
    firstPresent(destination, "icao_code", "icao") || flight?.arrival_icao || null;
  const alternateIcao = firstPresent(alternate, "icao_code", "icao") || null;

  const flightNumber =
    flightNum(flight?.flight_number) ||
    flightNum(firstPresent(general, "flight_number", "fltno", "flight_no", "flightnum"));
  const airline =
    firstPresent(general, "airline_name", "airline", "name_airline") ||
    "Qatar Airways";
  const aircraftType =
    firstPresent(aircraft, "icaocode", "icao_code", "type", "aircraft") ||
    flight?.aircraft_icao ||
    null;

  const std = hhmm(
    firstPresent(times, "sched_out", "schedout", "est_out", "estout") ||
      firstPresent(general, "sched_out", "est_out")
  );
  const sta = hhmm(
    firstPresent(times, "sched_in", "schedin", "est_in", "estin") ||
      firstPresent(general, "sched_in", "est_in")
  );
  const blockMin = toMinutes(
    firstPresent(times, "est_block", "sched_block", "block_time") ||
      firstPresent(general, "est_block", "block_time")
  );

  const fuelKg = {
    block: firstNum(fuel, "plan_ramp", "block_fuel_kg", "ramp"),
    takeoff: firstNum(fuel, "est_takeoff", "takeoff_fuel_kg", "takeoff"),
    trip: firstNum(fuel, "enroute_burn", "trip_fuel_kg", "enroute", "trip"),
    landing: firstNum(fuel, "est_ldg", "landing_fuel_kg", "ldg_fuel"),
    reserve: firstNum(fuel, "reserve", "reserve_fuel_kg", "total_reserve"),
    taxi: firstNum(fuel, "taxi_fuel", "taxi"),
  };

  const weightsKg = {
    tow: firstNum(weights, "est_tow", "tow_kg"),
    zfw: firstNum(weights, "est_zfw", "zfw_kg"),
    ldw: firstNum(weights, "est_ldw", "ldw_kg", "landing_weight_kg"),
  };

  return {
    flight_number: flightNumber,
    departure: departure ? String(departure).toUpperCase() : null,
    arrival: arrival ? String(arrival).toUpperCase() : null,
    departure_name: departure ? ICAO_NAMES[String(departure).toUpperCase()] || null : null,
    arrival_name: arrival ? ICAO_NAMES[String(arrival).toUpperCase()] || null : null,
    alternate: alternateIcao ? String(alternateIcao).toUpperCase() : null,
    airline,
    aircraft_type: aircraftType,
    date_label: null, // filled by the caller (today) — mappers stay time-free
    std,
    sta,
    block_min: blockMin,
    eet: durHhmm(blockMin),
    // Fuel, tonnes (null when the source had no value).
    fuel: {
      block: kgToT(fuelKg.block),
      takeoff: kgToT(fuelKg.takeoff),
      trip: kgToT(fuelKg.trip),
      landing: kgToT(fuelKg.landing),
      reserve_alt: kgToT(fuelKg.reserve),
      taxi: kgToT(fuelKg.taxi),
      extra: null, // not modelled; UI shows "—"
      deviation: null, // no live SimConnect check yet
    },
    weights: {
      tow: kgToT(weightsKg.tow),
      zfw: kgToT(weightsKg.zfw),
      ldw: kgToT(weightsKg.ldw),
    },
    pax: firstNum(weights, "pax_count", "passenger_count") ?? flight?.passengers ?? null,
    cargo_t: kgToT(firstNum(weights, "cargo_weight", "cargo_kg") ?? flight?.cargo ?? null),
  };
}

/* ── OFP view: waypoint table ───────────────────────────────────────── */

function altToFl(v) {
  const n = parseNumber(v);
  if (n == null) return null;
  if (n > 1000) return Math.round(n / 100);
  return Math.round(n);
}

function windCell(row) {
  const dir = firstNum(row, "wind_dir", "wind_direction", "wdir");
  const spd = firstNum(row, "wind_spd", "wind_speed", "wspd");
  if (dir != null && spd != null) {
    return `${Math.round(dir).toString().padStart(3, "0")}/${Math.round(spd)}`;
  }
  const vec = firstPresent(row, "wind", "wind_vector", "winds");
  if (vec != null) {
    const text = String(vec).trim().toUpperCase().replace("KT", "");
    if (text.includes("/")) {
      const [d, s] = text.split("/", 2).map((v) => parseNumber(v.trim()));
      if (d != null && s != null) {
        return `${Math.round(d).toString().padStart(3, "0")}/${Math.round(s)}`;
      }
    }
    const compact = text.replace(/\D/g, "");
    if (compact.length === 5 || compact.length === 6) {
      return `${compact.slice(0, 3)}/${compact.slice(3)}`;
    }
  }
  const comp = parsePm(
    firstPresent(row, "wind_component", "wind_comp", "windcomp", "wcomp", "wc")
  );
  if (comp == null) return null;
  return `${comp >= 0 ? "TW" : "HW"} ${Math.abs(comp).toFixed(0)}`;
}

/**
 * Build the flight-plan waypoint table rows from a raw SimBrief OFP payload.
 *
 * Columns: wpt, awy, fir, legNm, remNm, ete, legEte, alt, wind, burn, planFuel,
 * ato, act. ATO/ACT are live columns — always null until SimConnect provides
 * actuals. Missing navlog rows leave the leg cells null.
 */
export function mapOfpWaypoints(ofpData) {
  const d = ofpData || {};
  let rows = [];
  const navlog = d.navlog;
  if (Array.isArray(navlog)) rows = navlog;
  else if (navlog && typeof navlog === "object") {
    for (const key of ["fix", "fixes", "waypoints", "waypoint"]) {
      if (Array.isArray(navlog[key])) {
        rows = navlog[key];
        break;
      }
    }
  } else {
    for (const key of ["fix", "fixes", "waypoints", "navlog"]) {
      if (Array.isArray(d[key])) {
        rows = d[key];
        break;
      }
    }
  }

  const parsed = [];
  for (const r of rows) {
    if (!r || typeof r !== "object") continue;
    const ident = firstPresent(r, "ident", "id", "fix", "name", "waypoint", "wp", "via");
    const fl = altToFl(
      firstPresent(r, "fl", "flight_level") ||
        firstNum(r, "altitude_ft", "altitude_feet", "altitude", "alt")
    );
    parsed.push({
      ident: ident ? String(ident).toUpperCase() : null,
      name: firstPresent(r, "name_city", "city", "airport") || null,
      airway: firstPresent(r, "airway", "awy", "route") || null,
      fir: firstPresent(r, "fir", "flight_information_region") || null,
      legNm: firstNum(r, "leg_nm", "leg_dist", "distance", "dist", "dist_nm"),
      remNm: firstNum(r, "rem_nm", "remaining_nm", "dist_remaining"),
      ete: firstPresent(r, "ete", "elapsed_time"),
      legEte: firstPresent(r, "leg_ete", "leg_time"),
      alt: fl,
      wind: windCell(r),
      burn: firstNum(r, "burn", "fuel_burn", "burn_kg"),
      planFuel: firstNum(r, "pln_fuel", "planned_fuel", "fuel"),
    });
  }

  // Fill REM NM from the last known remaining distance when a row omits it.
  let lastRem = null;
  const hasRem = parsed.some((p) => p.remNm != null);
  if (hasRem) {
    for (const p of parsed) {
      if (p.remNm != null) lastRem = p.remNm;
      else p.remNm = lastRem;
    }
  }
  return parsed.length ? parsed : null;
}

/**
 * Sim-planned waypoint rows for sessions without a linked OFP: interpolates
 * great-circle legs between the entered ICAOs using a light-weight fuel
 * model. Always flagged derived=true so the UI shows "SIM PLANNED".
 */
export function modelSimPlan(flight) {
  const dep = flight?.departure_icao ? String(flight.departure_icao).toUpperCase() : null;
  const arr = flight?.arrival_icao ? String(flight.arrival_icao).toUpperCase() : null;
  const depPos = dep ? icaoLatlon(dep) : null;
  const arrPos = arr ? icaoLatlon(arr) : null;
  if (!depPos || !arrPos) return null;

  const greatCircle = haversineNm(depPos[0], depPos[1], arrPos[0], arrPos[1]);
  const dist = greatCircle == null ? null : Math.round(greatCircle * 1.12);
  if (dist == null || dist < 50) return null;

  const legs = 12;
  const flPlan = [
    340, 350, 360, 360, 360, 360, 360, 350, 350, 340, 260, 140,
  ];
  const groundSpeedKt = 470;
  const blockHours = dist / groundSpeedKt;
  const avgBurnT = 4.6;
  const totalTripT = Math.round(blockHours * avgBurnT * 10) / 10;
  const reserveT = Math.round(Math.max(30 / 60 * avgBurnT, 5) * 10) / 10;

  const ldwT = 30; // modelled landing weight, t
  const takeoffT = Math.round((ldwT + totalTripT) * 10) / 10;
  const blockT = Math.round((takeoffT + 1.1) * 10) / 10; // taxi fuel

  const depName = ICAO_NAMES[dep];
  const arrName = ICAO_NAMES[arr];
  const segDist = dist / (legs + 1);
  const perLegBurn = totalTripT / (legs + 1);

  let remaining = dist;
  let eteSec = 0;
  const advance = (legH) => {
    eteSec += legH * 3600;
    return `${Math.floor(eteSec / 3600)}:${String(Math.round((eteSec % 3600) / 60) % 60).padStart(2, "0")}`;
  };

  const rows = [];
  const push = ({ ident, name, fl, legNm, airway, planFuel, burn }) => {
    const legH = legNm == null ? null : legNm / groundSpeedKt;
    const ete = legH == null ? null : advance(legH);
    remaining = legNm == null ? remaining : Math.max(0, Math.round(remaining - legNm));
    rows.push({
      ident,
      name,
      airway: airway || null,
      fir: null,
      legNm: legNm == null ? null : Math.round(legNm),
      remNm: remaining,
      ete,
      legEte: legH == null ? null : `${Math.floor(legH)}:${String(Math.round(legH * 60) % 60).padStart(2, "0")}`,
      alt: fl,
      wind: null,
      burn: burn == null ? null : Math.round(burn * 10) / 10,
      planFuel: planFuel == null ? null : Math.round(planFuel * 10) / 10,
    });
  };

  push({ ident: dep, name: depName, fl: 340, legNm: null, airway: null, planFuel: blockT, burn: null });
  for (let i = 1; i <= legs; i++) {
    const t = i / (legs + 1);
    const isArr = i === legs;
    push({
      ident: isArr ? arr : null,
      name: isArr ? arrName : null,
      fl: flPlan[Math.min(i, flPlan.length - 1)],
      legNm: isArr ? remaining : Math.round(segDist),
      airway: null,
      planFuel: isArr ? ldwT : Math.round((ldwT + totalTripT * (1 - t)) * 10) / 10,
      burn: Math.round(perLegBurn * 10) / 10,
    });
  }

  return {
    derived: true,
    distance_nm: dist,
    block_min: Math.round(blockHours * 60),
    fuel: {
      block: blockT,
      takeoff: takeoffT,
      trip: totalTripT,
      landing: ldwT,
      reserve_alt: reserveT,
      taxi: 1.1,
      extra: null,
      deviation: null,
    },
    weights: {
      tow: Math.round(takeoffT * 10) / 10,
      zfw: Math.round((ldwT + totalTripT - reserveT) * 10) / 10,
      ldw: ldwT,
    },
    rows,
  };
}

/* ── Route map view ─────────────────────────────────────────────────── */

export function mapRouteView(flight, ofpData) {
  const hero = mapOfpHero(flight, ofpData);
  const wps = (ofpData?.route_waypoints ||
    (Array.isArray(ofpData?.navlog) ? ofpData.navlog : []))
    .map((w) => ({
      ident: w.ident || null,
      lat: w.lat ?? null,
      lon: w.lon ?? null,
      fl: w.flight_level ?? null,
    }))
    .filter((w) => w.lat != null && w.lon != null);

  const depPos = hero.departure ? icaoLatlon(hero.departure) : null;
  const arrPos = hero.arrival ? icaoLatlon(hero.arrival) : null;

  const points = [];
  if (depPos) points.push({ ident: hero.departure, name: hero.departure_name, lat: depPos[0], lon: depPos[1], fl: null, end: "dep" });
  for (const w of wps) {
    points.push({ ident: w.ident, name: null, lat: w.lat, lon: w.lon, fl: w.fl, end: null });
  }
  if (arrPos) points.push({ ident: hero.arrival, name: hero.arrival_name, lat: arrPos[0], lon: arrPos[1], fl: null, end: "arr" });

  const distanceNm =
    parseNumber(section(ofpData, "general").route_distance) ??
    modelSimPlan(flight)?.distance_nm ??
    null;

  return {
    hero,
    points,
    route_string: flight?.route || null,
    distance_nm: distanceNm,
    block_label: hero.eet ? `Block ${hero.eet}` : null,
  };
}

/* ── EDTO / risk / NAT view (static briefing snapshot) ──────────────── */

export function mapEdtoView(flight, ofpData) {
  const hero = mapOfpHero(flight, ofpData);
  const plan = ofpData?.route_waypoints?.length ? null : modelSimPlan(flight);
  const distance = plan?.distance_nm ?? null;
  return {
    static_briefing: true,
    generated: "15:59Z",
    hero,
    distance_nm: distance,
    block_label: hero.eet ? `Block ${hero.eet}` : null,
    route_string: flight?.route || null,
    official_notices: [
      {
        region: "PERSIAN GULF & GULF OF OMAN",
        status: "ACTIVE",
        updated: "15:59Z",
        current: true,
      },
    ],
    operator_risks: [
      { country: "UAE", level: "LEVEL 3 CAUTION", updated: "15:59Z", current: true },
      { country: "Oman", level: "LEVEL 3 CAUTION", updated: "15:59Z", current: true },
      { country: "India", level: "LEVEL 3 CAUTION", updated: "15:59Z", current: true },
      { country: "Indonesia", level: "LEVEL 3 CAUTION", updated: "15:59Z", current: true },
    ],
    nat_tracks: [
      {
        name: "NAT A",
        direction: "WESTBOUND",
        valid: "02 OCT 1130-1900Z",
        track: "VENIR 5330/20 51/30 48/40 45/50 RAFTN",
      },
      {
        name: "NAT B",
        direction: "WESTBOUND",
        valid: "02 OCT 1130-1900Z",
        track: "NEBIN 5230/20 50/40 47/50 BOBTU JAROM",
      },
      {
        name: "NAT C",
        direction: "WESTBOUND",
        valid: "02 OCT 1130-1900Z",
        track: "TOBOR 5130/20 49/30 46/40 43/50 JEBBY",
      },
    ],
  };
}

/* ── Crew Desk inbox messages ───────────────────────────────────────── */

const BADGE_COLORS = {
  DISPATCH: "badge--dispatch",
  "A-CDM": "badge--acdm",
  NOTAM: "badge--notam",
  WEATHER: "badge--weather",
  VATSIM: "badge--vatsim",
};

export function badgeClass(kind) {
  const k = String(kind || "").toUpperCase();
  return BADGE_COLORS[k] || "badge--dispatch";
}

/** Map one notification payload (crew_platform /api/crew/notifications). */
export function mapNotification(n) {
  if (!n) return null;
  let kind = "DISPATCH";
  const t = String(n.type || "").toLowerCase();
  if (t.includes("wind") || t.includes("temperature") || t.includes("weather") || t.includes("visibility")) {
    kind = "WEATHER";
  } else if (t.includes("acdm") || t.includes("slot")) {
    kind = "A-CDM";
  } else if (t.includes("notam")) {
    kind = "NOTAM";
  }
  return {
    id: n.id ?? null,
    kind,
    badge_class: badgeClass(kind),
    title: n.summary || `${n.icao || "OPS"} notification`,
    sender: n.provenance || "Qatar Airways Ops",
    preview: `${String(n.icao || "")} ${String(n.summary || "")}`.trim().slice(0, 90),
    timestamp: n.timestamp ?? null,
    body: n.body || n.summary || "",
  };
}

/** Static ops snapshot shown when no live notifications exist yet. */
export function defaultInboxMessages(flight) {
  const fno = flight?.flight_number || "QR815";
  const route = flight
    ? `${flight.departure_icao || "DOH"}/${flight.arrival_icao || "LHR"}`
    : "DOH/LHR";
  return [
    {
      id: "static-dispatch",
      kind: "DISPATCH",
      badge_class: badgeClass("DISPATCH"),
      title: `Dispatch message - ${fno}`,
      sender: "QR Operations",
      preview: `DISPATCH MSG FLIGHT ${fno} ${route} NO SIG WX / ALL SYSTEMS NOMINAL…`,
      timestamp: null,
      body: `Dispatch release for ${fno} ${route}. No significant weather. All systems nominal.`,
      static: true,
    },
    {
      id: "static-acdm-1",
      kind: "A-CDM",
      badge_class: badgeClass("A-CDM"),
      title: "A-CDM DOH - TOBT 15:45Z in 10 min",
      sender: "Hamad Airport A-CDM",
      preview: `TOBT REMINDER ${fno} ${route} CURRENT TOBT 15:45Z, EXPECT START UP…`,
      timestamp: null,
      body: `TOBT reminder ${fno}. Current TOBT 15:45Z. Expect start-up within 10 minutes.`,
      static: true,
    },
    {
      id: "static-notam",
      kind: "NOTAM",
      badge_class: badgeClass("NOTAM"),
      title: `NOTAM briefing - ${fno} • 5 stations`,
      sender: "Qatar Airways Ops",
      preview: `NOTAM BRIEFING ${fno} ${route} 5 STATIONS AFFECTED. SEE DETAILS…`,
      timestamp: null,
      body: `NOTAM briefing ${fno}: 5 stations affected. See details.`,
      static: true,
    },
    {
      id: "static-weather",
      kind: "WEATHER",
      badge_class: badgeClass("WEATHER"),
      title: `WX briefing - ${fno}`,
      sender: "Qatar Airways Meteo",
      preview: `WX BRIEFING ${fno} ${route} ENROUTE AND DESTINATION WEATHER…`,
      timestamp: null,
      body: `Weather briefing ${fno}: enroute and destination weather attached.`,
      static: true,
    },
    {
      id: "static-acdm-2",
      kind: "A-CDM",
      badge_class: badgeClass("A-CDM"),
      title: "A-CDM DOH - TOBT confirmed",
      sender: "Hamad Airport A-CDM",
      preview: `CDM STATUS ${fno} ${route} TOBT 15:45Z CONFIRMED, TSAT 15:30Z…`,
      timestamp: null,
      body: `CDM status ${fno}: TOBT 15:45Z confirmed, TSAT 15:30Z.`,
      static: true,
    },
    {
      id: "static-vatsim",
      kind: "VATSIM",
      badge_class: badgeClass("VATSIM"),
      title: `Connected - ${fno}`,
      sender: "Qatar Airways Ops Network",
      preview: `CONNECTED TO QR OPS NETWORK ATC AND COMPANY MESSAGES AVAILABLE F…`,
      timestamp: null,
      body: "Connected to QR Ops network. ATC and company messages available.",
      static: true,
    },
  ];
}
