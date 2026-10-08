/**
 * crewAuth — mock VA crew login + session persistence (M2b).
 *
 * Mock phase: ANY non-empty pilot ID + password is accepted. The real
 * VA authentication backend plugs in here later — the UI only talks to
 * login() / saveSession() / loadSession() / clearSession() and never to
 * storage directly.
 */

const SESSION_KEY = "qr.crew.session.v1";

function getStorage() {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

/**
 * Validate raw credential input. Returns { valid, errors } where errors
 * are human-readable strings (shown under the form when invalid).
 */
export function validateCredentials({ pilotId, password } = {}) {
  const errors = [];
  if (!pilotId || !String(pilotId).trim()) {
    errors.push("Pilot ID / PIN is required.");
  }
  if (!password || !String(password).trim()) {
    errors.push("VA password is required.");
  }
  return { valid: errors.length === 0, errors };
}

/**
 * Mock login: accepts any non-empty credentials. Returns
 * { ok, errors, session } — session is null when ok is false.
 */
export function login({ pilotId, password } = {}, now = Date.now()) {
  const check = validateCredentials({ pilotId, password });
  if (!check.valid) {
    return { ok: false, errors: check.errors, session: null };
  }
  return {
    ok: true,
    errors: [],
    session: {
      pilotId: String(pilotId).trim(),
      logged_in_at: now,
    },
  };
}

/**
 * Persist the crew session (localStorage). Pass null to clear.
 * Storage failures are swallowed — the session simply does not survive.
 */
export function saveSession(session) {
  const storage = getStorage();
  if (!storage) return session ? { ...session } : null;
  if (session) {
    storage.setItem(SESSION_KEY, JSON.stringify(session));
    return { ...session };
  }
  storage.removeItem(SESSION_KEY);
  return null;
}

/**
 * Load the persisted crew session, or null when absent/invalid/corrupt.
 */
export function loadSession() {
  const storage = getStorage();
  if (!storage) return null;
  try {
    const raw = storage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (!parsed.pilotId || !String(parsed.pilotId).trim()) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * Drop the persisted crew session (logout).
 */
export function clearSession() {
  const storage = getStorage();
  if (!storage) return;
  storage.removeItem(SESSION_KEY);
}
