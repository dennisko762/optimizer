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
  LogIn,
  ChevronRight,
  ExternalLink,
  Wrench,
} from "lucide-react";
import BoardingPanel from "./BoardingPanel.jsx";
import TechPanel, { TechStatusCard } from "./tech/TechPanel.jsx";

const TILES = [
  { id: "edesk", label: "eDesk", icon: ClipboardCheck, active: true },
  { id: "flight", label: "Flight / OFP", icon: BookOpen, active: true },
  { id: "optimizer", label: "CI Optimizer", icon: Gauge, active: true },
  { id: "setup", label: "Setup", icon: Settings, active: true },
  // Roadmap — disabled
  { id: "mail", label: "Mail & Notifications", icon: Mail, active: true },
  { id: "boarding", label: "Boarding", icon: Users, active: true },
  { id: "tech", label: "Tech", icon: Wrench, active: true },
  { id: "charts", label: "Charts", icon: Map, active: false, roadmap: true },
  { id: "flysmart", label: "FlySmart / Map", icon: Plane, active: false, roadmap: true },
  { id: "flightlog", label: "Flight Log", icon: FileText, active: false, roadmap: true },
  { id: "gsx", label: "GSX Remote", icon: MonitorSmartphone, active: false, roadmap: true },
];

export default function CrewShell({ onOpenOptimizer }) {
  const { selectedProvider, session, configReady, loading, error, startAuth, logout } =
    useCrewPlatform();
  const [currentTile, setCurrentTile] = useState("edesk");
  const [selectedFlight, setSelectedFlight] = useState(null);

  if (!selectedProvider) return null;

  function handleTileClick(tile) {
    if (!tile.active) return;
    // Only the CI Optimizer tile opens the optimizer. Flight / OFP shows the
    // pilot's SimBrief OFP for the selected flight — not the optimizer.
    if (tile.id === "optimizer") {
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

        {session?.authenticated ? (
          <div className="crew-sidebar__session">
            <div className="crew-sidebar__pilot">
              {session.display_name || session.callsign || "Pilot"}
            </div>
            <div className="crew-sidebar__crew-id">
              {session.crew_id ? `ID: ${session.crew_id}` : session.local ? "Local session" : ""}
            </div>
            <button className="crew-logout-btn" onClick={logout}>
              <LogOut size={14} />
              Sign Out
            </button>
          </div>
        ) : configReady?.ready ? (
          <div className="crew-sidebar__session">
            <button className="crew-signin-btn" onClick={startAuth} disabled={loading}>
              <LogIn size={14} />
              {loading ? "Connecting…" : "Sign in with vAMSYS"}
            </button>
            {error && <div className="crew-sidebar__error">{error}</div>}
          </div>
        ) : null}
      </nav>

      {/* Main content area */}
      <main className="crew-main">
        {currentTile === "edesk" && (
          <EDeskPanel
            selectedFlight={selectedFlight}
            onSelectFlight={setSelectedFlight}
            onOpenTech={() => setCurrentTile("tech")}
          />
        )}
        {currentTile === "flight" && (
          <FlightOFPPanel
            selectedFlight={selectedFlight}
            onOpenOptimizer={onOpenOptimizer}
          />
        )}
        {currentTile === "setup" && <SetupPanel />}
        {currentTile === "mail" && <NotificationPanel />}
        {currentTile === "boarding" && (
          <BoardingPanel
            flightId={selectedFlight?.flight_id || "default"}
            onClose={() => setCurrentTile("edesk")}
          />
        )}
        {currentTile === "tech" && <TechPanel />}
      </main>
    </div>
  );
}

/* ─── eDesk Panel ─────────────────────────────────────────────────── */

function EDeskPanel({ selectedFlight, onSelectFlight, onOpenTech }) {
  const { session, selectedProvider, apiBase } = useCrewPlatform();
  const [flights, setFlights] = useState([]);
  const [checkinResult, setCheckinResult] = useState(null);
  const [loadingFlights, setLoadingFlights] = useState(false);
  const [remoteCheckinLoading, setRemoteCheckinLoading] = useState(false);
  const [remoteCheckinError, setRemoteCheckinError] = useState(null);
  const [manual, setManual] = useState({
    flight_number: "",
    departure_icao: "",
    arrival_icao: "",
    aircraft_icao: "",
    callsign: "",
  });

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

  async function doManualCheckin() {
    if (!session?.authenticated) return;
    const flightId =
      `${(manual.departure_icao || "").toUpperCase()}`.trim() +
      "-" +
      `${(manual.arrival_icao || "").toUpperCase()}`.trim() +
      "-" +
      (manual.callsign || manual.flight_number || "LOCAL").trim();
    const flight = {
      flight_id: flightId,
      flight_number: manual.flight_number || null,
      departure_icao: manual.departure_icao,
      arrival_icao: manual.arrival_icao,
      aircraft_icao: manual.aircraft_icao,
      callsign: manual.callsign,
    };
    onSelectFlight(flight);
    setCheckinResult(null);
    try {
      const resp = await fetch(`${apiBase}/api/crew/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          flight_id: flightId,
          flight_number: flight.flight_number,
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

  function setManualField(field, value) {
    setManual((prev) => ({ ...prev, [field]: value }));
  }

  // Remote check-in via the documented v3 write path (POST /dispatch-url).
  // Opens the Phoenix dispatch URL in a new tab; the pilot completes the
  // dispatch form there. Requires the flights:write scope on the client.
  async function doRemoteCheckin(flight) {
    if (!session?.authenticated) return;
    setRemoteCheckinLoading(true);
    setRemoteCheckinError(null);
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/session/${session.session_id}/flights/${flight.flight_id}/dispatch`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
      );
      if (resp.ok) {
        const data = await resp.json();
        if (data?.dispatch_url) window.open(data.dispatch_url, "_blank", "noopener");
      } else {
        const body = await resp.text();
        setRemoteCheckinError(`Remote check-in failed: ${body}`);
      }
    } catch (e) {
      setRemoteCheckinError(`Remote check-in failed: ${e.message || e}`);
    } finally {
      setRemoteCheckinLoading(false);
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

      {/* Aircraft technical status (TechLog P1-T6) */}
      <TechStatusCard onOpenTech={onOpenTech} />

      {!session?.authenticated && (
        <div className="edesk-notice">
          Sign in through vAMSYS to view your flights and check in.
        </div>
      )}

      {/* Local mode: manual flight entry (no vAMSYS flight list) */}
      {session?.authenticated && session.local && (
        <div className="edesk-manual">
          <h3>Local Mode — Enter Flight</h3>
          <div className="edesk-manual-grid">
            <label>
              <span>Flight No.</span>
              <input
                value={manual.flight_number}
                onChange={(e) => setManualField("flight_number", e.target.value)}
                placeholder="LH2024"
              />
            </label>
            <label>
              <span>Dep ICAO</span>
              <input
                value={manual.departure_icao}
                onChange={(e) => setManualField("departure_icao", e.target.value.toUpperCase())}
                placeholder="EDDF"
                maxLength={4}
              />
            </label>
            <label>
              <span>Arr ICAO</span>
              <input
                value={manual.arrival_icao}
                onChange={(e) => setManualField("arrival_icao", e.target.value.toUpperCase())}
                placeholder="LEPA"
                maxLength={4}
              />
            </label>
            <label>
              <span>Aircraft</span>
              <input
                value={manual.aircraft_icao}
                onChange={(e) => setManualField("aircraft_icao", e.target.value.toUpperCase())}
                placeholder="A345"
              />
            </label>
            <label>
              <span>Callsign</span>
              <input
                value={manual.callsign}
                onChange={(e) => setManualField("callsign", e.target.value.toUpperCase())}
                placeholder="DLH2024"
              />
            </label>
          </div>
          <button
            className="edesk-checkin-btn"
            onClick={doManualCheckin}
            disabled={
              !manual.departure_icao || !manual.arrival_icao
            }
          >
            Validate &amp; Check In (Local)
          </button>
          <p className="edesk-notice">
            vAMSYS pilot login is not configured — flights are entered manually.
            Boarding and weights work with this session.
          </p>
        </div>
      )}

      {/* Flights */}
      {session?.authenticated && !session.local && (
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
          <div style={{ display: "flex", gap: "0.5rem", marginBottom: "0.5rem", flexWrap: "wrap" }}>
            <button className="edesk-checkin-btn" onClick={() => doCheckin(selectedFlight)}>
              Validate &amp; Check In
            </button>
            {session?.authenticated && !session.local && (
              <button
                className="edesk-checkin-btn edesk-checkin-btn--secondary"
                onClick={() => doRemoteCheckin(selectedFlight)}
                disabled={remoteCheckinLoading}
              >
                {remoteCheckinLoading ? "Opening dispatch…" : "Remote Check-In (Phoenix)"}
              </button>
            )}
          </div>
          {remoteCheckinError && <div className="edesk-error">{remoteCheckinError}</div>}

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

/* ─── Flight / OFP Panel ──────────────────────────────────────────── */

/**
 * Shows the SimBrief OFP for the eDesk-selected flight (not the optimizer).
 * For vAMSYS sessions the OFP is fetched from the booking's linked
 * SimbriefOfpData; for local sessions the manually entered flight data is
 * shown. The CI Optimizer remains reachable via an explicit button.
 */
function FlightOFPPanel({ selectedFlight, onOpenOptimizer }) {
  const { session, apiBase } = useCrewPlatform();
  const [ofp, setOfp] = useState(null);
  const [ofpError, setOfpError] = useState(null);
  const [loading, setLoading] = useState(false);

  const flightId = selectedFlight?.flight_id;
  const isLocal = Boolean(session?.local);

  async function loadOfp() {
    if (!session?.authenticated || !flightId || isLocal) return;
    setLoading(true);
    setOfpError(null);
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/session/${session.session_id}/flights/${flightId}/ofp`
      );
      if (resp.status === 404) {
        setOfp(null);
        setOfpError("No SimBrief OFP is linked to this booking yet.");
      } else if (resp.ok) {
        setOfp(await resp.json());
      } else {
        setOfp(null);
        setOfpError(await resp.text());
      }
    } catch (e) {
      setOfp(null);
      setOfpError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  if (!selectedFlight) {
    return (
      <div className="edesk-panel">
        <div className="edesk-header">
          <h2>Flight / OFP</h2>
        </div>
        <div className="edesk-notice">
          Select a flight in eDesk to see its SimBrief OFP.
        </div>
      </div>
    );
  }

  const ofpData = ofp?.ofp_data || {};
  const general = ofpData.general || {};

  return (
    <div className="edesk-panel">
      <div className="edesk-header">
        <h2>Flight / OFP</h2>
        <span className="edesk-provider-badge">
          {selectedFlight.flight_number || selectedFlight.callsign || "—"}
        </span>
      </div>

      <div className="ofp-route">
        <div className="ofp-route__ap">
          <span className="ofp-route__icao">{selectedFlight.departure_icao || "—"}</span>
          <span className="ofp-route__label">Departure</span>
        </div>
        <div className="ofp-route__leg">
          <span className="ofp-route__dist">{general.route_distance_nm ? `${general.route_distance_nm} nm` : ""}</span>
        </div>
        <div className="ofp-route__ap">
          <span className="ofp-route__icao">{selectedFlight.arrival_icao || "—"}</span>
          <span className="ofp-route__label">Arrival</span>
        </div>
      </div>

      {!isLocal && session?.authenticated && (
        <div style={{ display: "flex", gap: "0.5rem", marginBottom: "1rem" }}>
          <button className="edesk-btn-small" onClick={loadOfp} disabled={loading}>
            {loading ? "Loading…" : "Load SimBrief OFP"}
          </button>
        </div>
      )}

      {ofpError && <div className="edesk-notice">{ofpError}</div>}

      <div className="ofp-grid">
        <OfpItem label="Aircraft" value={general.aircraft_icao || selectedFlight.aircraft_icao} />
        <OfpItem label="Callsign" value={selectedFlight.callsign} />
        <OfpItem label="Pax" value={selectedFlight.passengers ?? general.passengers} />
        <OfpItem label="Cargo" value={selectedFlight.cargo ?? general.cargo} />
        <OfpItem label="Altitude" value={selectedFlight.altitude ? `${selectedFlight.altitude} ft` : general.altitude} />
        <OfpItem label="Cost Index" value={selectedFlight.cost_index} />
        <OfpItem label="Network" value={selectedFlight.network} />
        <OfpItem label="SIBT" value={general.sibt || selectedFlight.scheduled_departure_utc} />
        {general.icao_airline && <OfpItem label="OFP Airline" value={general.icao_airline} />}
        {general.flight_no && <OfpItem label="OFP Flight" value={general.flight_no} />}
      </div>

      {ofp?.pdf_url && (
        <a className="ofp-pdf-link" href={ofp.pdf_url} target="_blank" rel="noopener noreferrer">
          <ExternalLink size={14} /> OFP PDF ({ofp.created_at})
        </a>
      )}

      <div className="ofp-actions">
        <button className="edesk-checkin-btn" onClick={() => onOpenOptimizer?.(selectedFlight)}>
          Open in CI Optimizer
        </button>
        <span className="edesk-notice">
          {isLocal
            ? "Local session — showing the manually entered flight data."
            : "OFP data comes from the SimBrief file linked to the vAMSYS booking."}
        </span>
      </div>
    </div>
  );
}

function OfpItem({ label, value }) {
  return (
    <div className="ofp-item">
      <span className="ofp-item__label">{label}</span>
      <strong className="ofp-item__value">{value ?? "—"}</strong>
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
            <ConfigItem label="Redirect URI is HTTPS (v3 spec)" set={configReady.vamsys_redirect_uri_https} />
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
