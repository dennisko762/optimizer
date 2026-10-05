/**
 * useSimTelemetry — poll the M3 SimConnect live layer into React state.
 *
 * Wraps GET /api/simconnect/telemetry (see data_fetcher/sim/simconnect_routes.py)
 * on a fixed interval and POSTs optimizer targets to /api/simconnect/apply.
 *
 * The hook is deliberately small and side-effect-light: it owns the polling
 * timer and the "is the EFB server reachable" flag, and hands the raw telemetry
 * object straight back — all display shaping happens in the pure liveMappers so
 * the view-mappers stay node:test-able. A failed fetch (server down) sets
 * reachable=false but never throws; the UI then shows "NO SERVER", never fake
 * live values.
 */

import { useCallback, useEffect, useRef, useState } from "react";

const POLL_MS = 5000;

export function useSimTelemetry({ apiBase, enabled = true, destinationLat, destinationLon } = {}) {
  const [telemetry, setTelemetry] = useState(null);
  const [reachable, setReachable] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [applyState, setApplyState] = useState(null); // {busy, ok, error, applied}
  const timerRef = useRef(null);

  const fetchTelemetry = useCallback(async () => {
    if (!apiBase) return;
    setRefreshing(true);
    try {
      const params = new URLSearchParams();
      if (destinationLat != null) params.set("destinationLat", String(destinationLat));
      if (destinationLon != null) params.set("destinationLon", String(destinationLon));
      const qs = params.toString();
      const resp = await fetch(`${apiBase}/api/simconnect/telemetry${qs ? `?${qs}` : ""}`);
      if (resp.ok) {
        setTelemetry(await resp.json());
        setReachable(true);
      } else {
        // Endpoint exists but returned an error envelope — still "reachable",
        // the body (or a 404 if the route isn't mounted) decides the chip.
        setReachable(true);
        setTelemetry({ connected: false, lastError: `HTTP ${resp.status}`, flightStatePatch: {}, warnings: [] });
      }
    } catch {
      setReachable(false);
    } finally {
      setRefreshing(false);
    }
  }, [apiBase, destinationLat, destinationLon]);

  useEffect(() => {
    if (!enabled || !apiBase) return undefined;
    let cancelled = false;
    const run = async () => {
      await fetchTelemetry();
      if (cancelled) return;
      timerRef.current = setInterval(() => {
        if (!cancelled) fetchTelemetry();
      }, POLL_MS);
    };
    run();
    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [enabled, apiBase, fetchTelemetry]);

  // Push optimizer targets (flight level / mach) into the running sim.
  const apply = useCallback(
    async ({ flightLevel = null, mach = null, reason = null } = {}) => {
      if (!apiBase) return { ok: false, applied: false, error: "No API base." };
      setApplyState({ busy: true, ok: null, applied: null, error: null });
      try {
        const resp = await fetch(`${apiBase}/api/simconnect/apply`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ flightLevel, mach, reason }),
        });
        const body = await resp.json().catch(() => ({}));
        const result = {
          ok: resp.ok,
          applied: Boolean(body.applied),
          supported: Boolean(body.supported),
          errors: body.errors || [],
        };
        setApplyState({ busy: false, ok: result.ok, applied: result.applied, error: body.errors?.[0] || null });
        return result;
      } catch (e) {
        const error = e?.message || String(e);
        setApplyState({ busy: false, ok: false, applied: false, error });
        return { ok: false, applied: false, error };
      }
    },
    [apiBase]
  );

  return { telemetry, reachable, refreshing, refresh: fetchTelemetry, apply, applyState };
}
