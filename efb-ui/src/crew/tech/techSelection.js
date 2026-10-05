/**
 * Aircraft selection persistence for the TECH area.
 *
 * The selected registration is remembered per browser session (sessionStorage)
 * so the compact home card and the TECH area agree on which aircraft to show,
 * and so the choice survives tile switches. Only a registration (public
 * identifier) is stored — never any token or secret.
 */

const STORAGE_KEY = "crew.techlog.registration";

/**
 * @returns {string|null} The selected registration, or null.
 */
export function getSelectedRegistration() {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/**
 * @param {string} registration  ICAO registration to remember.
 */
export function setSelectedRegistration(registration) {
  try {
    if (registration) window.sessionStorage.setItem(STORAGE_KEY, registration);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode) — selection simply won't persist.
  }
}
