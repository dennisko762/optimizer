/**
 * Boarding view mappers — pure functions for the boarding panel.
 *
 * Follows the flightHandoff.js / notificationMappers.js pattern:
 * testable with node:test, no DOM or framework dependencies.
 */

/**
 * Compute a ring percentage from current/planned, clamped 0..100.
 * Returns 0 when planned <= 0.
 *
 * @param {number} current
 * @param {number} planned
 * @returns {number}
 */
export function ringPercent(current, planned) {
  if (typeof current !== "number" || typeof planned !== "number") return 0;
  if (planned <= 0) return 0;
  return Math.min(100, Math.max(0, (current / planned) * 100));
}

/**
 * Compute derived weights from editable inputs.
 *
 * @param {{ oewKg: number, paxCount: number, paxKgEach: number,
 *           bagCount: number, bagKgEach: number, cargoKg: number,
 *           fuelKg: number }} inputs
 * @returns {{ paxKg: number, bagKg: number, zfwKg: number, towKg: number }}
 */
export function computeWeights({
  oewKg = 0,
  paxCount = 0,
  paxKgEach = 0,
  bagCount = 0,
  bagKgEach = 0,
  cargoKg = 0,
  fuelKg = 0,
} = {}) {
  const paxKg = paxCount * paxKgEach;
  const bagKg = bagCount * bagKgEach;
  const zfwKg = oewKg + paxKg + bagKg + cargoKg;
  const towKg = zfwKg + fuelKg;
  return { paxKg, bagKg, zfwKg, towKg };
}

/**
 * Derive per-group inProgress flag from the current time.
 * The group whose scheduled boarding time is <= now and the next
 * group's time is > now (or doesn't exist) is marked inProgress.
 *
 * @param {Array<{ boardingTime: string, plannedPax: number }>} groups
 * @param {string} [nowStr]  Current time as HH:MM (for testing)
 * @returns {Array<{ boardingTime: string, plannedPax: number, inProgress: boolean }>}
 */
export function deriveGroups(groups, nowStr) {
  if (!groups || !Array.isArray(groups) || groups.length === 0) return [];

  const now = nowStr || new Date().toTimeString().slice(0, 5);

  return groups.map((g, i) => {
    const started = g.boardingTime <= now;
    const nextNotStarted =
      i + 1 >= groups.length || groups[i + 1].boardingTime > now;
    return {
      ...g,
      inProgress: started && nextNotStarted,
    };
  });
}

/**
 * Map flight + OFP + session state to the full boarding view model.
 *
 * @param {object|null} flight  eDesk selected flight
 * @param {object|null} ofp  SimBrief OFP data (planned pax, fuel, weights)
 * @param {object} sessionState  Session-persisted boarding state
 * @returns {object}  Full boarding view model
 */
export function planToBoardingViewModel(flight, ofp, sessionState = {}) {
  const header = {
    flightNumber:
      flight?.flight_number || flight?.callsign || sessionState.flightNumber || "",
    route: buildRoute(flight),
    sibt: flight?.sibt || sessionState.sibt || "",
    sobt: flight?.sobt || sessionState.sobt || "",
    blockTime: flight?.block_time || sessionState.blockTime || "",
  };

  const paxPlanned = sessionState.paxPlanned ?? ofp?.pax_count ?? 0;
  const paxAte = sessionState.paxAte ?? 0;
  const bagsExpected = sessionState.bagsExpected ?? ofp?.bag_count ?? 0;
  const bagsLoaded = sessionState.bagsLoaded ?? 0;

  const paxRing = {
    current: paxAte,
    planned: paxPlanned,
    percent: ringPercent(paxAte, paxPlanned),
  };

  const bagsRing = {
    current: bagsLoaded,
    planned: bagsExpected,
    percent: ringPercent(bagsLoaded, bagsExpected),
  };

  const contacts = sessionState.contacts || [];
  const updates = sessionState.updates || [];
  const groups = deriveGroups(sessionState.groups || []);

  // Weight computation
  const oewKg = ofp?.oew_kg ?? sessionState.oewKg ?? 0;
  const paxKgEach = sessionState.paxKgEach ?? ofp?.pax_kg_each ?? 84;
  const bagKgEach = sessionState.bagKgEach ?? ofp?.bag_kg_each ?? 15;
  const cargoKg = sessionState.cargoKg ?? ofp?.cargo_kg ?? 0;
  const fuelKg = sessionState.fuelKg ?? ofp?.fuel_kg ?? 0;

  const weights = computeWeights({
    oewKg,
    paxCount: paxAte,
    paxKgEach,
    bagCount: bagsLoaded,
    bagKgEach,
    cargoKg,
    fuelKg,
  });

  // Conflict detection: SimBrief vs crew entries
  const conflicts = [];
  if (
    ofp &&
    sessionState.paxPlanned != null &&
    ofp.pax_count != null &&
    sessionState.paxPlanned !== ofp.pax_count
  ) {
    conflicts.push({
      field: "paxPlanned",
      crewValue: sessionState.paxPlanned,
      simbriefValue: ofp.pax_count,
    });
  }
  if (
    ofp &&
    sessionState.bagsExpected != null &&
    ofp.bag_count != null &&
    sessionState.bagsExpected !== ofp.bag_count
  ) {
    conflicts.push({
      field: "bagsExpected",
      crewValue: sessionState.bagsExpected,
      simbriefValue: ofp.bag_count,
    });
  }

  return {
    header,
    contacts,
    updates,
    paxRing,
    bagsRing,
    groups,
    weights: { ...weights, oewKg, cargoKg, fuelKg, paxKgEach, bagKgEach },
    conflicts,
  };
}

/**
 * Build a route string like "EDDF → LEPA" from a flight object.
 */
function buildRoute(flight) {
  if (!flight) return "";
  const dep = flight.departure_icao || "????";
  const arr = flight.arrival_icao || "????";
  return `${dep} → ${arr}`;
}
