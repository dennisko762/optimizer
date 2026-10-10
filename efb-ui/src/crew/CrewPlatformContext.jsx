/**
 * Crew Platform context — provider/theme state, session, API calls.
 *
 * The theme engine applies CSS variables from the selected provider
 * without page reload. No flight/optimization logic branches on theme values.
 */

/* eslint-disable react-refresh/only-export-components */
import { createContext, useState, useEffect, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

/** localStorage key for the selected airline (survives a reload — G9). */
export const PROVIDER_STORAGE_KEY = "qr.crew.provider.v1";

export const CrewContext = createContext(null);

/**
 * Apply theme CSS variables to document root without reload.
 */
function applyTheme(themeVars) {
  const root = document.documentElement;
  for (const [key, value] of Object.entries(themeVars)) {
    root.style.setProperty(key, value);
  }
}

/**
 * Default theme applied before provider selection.
 *
 * DESIGN.md: the product's own brand surface is the QR SmartOps maroon, so
 * the provider gate opens in maroon rather than a neutral navy/slate (G9).
 */
const DEFAULT_THEME = {
  "--airline-primary": "#5c1a2e",
  "--airline-accent": "#e8a838",
  "--airline-bg": "#1a0a12",
  "--airline-surface": "#2a1019",
  "--airline-text": "#f6edf0",
  "--airline-text-secondary": "#c9a3ad",
};

export function CrewPlatformProvider({ children }) {
  const [providers, setProviders] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [session, setSession] = useState(null);
  const [configReady, setConfigReady] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch providers on mount. The previously chosen airline is restored in
  // the same async callback: the crew session already survives a reload
  // (crewAuth localStorage) but the airline choice did not, so every reload
  // dropped back to the selector (G9).
  useEffect(() => {
    fetch(`${API_BASE}/api/crew/providers`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        const providerList = Array.isArray(list) ? list : [];
        setProviders(providerList);
        let storedId = null;
        try {
          storedId = window.localStorage.getItem(PROVIDER_STORAGE_KEY);
        } catch {
          storedId = null;
        }
        if (!storedId) return;
        const provider = providerList.find((p) => p.id === storedId);
        if (provider) setSelectedProvider(provider);
      })
      .catch(() => setProviders([]));
  }, []);

  // Fetch config readiness on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/crew/config/readiness`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setConfigReady)
      .catch(() => setConfigReady(null));
  }, []);

  // On OAuth return, the app is reloaded at /?crew_session=<id>&provider=<id>.
  // Re-adopt that session (and the provider, for theme) instead of the
  // auto-local fallback.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const sessionId = params.get("crew_session");
    const providerId = params.get("provider");
    if (!sessionId) return;
    let cancelled = false;
    if (providerId) {
      fetch(`${API_BASE}/api/crew/providers`)
        .then((r) => (r.ok ? r.json() : []))
        .then((list) => {
          if (cancelled) return;
          const provider = list.find((p) => p.id === providerId);
          if (provider) setSelectedProvider(provider);
        })
        .catch(() => {});
    }
    fetch(`${API_BASE}/api/crew/session/${sessionId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) setSession(data);
      })
      .catch(() => {});
    window.history.replaceState({}, "", "/");
    return () => {
      cancelled = true;
    };
  }, []);

  // Apply theme when provider changes (no reload)
  useEffect(() => {
    if (selectedProvider?.theme) {
      applyTheme(selectedProvider.theme);
    } else {
      applyTheme(DEFAULT_THEME);
    }
  }, [selectedProvider]);

  const selectProvider = useCallback(
    (providerId) => {
      const provider = providers.find((p) => p.id === providerId);
      if (provider) {
        setSelectedProvider(provider);
        setSession(null);
        setError(null);
        try {
          window.localStorage.setItem(PROVIDER_STORAGE_KEY, provider.id);
        } catch {
          /* storage unavailable (private mode) — selection still applies */
        }
      }
    },
    [providers]
  );

  // When vAMSYS is not configured, automatically create a local (offline)
  // session for the selected provider so boarding / local check-in works
  // without OAuth. When vAMSYS IS configured, keep the OAuth flow.
  useEffect(() => {
    if (!selectedProvider) return;
    if (configReady?.ready) return; // vAMSYS available → OAuth flow
    let cancelled = false;
    fetch(`${API_BASE}/api/crew/session/local`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        provider_id: selectedProvider.id,
        display_name: "Local Pilot",
      }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!cancelled && data) {
          setSession({ ...data, authenticated: true });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [selectedProvider, configReady?.ready]);

  const startAuth = useCallback(async () => {
    if (!selectedProvider) return;
    setLoading(true);
    setError(null);
    try {
      const resp = await fetch(
        `${API_BASE}/api/crew/auth/start?provider_id=${selectedProvider.id}`,
        { method: "POST" }
      );
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      // Same-tab redirect to the vAMSYS consent page. The pilot signs in
      // there; vAMSYS redirects back to VAMSYS_REDIRECT_URI, where the server
      // exchanges the code and bounces to /?crew_session=… which the app
      // re-adopts (no session id survives a page navigation).
      window.location.assign(data.authorize_url);
    } catch (e) {
      setError(e.message);
      setLoading(false);
    }
  }, [selectedProvider]);

  const completeAuth = useCallback(
    async (code, state) => {
      if (!session) return;
      setLoading(true);
      try {
        const resp = await fetch(`${API_BASE}/api/crew/auth/callback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            code,
            state,
            session_id: session.session_id,
          }),
        });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        setSession(data);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [session]
  );

  const logout = useCallback(async () => {
    if (!session) return;
    try {
      await fetch(`${API_BASE}/api/crew/session/${session.session_id}/logout`, {
        method: "POST",
      });
    } catch {
      // best effort
    }
    setSession(null);
  }, [session]);

  const value = {
    providers,
    selectedProvider,
    selectProvider,
    session,
    configReady,
    loading,
    error,
    startAuth,
    completeAuth,
    logout,
    apiBase: API_BASE,
  };

  return <CrewContext.Provider value={value}>{children}</CrewContext.Provider>;
}
