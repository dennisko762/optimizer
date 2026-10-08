/**
 * Pure view-mappers for the SkyNexus-style live eWAS route map (M5-P).
 *
 * These turn the raw /api/crew/weather payloads (status / layer / route) into
 * the shapes the MapLibre map + cross-section render. No DOM, no fetch — node
 * --test can exercise them. All values are surfaced as-is from NOAA GFS
 * proxies; unavailable fields stay null (never synthesized).
 */

// ── status ──────────────────────────────────────────────────────────

const STATE_LABEL = {
  idle: "IDLE",
  checking: "CHECKING",
  downloading: "DOWNLOADING",
  processing: "PROCESSING",
  ready: "READY",
  error: "ERROR",
};

/**
 * Normalize GET /status into the header-chip + banner model.
 * @param {object} status raw status payload
 * @returns {{state:string,label:string,cycle:string|null,validAt:string|null,
 *   fetchedAt:string|null,stale:boolean|null,ageHours:number|null,
 *   hasPlan:boolean,hasRoute:boolean,cadence:string,source:string,
 *   lastError:string|null}}
 */
export function mapWeatherStatus(status) {
  const s = status || {};
  const fetchedAt = s.fetched_at ? new Date(s.fetched_at * 1000).toISOString() : null;
  return {
    state: s.state || "idle",
    label: STATE_LABEL[s.state] || (s.state ? String(s.state).toUpperCase() : "IDLE"),
    cycle: s.current_cycle || s.last_good || null,
    validAt: s.current_cycle ? s.current_cycle : null,
    fetchedAt,
    stale: typeof s.stale === "boolean" ? s.stale : null,
    ageHours: s.age_hours != null ? Number(s.age_hours) : null,
    hasPlan: Boolean(s.has_plan),
    hasRoute: Boolean(s.has_route),
    cadence: s.cadence || "6-hour model cycles, hourly forecast steps (T+0..T+36)",
    source: s.source || "NOAA GFS 0.25 deg (g2sub) — NOAA-derived proxy",
    lastError: s.last_error || null,
  };
}

// ── hazard layer ────────────────────────────────────────────────────

// Tier color ramps (Qatar-maroon-friendly, high contrast on dark basemap).
const RAMP = {
  turbulence: ["#f2b53c", "#f97316", "#ef4444"], // light / moderate / severe TI
  icing: ["#38bdf8", "#2563eb", "#7c3aed"], // light / moderate / severe CIP proxy
  cape: ["#e8a838", "#ef4444", "#c74a6a"], // 500 / 2500 / 5000 J/kg
  fronts: ["#38bdf8", "#e8a838"], // potential / strong front
  jet: ["#22d3ee", "#0ea5e9"], // extent / core
};

const DEFAULT_RAMP = ["#e8a838", "#ef4444", "#c74a6a"];

/** Fill color for a hazard feature's band (tier index). */
export function hazardColor(product, band) {
  const ramp = RAMP[product] || DEFAULT_RAMP;
  const b = band == null ? 0 : band;
  return ramp[Math.min(b, ramp.length - 1)];
}

/**
 * Normalize a GET /layer/{product} FeatureCollection into render-ready
 * features (GeoJSON coordinates are already [lon,lat] rings).
 * @param {object} layer raw layer payload
 * @param {string} product product key
 * @returns {{features:Array, unavailable:string|null, fl:number,
 *   offset:number|null, cycle:string|null, label:string, unit:string,
 *   thresholds:Array<number>}}
 */
export function mapLayerFeatures(layer, product) {
  const l = layer || {};
  return {
    features: Array.isArray(l.features) ? l.features : [],
    unavailable: l.unavailable || null,
    fl: l.fl != null ? Number(l.fl) : null,
    offset: l.offset != null ? Number(l.offset) : null,
    cycle: l.cycle || null,
    label: l.label || product,
    unit: l.unit || "",
    thresholds: Array.isArray(l.thresholds) ? l.thresholds : [],
    source: l.source || null,
  };
}

// ── route ───────────────────────────────────────────────────────────

/**
 * Normalize GET /route into map geometry + per-fix readout.
 * @param {object} route raw route payload (extract_route + samples + fl/offset)
 * @returns {{origin:string|null,destination:string|null,cruiseFl:number|null,
 *   callsign:string|null,aircraft:string|null,routeString:string|null,
 *   totalNm:number|null,pointCount:number,
 *   points:Array, unresolved:Array, lines:Array<Array<[number,number]>>,
 *   samples:Array, sampleFl:number|null, sampleOffset:number|null,
 *   provenance:string|null}}
 */
export function mapRouteForMap(route) {
  const r = route || {};
  const originIdent = (r.origin || "").toUpperCase();
  const destIdent = (r.destination || "").toUpperCase();
  const points = Array.isArray(r.points) ? r.points.map((p, i) => {
    const ident = (p.ident || `IDX${i}`).toUpperCase();
    return {
    index: p.index != null ? p.index : i,
    ident,
    name: p.name || null,
    lat: p.lat,
    lon: p.lon,
    // alt 0 means a ground fix (DEP/ARR) with no assigned FL -> null so the
    // cross-section can fall back to the planned cruise level.
    fl: p.alt != null && p.alt !== 0 ? Number(p.alt) : null,
    ete: p.ete || null,
    stage: (p.stage || null) ? String(p.stage).toUpperCase() : null,
    airway: p.airway || null,
    fir: p.fir || null,
    wind: p.wind || null,
    // Robust endpoint detection: explicit flag OR ident match OR stage.
    isOrigin: p.is_origin === true || ident === originIdent || p.stage === "DEP",
    isDest: p.is_dest === true || ident === destIdent || p.stage === "ARR",
    distNm: p.dist_nm != null ? Number(p.dist_nm) : null,
    cumNm: p.cum_nm != null ? Number(p.cum_nm) : null,
    bearingDeg: p.bearing_deg != null ? Number(p.bearing_deg) : null,
    };
  }) : [];

  // Drawn sub-polylines: prefer the server's antimeridian split (geojson
  // LineString features); fall back to one continuous line from points.
  let lines = [];
  const gj = r.geojson || {};
  const lineFeats = (gj.features || []).filter((f) =>
    f.geometry && f.geometry.type === "LineString"
  );
  if (lineFeats.length) {
    lines = lineFeats.map((f) => f.geometry.coordinates);
  } else if (points.length > 1) {
    lines = [points.map((p) => [p.lon, p.lat])];
  }

  const samples = (r.samples && Array.isArray(r.samples.points))
    ? r.samples.points : [];

  return {
    origin: r.origin || null,
    destination: r.destination || null,
    cruiseFl: r.cruise_fl != null ? Number(r.cruise_fl) : null,
    callsign: r.callsign || null,
    aircraft: r.aircraft || null,
    routeString: r.route || (r.origin && r.destination
      ? `${r.origin} DCT ${r.destination}` : null),
    totalNm: r.total_nm != null ? Number(r.total_nm) : null,
    pointCount: r.point_count != null ? Number(r.point_count) : points.length,
    points,
    unresolved: Array.isArray(r.unresolved) ? r.unresolved : [],
    lines,
    samples,
    sampleFl: r.samples && r.samples.fl != null ? Number(r.samples.fl) : (r.fl != null ? Number(r.fl) : null),
    sampleOffset: r.samples && r.samples.offset != null ? Number(r.samples.offset) : (r.offset != null ? Number(r.offset) : null),
    provenance: r.samples && r.samples.provenance ? r.samples.provenance : null,
  };
}

// ── cross-section ───────────────────────────────────────────────────

/**
 * Build the docked cross-section columns: one per route point, joining the
 * route geometry (distance/FL) with the per-fix weather sample by ident.
 * @param {Array} points mapRouteForMap().points
 * @param {Array} samples mapRouteForMap().samples
 * @returns {{columns:Array, maxDistNm:number|null, cruiseFl:number|null}}
 */
export function mapCrossSection(points, samples, cruiseFl) {
  const byIdent = new Map();
  for (const s of samples) if (s && s.ident) byIdent.set(s.ident, s);
  const columns = points.map((p) => {
    const s = byIdent.get(p.ident) || {};
    return {
      ident: p.ident,
      isOrigin: p.isOrigin,
      isDest: p.isDest,
      stage: p.stage,
      cumNm: p.cumNm,
      distNm: p.distNm,
      fl: p.fl != null ? p.fl : cruiseFl,
      windKt: s.wind_speed_kt != null ? Number(s.wind_speed_kt) : null,
      windFrom: s.wind_from_deg != null ? Number(s.wind_from_deg) : null,
      tailwindKt: s.tailwind_kt != null ? Number(s.tailwind_kt) : null,
      oatC: s.oat_c != null ? Number(s.oat_c) : null,
      turbTier: s.turbulence_tier != null ? Number(s.turbulence_tier) : null,
      iceTier: s.icing_tier != null ? Number(s.icing_tier) : null,
      jetTier: s.jet_tier != null ? Number(s.jet_tier) : null,
      ete: s.ete || p.ete || null,
      unavailable: Array.isArray(s.unavailable) ? s.unavailable.filter(Boolean) : [],
    };
  });
  const maxDistNm = points.length ? points[points.length - 1].cumNm : null;
  return { columns, maxDistNm, cruiseFl: cruiseFl != null ? Number(cruiseFl) : null };
}

// ── formatting helpers ──────────────────────────────────────────────

export function fmtFl(fl) {
  if (fl == null) return "—";
  return `FL${String(Math.round(fl)).padStart(3, "0")}`;
}

export function fmtWind(kts, fromDeg) {
  if (kts == null) return "—";
  const dir = fromDeg != null ? `${String(Math.round(fromDeg)).padStart(3, "0")}°` : "—";
  return `${Math.round(kts)} kt @ ${dir}`;
}

export function fmtTurb(tier) {
  if (tier == null) return "—";
  return ["", "LIGHT", "MODERATE", "SEVERE"][tier] || "—";
}

export function fmtIce(tier) {
  if (tier == null) return "—";
  return ["", "LIGHT", "MODERATE", "SEVERE"][tier] || "—";
}

/** Valid-time label for a forecast offset from a cycle id (YYYYMMDD_HH). */
export function validTimeLabel(cycle, offset) {
  if (!cycle) return null;
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})$/.exec(cycle);
  if (!m) return null;
  const runMin = Number(m[4]) * 60;
  const off = offset == null ? 0 : offset;
  const total = runMin + off * 60;
  const day = Math.floor(total / 1440);
  const hh = String(Math.floor((total % 1440) / 60)).padStart(2, "0");
  return day === 0 ? `${hh}Z` : `+${day}d ${hh}Z`;
}
