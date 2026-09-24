/**
 * PWA launcher shell — the main tablet interface.
 *
 * Tiles:
 * - eDesk (active)
 * - Flight / OFP (active — delegates to existing optimizer)
 * - CI Optimizer (active — delegates to existing optimizer)
 * - Setup (active)
 *
 * Future tiles visible as disabled roadmap entries:
 * - Mail & Notifications
 * - Boarding
 * - Charts
 * - FlySmart / moving map
 * - Flight Log
 * - GSX Remote
 */

import { useState } from "react";
import { useCrewPlatform } from "./useCrewPlatform.js";
import {
  Plane,
  Gauge,
  Settings,
  ClipboardCheck,
  BookOpen,
  Mail,
  Users,
  Map,
  FileText,
  MonitorSmartphone,
  LogOut,
  ChevronRight,
} from "lucide-react";
import BoardingPanel from "./BoardingPanel.jsx";

const TILES = [
  { id: "edesk", label: "eDesk", icon: ClipboardCheck, active: true },
  { id: "flight", label: "Flight / OFP", icon: BookOpen, active: true },
  { id: "optimizer", label: "CI Optimizer", icon: Gauge, active: true },
  { id: "setup", label: "Setup", icon: Settings, active: true },
  // Roadmap — disabled
  { id: "mail", label: "Mail & Notifications", icon: Mail, active: true },
  { id: "boarding", label: "Boarding", icon: Users, active: true },
  { id: "charts", label: "Charts", icon: Map, active: false, roadmap: true },
  { id: "flysmart", label: "FlySmart / Map", icon: Plane, active: false, roadmap: true },
  { id: "flightlog", label: "Flight Log", icon: FileText, active: false, roadmap: true },
  { id: "gsx", label: "GSX Remote", icon: MonitorSmartphone, active: false, roadmap: true },
];

export default function CrewShell({ onOpenOptimizer }) {
  const { selectedProvider, session, logout } = useCrewPlatform();
  const [currentTile, setCurrentTile] = useState("edesk");
  const [selectedFlight, setSelectedFlight] = useState(null);

  if (!selectedProvider) return null;

  function handleTileClick(tile) {
    if (!tile.active) return;
    if (tile.id === "optimizer" || tile.id === "flight") {
      // Hand the eDesk-selected flight to the optimizer so it opens with
      // the OFP context (route, number, aircraft, airline) pre-filled.
      onOpenOptimizer?.(selectedFlight);
      return;
    }
    setCurrentTile(tile.id);
  }

  return (
    <div
      className="crew-shell"
      style={{
        "--provider-primary": selectedProvider.theme["--airline-primary"],
        "--provider-accent": selectedProvider.theme["--airline-accent"],
      }}
    >
      {/* Sidebar */}
      <nav className="crew-sidebar">
        <div className="crew-sidebar__brand">
          <Plane size={20} style={{ color: "var(--provider-accent)" }} />
          <div>
            <div className="crew-sidebar__airline">{selectedProvider.display_name}</div>
            <div className="crew-sidebar__sub">Crew Operations</div>
          </div>
        </div>

        <div className="crew-sidebar__tiles">
          {TILES.map((tile) => {
            const Icon = tile.icon;
            return (
              <button
                key={tile.id}
                className={`crew-tile-btn ${currentTile === tile.id ? "crew-tile-btn--active" : ""} ${!tile.active ? "crew-tile-btn--disabled" : ""}`}
                onClick={() => handleTileClick(tile)}
                disabled={!tile.active}
              >
                <Icon size={16} />
                <span>{tile.label}</span>
                {tile.roadmap && <span className="crew-tile-roadmap">Soon</span>}
                {tile.active && <ChevronRight size={14} className="crew-tile-arrow" />}
              </button>
            );
          })}
        </div>

        {session?.authenticated && (
          <div className="crew-sidebar__session">
            <div className="crew-sidebar__pilot">
              {session.display_name || session.callsign || "Pilot"}
            </div>
            <div className="crew-sidebar__crew-id">
              {session.crew_id ? `ID: ${session.crew_id}` : ""}
            </div>
            <button className="crew-logout-btn" onClick={logout}>
              <LogOut size={14} />
              Sign Out
            </button>
          </div>
        )}
      </nav>

      {/* Main content area */}
      <main className="crew-main">
        {currentTile === "edesk" && (
          <EDeskPanel selectedFlight={selectedFlight} onSelectFlight={setSelectedFlight} />
        )}
        {currentTile === "setup" && <SetupPanel />}
        {currentTile === "mail" && <NotificationPanel />}
        {currentTile === "boarding" && (
          <BoardingPanel
            flightId={selectedFlight?.flight_id || "default"}
            onClose={() => setCurrentTile("edesk")}
          />
        )}
      </main>
    </div>
  );
}

/* ─── eDesk Panel ─────────────────────────────────────────────────── */

function EDeskPanel({ selectedFlight, onSelectFlight }) {
  const { session, selectedProvider, apiBase } = useCrewPlatform();
  const [flights, setFlights] = useState([]);
  const [checkinResult, setCheckinResult] = useState(null);
  const [loadingFlights, setLoadingFlights] = useState(false);

  async function loadFlights() {
    if (!session?.authenticated) return;
    setLoadingFlights(true);
    try {
      const resp = await fetch(`${apiBase}/api/crew/session/${session.session_id}/flights`);
      if (resp.ok) setFlights(await resp.json());
    } catch {
      // pass
    } finally {
      setLoadingFlights(false);
    }
  }

  async function doCheckin(flight) {
    if (!session?.authenticated) return;
    try {
      const resp = await fetch(`${apiBase}/api/crew/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          flight_id: flight.flight_id,
          simbrief_departure: flight.departure_icao,
          simbrief_arrival: flight.arrival_icao,
          simbrief_callsign: flight.callsign,
          simbrief_aircraft: flight.aircraft_icao,
        }),
      });
      if (resp.ok) setCheckinResult(await resp.json());
    } catch {
      // pass
    }
  }

  return (
    <div className="edesk-panel">
      <div className="edesk-header">
        <h2>eDesk</h2>
        <span className="edesk-provider-badge">{selectedProvider?.short_code}</span>
      </div>

      {/* Pilot identity */}
      {session?.authenticated && (
        <div className="edesk-identity">
          <div className="edesk-id-row">
            <span>Pilot</span>
            <strong>{session.display_name || session.callsign || "—"}</strong>
          </div>
          <div className="edesk-id-row">
            <span>Crew ID</span>
            <strong>{session.crew_id || "—"}</strong>
          </div>
          <div className="edesk-id-row">
            <span>Rank</span>
            <strong>{session.rank || "—"}</strong>
          </div>
        </div>
      )}

      {!session?.authenticated && (
        <div className="edesk-notice">
          Sign in through vAMSYS to view your flights and check in.
        </div>
      )}

      {/* Flights */}
      {session?.authenticated && (
        <div className="edesk-flights">
          <div className="edesk-section-header">
            <h3>Flights</h3>
            <button onClick={loadFlights} disabled={loadingFlights} className="edesk-btn-small">
              {loadingFlights ? "Loading..." : "Refresh"}
            </button>
          </div>
          {flights.length === 0 && <div className="edesk-notice">No flights loaded yet.</div>}
          {flights.map((f) => (
            <div
              key={f.flight_id}
              className={`edesk-flight-card ${selectedFlight?.flight_id === f.flight_id ? "edesk-flight-card--selected" : ""}`}
              onClick={() => onSelectFlight?.(f)}
            >
              <div className="edesk-flight-number">{f.flight_number || f.callsign || "—"}</div>
              <div className="edesk-flight-route">
                {f.departure_icao || "?"} → {f.arrival_icao || "?"}
              </div>
              <div className="edesk-flight-acft">{f.aircraft_icao || "—"}</div>
              <div className="edesk-flight-status">{f.status || "—"}</div>
            </div>
          ))}
        </div>
      )}

      {/* Check-in */}
      {selectedFlight && (
        <div className="edesk-checkin">
          <h3>Check-In: {selectedFlight.flight_number || selectedFlight.callsign}</h3>
          <button className="edesk-checkin-btn" onClick={() => doCheckin(selectedFlight)}>
            Validate & Check In
          </button>

          {checkinResult && (
            <div className={`edesk-checkin-result ${checkinResult.valid ? "edesk-checkin-result--ok" : "edesk-checkin-result--fail"}`}>
              <div>{checkinResult.valid ? "✓ Check-in valid" : "✗ Check-in failed"}</div>
              {checkinResult.errors.map((e, i) => (
                <div key={i} className="edesk-error">• {e}</div>
              ))}
              {checkinResult.warnings.map((w, i) => (
                <div key={i} className="edesk-warning">• {w}</div>
              ))}
              <div className="edesk-remote-status">
                Remote check-in: {checkinResult.remote_checkin_status}
                <br />
                <small>{checkinResult.remote_checkin_reason}</small>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Setup Panel ─────────────────────────────────────────────────── */

function SetupPanel() {
  const { configReady, selectedProvider } = useCrewPlatform();

  return (
    <div className="setup-panel">
      <h2>Setup</h2>

      <div className="setup-section">
        <h3>Configuration Readiness</h3>
        {configReady ? (
          <div className="setup-config-grid">
            <ConfigItem label="vAMSYS Client ID" set={configReady.vamsys_client_id_set} />
            <ConfigItem label="vAMSYS Redirect URI" set={configReady.vamsys_redirect_uri_set} />
            <ConfigItem label="Session Secret" set={configReady.session_secret_set} />
            <div className={`setup-readiness ${configReady.ready ? "setup-readiness--ok" : "setup-readiness--warn"}`}>
              {configReady.ready ? "✓ All configured" : "⚠ Missing configuration"}
            </div>
          </div>
        ) : (
          <div className="edesk-notice">Could not check configuration.</div>
        )}
      </div>

      <div className="setup-section">
        <h3>Active Provider</h3>
        <div className="setup-provider-info">
          <div>{selectedProvider?.display_name || "None selected"}</div>
          <div>ICAO: {selectedProvider?.icao || "—"}</div>
          <div>Short: {selectedProvider?.short_code || "—"}</div>
        </div>
      </div>
    </div>
  );
}

function ConfigItem({ label, set }) {
  return (
    <div className="setup-config-item">
      <span className={set ? "config-ok" : "config-missing"}>{set ? "✓" : "✗"}</span>
      <span>{label}</span>
      <span className="config-note">{set ? "Set (value hidden)" : "Not configured"}</span>
    </div>
  );
}

/* ─── Notification Panel ──────────────────────────────────────────── */

function NotificationPanel() {
  const { session, apiBase } = useCrewPlatform();
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(false);

  async function loadNotifications() {
    if (!session?.session_id) return;
    setLoading(true);
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/notifications?session_id=${session.session_id}`
      );
      if (resp.ok) setNotifications(await resp.json());
    } catch {
      // pass
    } finally {
      setLoading(false);
    }
  }

  async function clearAll() {
    if (!session?.session_id) return;
    try {
      await fetch(
        `${apiBase}/api/crew/notifications/clear?session_id=${session.session_id}`,
        { method: "POST" }
      );
      setNotifications([]);
    } catch {
      // pass
    }
  }

  return (
    <div className="edesk-panel">
      <div className="edesk-header">
        <h2>Mail & Notifications</h2>
      </div>
      <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
        <button className="edesk-btn-small" onClick={loadNotifications} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
        {notifications.length > 0 && (
          <button className="edesk-btn-small" onClick={clearAll}>Clear All</button>
        )}
      </div>

      {notifications.length === 0 && !loading && (
        <div className="edesk-notice">No operations notifications yet. Weather changes for your selected flight will appear here.</div>
      )}

      {notifications.map((n) => {
        const icon = n.type === "visibility" ? "👁" : n.type === "wind_direction" ? "🧭" : n.type === "wind_speed" ? "💨" : n.type === "temperature" ? "🌡" : "📋";
        const relTime = formatRelativeTimestamp(n.timestamp);
        return (
          <div key={n.id} className="edesk-flight-card" style={{ marginBottom: "0.5rem" }}>
            <div className="edesk-flight-number">
              <span>{icon}</span>{" "}
              <span>{n.icao}</span>
              <span className="edesk-flight-status" style={{ marginLeft: "auto" }}>{relTime}</span>
            </div>
            <div className="edesk-flight-route">{n.summary}</div>
            <div className="edesk-flight-acft" style={{ opacity: 0.6 }}>
              {n.provenance}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Format a unix timestamp (seconds) as a relative time string. */
function formatRelativeTimestamp(timestamp) {
  if (typeof timestamp !== "number") return "";
  const diff = Math.max(0, Date.now() / 1000 - timestamp);
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
