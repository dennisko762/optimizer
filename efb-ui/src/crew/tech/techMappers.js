/**
 * Tech view mappers — pure functions for the crew TECH area (TechLog P1-T6).
 *
 * Follows the boardingMappers.js / notificationMappers.js pattern:
 * testable with node:test, no DOM or framework dependencies.
 *
 * The TechLog domain models the real persistent aircraft (keyed by
 * registration). The UI shows operational state only — status strings and
 * counts — never internal health percentages.
 */

// ────────────────────────────────────────────────────────────────
// Domain constants (mirror crew_platform/technical/models.py)
// ────────────────────────────────────────────────────────────────

/** Defect lifecycle statuses, in lifecycle order. */
export const DEFECT_STATUS_VALUES = [
  "OPEN",
  "UNDER_REVIEW",
  "DEFERRED",
  "MEL_APPLIED",
  "RECTIFIED",
  "CLOSED",
];

/** Legal status transitions (mirrors DEFECT_STATUS_TRANSITIONS). */
export const DEFECT_STATUS_TRANSITIONS = {
  OPEN: ["UNDER_REVIEW", "DEFERRED", "MEL_APPLIED", "RECTIFIED", "CLOSED"],
  UNDER_REVIEW: ["DEFERRED", "MEL_APPLIED", "RECTIFIED", "CLOSED"],
  DEFERRED: ["MEL_APPLIED", "RECTIFIED", "CLOSED"],
  MEL_APPLIED: ["RECTIFIED", "CLOSED"],
  RECTIFIED: ["CLOSED"],
  CLOSED: [],
};

/** Defect statuses that are terminal (no further transitions). */
export const DEFECT_TERMINAL_STATUSES = ["RECTIFIED", "CLOSED"];

/** Live (non-terminal) defect statuses that influence technical status. */
export const DEFECT_LIVE_STATUSES = ["OPEN", "UNDER_REVIEW", "DEFERRED", "MEL_APPLIED"];

/** Maintenance action types (mirrors MAINTENANCE_ACTION_TYPES). */
export const MAINTENANCE_ACTION_TYPES = [
  "RECTIFICATION",
  "INSPECTION",
  "COMPONENT_SWAP",
  "GENERAL",
];

/** Derived aircraft technical status values (mirrors status.py). */
export const TECHNICAL_STATUS_VALUES = [
  "OPEN_DEFECTS",
  "DISPATCHABLE_WITH_MEL",
  "UNDER_REVIEW",
  "SERVICEABLE",
];

// ────────────────────────────────────────────────────────────────
// Display formatting
// ────────────────────────────────────────────────────────────────

/**
 * Normalise a raw status string to upper-case.
 * @param {string|null|undefined} raw
 * @returns {string}
 */
export function normalizeStatus(raw) {
  return (raw || "").toUpperCase();
}

/**
 * Crew-facing defect line, e.g. "PACK 1 REGULATION FAULT - OPEN".
 * Operational state only: component (or ATA chapter) + status.
 *
 * @param {object} defect  Raw defect from the API
 * @returns {string}
 */
export function defectDisplayName(defect) {
  if (!defect) return "";
  const subject =
    defect.system_component ||
    defect.description ||
    (defect.ata ? `ATA ${defect.ata}` : "DEFECT");
  const status = normalizeStatus(defect.status) || "OPEN";
  return `${String(subject).toUpperCase()} - ${status.replace(/_/g, " ")}`;
}

/**
 * Crew-facing technical status line, e.g. "DISPATCHABLE WITH MEL".
 *
 * @param {string} status  Raw status value (e.g. "DISPATCHABLE_WITH_MEL")
 * @returns {string}
 */
export function technicalStatusLabel(status) {
  return normalizeStatus(status).replace(/_/g, " ") || "—";
}

/**
 * Map a defect to its view model (adds display name + legal transitions).
 * @param {object} defect  Raw defect from the API
 * @returns {object}
 */
export function mapDefect(defect) {
  if (!defect) return null;
  const status = normalizeStatus(defect.status);
  return {
    ...defect,
    status,
    displayName: defectDisplayName(defect),
    legalTransitions: (DEFECT_STATUS_TRANSITIONS[status] || []).slice(),
    terminal: DEFECT_TERMINAL_STATUSES.includes(status),
    live: DEFECT_LIVE_STATUSES.includes(status),
  };
}

/**
 * Map an array of defects (keeps input order).
 * @param {Array<object>} defects
 * @returns {Array<object>}
 */
export function mapDefects(defects) {
  if (!Array.isArray(defects)) return [];
  return defects.map(mapDefect);
}

/**
 * Filter defects to the live (open, non-terminal) subset.
 * Mirrors crew_platform/technical/status.py:live_defects.
 * @param {Array<object>} defects
 * @returns {Array<object>}
 */
export function openDefects(defects) {
  if (!Array.isArray(defects)) return [];
  return defects.filter((d) => DEFECT_LIVE_STATUSES.includes(normalizeStatus(d && d.status)));
}

/**
 * Derive the aircraft technical status from a defect set — mirrors
 * crew_platform/technical/status.py:derive_status.
 * Precedence: OPEN → OPEN_DEFECTS; DEFERRED/MEL_APPLIED →
 * DISPATCHABLE_WITH_MEL; UNDER_REVIEW → UNDER_REVIEW; else SERVICEABLE.
 * Terminal defects (RECTIFIED/CLOSED) never influence the result.
 *
 * @param {Array<object|string>} defects  Defects or raw status strings
 * @returns {string}  One of TECHNICAL_STATUS_VALUES
 */
export function deriveTechnicalStatus(defects) {
  const statuses = (Array.isArray(defects) ? defects : []).map((d) =>
    normalizeStatus(typeof d === "object" && d !== null ? d.status : d)
  );
  if (statuses.includes("OPEN")) return "OPEN_DEFECTS";
  if (statuses.some((s) => s === "DEFERRED" || s === "MEL_APPLIED")) {
    return "DISPATCHABLE_WITH_MEL";
  }
  if (statuses.includes("UNDER_REVIEW")) return "UNDER_REVIEW";
  return "SERVICEABLE";
}

/**
 * Map the GET /aircraft/{reg}/status response (T5) to the compact status
 * card view model used by both the home tile and the TECH area header.
 *
 * @param {object|null} status  Raw status response
 * @returns {object}  { registration, type, status, statusLabel, openCount, defects[] }
 */
export function mapStatusResponse(status) {
  const defects = mapDefects(status?.open_defects_list || []);
  const open = defects.filter((d) => d.live);
  const rawStatus = status?.current_technical_status || deriveTechnicalStatus(open);
  return {
    registration: status?.registration || "",
    type: status?.type || "",
    status: normalizeStatus(rawStatus),
    statusLabel: technicalStatusLabel(rawStatus),
    openCount: open.length,
    defects: open,
  };
}

/**
 * Map a tech-log entry to its view model (display text + defect count).
 * @param {object} entry  Raw tech-log entry from the API
 * @param {number} [defectCount]  Number of defects attached to this entry
 * @returns {object}
 */
export function mapTechlogEntry(entry, defectCount = 0) {
  if (!entry) return null;
  return {
    ...entry,
    status: normalizeStatus(entry.status),
    defectCount,
  };
}

/**
 * Sort tech-log entries newest first (the T3 endpoint returns insertion
 * order; the view requires newest first per the T6 spec).
 *
 * @param {Array<object>} entries  Raw or mapped entries
 * @returns {Array<object>}
 */
export function sortTechlogNewestFirst(entries) {
  if (!Array.isArray(entries)) return [];
  const ts = (e) => {
    const raw = e?.created_at;
    const t = raw ? Date.parse(raw) : NaN;
    return Number.isNaN(t) ? 0 : t;
  };
  return entries.slice().sort((a, b) => ts(b) - ts(a) || (b.id || 0) - (a.id || 0));
}

/**
 * Map a maintenance action to its view model.
 * @param {object} action  Raw maintenance action from the API
 * @returns {object}
 */
export function mapMaintenanceAction(action) {
  if (!action) return null;
  return {
    ...action,
    action_type: normalizeStatus(action.action_type),
  };
}

/**
 * Map the compact home-card payload: (registration, status response) into
 * the minimal tile model. Falls back gracefully when no aircraft is known.
 *
 * @param {object|null} aircraft  Raw aircraft dict (registration + type)
 * @param {object|null} status  Raw /status response, or null if unknown
 * @returns {object}  { registration, type, status, statusLabel, openCount, known }
 */
export function mapHomeCardModel(aircraft, status) {
  if (status && status.registration) {
    return mapStatusResponse(status);
  }
  return {
    registration: aircraft?.registration || "",
    type: aircraft?.type || "",
    status: "",
    statusLabel: "",
    openCount: 0,
    defects: [],
  };
}
