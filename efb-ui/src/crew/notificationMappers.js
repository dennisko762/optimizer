/**
 * Notification view mappers — API notification events → UI display objects.
 *
 * Pure functions, no React. Follows the flightHandoff.js pattern:
 * testable with node:test, no DOM or framework dependencies.
 */

/**
 * Map a raw notification event from the API to a display object.
 *
 * @param {object} event  Raw event from GET /api/crew/notifications
 *   { id, type, icao, summary, provenance, timestamp, observed }
 * @returns {{ id: string, typeIcon: string, typeLabel: string,
 *             icao: string, summary: string, relativeTime: string,
 *             provenance: string }}
 */
export function notificationToDisplay(event, now = Date.now() / 1000) {
  if (!event) return null;

  return {
    id: event.id || "",
    typeIcon: typeToIcon(event.type),
    typeLabel: typeToLabel(event.type),
    icao: event.icao || "",
    summary: event.summary || "",
    relativeTime: formatRelativeTime(event.timestamp, now),
    provenance: event.provenance || "",
  };
}

/**
 * Map a list of notification events to display objects.
 * Filters out nulls.
 *
 * @param {object[]} events  Array of raw events
 * @param {number} [now]  Current time in seconds (for testing)
 * @returns {object[]}
 */
export function mapNotifications(events, now = Date.now() / 1000) {
  if (!events || !Array.isArray(events)) return [];
  return events
    .map((e) => notificationToDisplay(e, now))
    .filter((d) => d !== null);
}

/**
 * Map notification type string to a display icon character.
 * (Using simple text icons; the React component maps these to lucide icons.)
 */
export function typeToIcon(type) {
  const icons = {
    visibility: "👁",
    wind_direction: "🧭",
    wind_speed: "💨",
    temperature: "🌡",
  };
  return icons[type] || "📋";
}

/**
 * Map notification type string to a human label.
 */
export function typeToLabel(type) {
  const labels = {
    visibility: "Visibility",
    wind_direction: "Wind Direction",
    wind_speed: "Wind Speed",
    temperature: "Temperature",
  };
  return labels[type] || type || "Unknown";
}

/**
 * Format a unix timestamp as a relative time string.
 *
 * @param {number} timestamp  Event timestamp (seconds)
 * @param {number} now  Current time (seconds)
 * @returns {string}
 */
export function formatRelativeTime(timestamp, now) {
  if (typeof timestamp !== "number" || typeof now !== "number") return "";

  const diff = Math.max(0, now - timestamp);

  if (diff < 60) return "just now";
  if (diff < 3600) {
    const mins = Math.floor(diff / 60);
    return `${mins}m ago`;
  }
  if (diff < 86400) {
    const hrs = Math.floor(diff / 3600);
    return `${hrs}h ago`;
  }
  const days = Math.floor(diff / 86400);
  return `${days}d ago`;
}
