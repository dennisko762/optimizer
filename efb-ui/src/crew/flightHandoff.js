/**
 * Flight handoff mapping — eDesk roster flight → optimizer context.
 *
 * Pure function, no React. The crew shell (Pilot API flights) hands a
 * selected flight plus the active provider to the optimizer, which
 * pre-fills its flight context / state from these patches. The provider
 * ICAO is the airline code the optimizer expects.
 */

/**
 * @param {object|null} flight  Roster flight from the Pilot API
 *        (flight_number, departure_icao, arrival_icao, aircraft_icao, ...)
 * @param {object|null} provider  Active provider profile (icao, short_code, ...)
 * @returns {{flightContextPatch: object, flightStatePatch: object}|null}
 */
export function flightToOptimizerContext(flight, provider) {
  if (!flight) return null;

  return {
    flightContextPatch: {
      origin: flight.departure_icao || null,
      destination: flight.arrival_icao || null,
      flightNumber: flight.flight_number || flight.callsign || null,
      airline: provider?.icao || provider?.short_code || null,
    },
    flightStatePatch: {
      aircraft: flight.aircraft_icao || null,
    },
  };
}

/**
 * Seed optimizer flight context from a handoff, filling only fields the
 * roster flight actually carries. Absent fields keep the base value.
 * Pure: returns a new object (base unchanged).
 */
export function applyHandoffContext(base, mapped) {
  if (!mapped) return base;
  const next = { ...base };
  const { origin, destination, flightNumber, airline } = mapped.flightContextPatch;
  if (origin != null) next.origin = origin;
  if (destination != null) next.destination = destination;
  if (flightNumber != null) next.flightNumber = flightNumber;
  if (airline != null) next.airline = airline;
  return next;
}

/**
 * Seed optimizer flight state from a handoff (aircraft only in v1).
 * Pure: returns a new object (base unchanged).
 */
export function applyHandoffState(base, mapped) {
  if (!mapped) return base;
  const { aircraft } = mapped.flightStatePatch;
  if (aircraft == null) return base;
  return { ...base, aircraft };
}
