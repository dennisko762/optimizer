/**
 * useFlightCheckins — React hook over crewCheckin.js (M2b).
 *
 * Local check-in state with a subscription-style refresh: the component
 * re-reads storage on a tick (after its own check-in) — no external
 * storage library needed in the mock phase.
 */

import { useCallback, useState } from "react";
import { checkInFlight, getCheckin, isCheckedIn } from "./crewCheckin.js";

export function useFlightCheckins() {
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const checkIn = useCallback(
    (flightId, pilot) => {
      const result = checkInFlight(flightId, pilot);
      if (result.ok) refresh();
      return result;
    },
    [refresh]
  );

  const checkedIn = useCallback(
    (flightId) => {
      // `tick` is what invalidates the memoized read.
      void tick;
      return isCheckedIn(flightId);
    },
    [tick]
  );

  const record = useCallback(
    (flightId) => {
      void tick;
      return getCheckin(flightId);
    },
    [tick]
  );

  return { checkIn, checkedIn, record, refresh };
}
