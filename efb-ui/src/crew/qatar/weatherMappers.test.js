import test from "node:test";
import assert from "node:assert/strict";
import {
  mapWeatherStatus,
  mapLayerFeatures,
  hazardColor,
  mapRouteForMap,
  mapCrossSection,
  fmtFl,
  fmtWind,
  fmtTurb,
  validTimeLabel,
} from "./weatherMappers.js";

// ── status ──────────────────────────────────────────────────────────

test("mapWeatherStatus normalizes raw status payload", () => {
  const st = mapWeatherStatus({
    state: "ready",
    current_cycle: "20261005_18",
    last_good: "20261005_18",
    fetched_at: 1760000000,
    stale: true,
    age_hours: 7.2,
    has_plan: true,
    has_route: false,
    cadence: "6-hour model cycles, hourly forecast steps (T+0..T+36)",
    source: "NOAA NOMADS GFS 0.25 deg (g2sub)",
    last_error: null,
  });
  assert.equal(st.state, "ready");
  assert.equal(st.label, "READY");
  assert.equal(st.cycle, "20261005_18");
  assert.equal(st.stale, true);
  assert.equal(st.ageHours, 7.2);
  assert.equal(st.hasPlan, true);
  assert.equal(st.hasRoute, false);
  assert.ok(st.fetchedAt.includes("T"));
});

test("mapWeatherStatus tolerates missing fields (degraded)", () => {
  const st = mapWeatherStatus({});
  assert.equal(st.state, "idle");
  assert.equal(st.cycle, null);
  assert.equal(st.stale, null);
  assert.equal(st.hasPlan, false);
});

// ── layer ───────────────────────────────────────────────────────────

test("mapLayerFeatures passes features + metadata through", () => {
  const layer = mapLayerFeatures({
    type: "FeatureCollection",
    features: [
      {
        type: "Polygon",
        coordinates: [[[10, 50], [12, 50], [12, 52], [10, 52], [10, 50]]],
        properties: { band: 1, min: 0.3 },
      },
    ],
    product: "turbulence",
    label: "Clear-air turbulence",
    unit: "m^2/s^3 (TI1)",
    fl: 340,
    offset: 6,
    cycle: "20261005_18",
    thresholds: [0.15, 0.3, 0.45],
    source: "NOAA GFS 0.25 deg (g2sub)",
  }, "turbulence");
  assert.equal(layer.features.length, 1);
  assert.equal(layer.fl, 340);
  assert.equal(layer.offset, 6);
  assert.equal(layer.unavailable, null);
  assert.deepEqual(layer.thresholds, [0.15, 0.3, 0.45]);
});

test("mapLayerFeatures surfaces unavailable reason", () => {
  const layer = mapLayerFeatures({
    type: "FeatureCollection",
    features: [],
    unavailable: "Icing is unavailable for FL340 at T+0h",
    fl: 340,
    offset: 0,
  }, "icing");
  assert.equal(layer.features.length, 0);
  assert.match(layer.unavailable, /unavailable/);
});

test("hazardColor maps band index into the per-product ramp", () => {
  assert.equal(hazardColor("turbulence", 0), "#f2b53c");
  assert.equal(hazardColor("turbulence", 2), "#ef4444");
  // clamps above the ramp length
  assert.equal(hazardColor("turbulence", 9), "#ef4444");
  assert.equal(hazardColor("unknown", 0), "#e8a838");
});

// ── route ───────────────────────────────────────────────────────────

const ROUTE_PAYLOAD = {
  origin: "DOH",
  destination: "LHR",
  cruise_fl: 380,
  callsign: "QTR815",
  aircraft: "B777-300ER",
  route: "DOH QTR LHR",
  total_nm: 3470.4,
  point_count: 4,
  unresolved: [],
  points: [
    { index: 0, ident: "DOH", lat: 25.25, lon: 51.17, alt: 0, ete: "00:00", stage: "DEP", dist_nm: 0, cum_nm: 0, bearing_deg: null },
    { index: 1, ident: "ABTN", lat: 41.0, lon: 44.0, alt: 380, ete: "02:10", stage: "ENR", airway: "QTR", dist_nm: 1330, cum_nm: 1330, bearing_deg: 300.2 },
    { index: 2, ident: "LERTO", lat: 47.0, lon: 33.0, alt: 380, ete: "04:05", stage: "ENR", dist_nm: 1010, cum_nm: 2340, bearing_deg: 295.0 },
    { index: 3, ident: "LHR", lat: 51.47, lon: -0.45, alt: 0, ete: "07:30", stage: "ARR", dist_nm: 1130, cum_nm: 3470, bearing_deg: 350.1 },
  ],
  geojson: {
    type: "FeatureCollection",
    features: [
      { type: "Feature", geometry: { type: "Point", coordinates: [51.17, 25.25] } },
      {
        type: "Feature",
        properties: { segment: 0, kind: "route-line" },
        geometry: {
          type: "LineString",
          coordinates: [[51.17, 25.25], [44.0, 41.0], [33.0, 47.0], [-0.45, 51.47]],
        },
      },
    ],
  },
  samples: {
    cycle: "20261005_18",
    fl: 340,
    offset: 0,
    provenance: "NOAA GFS 0.25 deg (g2sub)",
    points: [
      { ident: "DOH", wind_speed_kt: 22.4, wind_from_deg: 285, tailwind_kt: -3.1, oat_c: -41.2, turbulence: 0.12, turbulence_tier: 0, icing: 0.05, icing_tier: 0, jet_tier: 1, unavailable: [] },
      { ident: "ABTN", wind_speed_kt: 41.0, wind_from_deg: 290, tailwind_kt: 12.5, oat_c: -47.0, turbulence: 0.34, turbulence_tier: 1, icing: 0.22, icing_tier: 1, jet_tier: 2, unavailable: [] },
    ],
  },
};

test("mapRouteForMap extracts points, lines and samples", () => {
  const r = mapRouteForMap(ROUTE_PAYLOAD);
  assert.equal(r.origin, "DOH");
  assert.equal(r.destination, "LHR");
  assert.equal(r.pointCount, 4);
  assert.equal(r.points[1].ident, "ABTN");
  assert.equal(r.points[1].fl, 380);
  assert.equal(r.points[1].isOrigin, false);
  assert.equal(r.points[0].isOrigin, true);
  assert.equal(r.points[3].isDest, true);
  assert.equal(r.totalNm, 3470.4);
  // antimeridian-split lines from geojson
  assert.equal(r.lines.length, 1);
  assert.equal(r.lines[0].length, 4);
  assert.deepEqual(r.lines[0][0], [51.17, 25.25]);
  // samples joined
  assert.equal(r.samples.length, 2);
  assert.equal(r.sampleFl, 340);
});

test("mapRouteForMap falls back to a single line from points when no geojson", () => {
  const r = mapRouteForMap({ ...ROUTE_PAYLOAD, geojson: { type: "FeatureCollection", features: [] } });
  assert.equal(r.lines.length, 1);
  assert.equal(r.lines[0].length, 4);
});

test("mapRouteForMap keeps unresolved fixes as explicit data errors", () => {
  const r = mapRouteForMap({
    ...ROUTE_PAYLOAD,
    unresolved: [{ index: 2, ident: "NOPE", stage: "ENR", reason: "missing coordinates" }],
  });
  assert.equal(r.unresolved.length, 1);
  assert.equal(r.unresolved[0].ident, "NOPE");
});

// ── cross-section ───────────────────────────────────────────────────

test("mapCrossSection joins route points with samples by ident", () => {
  const r = mapRouteForMap(ROUTE_PAYLOAD);
  const cs = mapCrossSection(r.points, r.samples, r.cruiseFl);
  assert.equal(cs.columns.length, 4);
  assert.equal(cs.cruiseFl, 380);
  // ABTN has a sample
  const abtn = cs.columns.find((c) => c.ident === "ABTN");
  assert.equal(abtn.windKt, 41.0);
  assert.equal(abtn.turbTier, 1);
  assert.equal(abtn.oatC, -47.0);
  assert.equal(abtn.cumNm, 1330);
  // LHR has no sample -> nulls, never fabricated
  const lhr = cs.columns.find((c) => c.ident === "LHR");
  assert.equal(lhr.windKt, null);
  assert.equal(lhr.oatC, null);
  assert.equal(lhr.fl, 380); // falls back to cruise FL
  assert.equal(cs.maxDistNm, 3470);
});

// ── formatting ──────────────────────────────────────────────────────

test("fmtFl pads flight levels", () => {
  assert.equal(fmtFl(340), "FL340");
  assert.equal(fmtFl(50), "FL050");
  assert.equal(fmtFl(null), "—");
});

test("fmtWind shows speed + direction FROM", () => {
  assert.equal(fmtWind(41.0, 290), "41 kt @ 290°");
  assert.equal(fmtWind(null, null), "—");
});

test("fmtTurb maps tier to label", () => {
  assert.equal(fmtTurb(0), "—");
  assert.equal(fmtTurb(1), "LIGHT");
  assert.equal(fmtTurb(2), "MODERATE");
  assert.equal(fmtTurb(3), "SEVERE");
});

test("validTimeLabel computes offset time from cycle id", () => {
  assert.equal(validTimeLabel("20261005_18", 0), "18Z");
  assert.equal(validTimeLabel("20261005_18", 6), "+1d 00Z");
  assert.equal(validTimeLabel("20261005_18", 24), "+1d 18Z");
  assert.equal(validTimeLabel(null, 0), null);
});
