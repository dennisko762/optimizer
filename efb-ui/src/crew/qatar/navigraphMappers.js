/**
 * Mappers for Navigraph data (M4).
 *
 * All Navigraph endpoints answer with the same envelope:
 *
 *   { status, datatype, data, fresh, age_seconds, subscription, detail }
 *
 * where status is one of ok | stale | not_configured | not_authenticated |
 * not_subscribed | rate_limited | offline. The UI must render EVERY one of
 * those as a readable state, so these mappers normalise the envelope into a
 * single view model with a human label and never throw on a missing field.
 */

export const NAVIGRAPH_STATUS_LABEL = {
  ok: "LIVE",
  stale: "CACHED",
  not_configured: "NOT CONFIGURED",
  not_authenticated: "SIGN IN REQUIRED",
  not_subscribed: "NOT SUBSCRIBED",
  rate_limited: "RATE LIMITED",
  offline: "OFFLINE",
  bad_request: "UNAVAILABLE",
  no_stations: "NO FLIGHT",
};

/** Badge class per status — maroon-theme tokens, defined in qatar.css. */
export const NAVIGRAPH_STATUS_CLASS = {
  ok: "badge--ok",
  stale: "badge--weather",
  not_configured: "badge--dispatch",
  not_authenticated: "badge--dispatch",
  not_subscribed: "badge--notam",
  rate_limited: "badge--notam",
  offline: "badge--notam",
  bad_request: "badge--notam",
  no_stations: "badge--dispatch",
};

function ageLabel(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return null;
  const s = Math.max(0, Math.round(Number(seconds)));
  if (s < 60) return `${s}s old`;
  if (s < 3600) return `${Math.round(s / 60)}m old`;
  if (s < 86400) return `${Math.round(s / 3600)}h old`;
  return `${Math.round(s / 86400)}d old`;
}

/**
 * Normalise any Navigraph envelope.
 * Returns { status, label, badgeClass, available, data, age, detail, subscription }.
 */
export function mapNavigraphEnvelope(resp) {
  if (!resp || typeof resp !== "object") {
    return {
      status: "offline",
      label: NAVIGRAPH_STATUS_LABEL.offline,
      badgeClass: NAVIGRAPH_STATUS_CLASS.offline,
      available: false,
      data: null,
      age: null,
      detail: "No response from the crew platform.",
      subscription: null,
    };
  }
  const status = String(resp.status || "offline");
  const available = status === "ok" || status === "stale";
  return {
    status,
    label: NAVIGRAPH_STATUS_LABEL[status] || status.toUpperCase(),
    badgeClass: NAVIGRAPH_STATUS_CLASS[status] || "badge--dispatch",
    available,
    data: resp.data ?? null,
    age: ageLabel(resp.age_seconds),
    ageSeconds: resp.age_seconds ?? null,
    detail:
      resp.detail ||
      resp.note ||
      resp.subscription?.detail ||
      "",
    subscription: resp.subscription || null,
  };
}

/* ── Subscription status (Profile screen gate panel) ─────────────────── */

const DATATYPE_LABEL = {
  airport: "Airport data",
  charts_index: "Airport charts (Jeppesen)",
  chart_image: "Chart images",
  tile: "Enroute chart tiles",
  airspace: "Airspace / airways (FMS data)",
  notam: "NOTAM feed",
  risk: "Operational risk feed",
  nat: "NAT tracks",
  userinfo: "Account",
};

/**
 * Map /api/crew/navigraph/status into rows for the subscription gate panel.
 * Shows the crew exactly which data they are entitled to, with no raw error.
 */
export function mapNavigraphStatus(resp) {
  if (!resp || typeof resp !== "object") {
    return {
      configured: false,
      authenticated: false,
      subscriptions: [],
      rows: [],
      rateLimit: null,
      cache: null,
      headline: "Navigraph unavailable",
    };
  }
  const datatypes = resp.datatypes && typeof resp.datatypes === "object" ? resp.datatypes : {};
  const rows = Object.keys(DATATYPE_LABEL)
    .filter((key) => key !== "userinfo" && datatypes[key])
    .map((key) => {
      const gate = datatypes[key] || {};
      const status = String(gate.status || "not_configured");
      return {
        id: key,
        label: DATATYPE_LABEL[key] || key,
        status,
        statusLabel: NAVIGRAPH_STATUS_LABEL[status] || status.toUpperCase(),
        badgeClass: NAVIGRAPH_STATUS_CLASS[status] || "badge--dispatch",
        allowed: Boolean(gate.allowed),
        requires: gate.required_subscription || null,
        detail: gate.detail || "",
      };
    });

  let headline = "Navigraph not configured";
  if (resp.configured && resp.authenticated) {
    const subs = resp.subscriptions || [];
    headline = subs.length
      ? `Navigraph signed in — ${subs.join(", ")}`
      : "Navigraph signed in — no active subscription (demo airports only)";
  } else if (resp.configured) {
    headline = "Navigraph configured — pilot sign-in required";
  }

  return {
    configured: Boolean(resp.configured),
    authenticated: Boolean(resp.authenticated),
    subscriptions: resp.subscriptions || [],
    demoAirports: resp.demo_airports || [],
    rows,
    rateLimit: resp.rate_limit || null,
    cache: resp.cache || null,
    headline,
  };
}

/* ── NOTAMs (Crew Desk inbox + detail panel) ─────────────────────────── */

const SEVERITY_ORDER = { critical: 0, caution: 1, info: 2 };

/**
 * Turn the /notams envelope into Crew-Desk inbox messages carrying the
 * NOTAM badge (qatar-02). Returns [] for every degraded status, so the
 * inbox simply shows its other messages.
 */
export function mapNotamMessages(resp) {
  const env = mapNavigraphEnvelope(resp);
  if (!env.available || !Array.isArray(env.data)) return [];
  return env.data
    .slice()
    .sort(
      (a, b) =>
        (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3)
    )
    .map((n) => {
      const text = String(n.text || "").replace(/\s+/g, " ").trim();
      const validity = n.permanent
        ? "PERM"
        : [n.start, n.end].filter(Boolean).map(zulu).join(" – ") || "—";
      return {
        id: `notam-${n.id || text.slice(0, 12)}`,
        kind: "NOTAM",
        badge_class: "badge--notam",
        severity: n.severity || "info",
        title: `${n.icao || "ENR"} ${n.id || ""}`.trim(),
        sender: env.status === "stale" ? `Navigraph NOTAM • ${env.age}` : "Navigraph NOTAM",
        preview: text.slice(0, 90),
        timestamp: null,
        body: `${text}\n\nVALIDITY: ${validity}${n.q_code ? `\nQ-CODE: ${n.q_code}` : ""}`,
        navigraph: true,
      };
    });
}

function zulu(iso) {
  if (!iso) return "";
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!m) return String(iso);
  return `${m[3]}${monthAbbr(m[2])} ${m[4]}${m[5]}Z`;
}

function monthAbbr(mm) {
  const names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                 "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
  const idx = Number(mm) - 1;
  return names[idx] || "";
}

/** One-line NOTAM summary for the inbox header / preflight checklist. */
export function mapNotamSummary(resp) {
  if (resp == null) {
    // Nothing fetched yet — do not claim a failure.
    return {
      available: false,
      status: "loading",
      label: "LOADING",
      badgeClass: "badge--dispatch",
      text: "Loading NOTAM briefing…",
      total: 0,
    };
  }
  const env = mapNavigraphEnvelope(resp);
  const summary = resp?.summary || {};
  if (!env.available) {
    return {
      available: false,
      status: env.status,
      label: env.label,
      badgeClass: env.badgeClass,
      text: env.detail || `NOTAM briefing ${env.label.toLowerCase()}`,
      total: 0,
    };
  }
  const counts = summary.counts || {};
  const parts = [];
  if (counts.critical) parts.push(`${counts.critical} critical`);
  if (counts.caution) parts.push(`${counts.caution} caution`);
  const stations = summary.station_count ?? (summary.stations || []).length;
  return {
    available: true,
    status: env.status,
    label: env.label,
    badgeClass: env.badgeClass,
    total: summary.total ?? 0,
    stations,
    text:
      `${summary.total ?? 0} NOTAM${(summary.total ?? 0) === 1 ? "" : "S"}` +
      ` • ${stations} station${stations === 1 ? "" : "s"}` +
      (parts.length ? ` • ${parts.join(", ")}` : "") +
      (env.status === "stale" && env.age ? ` • cached ${env.age}` : ""),
  };
}

/* ── EDTO / Risks (qatar-05) ─────────────────────────────────────────── */

/**
 * Merge the live risk bulletin over the static EDTO view model.
 *
 * The static snapshot stays as the fallback so the screen never empties;
 * when live data is available it replaces the notices, operator risks and
 * NAT tracks, and `live` flips to true so the UI can label the source.
 */
export function mapRiskView(resp, staticView) {
  const env = mapNavigraphEnvelope(resp);
  const base = staticView || {};
  if (!env.available || !env.data) {
    return {
      ...base,
      live: false,
      status: env.status,
      statusLabel: env.label,
      badgeClass: env.badgeClass,
      statusDetail: env.detail,
      sourceLabel: "static briefing snapshot",
    };
  }
  const d = env.data || {};
  const notices = Array.isArray(d.official_notices) ? d.official_notices : [];
  const operator = Array.isArray(d.operator_risks) ? d.operator_risks : [];
  const nat = Array.isArray(d.nat_tracks) ? d.nat_tracks : [];
  return {
    ...base,
    live: true,
    status: env.status,
    statusLabel: env.label,
    badgeClass: env.badgeClass,
    statusDetail: env.detail,
    sourceLabel:
      env.status === "stale"
        ? `operator risk feed • cached ${env.age}`
        : "operator risk feed • live",
    official_notices: notices.length
      ? notices.map((n) => ({
          region: n.region,
          status: n.status,
          updated: n.updated || null,
          level: n.level ?? null,
          detail: n.detail || null,
          current: true,
        }))
      : base.official_notices || [],
    operator_risks: operator.length
      ? operator.map((r) => ({
          country: r.region,
          level: r.status,
          updated: r.updated || null,
          current: true,
        }))
      : base.operator_risks || [],
    nat_tracks: nat.length
      ? nat.map((t) => ({
          name: t.name,
          direction: t.direction || "",
          valid: t.valid || "",
          track: t.track || "",
          levels: Array.isArray(t.levels) ? t.levels : [],
          tmi: t.tmi || null,
        }))
      : base.nat_tracks || [],
  };
}

/* ── Enroute chart tiles (qatar-04 route map overlay) ────────────────── */

const TILE_LAYERS = [
  { id: "ifr.hi.day", label: "ENR HI" },
  { id: "ifr.lo.day", label: "ENR LO" },
  { id: "world.day", label: "WORLD" },
];

export const NAVIGRAPH_TILE_LAYERS = TILE_LAYERS;

/** Web-Mercator tile x/y for a lat/lon at a zoom level. */
export function tileForLatLon(lat, lon, zoom) {
  if (lat == null || lon == null || !Number.isFinite(lat) || !Number.isFinite(lon)) {
    return null;
  }
  const z = Math.max(0, Math.min(18, Math.round(zoom)));
  const n = 2 ** z;
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const latRad = (clampedLat * Math.PI) / 180;
  const x = Math.floor(((lon + 180) / 360) * n);
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
  );
  return {
    z,
    x: Math.min(n - 1, Math.max(0, x)),
    y: Math.min(n - 1, Math.max(0, y)),
  };
}

/**
 * Pick the tile grid that covers a route's bounding box.
 *
 * Deliberately bounded: at most `maxTiles` tiles are requested, and the
 * zoom is reduced until the route fits. A live EFB must not fan out
 * hundreds of tile requests — that is both a rate-limit and a ToS issue.
 */
export function routeTileGrid(points, { maxTiles = 12, maxZoom = 6 } = {}) {
  const pts = (points || []).filter(
    (p) => Number.isFinite(p?.lat) && Number.isFinite(p?.lon)
  );
  if (pts.length < 2) return null;
  const lats = pts.map((p) => p.lat);
  const lons = pts.map((p) => p.lon);
  const bbox = {
    minLat: Math.min(...lats),
    maxLat: Math.max(...lats),
    minLon: Math.min(...lons),
    maxLon: Math.max(...lons),
  };
  for (let z = Math.min(18, Math.round(maxZoom)); z >= 0; z -= 1) {
    const nw = tileForLatLon(bbox.maxLat, bbox.minLon, z);
    const se = tileForLatLon(bbox.minLat, bbox.maxLon, z);
    if (!nw || !se) return null;
    const cols = se.x - nw.x + 1;
    const rows = se.y - nw.y + 1;
    if (cols > 0 && rows > 0 && cols * rows <= maxTiles) {
      const tiles = [];
      for (let x = nw.x; x <= se.x; x += 1) {
        for (let y = nw.y; y <= se.y; y += 1) tiles.push({ z, x, y });
      }
      return { z, x0: nw.x, y0: nw.y, cols, rows, tiles, bbox };
    }
  }
  return null;
}

/** Backend proxy URL for one tile (the access token never reaches the UI). */
export function tileUrl(apiBase, layer, tile, retina = false) {
  if (!tile) return null;
  const base = apiBase || "";
  return (
    `${base}/api/crew/navigraph/tiles/${layer}/${tile.z}/${tile.x}/${tile.y}` +
    (retina ? "?retina=true" : "")
  );
}

/** Tile side length in the SVG user space used by the chart underlay. */
export const TILE_PX = 256;

/**
 * Project lat/lon into the tile grid's own pixel space.
 *
 * The route map's default projection is equirectangular (qatarMappers
 * projectMap), which CANNOT host Web-Mercator tiles — the latitude
 * spacing differs. So the chart-underlay mode re-projects the route into
 * mercator tile pixels, and only that mode uses this function. Returns
 * null outside the grid's zoom/clamp range.
 */
export function projectTilePixels(lat, lon, grid) {
  if (!grid || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const n = 2 ** grid.z;
  const clampedLat = Math.max(-85.05112878, Math.min(85.05112878, lat));
  const latRad = (clampedLat * Math.PI) / 180;
  const worldX = ((lon + 180) / 360) * n;
  const worldY =
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return {
    x: Math.round((worldX - grid.x0) * TILE_PX * 10) / 10,
    y: Math.round((worldY - grid.y0) * TILE_PX * 10) / 10,
  };
}

/** SVG viewBox covering a whole tile grid. */
export function tileGridViewBox(grid) {
  if (!grid) return null;
  return {
    minx: 0,
    miny: 0,
    w: grid.cols * TILE_PX,
    h: grid.rows * TILE_PX,
  };
}

/* ── Airspace / airway enrichment for the waypoint table ─────────────── */

/**
 * Annotate OFP waypoint rows with the AIRAC cycle the airway data comes
 * from, so the crew can see whether the enroute structure shown is
 * current. Navigraph delivers airways as AIRAC packages, not per-waypoint
 * queries, so this labels provenance rather than inventing values.
 */
export function annotateAirspace(rows, navdataResp) {
  const env = mapNavigraphEnvelope(navdataResp);
  const cycle = navdataResp?.airac_cycle || null;
  const entitled = Boolean(navdataResp?.entitled_current);
  const list = Array.isArray(rows) ? rows : [];
  return {
    rows: list.map((r) => ({
      ...r,
      airacCycle: env.available ? cycle : null,
      airspaceSource: env.available
        ? entitled
          ? `AIRAC ${cycle}`
          : `AIRAC ${cycle} (outdated — no FMS data subscription)`
        : null,
    })),
    cycle,
    entitled,
    status: env.status,
    statusLabel: env.label,
    badgeClass: env.badgeClass,
    detail: env.detail,
    available: env.available,
  };
}
