import test from "node:test";
import assert from "node:assert/strict";
import {
  parseNumber,
  parsePm,
  kgToT,
  hhmm,
  durHhmm,
  haversineNm,
  projectMap,
  greatCircleInterpolate,
  mapOfpHero,
  mapOfpWaypoints,
  modelSimPlan,
  mapRouteView,
  mapEdtoView,
  mapNotification,
  badgeClass,
  defaultInboxMessages,
} from "./qatarMappers.js";

/* ── helpers ────────────────────────────────────────────────────────── */

test("parseNumber handles numbers, comma strings, P/M-free text", () => {
  assert.equal(parseNumber(213.7), 213.7);
  assert.equal(parseNumber("1 234.5"), 1234.5);
  assert.equal(parseNumber("1,234.5"), 1234.5);
  assert.equal(parseNumber("abc"), null);
  assert.equal(parseNumber(null), null);
});

test("parsePm handles P/M notation and plain signed values", () => {
  assert.equal(parsePm("P017"), 17);
  assert.equal(parsePm("M025"), -25);
  assert.equal(parsePm("+3"), 3);
  assert.equal(parsePm("-2"), -2);
  assert.equal(parsePm(null), null);
});

test("kgToT converts kilograms to rounded tonnes", () => {
  assert.equal(kgToT(213700), 213.7);
  assert.equal(kgToT(23300), 23.3);
  assert.equal(kgToT(null), null);
});

test("hhmm handles HH:MM strings and unix seconds", () => {
  assert.equal(hhmm("08:10"), "08:10");
  assert.equal(hhmm("8:05"), "08:05");
  assert.equal(hhmm(1717400000 + 8 * 3600 + 10 * 60 - 1717400000), "08:10"); // 29500 s since midnight
});

test("hhmm handles ISO 8601 and HH:MM:SS strings", () => {
  assert.equal(hhmm("2026-10-05T08:10:00Z"), "08:10");
  assert.equal(hhmm("2026-10-05T08:10:00"), "08:10");
  assert.equal(hhmm("08:10:00"), "08:10");
  assert.equal(hhmm("13:20:00"), "13:20");
});

test("durHhmm formats minutes as H:MM", () => {
  assert.equal(durHhmm(430), "7:10");
  assert.equal(durHhmm(60), "1:00");
  assert.equal(durHhmm(null), null);
});

test("haversineNm DOH→LHR is in the 2700–2950 NM band", () => {
  const d = haversineNm(25.2731, 51.1671, 51.47, -0.4543);
  assert.ok(d > 2700 && d < 2950, `got ${d}`);
});

test("projectMap maps poles/dateline into the viewBox", () => {
  assert.deepEqual(projectMap(90, -180, 100, 100), { x: 0, y: 0 });
  assert.deepEqual(projectMap(-90, 180, 100, 100), { x: 100, y: 100 });
  assert.deepEqual(projectMap(0, 0, 100, 100), { x: 50, y: 50 });
});

test("greatCircleInterpolate endpoints and midpoint sanity", () => {
  const [la1, lo1] = greatCircleInterpolate(25.27, 51.17, 51.47, -0.45, 0);
  assert.ok(Math.abs(la1 - 25.27) < 1e-3);
  assert.ok(Math.abs(lo1 - 51.17) < 1e-3);
  const [la2, lo2] = greatCircleInterpolate(25.27, 51.17, 51.47, -0.45, 1);
  assert.ok(Math.abs(la2 - 51.47) < 1e-3);
  assert.ok(Math.abs(lo2 - -0.45) < 1e-3);
  const [mLat, mLon] = greatCircleInterpolate(25.27, 51.17, 51.47, -0.45, 0.5);
  assert.ok(mLat > 25.27 && mLat < 51.47);
  assert.ok(mLon < 51.17 && mLon > -90); // heading NW toward Europe
});

/* ── OFP hero ───────────────────────────────────────────────────────── */

const SAMPLE_OFP = {
  general: {
    callsign: "QTR815",
    flight_number: "QR815",
    airline_icao: "QTR",
    route_distance: "3052",
    avg_wind_comp: "M025",
    isa_dev: "P002",
    cruise_mach: "0.85",
  },
  times: {
    sched_out: 29400, // 08:10Z seconds since midnight
    sched_in: 48000, // 13:20Z
    est_block: "07:10",
  },
  aircraft: { icaocode: "B777", reg: "A7-BAC" },
  origin: { icao_code: "DOH" },
  destination: { icao_code: "LHR" },
  alternate: { icao_code: "LGW" },
  weights: { est_tow: "212600", est_zfw: "193000", est_ldw: "23300", pax_count: 290, cargo_weight: "3000" },
  fuel: { plan_ramp: "213700", est_takeoff: "212600", enroute_burn: "189400", est_ldg: "23300", reserve: "10300", taxi_fuel: "1100" },
};

test("mapOfpHero maps a SimBrief v2-shaped OFP", () => {
  const hero = mapOfpHero({ flight_number: null, departure_icao: null, arrival_icao: null }, SAMPLE_OFP);
  assert.equal(hero.flight_number, "QR815");
  assert.equal(hero.departure, "DOH");
  assert.equal(hero.arrival, "LHR");
  assert.equal(hero.alternate, "LGW");
  assert.equal(hero.aircraft_type, "B777");
  assert.equal(hero.std, "08:10");
  assert.equal(hero.sta, "13:20");
  assert.equal(hero.eet, "7:10");
  assert.equal(hero.fuel.block, 213.7);
  assert.equal(hero.fuel.takeoff, 212.6);
  assert.equal(hero.fuel.trip, 189.4);
  assert.equal(hero.fuel.landing, 23.3);
  assert.equal(hero.fuel.reserve_alt, 10.3);
  assert.equal(hero.fuel.taxi, 1.1);
  assert.equal(hero.weights.tow, 212.6);
  assert.equal(hero.pax, 290);
});

test("mapOfpHero falls back to booking flight data and leaves gaps null", () => {
  const hero = mapOfpHero(
    { flight_number: "QR100", departure_icao: "syd", arrival_icao: "DOH" },
    null
  );
  assert.equal(hero.flight_number, "QR100");
  assert.equal(hero.departure, "SYD");
  assert.equal(hero.arrival, "DOH");
  assert.equal(hero.departure_name, "Sydney Kingsford Smith");
  assert.equal(hero.std, null);
  assert.equal(hero.fuel.block, null);
  assert.equal(hero.eet, null);
});

test("mapOfpHero accepts v1-shaped keys (flight_no, fltno, est_out)", () => {
  const v1 = {
    general: { fltno: "qr815", est_out: "09:00", est_in: "15:30" },
    times: {},
    origin: { icao: "DOH" },
    destination: { icao: "LHR" },
    fuel: { ramp: 200000 },
  };
  const hero = mapOfpHero({}, v1);
  assert.equal(hero.flight_number, "QR815");
  assert.equal(hero.departure, "DOH");
  assert.equal(hero.std, "09:00");
  assert.equal(hero.sta, "15:30");
  assert.equal(hero.fuel.block, 200);
});

test("mapOfpHero maps the flat EFB flightplan view (tonnes, ISO times)", () => {
  const flat = {
    source: "SimBrief",
    origin: "EDDF",
    origin_name: "Frankfurt am Main",
    destination: "RJAA",
    destination_name: "Tokyo Haneda",
    alternate: "RJTT",
    flight_number: "1234",
    callsign: "TST1234",
    airline_icao: "TST",
    aircraft: "Boeing 777-300ER",
    aircraft_icao: "B77L",
    registration: "D-XXXX",
    route_distance_nm: 7100,
    std_utc: "2026-10-05T13:20:00Z",
    sta_utc: "2026-10-05T22:40:00Z",
    ete_min: 560,
    generated_at: "2026-10-05T09:12:00",
    airac: "2610",
    fuel: { block: 48.5, takeoff: 47.4, trip: 42.1, landing: 5.3, taxi: 1.1, reserve: 9.8, reserve_alt: 12.4 },
    weights: { tow: 212.6, zfw: 193.0, ldw: 65.5 },
    pax_count: 320,
    cargo_kg: 4000,
  };
  const hero = mapOfpHero({}, flat);
  assert.equal(hero.flight_number, "1234");
  assert.equal(hero.departure, "EDDF");
  assert.equal(hero.arrival, "RJAA");
  assert.equal(hero.alternate, "RJTT");
  assert.equal(hero.departure_name, "Frankfurt am Main");
  assert.equal(hero.aircraft_type, "Boeing 777-300ER");
  assert.equal(hero.aircraft_reg, "D-XXXX");
  assert.equal(hero.std, "13:20");
  assert.equal(hero.sta, "22:40");
  assert.equal(hero.eet, "9:20");
  assert.equal(hero.fuel.block, 48.5); // already tonnes — NOT re-divided
  assert.equal(hero.fuel.reserve_alt, 12.4);
  assert.equal(hero.weights.tow, 212.6);
  assert.equal(hero.pax, 320);
});

/* ── waypoint table ─────────────────────────────────────────────────── */

const NAVLOG = [
  { ident: "DOH", name_city: "Doha", fl: "340", wind: "320/25", pln_fuel: "212.6", rem_nm: 3701 },
  { ident: "KADOM", airway: "Y711", fir: "OBBB", distance: 142, fl: "340", wind: "318/28", burn: 2800, pln_fuel: 209.8, rem_nm: 3559 },
  { ident: "SOGBI", airway: "Y711", fir: "OBBB", distance: 198, fl: "340", wind: "315/31", burn: 3900, pln_fuel: 205.9, rem_nm: 3361 },
  { ident: "PENIL", airway: "Y711", fir: "OIIX", distance: 247, fl: "350", wind: "312/35", burn: 4800, pln_fuel: 201.1, rem_nm: 3114 },
  { ident: "LHR", name_city: "London Heathrow", distance: 176, fl: "140", wind: "245/40", burn: 3300, pln_fuel: 142.1, rem_nm: 0 },
];

test("mapOfpWaypoints maps navlog rows and fills REM NM", () => {
  const rows = mapOfpWaypoints({ navlog: NAVLOG });
  assert.equal(rows.length, 5);
  assert.equal(rows[0].ident, "DOH");
  assert.equal(rows[0].alt, 340);
  assert.equal(rows[0].wind, "320/25");
  assert.equal(rows[1].airway, "Y711");
  assert.equal(rows[1].legNm, 142);
  assert.equal(rows[1].burn, 2800);
  assert.equal(rows[4].remNm, 0);
});

test("mapOfpWaypoints returns null without a navlog", () => {
  assert.equal(mapOfpWaypoints({}), null);
  assert.equal(mapOfpWaypoints(null), null);
});

test("mapOfpWaypoints handles P/M wind components", () => {
  const rows = mapOfpWaypoints({ navlog: [{ ident: "XYZ", wind_comp: "M025" }] });
  assert.equal(rows[0].wind, "HW 25");
});

test("mapOfpWaypoints maps real SimBrief v2 navlog rows (string values, kg fuel)", () => {
  const rows = mapOfpWaypoints({
    navlog: [
      {
        ident: "DF174", name: "DF174", type: "wpt", via_airway: "CIND8S", fir: "",
        pos_lat: "49.884892", pos_long: "8.753469", distance: "4",
        altitude_feet: "9200", time_total: "00:11:00", time_leg: "00:11:00",
        fuel_leg: "950", fuel_plan_onboard: "70000",
        wind_dir: "235", wind_spd: "18",
      },
      {
        ident: "LIPOT", name: "LIPOT", type: "wpt", distance: "61",
        altitude_feet: "29000", time_total: "00:42:00", time_leg: "00:31:00",
        fuel_leg: "4200", fuel_plan_onboard: "69050",
        wind_dir: "250", wind_spd: "35",
      },
    ],
  });
  assert.equal(rows.length, 2);
  assert.equal(rows[0].ident, "DF174");
  assert.equal(rows[0].airway, "CIND8S");
  assert.equal(rows[0].legNm, 4);
  assert.equal(rows[0].ete, "00:11");
  assert.equal(rows[0].legEte, "00:11");
  assert.equal(rows[0].alt, 92); // 9200 ft → FL092
  assert.equal(rows[0].wind, "235/18");
  assert.equal(rows[0].burn, 950); // kg — UI divides by 1000 for display
  assert.equal(rows[0].planFuel, 70); // 70000 kg → 70 t
  // REM NM filled from the end when absent: DF174 has the 61 NM final leg ahead.
  assert.equal(rows[1].remNm, 0);
  assert.equal(rows[0].remNm, 61);
});

test("mapOfpWaypoints maps the flat EFB flightplan view rows (tonnes, alt key)", () => {
  const rows = mapOfpWaypoints({
    source: "SimBrief",
    waypoints: [
      { ident: "EDDF", name: "Frankfurt", leg_nm: null, rem_nm: 7100, ete: null, leg_ete: null, alt: null, wind: null, burn: null, plan_fuel_t: null, stage: "DEP" },
      { ident: "DF174", via_airway: "CIND8S", fir: "EDGG", leg_nm: 4, rem_nm: 7096, ete: "00:11", leg_ete: "00:11", alt: 92, wind: "235/18", burn: 950, plan_fuel_t: 70.0, stage: "CLB" },
      { ident: "RJAA", name: "Tokyo", leg_nm: 61, rem_nm: 0, ete: "09:20", leg_ete: "09:09", alt: 140, wind: null, burn: 3300, plan_fuel_t: 5.3, stage: "ARR" },
    ],
  });
  assert.equal(rows.length, 3);
  assert.equal(rows[1].airway, "CIND8S");
  assert.equal(rows[1].alt, 92); // flat view `alt` is already FL
  assert.equal(rows[1].burn, 950); // flat view burn is kg (kgFactor=1)
  assert.equal(rows[1].planFuel, 70.0); // plan_fuel_t already tonnes
  assert.equal(rows[0].remNm, 7100);
  assert.equal(rows[2].remNm, 0);
});

/* ── sim plan fallback ──────────────────────────────────────────────── */

test("modelSimPlan derives a complete 13-row plan for DOH→LHR", () => {
  const plan = modelSimPlan({
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
  });
  assert.ok(plan, "plan should exist");
  assert.equal(plan.derived, true);
  assert.equal(plan.rows.length, 13); // 1 departure + 12 legs
  assert.equal(plan.rows[0].ident, "DOH");
  assert.equal(plan.rows[0].remNm, plan.distance_nm);
  assert.equal(plan.rows[12].ident, "LHR");
  assert.equal(plan.rows[12].remNm, 0);
  assert.equal(plan.rows[12].alt, 140);
  // REM NM strictly decreasing.
  for (let i = 1; i < plan.rows.length; i++) {
    assert.ok(plan.rows[i].remNm < plan.rows[i - 1].remNm);
  }
  // Fuel math (mockup-consistent): block = takeoff + taxi, takeoff ≈ trip + landing.
  assert.ok(Math.abs(plan.fuel.block - (plan.fuel.takeoff + plan.fuel.taxi)) < 0.11);
  assert.ok(Math.abs(plan.fuel.takeoff - (plan.fuel.trip + plan.fuel.landing)) < 0.2);
  // Distance: 1.12x corridor allowance over the ~2814 NM great circle.
  assert.ok(plan.distance_nm > 2900 && plan.distance_nm < 3500, `got ${plan.distance_nm}`);
  // Legs: 12 equal legs + one final leg filling the rest; all positive.
  assert.ok(plan.rows.every((r, i) => i === 0 ? r.legNm == null : r.legNm > 0));
  assert.ok(plan.rows[12].legNm > 0 && plan.rows[12].legNm < 500);
});

test("modelSimPlan returns null without known ICAO positions", () => {
  assert.equal(modelSimPlan({ departure_icao: "ZZZ", arrival_icao: "LHR" }), null);
  assert.equal(modelSimPlan(null), null);
});

/* ── route + edto views ─────────────────────────────────────────────── */

test("mapRouteView builds projected-capable point list", () => {
  const view = mapRouteView(
    { flight_number: "QR815", departure_icao: "DOH", arrival_icao: "LHR", route: "DCT DOH LHR" },
    SAMPLE_OFP
  );
  assert.equal(view.hero.departure, "DOH");
  assert.equal(view.points[0].end, "dep");
  assert.equal(view.points[view.points.length - 1].end, "arr");
  assert.ok(view.distance_nm > 2700 && view.distance_nm < 5000);
  assert.equal(view.route_string, "DCT DOH LHR");
});

test("mapEdtoView carries the static briefing snapshot", () => {
  const view = mapEdtoView(
    { flight_number: "QR815", departure_icao: "DOH", arrival_icao: "LHR" },
    null
  );
  assert.equal(view.static_briefing, true);
  assert.equal(view.official_notices[0].region, "PERSIAN GULF & GULF OF OMAN");
  assert.equal(view.official_notices[0].status, "ACTIVE");
  assert.equal(view.operator_risks.length, 4);
  assert.equal(view.operator_risks[0].level, "LEVEL 3 CAUTION");
  assert.equal(view.nat_tracks.length, 3);
  assert.equal(view.nat_tracks[0].name, "NAT A");
  assert.ok(view.distance_nm > 0);
});

/* ── inbox ──────────────────────────────────────────────────────────── */

test("badgeClass maps known kinds and defaults to dispatch", () => {
  assert.equal(badgeClass("DISPATCH"), "badge--dispatch");
  assert.equal(badgeClass("a-cdm"), "badge--acdm");
  assert.equal(badgeClass("NOTAM"), "badge--notam");
  assert.equal(badgeClass("WEATHER"), "badge--weather");
  assert.equal(badgeClass("VATSIM"), "badge--vatsim");
  assert.equal(badgeClass("whatever"), "badge--dispatch");
});

test("mapNotification classifies weather-ish types as WEATHER", () => {
  const n = mapNotification({
    id: 1,
    type: "wind_speed",
    icao: "DOH",
    summary: "Wind 320/25",
    provenance: "Qatar Airways Meteo",
    timestamp: 1760000000,
  });
  assert.equal(n.kind, "WEATHER");
  assert.equal(n.badge_class, "badge--weather");
  assert.equal(n.sender, "Qatar Airways Meteo");
});

test("mapNotification defaults to DISPATCH for plain ops types", () => {
  const n = mapNotification({ id: 2, type: "ops", icao: "LHR", summary: "Release ok" });
  assert.equal(n.kind, "DISPATCH");
});

test("defaultInboxMessages mirrors the mockup's six entries", () => {
  const msgs = defaultInboxMessages({
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
  });
  assert.equal(msgs.length, 6);
  assert.deepEqual(
    msgs.map((m) => m.kind),
    ["DISPATCH", "A-CDM", "NOTAM", "WEATHER", "A-CDM", "VATSIM"]
  );
  assert.ok(msgs[0].title.includes("QR815"));
  assert.ok(msgs.every((m) => m.badge_class && m.sender && m.preview));
});
