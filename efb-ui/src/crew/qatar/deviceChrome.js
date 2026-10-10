/**
 * deviceChrome — pure state helpers for the EFB device chrome (Surface-class
 * tablet status bar).
 *
 * The React layer (useDeviceChrome.jsx / DeviceChrome.jsx) owns the browser
 * APIs and the timers; everything that can be a pure function lives here so
 * it is testable without a DOM:
 *
 *   - user preferences (brightness / airplane mode / notifications off) and
 *     their localStorage round-trip,
 *   - the battery view model, built from `navigator.getBattery()`,
 *   - the network view model, built from `navigator.onLine` +
 *     `navigator.connection`.
 *
 * Honesty rule (AGENTS.md): when a browser API is missing we return an
 * explicit UNKNOWN state. No battery percentage, link quality or online flag
 * is ever invented — the old hard-coded "97" is exactly the defect this
 * module exists to prevent.
 */

export const CHROME_STORAGE_KEY = "qr.device.chrome.v1";

/** Perceived-brightness bounds. The floor stays readable in a dark cockpit. */
export const BRIGHTNESS_MIN = 40;
export const BRIGHTNESS_MAX = 100;
export const BRIGHTNESS_STEP = 5;

/** Maximum opacity of the dim layer at BRIGHTNESS_MIN. */
const MAX_DIM = 0.8;

export const DEFAULT_PREFS = Object.freeze({
  brightness: BRIGHTNESS_MAX,
  airplaneMode: false,
  notificationsOff: false,
});

function clampBrightness(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return DEFAULT_PREFS.brightness;
  return Math.min(BRIGHTNESS_MAX, Math.max(BRIGHTNESS_MIN, Math.round(n)));
}

/**
 * Coerce anything (parsed JSON, partial object, garbage) into a valid prefs
 * object. Unknown keys are dropped; invalid values fall back to the default.
 */
export function normalizePrefs(raw) {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_PREFS };
  return {
    brightness: clampBrightness(
      raw.brightness == null ? DEFAULT_PREFS.brightness : raw.brightness
    ),
    airplaneMode: raw.airplaneMode === true,
    notificationsOff: raw.notificationsOff === true,
  };
}

function getStorage() {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

/** Load persisted chrome preferences, or the defaults when absent/corrupt. */
export function loadPrefs() {
  const storage = getStorage();
  if (!storage) return { ...DEFAULT_PREFS };
  try {
    const raw = storage.getItem(CHROME_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    return normalizePrefs(JSON.parse(raw));
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

/**
 * Persist chrome preferences. Storage failures are swallowed: the toggles
 * keep working for the session, they just do not survive a reload.
 */
export function savePrefs(prefs) {
  const next = normalizePrefs(prefs);
  const storage = getStorage();
  if (!storage) return next;
  try {
    storage.setItem(CHROME_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* quota / private mode — session-only is an acceptable degradation */
  }
  return next;
}

/**
 * Opacity of the dim layer for a brightness percentage. 100 % → fully
 * transparent (0), BRIGHTNESS_MIN → MAX_DIM.
 */
export function dimOpacity(brightness) {
  const level = clampBrightness(brightness);
  const span = BRIGHTNESS_MAX - BRIGHTNESS_MIN;
  if (span <= 0) return 0;
  const ratio = (BRIGHTNESS_MAX - level) / span;
  return Math.round(ratio * MAX_DIM * 1000) / 1000;
}

/** Inline style carrying the brightness as a CSS custom property. */
export function brightnessStyle(brightness) {
  return { "--qr-dim": String(dimOpacity(brightness)) };
}

/* ── battery ────────────────────────────────────────────────────────── */

/**
 * Battery view model from a BatteryManager-shaped object
 * (`{ level: 0..1, charging: boolean }`) or null when the Battery Status API
 * is unavailable / still pending.
 *
 * Unknown stays unknown: `percent` is null and the label is an em dash.
 */
export function mapBatteryState(raw) {
  const level = raw && typeof raw.level === "number" ? raw.level : null;
  if (level == null || !Number.isFinite(level) || level < 0 || level > 1) {
    return {
      known: false,
      percent: null,
      charging: null,
      label: "—",
      title: "Battery level unavailable — this browser exposes no Battery Status API.",
    };
  }
  const percent = Math.round(level * 100);
  const charging = raw.charging === true;
  return {
    known: true,
    percent,
    charging,
    label: `${percent}%`,
    title: charging
      ? `Battery ${percent}% — charging (browser Battery Status API).`
      : `Battery ${percent}% — on battery (browser Battery Status API).`,
  };
}

/* ── network / signal ───────────────────────────────────────────────── */

const BARS_BY_EFFECTIVE_TYPE = {
  "slow-2g": 1,
  "2g": 1,
  "3g": 2,
  "4g": 4,
};

/**
 * Network view model.
 *
 * @param {object} input
 * @param {boolean|null|undefined} input.online        navigator.onLine
 * @param {string|null} input.effectiveType            navigator.connection.effectiveType
 * @param {boolean} input.airplaneMode                 user toggle
 */
export function mapNetworkState({
  online,
  effectiveType = null,
  airplaneMode = false,
} = {}) {
  if (airplaneMode) {
    return {
      mode: "airplane",
      online: false,
      wifi: false,
      bars: 0,
      label: "AIRPLANE MODE",
      title: "Airplane mode is on — outbound polling is suspended.",
    };
  }
  if (typeof online !== "boolean") {
    return {
      mode: "unknown",
      online: null,
      wifi: null,
      bars: null,
      label: "LINK —",
      title: "Network status unavailable — this environment exposes no navigator.onLine.",
    };
  }
  if (!online) {
    return {
      mode: "offline",
      online: false,
      wifi: false,
      bars: 0,
      label: "OFFLINE",
      title: "The browser reports no network connection (navigator.onLine = false).",
    };
  }
  const key = typeof effectiveType === "string" ? effectiveType.toLowerCase() : null;
  const bars = key && key in BARS_BY_EFFECTIVE_TYPE ? BARS_BY_EFFECTIVE_TYPE[key] : null;
  return {
    mode: "online",
    online: true,
    wifi: true,
    bars,
    label: "ONLINE",
    title: bars
      ? `Online — link quality ${key} (navigator.connection).`
      : "Online — link quality unavailable (no navigator.connection in this browser).",
  };
}

/* ── notices (in-app alerts, suppressible by Do Not Disturb) ────────── */

export const NOTICE_TTL_MS = 6000;

/**
 * Fold a new notice into the list, or record it as suppressed when Do Not
 * Disturb is on. Returns a new state object; never mutates the input.
 */
export function applyNotice(state, notice, { notificationsOff = false } = {}) {
  const current = state || { notices: [], suppressed: 0 };
  if (!notice || !notice.text) return current;
  if (notificationsOff) {
    return { notices: current.notices, suppressed: current.suppressed + 1 };
  }
  const entry = {
    id: notice.id || `n-${Date.now()}-${current.notices.length}`,
    kind: notice.kind === "error" ? "error" : notice.kind === "warn" ? "warn" : "info",
    text: String(notice.text),
  };
  // Newest first, capped: the host is a corner stack, not a log.
  return { notices: [entry, ...current.notices].slice(0, 4), suppressed: current.suppressed };
}

/** Drop one notice by id. */
export function dismissNotice(state, id) {
  const current = state || { notices: [], suppressed: 0 };
  return {
    notices: current.notices.filter((n) => n.id !== id),
    suppressed: current.suppressed,
  };
}
