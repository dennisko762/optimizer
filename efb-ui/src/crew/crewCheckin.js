/**
 * crewCheckin — per-flight local check-in state (M2b).
 *
 * Mock phase: checking a flight in stores a record under its flight_id in
 * localStorage — that is what tells the "company" the crew is on site and
 * ready. The VA check-in API plugs in here later; the UI only calls the
 * pure functions below.
 */

const CHECKIN_KEY = "qr.crew.checkins.v1";

function getStorage() {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

/**
 * Parse the raw localStorage payload into a { flight_id: record } map.
 * Corrupt or wrong-shaped payloads become {} — never throw.
 */
export function parseCheckins(raw) {
  if (typeof raw !== "string" || !raw.trim()) return {};
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
  return parsed;
}

/**
 * Load all check-in records: { [flight_id]: { at, pilot } }.
 */
export function loadCheckins() {
  const storage = getStorage();
  if (!storage) return {};
  return parseCheckins(storage.getItem(CHECKIN_KEY));
}

/**
 * Persist the full check-in map.
 */
export function saveCheckins(map) {
  const storage = getStorage();
  if (!storage) return;
  storage.setItem(CHECKIN_KEY, JSON.stringify(map));
}

/**
 * Check a flight in. Returns { ok, record } — ok is false (record null)
 * when no usable flight id was given. Overwrites any earlier record.
 */
export function checkInFlight(flightId, pilot = null, now = Date.now()) {
  if (!flightId || !String(flightId).trim()) {
    return { ok: false, error: "No flight selected.", record: null };
  }
  const key = String(flightId).trim();
  const map = loadCheckins();
  const pilotId = pilot?.pilotId ? String(pilot.pilotId).trim() : null;
  const record = {
    at: now,
    pilot: pilotId || null,
  };
  map[key] = record;
  saveCheckins(map);
  return { ok: true, record };
}

/**
 * True when the given flight id has a check-in record.
 */
export function isCheckedIn(flightId) {
  if (!flightId) return false;
  return Boolean(loadCheckins()[String(flightId).trim()]);
}

/**
 * The check-in record for a flight, or null.
 */
export function getCheckin(flightId) {
  if (!flightId) return null;
  return loadCheckins()[String(flightId).trim()] || null;
}
