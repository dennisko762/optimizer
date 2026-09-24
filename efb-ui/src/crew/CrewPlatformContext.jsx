/**
 * Crew Platform context — provider/theme state, session, API calls.
 *
 * The theme engine applies CSS variables from the selected provider
 * without page reload. No flight/optimization logic branches on theme values.
 */

/* eslint-disable react-refresh/only-export-components */
import { createContext, useState, useEffect, useCallback } from "react";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

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
 * Default (neutral) theme applied before provider selection.
 */
const DEFAULT_THEME = {
  "--airline-primary": "#334155",
  "--airline-accent": "#64748b",
  "--airline-bg": "#0a0e14",
  "--airline-surface": "#111820",
  "--airline-text": "#e8ecf0",
  "--airline-text-secondary": "#a0a0a0",
};

export function CrewPlatformProvider({ children }) {
  const [providers, setProviders] = useState([]);
  const [selectedProvider, setSelectedProvider] = useState(null);
  const [session, setSession] = useState(null);
  const [configReady, setConfigReady] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch providers on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/crew/providers`)
      .then((r) => (r.ok ? r.json() : []))
      .then(setProviders)
      .catch(() => setProviders([]));
  }, []);

  // Fetch config readiness on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/crew/config/readiness`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setConfigReady)
      .catch(() => setConfigReady(null));
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
      }
    },
    [providers]
  );

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
      // Open vAMSYS consent in new tab (no password collected)
      window.open(data.authorize_url, "_blank", "noopener");
      setSession({ session_id: data.session_id, authenticated: false });
    } catch (e) {
      setError(e.message);
    } finally {
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
