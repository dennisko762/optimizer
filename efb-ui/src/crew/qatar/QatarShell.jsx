/**
 * QatarShell — the "QR SmartOps" iPad interface (Qatar maroon look, 05.10.).
 *
 * Screens: Crew Desk (My Flights / Crew Desk / Profile), Flightplan,
 * Route, EDTO + Risks. Legacy functionality is mapped in, not lost:
 * eDesk check-in → My Flights, OFP → Flightplan, Notifications → Crew Desk
 * Inbox, Boarding → My Flights action, Tech → Crew Desk "Tech Log" tab,
 * Setup → Profile, CI Optimizer → gold "Import New Plan" action.
 *
 * All styling is in qatar.css via the QR maroon palette — this file carries
 * no hardcoded brand colors.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Plane,
  PlaneTakeoff,
  AlertTriangle,
  Inbox,
  Trash2,
  ClipboardCheck,
  Cloud,
  Sun,
  RefreshCw,
  MoreVertical,
  BatteryFull,
  Signal,
  Wifi,
  Mail,
  User,
  ArrowRight,
  Play,
  Pause,
  ChevronRight,
  ExternalLink,
  Mountain,
  Wrench,
  LogOut,
  LogIn,
  CheckCircle2,
  XCircle,
  CloudRain,
  Gauge,
} from "lucide-react";
import { useCrewPlatform } from "../useCrewPlatform.js";
import {
  mapOfpHero,
  mapOfpWaypoints,
  modelSimPlan,
  mapRouteView,
  mapEdtoView,
  mapNotification,
  defaultInboxMessages,
  projectMap,
} from "./qatarMappers.js";
import BoardingPanel from "../BoardingPanel.jsx";
import TechPanel from "../tech/TechPanel.jsx";

/* ─── shared bits ──────────────────────────────────────────────────── */

function TopHeader({ center, right, utc }) {
  return (
    <header className="qr-topbar">
      <div className="qr-topbar__left">
        <span className="qr-clock mono">{utc?.time || "00:00"}</span>
        <span className="qr-date">{utc?.date || "—"}</span>
      </div>
      <div className="qr-topbar__center">{center}</div>
      <div className="qr-topbar__right">
        {right}
        <Signal size={15} />
        <Wifi size={15} />
        <span className="qr-batt mono">
          97 <BatteryFull size={16} className="qr-batt__icon" />
        </span>
      </div>
    </header>
  );
}

function useUtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(id);
  }, []);
  const time = `${String(now.getUTCHours()).padStart(2, "0")}:${String(now.getUTCMinutes()).padStart(2, "0")}`;
  const months = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  const days = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  const date = `${days[now.getUTCDay()]}, ${now.getUTCDate()} ${months[now.getUTCMonth()]}`;
  return { time, date };
}

function QrLogo({ small }) {
  return (
    <span className={`qr-logo ${small ? "qr-logo--small" : ""}`}>
      <span className="qr-logo__word">QATAR</span>
      <span className="qr-logo__oryx">
        <svg viewBox="0 0 64 32" aria-hidden="true">
          <path
            d="M2 22 C 14 10, 30 4, 60 6 C 42 12, 34 16, 28 22 C 24 26, 16 27, 10 25 C 6 24, 4 23, 2 22 Z"
            fill="currentColor"
            opacity="0.9"
          />
          <path d="M60 6 L 48 10" stroke="currentColor" strokeWidth="1.5" fill="none" />
        </svg>
      </span>
      <span className="qr-logo__air">AIRWAYS</span>
    </span>
  );
}

function PillTabs({ tabs, active, onSelect }) {
  return (
    <div className="qr-tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          className={`qr-tab ${active === t.id ? "qr-tab--active" : ""}`}
          onClick={() => onSelect(t.id)}
        >
          {t.icon && <t.icon size={15} />}
          {t.label}
        </button>
      ))}
    </div>
  );
}

function Dash({ v, unit }) {
  if (v == null || v === "") return <span className="qr-dash">—</span>;
  return (
    <span className="mono qr-value">
      {v}
      {unit && <span className="qr-unit">{unit}</span>}
    </span>
  );
}

/* ─── shell ────────────────────────────────────────────────────────── */

const SMARTOPS_TABS = [
  { id: "overview", label: "Overview" },
  { id: "times", label: "Times" },
  { id: "flightplan", label: "Flightplan" },
  { id: "route", label: "Route" },
  { id: "runways", label: "Runways" },
  { id: "weather", label: "Weather" },
  { id: "briefing", label: "Briefing" },
  { id: "edto", label: "EDTO" },
];

export default function QatarShell({ onOpenOptimizer }) {
  const { selectedProvider, session, apiBase } = useCrewPlatform();
  const utc = useUtcClock();

  const [screen, setScreen] = useState("crewdesk"); // crewdesk | myflights | profile
  const [tab, setTab] = useState("flightplan");
  const [flight, setFlight] = useState(null);
  const [ofp, setOfp] = useState(null);
  const [ofpError, setOfpError] = useState(null);
  const [boarding, setBoarding] = useState(false);
  const ofpFlightRef = useRef(null);

  // OFP load is triggered by the explicit flight pick (no mount effect):
  // the click handler owns the fetch + cancellation guard.
  async function openFlight(f) {
    setFlight(f);
    setScreen("smartops");
    setOfp(null);
    setOfpError(null);
    if (!session?.authenticated || session.local) return;
    ofpFlightRef.current = f.flight_id;
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/session/${session.session_id}/flights/${f.flight_id}/ofp`
      );
      if (ofpFlightRef.current !== f.flight_id) return;
      if (resp.status === 404) {
        setOfpError("No SimBrief OFP is linked to this booking yet.");
      } else if (resp.ok) {
        setOfp(await resp.json());
      } else {
        setOfpError(await resp.text());
      }
    } catch (e) {
      if (ofpFlightRef.current === f.flight_id) {
        setOfpError(String(e.message || e));
      }
    }
  }

  if (!selectedProvider) return null;

  return (
    <div className="qr-shell">
      {screen === "crewdesk" && (
        <CrewDeskScreen utc={utc} onNavigate={setScreen} />
      )}

      {screen === "myflights" && (
        <MyFlightsScreen
          utc={utc}
          onBack={() => setScreen("crewdesk")}
          flight={flight}
          onPick={(f) => {
            openFlight(f);
          }}
          onBoarding={(f) => {
            setFlight(f);
            setBoarding(true);
          }}
        />
      )}

      {screen === "profile" && (
        <ProfileScreen
          utc={utc}
          onBack={() => setScreen("crewdesk")}
          onOpenOptimizer={onOpenOptimizer}
        />
      )}

      {screen === "smartops" && flight && (
        <SmartOpsScreen
          utc={utc}
          flight={flight}
          ofp={ofp}
          ofpError={ofpError}
          tab={tab}
          setTab={setTab}
          onOpenOptimizer={onOpenOptimizer}
        />
      )}

      {boarding && (
        <BoardingPanel
          flightId={flight?.flight_id || "default"}
          onClose={() => setBoarding(false)}
        />
      )}
    </div>
  );
}

/* ─── Crew Desk (qatar-02) ─────────────────────────────────────────── */

function CrewDeskScreen({ utc, onNavigate }) {
  const { session, apiBase } = useCrewPlatform();
  const [tab, setTab] = useState("inbox");
  const [notifications, setNotifications] = useState([]);
  const [selectedMsg, setSelectedMsg] = useState(null);

  async function loadNotifications() {
    if (!session?.session_id) return;
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/notifications?session_id=${session.session_id}`
      );
      if (resp.ok) setNotifications((await resp.json()).map(mapNotification));
    } catch {
      /* pass */
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
      setSelectedMsg(null);
    } catch {
      /* pass */
    }
  }

  const messages = notifications.length ? notifications : defaultInboxMessages(null);

  const TABS = [
    { id: "inbox", label: "Inbox", icon: Mail },
    { id: "trash", label: "Trash", icon: Trash2 },
    { id: "preflight", label: "Preflight", icon: ClipboardCheck },
    { id: "weather", label: "Weather", icon: Cloud },
    { id: "techlog", label: "Tech Log", icon: Wrench },
  ];

  return (
    <div className="qr-screen qr-crewdesk">
      <TopHeader
        utc={utc}
        center={
          <div className="qr-tabstrip">
            <PillTabs tabs={TABS} active={tab} onSelect={setTab} />
          </div>
        }
        right={<QrLogo small />}
      />

      <div className="qr-crewdesk__body">
        <aside className="qr-sidebar">
          <button className="qr-side-item" onClick={() => onNavigate("myflights")}>
            <Plane size={17} />
            My Flights
          </button>
          <button className="qr-side-item qr-side-item--active">
            <Inbox size={17} />
            Crew Desk
          </button>
          <button className="qr-side-item" onClick={() => onNavigate("profile")}>
            <User size={17} />
            Profile
          </button>
          <div className="qr-sidebar__brand">
            <QrLogo />
          </div>
        </aside>

        <section className="qr-inbox">
          <div className="qr-inbox__head">
            <span className="qr-label">
              INBOX — {messages.length} MESSAGES
            </span>
            <span style={{ display: "flex", gap: 4 }}>
              <button className="qr-linkbtn" onClick={loadNotifications}>
                REFRESH
              </button>
              <button className="qr-linkbtn" onClick={clearAll}>
                MARK ALL READ
              </button>
            </span>
          </div>
          <div className="qr-inbox__list">
            {tab === "techlog" ? (
              <TechPanel embedded />
            ) : tab === "trash" ? (
              <div className="qr-empty">Trash is empty.</div>
            ) : tab === "preflight" ? (
              <PreflightBriefing onOpenTech={() => setTab("techlog")} />
            ) : tab === "weather" ? (
              <WeatherBriefing />
            ) : (
              messages.map((m) => (
                <button
                  key={m.id}
                  className={`qr-msg ${selectedMsg?.id === m.id ? "qr-msg--selected" : ""}`}
                  onClick={() => setSelectedMsg(m)}
                >
                  <div className="qr-msg__top">
                    <span className={`qr-badge ${m.badge_class}`}>{m.kind}</span>
                    <span className="qr-msg__time mono">
                      {m.timestamp ? new Date(m.timestamp * 1000).toISOString().slice(11, 16) + "z" : "—"}
                    </span>
                  </div>
                  <div className="qr-msg__title">{m.title}</div>
                  <div className="qr-msg__sender">{m.sender}</div>
                  <div className="qr-msg__preview">{m.preview}</div>
                </button>
              ))
            )}
          </div>
        </section>

        <section className="qr-detail">
          {selectedMsg ? (
            <>
              <span className={`qr-badge ${selectedMsg.badge_class}`}>{selectedMsg.kind}</span>
              <h2>{selectedMsg.title}</h2>
              <div className="qr-detail__meta">{selectedMsg.sender}</div>
              <p>{selectedMsg.body}</p>
            </>
          ) : (
            <div className="qr-detail__empty">
              <Mail size={44} />
              <h2>No message selected.</h2>
              <span>Choose a message on the left.</span>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function PreflightBriefing({ onOpenTech }) {
  return (
    <div className="qr-briefing">
      <h3>PREFLIGHT CHECKLIST</h3>
      {[
        ["eDesk check-in", "Validate flight in My Flights"],
        ["OFP loaded", "SimBrief OFP linked (Flightplan tab)"],
        ["Aircraft tech status", "TECH area — Tech Log"],
        ["NOTAMs reviewed", "Inbox — NOTAM briefing"],
        ["Weather reviewed", "Inbox — WX briefing"],
      ].map(([title, sub]) => (
        <div className="qr-brief-item" key={title}>
          <CheckCircle2 size={15} className="qr-brief-ok" />
          <div>
            <div>{title}</div>
            <div className="qr-brief-sub">{sub}</div>
          </div>
        </div>
      ))}
      <button className="qr-goldbtn" onClick={onOpenTech}>
        <Wrench size={15} /> Open Tech Log
      </button>
    </div>
  );
}

function WeatherBriefing() {
  return (
    <div className="qr-briefing">
      <h3>WEATHER BRIEFING</h3>
      <div className="qr-brief-item">
        <CloudRain size={15} className="qr-brief-ok" />
        <div>
          <div>WX briefing available in Inbox</div>
          <div className="qr-brief-sub">Enroute + destination MET/TAF per flight</div>
        </div>
      </div>
      <div className="qr-notice">Live METAR/TAF feed is not connected yet — briefing content follows from the selected flight's OFP.</div>
    </div>
  );
}

/* ─── My Flights (eDesk functionality, Qatar look) ─────────────────── */

function MyFlightsScreen({ utc, onBack, flight, onPick, onBoarding }) {
  const { session, configReady, loading, error, startAuth, apiBase } = useCrewPlatform();
  const [flights, setFlights] = useState([]);
  const [manual, setManual] = useState({
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
    aircraft_icao: "B777-300ER",
    callsign: "QTR815",
  });
  const [result, setResult] = useState(null);
  const [checkinError, setCheckinError] = useState(null);

  async function loadFlights() {
    if (!session?.authenticated) return;
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/session/${session.session_id}/flights`
      );
      if (resp.ok) setFlights(await resp.json());
    } catch {
      /* pass */
    }
  }

  // Roster: fetch when the authenticated session becomes available
  // (subscription-style — setState only in the async callback).
  useEffect(() => {
    if (!session?.authenticated) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(
          `${apiBase}/api/crew/session/${session.session_id}/flights`
        );
        if (!cancelled && resp.ok) setFlights(await resp.json());
      } catch {
        /* pass */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [session?.authenticated, session?.session_id, apiBase]);
  async function doCheckin(f) {
    if (!session?.authenticated) return;
    setResult(null);
    setCheckinError(null);
    try {
      const resp = await fetch(`${apiBase}/api/crew/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          flight_id: f.flight_id,
          flight_number: f.flight_number,
          simbrief_departure: f.departure_icao,
          simbrief_arrival: f.arrival_icao,
          simbrief_callsign: f.callsign,
          simbrief_aircraft: f.aircraft_icao,
        }),
      });
      if (resp.ok) setResult(await resp.json());
      else setCheckinError(await resp.text());
    } catch (e) {
      setCheckinError(String(e.message || e));
    }
  }

  return (
    <div className="qr-screen">
      <TopHeader
        utc={utc}
        center={<span className="qr-topbar__title">MY FLIGHTS</span>}
        right={<QrLogo small />}
      />
      <div className="qr-screen__body">
        <div className="qr-hero-card">
          <div className="qr-hero-card__row">
            <button className="qr-linkbtn" onClick={onBack}>
              ← CREW DESK
            </button>
            {session?.authenticated && (
              <span className="qr-label">{session.display_name || "PILOT"}</span>
            )}
          </div>

          {!session?.authenticated && (
            <div className="qr-notice">
              {configReady?.ready ? (
                <button className="qr-goldbtn" onClick={startAuth} disabled={loading}>
                  <LogIn size={15} /> {loading ? "Connecting…" : "Sign in with vAMSYS"}
                </button>
              ) : (
                "Sign in through vAMSYS to view your flight list — or enter a flight manually below."
              )}
              {error && <div className="qr-error">{error}</div>}
            </div>
          )}

          {session?.authenticated && (
            <>
              <div className="qr-section-head">
                <h3>FLIGHT ROSTER</h3>
                <button className="qr-linkbtn" onClick={loadFlights}>
                  REFRESH
                </button>
              </div>
              {flights.length === 0 && (
                <div className="qr-notice">No flights loaded yet{session.local ? " (local session)" : ""}.</div>
              )}
              {flights.map((f) => (
                <button
                  key={f.flight_id}
                  className={`qr-flight-row ${flight?.flight_id === f.flight_id ? "qr-flight-row--selected" : ""}`}
                  onClick={() => onPick(f)}
                >
                  <span className="mono qr-flight-row__no">{f.flight_number || f.callsign || "—"}</span>
                  <span className="qr-flight-row__route mono">
                    {f.departure_icao || "?"} <ArrowRight size={12} /> {f.arrival_icao || "?"}
                  </span>
                  <span className="qr-flight-row__type">{f.aircraft_icao || "—"}</span>
                  <span className="qr-flight-row__status">{f.status || "—"}</span>
                  <span style={{ display: "flex", gap: 6 }}>
                    <span
                      className="qr-chip qr-chip--ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        doCheckin(f);
                      }}
                    >
                      CHECK-IN
                    </span>
                    <span
                      className="qr-chip qr-chip--ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        onBoarding(f);
                      }}
                    >
                      BOARDING
                    </span>
                  </span>
                </button>
              ))}
            </>
          )}

          {(session?.local || !session?.authenticated) && (
            <div className="qr-section-head qr-section-head--mt">
              <h3>MANUAL FLIGHT ENTRY</h3>
            </div>
          )}
          {(session?.local || !session?.authenticated) && (
            <div className="qr-manual-grid">
              <label><span>Flight No.</span>
                <input value={manual.flight_number} onChange={(e) => setManual({ ...manual, flight_number: e.target.value })} placeholder="QR815" />
              </label>
              <label><span>Dep ICAO</span>
                <input value={manual.departure_icao} onChange={(e) => setManual({ ...manual, departure_icao: e.target.value.toUpperCase() })} placeholder="DOH" maxLength={4} />
              </label>
              <label><span>Arr ICAO</span>
                <input value={manual.arrival_icao} onChange={(e) => setManual({ ...manual, arrival_icao: e.target.value.toUpperCase() })} placeholder="LHR" maxLength={4} />
              </label>
              <label><span>Aircraft</span>
                <input value={manual.aircraft_icao} onChange={(e) => setManual({ ...manual, aircraft_icao: e.target.value.toUpperCase() })} placeholder="B777-300ER" />
              </label>
              <label><span>Callsign</span>
                <input value={manual.callsign} onChange={(e) => setManual({ ...manual, callsign: e.target.value.toUpperCase() })} placeholder="QTR815" />
              </label>
              <div className="qr-manual-grid__action">
                <button
                  className="qr-goldbtn"
                  disabled={!manual.departure_icao || !manual.arrival_icao}
                  onClick={() => {
                    const f = {
                      flight_id:
                        manual.departure_icao + "-" + manual.arrival_icao + "-" +
                        (manual.callsign || manual.flight_number || "LOCAL"),
                      ...manual,
                    };
                    onPick(f);
                  }}
                >
                  <PlaneTakeoff size={15} /> OPEN FLIGHT
                </button>
              </div>
            </div>
          )}

          {result && (
            <div className={`qr-result ${result.valid ? "qr-result--ok" : "qr-result--fail"}`}>
              <div className="qr-result__title">
                {result.valid ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
                {result.valid ? "CHECK-IN VALID" : "CHECK-IN FAILED"}
              </div>
              {result.errors?.map((e, i) => (<div key={i}>• {e}</div>))}
              {result.warnings?.map((w, i) => (<div key={i} className="qr-result__warn">• {w}</div>))}
            </div>
          )}
          {checkinError && <div className="qr-error">{checkinError}</div>}
        </div>
      </div>
    </div>
  );
}

/* ─── Profile (Setup functionality, Qatar look) ────────────────────── */

function ProfileScreen({ utc, onBack, onOpenOptimizer }) {
  const { session, selectedProvider, configReady, logout } = useCrewPlatform();
  return (
    <div className="qr-screen">
      <TopHeader
        utc={utc}
        center={<span className="qr-topbar__title">PROFILE</span>}
        right={<QrLogo small />}
      />
      <div className="qr-screen__body">
        <div className="qr-hero-card">
          <button className="qr-linkbtn" onClick={onBack}>← CREW DESK</button>
          <h3>PILOT PROFILE</h3>
          <div className="qr-idgrid">
            <div><span className="qr-label">NAME</span><strong>{session?.display_name || "Local Pilot"}</strong></div>
            <div><span className="qr-label">CREW ID</span><strong className="mono">{session?.crew_id || "—"}</strong></div>
            <div><span className="qr-label">RANK</span><strong>{session?.rank || "—"}</strong></div>
            <div><span className="qr-label">AIRLINE</span><strong>{selectedProvider?.display_name || "—"}</strong></div>
          </div>

          <h3>CONFIGURATION READINESS</h3>
          <div className="qr-configlist">
            {[
              ["vAMSYS Client ID", configReady?.vamsys_client_id_set],
              ["vAMSYS Redirect URI", configReady?.vamsys_redirect_uri_set],
              ["Redirect URI HTTPS (v3)", configReady?.vamsys_redirect_uri_https],
              ["Session Secret", configReady?.session_secret_set],
            ].map(([label, set]) => (
              <div key={label} className="qr-configrow">
                <span className={set ? "qr-cfg-ok" : "qr-cfg-miss"}>{set ? "✓" : "✗"}</span>
                <span>{label}</span>
                <span className="qr-configrow__note">{set ? "Set (value hidden)" : "Not configured"}</span>
              </div>
            ))}
          </div>
          {configReady && (
            <div className={`qr-readiness ${configReady.ready ? "qr-readiness--ok" : "qr-readiness--warn"}`}>
              {configReady.ready ? "✓ All configured" : "⚠ Missing configuration"}
            </div>
          )}

          <div className="qr-profile__actions">
            <button className="qr-goldbtn" onClick={() => onOpenOptimizer?.(null)}>
              <Gauge size={15} /> Open CI Optimizer
            </button>
            {session?.authenticated && (
              <button className="qr-ghostbtn" onClick={logout}>
                <LogOut size={14} /> SIGN OUT
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── QR SmartOps (flightplan / route / edto) ──────────────────────── */

function SmartOpsScreen({ utc, flight, ofp, ofpError, tab, setTab, onOpenOptimizer }) {
  const ofpData = ofp?.ofp_data || null;
  const hero = useMemo(() => mapOfpHero(flight, ofpData), [flight, ofpData]);
  const simPlan = useMemo(
    () => (ofpData ? null : modelSimPlan(flight)),
    [ofpData, flight]
  );
  const waypoints = useMemo(() => {
    if (ofpData) return { derived: false, rows: mapOfpWaypoints(ofpData) };
    return simPlan ? { derived: true, rows: simPlan.rows } : null;
  }, [ofpData, simPlan]);
  const fuel = (ofpData ? hero.fuel : simPlan?.fuel) || {};
  const weights = (ofpData ? hero.weights : simPlan?.weights) || {};
  const distanceNm =
    (ofpData?.general?.route_distance ? Number(String(ofpData.general.route_distance).replace(/[,\s]/g, "")) : null) ??
    simPlan?.distance_nm ??
    null;

  const dateLabel = useMemo(() => {
    const months = ["January","February","March","April","May","June","July","August","September","October","November","December"];
    const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
    const d = new Date();
    return `${days[d.getUTCDay()]}, ${d.getUTCDate()} ${months[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }, []);

  return (
    <div className="qr-screen qr-smartops">
      <TopHeader
        utc={utc}
        center={
          <div className="qr-tabstrip qr-tabstrip--pillbar">
            <PillTabs tabs={SMARTOPS_TABS} active={tab} onSelect={setTab} />
          </div>
        }
        right={
          <>
            <Sun size={16} className="qr-topbar__icon" />
            <RefreshCw size={16} className="qr-topbar__icon" />
            <MoreVertical size={16} className="qr-topbar__icon" />
          </>
        }
      />
      <div className="qr-smartops__brand">
        <QrLogo />
      </div>

      {(tab === "flightplan" || tab === "overview" || tab === "times" || tab === "briefing") && (
        <>
          <div className="qr-hero">
            <div className="qr-hero__left">
              <div className="qr-hero__no mono">{hero.flight_number || "—"}</div>
              <div className="qr-hero__route mono">
                {hero.departure || "—"} <span className="qr-hero__arrow">→</span> {hero.arrival || "—"}
              </div>
              <div className="qr-hero__sub">
                {hero.airline} &nbsp;|&nbsp; {hero.aircraft_type || flight?.aircraft_icao || "—"}
              </div>
            </div>
            <div className="qr-hero__right">
              <div>{dateLabel}</div>
              <div>
                {hero.departure_name || hero.departure || "—"} → {hero.arrival_name || hero.arrival || "—"}
              </div>
              <div className="mono">
                STD {hero.std || "—"} &nbsp;|&nbsp; STA {hero.sta || "—"} &nbsp;|&nbsp; EET {hero.eet || "—"}
              </div>
            </div>
          </div>

          <div className="qr-ofp-banner">
            <div className="qr-ofp-banner__text">
              <strong>
                {ofpData ? "Operational flightplan (OFP) loaded from SimBrief." : simPlan ? "Sim-planned flight (SIM PLANNED) — no OFP linked yet." : "No flightplan data available."}
              </strong>
              <span className="qr-ofp-banner__sub">
                {ofpData
                  ? `Generated ${ofp?.created_at ? new Date(ofp.created_at).toISOString().slice(11, 16) + " Z" : "—"} · NAV data AIRAC —`
                  : simPlan
                    ? "Derived from entered ICAOs + a light fuel model — for display only, never presented as real OFP values."
                    : "Select a flight with a linked SimBrief OFP."}
              </span>
              {ofpError && <span className="qr-ofp-banner__err">{ofpError}</span>}
            </div>
            <div className="qr-ofp-banner__actions">
              <button className="qr-goldbtn" onClick={() => onOpenOptimizer?.(flight)}>
                Import New Plan
              </button>
              {ofp?.pdf_url && (
                <a className="qr-linkbtn" href={ofp.pdf_url} target="_blank" rel="noopener noreferrer">
                  <ExternalLink size={13} /> OFP PDF
                </a>
              )}
            </div>
          </div>

          <div className="qr-fuelrow">
            <div className="qr-fuelcell">
              <span className="qr-label">DEVIATION</span>
              <Dash v={fuel.deviation} />
              <span className="qr-fuelcell__sub">No active check</span>
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">BLOCK FUEL</span>
              <Dash v={fuel.block} unit=" t" />
              <span className="qr-fuelcell__sub">TAXI {fuel.taxi != null ? `${fuel.taxi} t` : "—"}</span>
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">TAKEOFF FUEL</span>
              <Dash v={fuel.takeoff} unit=" t" />
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">TRIP FUEL</span>
              <Dash v={fuel.trip} unit=" t" />
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">PLANNED LDG FUEL</span>
              <Dash v={fuel.landing} unit=" t" />
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">EXTRA FUEL</span>
              <Dash v={fuel.extra} />
            </div>
            <div className="qr-fuelcell">
              <span className="qr-label">RES + ALTN</span>
              <Dash v={fuel.reserve_alt} unit=" t" />
            </div>
            <div className="qr-fuelcell qr-fuelcell--toggle">
              <div className="qr-toggle">
                <span className="qr-toggle__btn qr-toggle__btn--active">t</span>
                <span className="qr-toggle__btn">kg</span>
              </div>
            </div>
          </div>

          <div className="qr-weights mono">
            <span>TOW {weights.tow != null ? `${weights.tow} t` : "—"}</span>
            <span>ZFW {weights.zfw != null ? `${weights.zfw} t` : "—"}</span>
            <span>LDW {weights.ldw != null ? `${weights.ldw} t` : "—"}</span>
            <span>PAX {hero.pax != null ? hero.pax : "—"}</span>
            {distanceNm != null && <span>DIST {distanceNm} NM</span>}
          </div>

          {waypoints?.rows ? (
            <table className="qr-wpt-table">
              <thead>
                <tr>
                  <th>WPT</th><th>AWY</th><th>FIR</th><th>LEG NM</th><th>REM NM</th>
                  <th>ETE</th><th>LEG ETE</th><th>ALT</th><th>WIND</th><th>BURN</th>
                  <th>PLN FUEL</th><th>ATO</th><th>ACT</th>
                </tr>
              </thead>
              <tbody>
                {waypoints.rows.map((w, i) => (
                  <tr key={i} className={i === 0 || w.ident === hero.arrival ? "qr-wpt-table--ap" : ""}>
                    <td>
                      <span className="mono qr-wpt__ident">{w.ident || `WP${i - 1}`}</span>
                      {w.name && <span className="qr-wpt__name">{w.name}</span>}
                    </td>
                    <td className="mono">{w.airway || "—"}</td>
                    <td className="mono">{w.fir || "—"}</td>
                    <td className="mono">{w.legNm != null ? w.legNm : "—"}</td>
                    <td className="mono">{w.remNm != null ? w.remNm : "—"}</td>
                    <td className="mono">{w.ete || "—"}</td>
                    <td className="mono">{w.legEte || "—"}</td>
                    <td className="mono">{w.alt != null ? `FL${w.alt}` : "—"}</td>
                    <td className="mono">{w.wind || "—"}</td>
                    <td className="mono">{w.burn != null ? `${Math.round(w.burn / 100) / 10} t` : "—"}</td>
                    <td className="mono qr-wpt__fuel">{w.planFuel != null ? `${w.planFuel} t` : "—"}</td>
                    <td className="mono">—</td>
                    <td className="mono">—</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="qr-notice">
              No waypoint table available — enter a flight in My Flights with known departure/arrival ICAOs.
            </div>
          )}
        </>
      )}

      {tab === "route" && (
        <RouteScreen flight={flight} ofpData={ofpData} distanceNm={distanceNm} utc={utc} />
      )}

      {tab === "edto" && (
        <EdtoScreen flight={flight} ofpData={ofpData} />
      )}

      {(tab === "runways" || tab === "weather") && (
        <div className="qr-notice qr-notice--center">
          {tab === "runways" ? "Runway data follows from the OFP (arrival/departure runways) — not linked to this booking yet." : "Live weather overlay — WX TIME playback and ATC sectors are available on the Route tab."}
        </div>
      )}
    </div>
  );
}

/* ─── Route (qatar-04) ─────────────────────────────────────────────── */

function RouteScreen({ flight, ofpData, distanceNm, utc }) {
  const [wxPlaying, setWxPlaying] = useState(false);
  const [wxOffset, setWxOffset] = useState(0);
  const [altMode, setAltMode] = useState("AUTO");

  const view = useMemo(() => {
    const v = mapRouteView(flight, ofpData);
    const pts = v.points
      .map((p) => ({ ...p, xy: projectMap(p.lat, p.lon) }))
      .filter((p) => p.xy);
    // Fit: keep all points inside the 1000x560 box with margin.
    const xs = pts.map((p) => p.xy.x);
    const ys = pts.map((p) => p.xy.y);
    const pad = 60;
    const minx = Math.max(0, Math.min(...xs) - pad);
    const maxx = Math.min(1000, Math.max(...xs) + pad);
    const miny = Math.max(0, Math.min(...ys) - pad);
    const maxy = Math.min(560, Math.max(...ys) + pad);
    return { ...v, pts, vb: { minx, miny, w: Math.max(200, maxx - minx), h: Math.max(140, maxy - miny) } };
  }, [flight, ofpData]);

  const routeString = view.route_string ||
    (view.hero.departure && view.hero.arrival
      ? `${view.hero.departure} DCT ${view.hero.arrival}`
      : null);

  return (
    <div className="qr-route">
      <div className="qr-route__map">
        <svg viewBox={`${view.vb.minx} ${view.vb.miny} ${view.vb.w} ${view.vb.h}`} className="qr-route__svg" preserveAspectRatio="xMidYMid meet">
          <defs>
            <radialGradient id="qr-mapglow" cx="50%" cy="40%" r="80%">
              <stop offset="0%" className="qr-svg-fill-a" />
              <stop offset="100%" className="qr-svg-fill-b" />
            </radialGradient>
          </defs>
          <rect x={view.vb.minx - 50} y={view.vb.miny - 50} width={view.vb.w + 100} height={view.vb.h + 100} fill="url(#qr-mapglow)" />
          {/* graticule */}
          {Array.from({ length: 13 }).map((_, i) => (
            <line key={`g${i}`} x1={i * 100} y1={view.vb.miny - 50} x2={i * 100} y2={view.vb.miny + view.vb.h + 50} className="qr-svg-grat" strokeWidth="1" />
          ))}
          {Array.from({ length: 7 }).map((_, i) => (
            <line key={`gh${i}`} x1={view.vb.minx - 50} y1={i * 100} x2={view.vb.minx + view.vb.w + 50} y2={i * 100} className="qr-svg-grat" strokeWidth="1" />
          ))}

          {/* route polyline */}
          {view.pts.length > 1 && (
            <polyline
              points={view.pts.map((p) => `${p.xy.x},${p.xy.y}`).join(" ")}
              fill="none"
              className="qr-svg-route"
              strokeWidth="2"
            />
          )}

          {/* waypoints */}
          {view.pts.map((p, i) => {
            const isEnd = p.end === "dep" || p.end === "arr";
            return (
              <g key={i}>
                {isEnd ? (
                  <>
                    <circle cx={p.xy.x} cy={p.xy.y} r="6" fill="none" className="qr-svg-end" strokeWidth="2" />
                    <circle cx={p.xy.x} cy={p.xy.y} r="2" className="qr-svg-end-fill" />
                  </>
                ) : (
                  <>
                    <circle cx={p.xy.x} cy={p.xy.y} r="2.5" className="qr-svg-wp" />
                    <path d={`M ${p.xy.x - 5} ${p.xy.y - 12} L ${p.xy.x + 5} ${p.xy.y - 12} L ${p.xy.x} ${p.xy.y - 4} Z`} className="qr-svg-wp-tri" />
                  </>
                )}
                {p.ident && (
                  <text x={p.xy.x + 8} y={p.xy.y - 8} className="qr-wp-label mono">
                    {p.ident}
                    {p.fl ? ` FL${p.fl}` : ""}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        <div className="qr-route__layers">
          <span className="qr-chip">Layers</span>
        </div>
        <div className="qr-route__terrain">
          <Mountain size={18} />
          <div>
            <strong>ROUTE TERRAIN</strong>
            <span>Terrain data available</span>
          </div>
          <ChevronRight size={16} />
        </div>

        <div className="qr-route__wxpanel">
          <div className="qr-route__wxhead">
            <span className="qr-label">WX TIME</span>
            <span className="mono">{utc.time} z • {wxOffset === 0 ? "NOW" : `${wxOffset > 0 ? "+" : ""}${wxOffset}h`}</span>
          </div>
          <div className="qr-route__wxrow">
            <button className="qr-wxbtn" onClick={() => setWxPlaying((v) => !v)}>
              {wxPlaying ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <span className="qr-wxbtn qr-wxbtn--static">-12h</span>
            <input
              type="range"
              min={-12}
              max={12}
              value={wxOffset}
              onChange={(e) => setWxOffset(Number(e.target.value))}
              className="qr-slider"
            />
            <span className="qr-wxbtn qr-wxbtn--static">+12h</span>
            <span className={`qr-wxbtn ${wxOffset === 0 ? "qr-wxbtn--on" : ""}`} onClick={() => setWxOffset(0)}>NOW</span>
          </div>
          <div className="qr-route__atc">
            <div className="qr-route__wxhead">
              <span className="qr-label">ATC SECTORS</span>
              <span className="qr-badge badge--vatsim">VATSIM</span>
              <span className="mono">FL390</span>
            </div>
            <div className="qr-route__wxrow">
              <input type="range" min={0} max={100} defaultValue={60} className="qr-slider" />
              <span className="qr-label">DISPLAY ALTITUDE</span>
              <button className={`qr-wxbtn ${altMode === "AUTO" ? "qr-wxbtn--on" : ""}`} onClick={() => setAltMode("AUTO")}>AUTO</button>
              <button className={`qr-wxbtn ${altMode === "OFF" ? "qr-wxbtn--on" : ""}`} onClick={() => setAltMode("OFF")}>OFF FL320</button>
            </div>
          </div>
        </div>

        <div className="qr-route__legend">
          <span className="qr-label">PRECIP</span>
          {[["#4a2230", "None"], ["#2563eb", "Light"], ["#e8a838", "Moderate"], ["#ef4444", "Heavy"], ["#c74a6a", "CB"]].map(([c, l]) => (
            <span key={l} className="qr-legend__item"><i style={{ background: c }} />{l}</span>
          ))}
          <span className="qr-legend__sep" />
          <span className="qr-label">SIGMET</span>
          {[["#ef4444", "Thunderstorm"], ["#e8a838", "Turbulence"], ["#38bdf8", "Icing"], ["#c74a6a", "Volcanic Ash"], ["#4ade80", "TS (Tropical)"]].map(([c, l]) => (
            <span key={l} className="qr-legend__item"><i style={{ background: c }} />{l}</span>
          ))}
        </div>
      </div>

      <div className="qr-route__meta">
        <div className="mono">{routeString}</div>
        <div>
          {distanceNm != null ? `${distanceNm} NM` : "—"} &nbsp;•&nbsp; {view.block_label || "—"}
        </div>
        <div className="qr-route__alt">LHR / LGW</div>
      </div>
    </div>
  );
}

/* ─── EDTO + Risks (qatar-05) ──────────────────────────────────────── */

function EdtoScreen({ flight, ofpData }) {
  const view = useMemo(() => mapEdtoView(flight, ofpData), [flight, ofpData]);
  return (
    <div className="qr-edto">
      <div className="qr-edto__map">
        <svg viewBox="140 40 720 460" className="qr-route__svg" preserveAspectRatio="xMidYMid meet">
          <rect x="90" y="10" width="820" height="540" className="qr-svg-bg" />
          {(() => {
            const dep = projectMap(25.2731, 51.1671);
            const arr = projectMap(51.47, -0.4543);
            return (
              <>
                <polyline
                  points={[dep, ...Array.from({ length: 5 }, (_, i) => {
                    const [la, lo] = [25.2731 + (51.47 - 25.2731) * ((i + 1) / 6), 51.1671 + (-0.4543 - 51.1671) * ((i + 1) / 6)];
                    return projectMap(la, lo);
                  }), arr].map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none" className="qr-svg-route" strokeWidth="2"
                />
                <circle cx={dep.x} cy={dep.y} r="6" fill="none" className="qr-svg-end" strokeWidth="2" />
                <circle cx={dep.x} cy={dep.y} r="2" className="qr-svg-end-fill" />
                <text x={dep.x + 10} y={dep.y + 4} className="qr-wp-label mono">DOH</text>
                <circle cx={arr.x} cy={arr.y} r="6" fill="none" className="qr-svg-end" strokeWidth="2" />
                <circle cx={arr.x} cy={arr.y} r="2" className="qr-svg-end-fill" />
                <text x={arr.x + 10} y={arr.y + 4} className="qr-wp-label mono">LHR</text>
              </>
            );
          })()}
        </svg>
        <div className="qr-edto__routeinfo">
          <div className="mono">{view.route_string || "DOH – LHR"}</div>
          <div className="qr-edto__stats">
            {view.distance_nm != null && <span>{view.distance_nm} NM</span>}
            {view.block_label && <span>{view.block_label}</span>}
            <span>LHR / LGW</span>
          </div>
        </div>
      </div>

      <div className="qr-edto__risks">
        <section className="qr-risk-section">
          <div className="qr-risk-section__head">
            <h3>OPERATIONAL RISK INFORMATION</h3>
          </div>
          <div className="qr-risk-sub">
            <span className="qr-label">OFFICIAL RISK NOTICES</span>
            <span className="mono">{view.generated}</span>
            <span className="qr-badge badge--ok">CURRENT</span>
          </div>
          {view.official_notices.map((n) => (
            <div key={n.region} className="qr-risk-row qr-risk-row--active">
              <AlertTriangle size={15} className="qr-risk-row__icon" />
              <div>
                <strong>AIRSPACE {n.region}</strong>
                <span className="qr-risk-row__status">{n.status}</span>
              </div>
            </div>
          ))}
          <div className="qr-risk-sub">
            <span className="qr-label">OPERATOR RISK INFORMATION</span>
            <span className="mono">{view.generated}</span>
            <span className="qr-badge badge--ok">CURRENT</span>
          </div>
          {view.operator_risks.map((r) => (
            <div key={r.country} className="qr-risk-row">
              <div className="qr-risk-row__thumb" />
              <div className="qr-risk-row__body">
                <strong>{r.country}</strong>
                <span className="qr-risk-row__status">{r.level}</span>
              </div>
            </div>
          ))}
        </section>

        <section className="qr-risk-section">
          <div className="qr-risk-sub">
            <span className="qr-label">NAT TRACKS</span>
            <span className="mono">15:54Z</span>
          </div>
          {view.nat_tracks.map((t) => (
            <div key={t.name} className="qr-nat">
              <div className="qr-nat__head">
                <strong>{t.name} {t.direction}</strong>
                <span className="mono qr-nat__valid">{t.valid}</span>
              </div>
              <div className="mono qr-nat__track">{t.track}</div>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
