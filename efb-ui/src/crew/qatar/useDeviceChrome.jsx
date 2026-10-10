/**
 * useDeviceChrome — the EFB's "device layer".
 *
 * One provider sits above every screen and owns what a real tablet's status
 * bar owns: brightness, airplane mode, Do Not Disturb, the Home action, the
 * per-screen Refresh action and the real device/network indicators.
 *
 * Why a context and not props: TopHeader is rendered by five independent
 * screens, and the status cluster has to be identical (and functional) on all
 * of them. Threading a dozen props through each screen is exactly how the
 * current decorative cluster came to exist.
 *
 * Screens rendered OUTSIDE the provider (component tests do this) fall back to
 * DEFAULT_CHROME: the cluster still renders, the indicators report UNKNOWN and
 * the actions are inert. No fabricated state, no crash.
 */
/* eslint-disable react-refresh/only-export-components -- context + provider + hooks, same convention as CrewPlatformContext.jsx */

// eslint-disable-next-line no-unused-vars -- classic JSX transform in component tests.
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  DEFAULT_PREFS,
  applyNotice,
  dismissNotice as dismissNoticeIn,
  loadPrefs,
  mapBatteryState,
  mapNetworkState,
  savePrefs,
} from "./deviceChrome.js";

const noop = () => {};

export const DEFAULT_CHROME = Object.freeze({
  prefs: DEFAULT_PREFS,
  battery: mapBatteryState(null),
  network: mapNetworkState({ online: undefined }),
  /** True while airplane mode suspends every outbound poll. */
  offline: false,
  screen: null,
  setBrightness: noop,
  setAirplaneMode: noop,
  setNotificationsOff: noop,
  goHome: null,
  goToSettings: null,
  registerRefresh: () => noop,
  refresh: async () => ({ ok: false, text: "No refresh handler on this screen." }),
  refreshState: null,
  notices: [],
  suppressedCount: 0,
  notify: noop,
  dismissNotice: noop,
  appVersion: null,
});

export const DeviceChromeContext = createContext(DEFAULT_CHROME);

export function useDeviceChrome() {
  return useContext(DeviceChromeContext);
}

/**
 * Register this screen's "re-fetch what is on screen now" handler. The handler
 * may return `{ ok, text }`; returning nothing counts as success.
 */
export function useScreenRefresh(handler, label) {
  const { registerRefresh } = useDeviceChrome();
  const ref = useRef(handler);
  useEffect(() => {
    // Latest-handler ref: the registered closure always calls what the screen
    // re-fetched most recently, without re-registering on every render.
    ref.current = handler;
  });
  useEffect(() => {
    if (!registerRefresh) return undefined;
    return registerRefresh((...args) => ref.current?.(...args), label);
  }, [registerRefresh, label]);
}

/* ── browser signal probes ──────────────────────────────────────────── */

function useBatterySignal() {
  const [raw, setRaw] = useState(null);

  useEffect(() => {
    const nav = typeof navigator !== "undefined" ? navigator : null;
    if (!nav || typeof nav.getBattery !== "function") return undefined;
    let cancelled = false;
    let manager = null;
    const sync = () => {
      if (cancelled || !manager) return;
      setRaw({ level: manager.level, charging: manager.charging });
    };
    nav
      .getBattery()
      .then((mgr) => {
        if (cancelled || !mgr) return;
        manager = mgr;
        sync();
        mgr.addEventListener?.("levelchange", sync);
        mgr.addEventListener?.("chargingchange", sync);
      })
      .catch(() => {
        /* permission policy may reject — stays UNKNOWN, never invented */
      });
    return () => {
      cancelled = true;
      manager?.removeEventListener?.("levelchange", sync);
      manager?.removeEventListener?.("chargingchange", sync);
    };
  }, []);

  return raw;
}

function useNetworkSignal() {
  const read = useCallback(() => {
    const nav = typeof navigator !== "undefined" ? navigator : null;
    if (!nav) return { online: undefined, effectiveType: null };
    const conn = nav.connection || nav.mozConnection || nav.webkitConnection || null;
    return {
      online: typeof nav.onLine === "boolean" ? nav.onLine : undefined,
      effectiveType: conn && typeof conn.effectiveType === "string" ? conn.effectiveType : null,
    };
  }, []);

  const [signal, setSignal] = useState(read);

  useEffect(() => {
    if (typeof window === "undefined" || typeof window.addEventListener !== "function") {
      return undefined;
    }
    const sync = () => setSignal(read());
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    const nav = typeof navigator !== "undefined" ? navigator : null;
    const conn = nav?.connection || nav?.mozConnection || nav?.webkitConnection || null;
    conn?.addEventListener?.("change", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
      conn?.removeEventListener?.("change", sync);
    };
  }, [read]);

  return signal;
}

/* ── provider ───────────────────────────────────────────────────────── */

/**
 * @param {object} props
 * @param {string} props.screen      current screen id (home | crewdesk | …)
 * @param {Function} props.onNavigate  screen setter from QatarShell
 * @param {string} props.appVersion  build version shown in the About entry
 */
export function DeviceChromeProvider({ screen, onNavigate, appVersion, children }) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const [refreshState, setRefreshState] = useState(null);
  const [noticeState, setNoticeState] = useState({ notices: [], suppressed: 0 });
  const refreshRef = useRef(null);
  const refreshLabelRef = useRef(null);

  const batteryRaw = useBatterySignal();
  const netSignal = useNetworkSignal();

  const battery = useMemo(() => mapBatteryState(batteryRaw), [batteryRaw]);
  const network = useMemo(
    () =>
      mapNetworkState({
        online: netSignal.online,
        effectiveType: netSignal.effectiveType,
        airplaneMode: prefs.airplaneMode,
      }),
    [netSignal.online, netSignal.effectiveType, prefs.airplaneMode]
  );

  const update = useCallback((patch) => {
    setPrefs((prev) => savePrefs({ ...prev, ...patch }));
  }, []);

  const setBrightness = useCallback((value) => update({ brightness: value }), [update]);
  const setNotificationsOff = useCallback(
    (value) => update({ notificationsOff: Boolean(value) }),
    [update]
  );

  const notify = useCallback(
    (notice) => {
      setNoticeState((prev) =>
        applyNotice(prev, notice, { notificationsOff: prefs.notificationsOff })
      );
    },
    [prefs.notificationsOff]
  );

  const dismiss = useCallback((id) => {
    setNoticeState((prev) => dismissNoticeIn(prev, id));
  }, []);

  const registerRefresh = useCallback((handler, label) => {
    refreshRef.current = handler;
    refreshLabelRef.current = label || null;
    return () => {
      if (refreshRef.current === handler) {
        refreshRef.current = null;
        refreshLabelRef.current = null;
      }
    };
  }, []);

  const refresh = useCallback(async () => {
    const handler = refreshRef.current;
    if (!handler) {
      const out = { ok: false, text: "Nothing to refresh on this screen." };
      setRefreshState({ ...out, busy: false, at: Date.now() });
      return out;
    }
    if (prefs.airplaneMode) {
      const out = {
        ok: false,
        text: "Airplane mode is on — turn it off to re-fetch live data.",
      };
      setRefreshState({ ...out, busy: false, at: Date.now() });
      notify({ kind: "warn", text: out.text });
      return out;
    }
    setRefreshState({ busy: true, ok: null, text: null, at: null });
    try {
      const result = (await handler()) || {};
      const ok = result.ok !== false;
      const label = refreshLabelRef.current;
      const out = {
        ok,
        text:
          result.text ||
          (ok ? `${label || "Screen"} data re-fetched.` : `${label || "Screen"} refresh failed.`),
      };
      setRefreshState({ ...out, busy: false, at: Date.now() });
      notify({ kind: ok ? "info" : "error", text: out.text });
      return out;
    } catch (e) {
      const out = { ok: false, text: `Refresh failed (${String(e?.message || e)}).` };
      setRefreshState({ ...out, busy: false, at: Date.now() });
      notify({ kind: "error", text: out.text });
      return out;
    }
  }, [notify, prefs.airplaneMode]);

  // Airplane mode off → the polls resume on their own (the hooks re-enable),
  // and the current screen is re-fetched once so the crew does not sit on
  // data frozen at the moment they went offline.
  const setAirplaneMode = useCallback(
    (value) => {
      const next = Boolean(value);
      update({ airplaneMode: next });
      if (next) {
        setNoticeState((prev) =>
          applyNotice(prev, {
            kind: "warn",
            text: "Airplane mode on — sim telemetry, weather, SimBrief and Navigraph polling suspended.",
          }, { notificationsOff: prefs.notificationsOff })
        );
      } else {
        setNoticeState((prev) =>
          applyNotice(prev, {
            kind: "info",
            text: "Airplane mode off — live polling resumed.",
          }, { notificationsOff: prefs.notificationsOff })
        );
        const handler = refreshRef.current;
        if (handler) Promise.resolve(handler()).catch(() => {});
      }
    },
    [prefs.notificationsOff, update]
  );

  const goHome = useCallback(() => onNavigate?.("home"), [onNavigate]);
  const goToSettings = useCallback(() => onNavigate?.("settings"), [onNavigate]);

  const value = useMemo(
    () => ({
      prefs,
      battery,
      network,
      offline: prefs.airplaneMode,
      screen: screen || null,
      setBrightness,
      setAirplaneMode,
      setNotificationsOff,
      goHome: onNavigate ? goHome : null,
      goToSettings: onNavigate ? goToSettings : null,
      registerRefresh,
      refresh,
      refreshState,
      notices: noticeState.notices,
      suppressedCount: noticeState.suppressed,
      notify,
      dismissNotice: dismiss,
      appVersion: appVersion || null,
    }),
    [
      appVersion,
      battery,
      dismiss,
      goHome,
      goToSettings,
      network,
      noticeState.notices,
      noticeState.suppressed,
      notify,
      onNavigate,
      prefs,
      refresh,
      refreshState,
      registerRefresh,
      screen,
      setAirplaneMode,
      setBrightness,
      setNotificationsOff,
    ]
  );

  return (
    <DeviceChromeContext.Provider value={value}>{children}</DeviceChromeContext.Provider>
  );
}
