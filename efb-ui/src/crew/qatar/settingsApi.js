/**
 * settingsApi — client logic for the EFB Settings screen.
 *
 * Kept out of the component so the validation rules and the honest
 * success/failure mapping are unit-testable without a renderer.
 *
 * Secrets are write-only by design: nothing here stores, caches or reads
 * back a client secret. The backend returns booleans, and this module only
 * ever forwards what the crew typed.
 */

/** Allowed shapes — mirrors crew_platform/settings.py so the UI can label
 *  a bad value before a round trip. The backend re-validates regardless. */
export const SIMBRIEF_USER_RE = /^[A-Za-z0-9][A-Za-z0-9._~@-]*$/;
export const NAVIGRAPH_CLIENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._~-]*$/;
export const NAVIGRAPH_CLIENT_SECRET_RE = /^[A-Za-z0-9._~+/=:@-]+$/;

/** Navigraph's developer portal — where a client id/secret comes from. */
export const NAVIGRAPH_PORTAL_URL = "https://developers.navigraph.com";

function check(value, { re, max, label, hint }) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return { valid: false, value: "", error: `${label} is required.` };
  if (trimmed.length > max) {
    return {
      valid: false,
      value: trimmed,
      error: `${label} is too long (maximum ${max} characters).`,
    };
  }
  if (!re.test(trimmed)) {
    return { valid: false, value: trimmed, error: `${label}: ${hint}` };
  }
  return { valid: true, value: trimmed, error: null };
}

export function validateSimbriefUser(value) {
  return check(value, {
    re: SIMBRIEF_USER_RE,
    max: 64,
    label: "SimBrief username",
    hint: "letters, digits and . _ - ~ @ only, starting with a letter or digit.",
  });
}

export function validateNavigraphClientId(value) {
  return check(value, {
    re: NAVIGRAPH_CLIENT_ID_RE,
    max: 128,
    label: "Navigraph client ID",
    hint: "letters, digits and . _ - ~ only, as shown in the developer portal.",
  });
}

export function validateNavigraphClientSecret(value) {
  return check(value, {
    re: NAVIGRAPH_CLIENT_SECRET_RE,
    max: 512,
    label: "Navigraph client secret",
    hint: "paste the secret exactly as issued (no spaces or quotes).",
  });
}

/**
 * Turn the /api/crew/settings body into what the screen renders.
 *
 * A missing/unreachable body is reported as such — never as "configured".
 */
export function mapSettingsView(state) {
  if (!state || typeof state !== "object") {
    return {
      available: false,
      simbrief: { configured: false, user: "" },
      navigraph: {
        configured: false,
        clientIdSet: false,
        clientSecretSet: false,
        accessTokenInjected: false,
        scopes: [],
      },
      envWritable: false,
      envExists: false,
      remoteWritesAllowed: false,
      notice: "Settings unavailable — the bridge did not answer.",
    };
  }
  const simbrief = state.simbrief || {};
  const navigraph = state.navigraph || {};
  const envFile = state.env_file || {};
  const view = {
    available: true,
    simbrief: {
      configured: Boolean(simbrief.configured),
      user: simbrief.user || "",
    },
    navigraph: {
      configured: Boolean(navigraph.configured),
      clientIdSet: Boolean(navigraph.client_id_set),
      clientSecretSet: Boolean(navigraph.client_secret_set),
      accessTokenInjected: Boolean(navigraph.access_token_injected),
      scopes: Array.isArray(navigraph.scopes) ? navigraph.scopes : [],
    },
    envWritable: Boolean(envFile.writable),
    envExists: Boolean(envFile.exists),
    remoteWritesAllowed: Boolean(state.remote_writes_allowed),
    notice: null,
  };
  if (!view.envWritable) {
    view.notice =
      "The bridge settings file is not writable — changes cannot be saved. " +
      "Check the permissions of the .env file next to the bridge.";
  }
  return view;
}

/** Status pill for /api/simbrief/config/readiness. */
export function mapSimbriefPill(readiness) {
  if (!readiness || typeof readiness !== "object") {
    return { label: "UNKNOWN", badgeClass: "badge--dispatch", configured: false };
  }
  return readiness.configured
    ? { label: "CONFIGURED", badgeClass: "badge--ok", configured: true }
    : { label: "NOT CONFIGURED", badgeClass: "badge--notam", configured: false };
}

/**
 * Human-readable message for a failed save. Field errors from the 422 body
 * are surfaced per field; everything else becomes one honest sentence.
 */
export function describeSaveFailure(status, body) {
  const detail = body && typeof body === "object" ? body.detail : null;
  if (detail && typeof detail === "object" && detail.errors) {
    return {
      message: detail.message || "Some settings were rejected.",
      fieldErrors: detail.errors,
    };
  }
  if (typeof detail === "string" && detail) {
    return { message: detail, fieldErrors: {} };
  }
  return { message: `Save failed (HTTP ${status}).`, fieldErrors: {} };
}

async function readJson(resp) {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

export async function fetchSettings(apiBase, sessionId) {
  if (!sessionId) return { ok: false, state: null, error: "No crew session." };
  try {
    const resp = await fetch(
      `${apiBase || ""}/api/crew/settings?session_id=${encodeURIComponent(sessionId)}`
    );
    const body = await readJson(resp);
    if (!resp.ok) {
      return {
        ok: false,
        state: null,
        error: describeSaveFailure(resp.status, body).message,
      };
    }
    return { ok: true, state: body, error: null };
  } catch (e) {
    return { ok: false, state: null, error: String(e?.message || e) };
  }
}

/**
 * PUT the submitted values. Only keys present in `values` are sent, so a
 * blank field never clears an existing setting by accident.
 */
export async function saveSettings(apiBase, sessionId, values) {
  if (!sessionId) {
    return { ok: false, state: null, message: "No crew session.", fieldErrors: {} };
  }
  try {
    const resp = await fetch(`${apiBase || ""}/api/crew/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, ...values }),
    });
    const body = await readJson(resp);
    if (!resp.ok) {
      const failure = describeSaveFailure(resp.status, body);
      return { ok: false, state: null, ...failure };
    }
    return { ok: true, state: body, message: "Saved.", fieldErrors: {} };
  } catch (e) {
    return {
      ok: false,
      state: null,
      message: String(e?.message || e),
      fieldErrors: {},
    };
  }
}

export async function fetchSimbriefReadiness(apiBase) {
  try {
    const resp = await fetch(`${apiBase || ""}/api/simbrief/config/readiness`);
    if (!resp.ok) return null;
    return await readJson(resp);
  } catch {
    return null;
  }
}

/**
 * "Test" — a REAL SimBrief fetch through the bridge. Reports exactly what
 * happened; a failure is never dressed up as a success.
 */
export async function testSimbrief(apiBase) {
  try {
    const resp = await fetch(
      `${apiBase || ""}/api/simbrief/flightplan/live?refresh=true`
    );
    const body = await readJson(resp);
    if (resp.ok) {
      const origin = body?.origin || body?.general?.icao_airline || null;
      const dest = body?.destination || null;
      const route = origin && dest ? ` — latest OFP ${origin} → ${dest}` : "";
      return { ok: true, message: `SimBrief responded with a real OFP${route}.` };
    }
    const detail =
      (body && typeof body.detail === "string" && body.detail) ||
      `HTTP ${resp.status}`;
    return { ok: false, message: `SimBrief test failed — ${detail}` };
  } catch (e) {
    return {
      ok: false,
      message: `SimBrief test failed — ${String(e?.message || e)}`,
    };
  }
}
