/**
 * briefingMappers — pure view-model mappers for the aggregated aviation
 * briefing layer (/api/crew/weather/briefing/...). No React, no DOM —
 * node:test covers these directly (see briefingMappers.test.js).
 *
 * Convention shared with weatherMappers.js: unavailable values render "—";
 * state/label/age must stay visible so the UI never shows a gated/disabled
 * source as if it were live, and the three ATIS/briefing labels
 * (`VATSIM ATIS`, `Real-world D-ATIS — <provider>`, `METAR briefing`) are
 * never conflated.
 */

const STATE_BADGE = {
  ok: "LIVE",
  stale: "STALE",
  loading: "LOADING",
  unavailable: "UNAVAILABLE",
  provider_error: "ERROR",
  disabled: "DISABLED",
};

export function stateBadge(state) {
  return STATE_BADGE[state] || String(state || "—").toUpperCase();
}

/** Age in whole minutes since `retrieved_utc`/`issued_utc`, or null. */
export function ageMinutes(product, now = Date.now()) {
  const ts = product?.issued_utc || product?.retrieved_utc;
  if (!ts) return null;
  const t = Date.parse(ts);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((now - t) / 60000));
}

export function mapProduct(product) {
  if (!product || typeof product !== "object") {
    return {
      label: "—", state: "unavailable", badge: "UNAVAILABLE",
      raw: null, detail: null, ageMin: null, stale: false, station: null,
    };
  }
  return {
    label: product.label || product.product || "—",
    state: product.state || "unavailable",
    badge: stateBadge(product.state),
    raw: product.raw || null,
    detail: product.detail || null,
    ageMin: ageMinutes(product),
    stale: Boolean(product.stale),
    station: product.station || null,
    issuedUtc: product.issued_utc || null,
    validFromUtc: product.valid_from_utc || null,
    validUntilUtc: product.valid_until_utc || null,
  };
}

/** Maps the /briefing/atis/{icao} 3-way response for the UI — each of the
 * three candidates keeps its OWN label/state; `selected` says which one
 * is primary so the UI can show it foregrounded while still listing the
 * other two (never hiding a disabled/unavailable source silently). */
export function mapAtisResolution(body) {
  if (!body || typeof body !== "object") {
    return { station: null, selected: null, candidates: [] };
  }
  const order = ["real_world_datis", "vatsim_atis", "metar_briefing"];
  return {
    station: body.station || null,
    selected: body.selected || null,
    candidates: order.map((key) => ({ key, ...mapProduct(body[key]) })),
  };
}

export function mapMetarTafList(body) {
  const products = Array.isArray(body?.products) ? body.products : [];
  return products.map(mapProduct);
}

export function mapSigmetList(body) {
  const products = Array.isArray(body?.products) ? body.products : [];
  return products.map((p) => ({ ...mapProduct(p), geometry: p.geometry || null }));
}

export function mapLightning(body) {
  const p = mapProduct(body);
  return { ...p, flashCount: body?.flash_count ?? null, collection: body?.collection ?? null };
}
