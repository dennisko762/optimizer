/**
 * useNavigraph — subscription-gated Navigraph data for the Qatar screens.
 *
 * Fetch policy is deliberately frugal, because Navigraph rate-limits and
 * its ToS forbids bulk retrieval:
 *
 * - status is fetched once per mount and after a sign-in
 * - NOTAMs are fetched once per station set (and on explicit refresh)
 * - the risk bulletin is fetched once per mount, refreshed every 15 min
 * - tiles/charts are plain <img> loads against the backend proxy, which
 *   caches them, so the browser never touches api.navigraph.com
 *
 * Nothing here throws: a failed fetch resolves to a degraded envelope so
 * the mappers can label the screen instead of blanking it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const RISK_REFRESH_MS = 15 * 60 * 1000;

async function getJson(url) {
  try {
    const resp = await fetch(url);
    const body = await resp.json().catch(() => null);
    if (body && typeof body === "object") return body;
    return { status: "offline", data: null, detail: `HTTP ${resp.status}` };
  } catch (e) {
    return {
      status: "offline",
      data: null,
      detail: e?.message || "crew platform unreachable",
    };
  }
}

export function useNavigraph({ apiBase, stations = [], enabled = true } = {}) {
  const [status, setStatus] = useState(null);
  const [notams, setNotams] = useState(null);
  const [risks, setRisks] = useState(null);
  const [navdata, setNavdata] = useState(null);
  const [busy, setBusy] = useState(false);
  const [signIn, setSignIn] = useState(null); // {user_code, verification_uri, ...}
  const pollRef = useRef(null);

  const base = apiBase || "";
  // Station codes as the app carries them. The flight fields are named
  // *_icao but in practice hold either ICAO (OTHH) or IATA (DOH), so both
  // 3- and 4-letter codes are accepted and passed through verbatim.
  const stationKey = useMemo(
    () =>
      Array.from(
        new Set(
          (stations || [])
            .filter(Boolean)
            .map((s) => String(s).trim().toUpperCase())
            .filter((s) => /^[A-Z0-9]{3,4}$/.test(s))
        )
      )
        .sort()
        .join(","),
    [stations]
  );

  const refreshStatus = useCallback(async () => {
    if (!enabled) return null;
    const body = await getJson(`${base}/api/crew/navigraph/status`);
    setStatus(body);
    return body;
  }, [base, enabled]);

  const refreshNotams = useCallback(async () => {
    if (!enabled) {
      setNotams(null);
      return null;
    }
    if (!stationKey) {
      // No active flight yet — a declared state, not a failure.
      const body = {
        status: "no_stations",
        data: [],
        detail: "No flight selected — open a flight to load its NOTAMs.",
        summary: { total: 0, counts: {}, stations: [], station_count: 0 },
      };
      setNotams(body);
      return body;
    }
    const body = await getJson(
      `${base}/api/crew/navigraph/notams?icao=${encodeURIComponent(stationKey)}`
    );
    setNotams(body);
    return body;
  }, [base, enabled, stationKey]);

  const refreshRisks = useCallback(async () => {
    if (!enabled) return null;
    const body = await getJson(`${base}/api/crew/navigraph/risks`);
    setRisks(body);
    return body;
  }, [base, enabled]);

  const refreshNavdata = useCallback(async () => {
    if (!enabled) return null;
    const body = await getJson(`${base}/api/crew/navigraph/navdata`);
    setNavdata(body);
    return body;
  }, [base, enabled]);

  const refreshAll = useCallback(async () => {
    setBusy(true);
    try {
      await Promise.all([
        refreshStatus(),
        refreshNotams(),
        refreshRisks(),
        refreshNavdata(),
      ]);
    } finally {
      setBusy(false);
    }
  }, [refreshStatus, refreshNotams, refreshRisks, refreshNavdata]);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    (async () => {
      await refreshStatus();
      if (!cancelled) await refreshNavdata();
      if (!cancelled) await refreshRisks();
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshStatus, refreshNavdata, refreshRisks]);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    (async () => {
      if (!cancelled) await refreshNotams();
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled, stationKey, refreshNotams]);

  useEffect(() => {
    if (!enabled) return undefined;
    const id = setInterval(refreshRisks, RISK_REFRESH_MS);
    return () => clearInterval(id);
  }, [enabled, refreshRisks]);

  /* ── Device-flow sign-in (pilot uses their own Navigraph account) ──── */

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startSignIn = useCallback(async () => {
    stopPolling();
    setBusy(true);
    try {
      const resp = await fetch(`${base}/api/crew/navigraph/auth/device`, {
        method: "POST",
      });
      const body = await resp.json().catch(() => ({ status: "error" }));
      setSignIn(body);
      if (body.status !== "pending" || !body.user_code) return body;

      const intervalMs = Math.max(2, Number(body.interval) || 5) * 1000;
      pollRef.current = setInterval(async () => {
        const poll = await fetch(
          `${base}/api/crew/navigraph/auth/device/poll?user_code=${encodeURIComponent(
            body.user_code
          )}`,
          { method: "POST" }
        )
          .then((r) => r.json())
          .catch(() => ({ status: "error" }));
        setSignIn((prev) => ({ ...(prev || {}), ...poll }));
        if (poll.status !== "pending" && poll.status !== "slow_down") {
          stopPolling();
          if (poll.status === "authorized") await refreshAll();
        }
      }, intervalMs);
      return body;
    } finally {
      setBusy(false);
    }
  }, [base, refreshAll, stopPolling]);

  const signOut = useCallback(async () => {
    stopPolling();
    setSignIn(null);
    await fetch(`${base}/api/crew/navigraph/auth/signout`, { method: "POST" }).catch(
      () => {}
    );
    await refreshAll();
  }, [base, refreshAll, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]);

  return {
    status,
    notams,
    risks,
    navdata,
    busy,
    signIn,
    stations: stationKey ? stationKey.split(",") : [],
    refreshAll,
    refreshStatus,
    refreshNotams,
    refreshRisks,
    startSignIn,
    signOut,
  };
}
