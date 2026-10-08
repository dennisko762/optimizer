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
  ExternalLink,
  Wrench,
  LogOut,
  LogIn,
  CheckCircle2,
  XCircle,
  CloudRain,
  Gauge,
  Download,
} from "lucide-react";
import { useCrewPlatform } from "../useCrewPlatform.js";
import { loadSession, clearSession } from "../crewAuth.js";
import { useFlightCheckins } from "../useFlightCheckins.js";
import {
  mapOfpHero,
  mapOfpWaypoints,
  modelSimPlan,
  mapEdtoView,
  mapNotification,
  defaultInboxMessages,
  projectMap,
} from "./qatarMappers.js";
import CrewLogin from "./CrewLogin.jsx";
import MapWeatherPanel from "./MapWeatherPanel.jsx";
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
  const { selectedProvider, apiBase } = useCrewPlatform();
  const utc = useUtcClock();

  // M2b: VA crew login gate — persisted session (crewAuth) from a previous
  // app start, or a fresh login screen when absent.
  const [crewSession, setCrewSession] = useState(() => loadSession());

  const [screen, setScreen] = useState("home"); // home | crewdesk | profile | smartops
  const [tab, setTab] = useState("flightplan");
  const [flight, setFlight] = useState(null);
  const [ofp, setOfp] = useState(null);
  const [ofpError, setOfpError] = useState(null);
  const [importing, setImporting] = useState(false);
  const [boarding, setBoarding] = useState(false);
  const [lastPlan, setLastPlan] = useState(null);
  const ofpFlightRef = useRef(null);

  const { checkIn, checkedIn, record: checkinRecord } = useFlightCheckins();

  function handleLogout() {
    clearSession();
    setCrewSession(null);
    setScreen("home");
    setFlight(null);
  }

  // The last saved SimBrief plan is loaded at app start (spec 4/4): the
  // first flight the crew opens then shows that plan instead of re-fetching.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${apiBase}/api/simbrief/flightplans`);
        if (!resp.ok) return;
        const body = await resp.json();
        const key = body?.last_plan;
        if (!key) return;
        const planResp = await fetch(`${apiBase}/api/simbrief/flightplans/${encodeURIComponent(key)}`);
        if (!planResp.ok) return;
        const plan = await planResp.json();
        if (!cancelled && plan?.flightplan) setLastPlan(plan.flightplan);
      } catch {
        /* no saved plans yet — live fetch remains the source */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBase]);

  // OFP load: the live SimBrief OFP is fetched once per session (it is a
  // single "current flight" document). The first flight open triggers it;
  // later picks reuse the cached plan.
  async function loadLiveFlightplan() {
    setOfpError(null);
    try {
      const resp = await fetch(`${apiBase}/api/simbrief/flightplan/live`);
      if (resp.ok) {
        setOfp({ ofp_data: await resp.json() });
      } else if (resp.status === 503) {
        // No SIMBRIEF_USER on the bridge — local demo mode keeps working.
        setOfpError("SimBrief bridge not configured (SIMBRIEF_USER) — local flight demo only.");
      } else {
        setOfpError(`SimBrief OFP unavailable (HTTP ${resp.status}).`);
      }
    } catch (e) {
      setOfpError(String(e.message || e));
    }
  }

  async function doImportNewPlan() {
    setImporting(true);
    try {
      const resp = await fetch(`${apiBase}/api/simbrief/flightplan/import`, { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok) {
        setOfp({ ofp_data: body.flightplan });
        setOfpError(null);
      } else {
        setOfpError(body?.detail || `SimBrief import failed (HTTP ${resp.status}).`);
      }
    } catch (e) {
      setOfpError(String(e.message || e));
    } finally {
      setImporting(false);
    }
  }

  // My Flights "LOAD FLIGHTS": re-fetch the current SimBrief OFP in the
  // background and persist it to the plan store (same import endpoint as
  // "Import New Plan"). On success the freshly saved plan becomes the
  // last-plan used when the next flight is opened.
  async function loadFlights() {
    try {
      const resp = await fetch(`${apiBase}/api/simbrief/flightplan/import`, { method: "POST" });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok && body?.flightplan) {
        setLastPlan(body.flightplan);
        return { ok: true, key: body.key };
      }
      return { ok: false, detail: body?.detail || `SimBrief fetch failed (HTTP ${resp.status}).` };
    } catch (e) {
      return { ok: false, detail: String(e.message || e) };
    }
  }

  // Opening a flight loads a plan: an explicitly passed saved plan wins,
  // then the last saved plan (loaded at app start), then the live SimBrief
  // OFP (a single "current flight" document, fetched once per session).
  async function openFlight(f, savedPlan) {
    setFlight(f);
    setScreen("smartops");
    setOfpError(null);
    ofpFlightRef.current = f.flight_id;
    if (savedPlan) {
      setOfp({ ofp_data: savedPlan });
      return;
    }
    if (!ofp) {
      if (lastPlan) {
        setOfp({ ofp_data: lastPlan });
      } else {
        await loadLiveFlightplan();
      }
    }
  }

  if (!selectedProvider) return null;

  // M2b: every app start lands on the crew login until a session exists.
  if (!crewSession) {
    return (
      <div className="qr-shell">
        <CrewLogin session={crewSession} onLoggedIn={setCrewSession} />
      </div>
    );
  }

  return (
    <div className="qr-shell">
      {screen === "home" && (
        <HomeScreen
          utc={utc}
          session={crewSession}
          onNavigate={setScreen}
          onOpenFlight={(f, savedPlan) => openFlight(f, savedPlan)}
          onBoarding={(f) => {
            setFlight(f);
            setBoarding(true);
          }}
          checkIn={checkIn}
          checkedIn={checkedIn}
          checkinRecord={checkinRecord}
          loadFlights={loadFlights}
        />
      )}

      {screen === "crewdesk" && (
        <CrewDeskScreen
          utc={utc}
          onNavigate={setScreen}
          pilotName={crewSession.pilotId}
        />
      )}

      {screen === "profile" && (
        <ProfileScreen
          utc={utc}
          onBack={() => setScreen("home")}
          onOpenOptimizer={onOpenOptimizer}
          crewSession={crewSession}
          onLogout={handleLogout}
        />
      )}

      {screen === "smartops" && flight && (
        <SmartOpsScreen
          utc={utc}
          flight={flight}
          ofp={ofp}
          ofpError={ofpError}
          importing={importing}
          tab={tab}
          setTab={setTab}
          onImportNewPlan={doImportNewPlan}
          onBack={() => setScreen("home")}
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

/* ─── shared sidebar (Home / Crew Desk) ───────────────────────────── */

function Sidebar({ active, onNavigate, pilotName }) {
  return (
    <aside className="qr-sidebar">
      <button
        className={`qr-side-item ${active === "home" ? "qr-side-item--active" : ""}`}
        onClick={() => onNavigate("home")}
      >
        <Plane size={17} />
        Home
      </button>
      <button
        className={`qr-side-item ${active === "crewdesk" ? "qr-side-item--active" : ""}`}
        onClick={() => onNavigate("crewdesk")}
      >
        <Inbox size={17} />
        Crew Desk
      </button>
      <button
        className={`qr-side-item ${active === "profile" ? "qr-side-item--active" : ""}`}
        onClick={() => onNavigate("profile")}
      >
        <User size={17} />
        Profile
      </button>
      {pilotName && <span className="qr-sidebar__pilot mono">{pilotName}</span>}
      <div className="qr-sidebar__brand">
        <QrLogo />
      </div>
    </aside>
  );
}

/* ─── Company News (placeholder, M2b) ─────────────────────────────── */

// Placeholder announcements for the Home screen — the live company feed
// replaces this list later; the panel keeps its structure and look.
const PLACEHOLDER_NEWS = [
  {
    id: "n-airac",
    kind: "NOTICE",
    badge_class: "badge--dispatch",
    time: "06:40Z",
    title: "AIRAC 2610 now effective",
    preview:
      "New cycle in NAV data from 26 OCT — check updated charts and route notes for your next flight.",
  },
  {
    id: "n-duty",
    kind: "OPS",
    badge_class: "badge--notam",
    time: "05:10Z",
    title: "QR815 DOH → LHR duty extension",
    preview:
      "Duty time extended to 13:30Z for the 05 OCT rotation. Report time at DOH unchanged.",
  },
  {
    id: "n-maint",
    kind: "MAINT",
    badge_class: "badge--ok",
    time: "22:40Z",
    title: "A6713 A-check completed",
    preview:
      "Back in service 04 OCT — Tech Log entry updated, no open defects on the aircraft.",
  },
];

function CompanyNewsPanel() {
  return (
    <section className="qr-news">
      <div className="qr-news__head">
        <h3>COMPANY NEWS</h3>
        <span className="qr-badge badge--dispatch">PLACEHOLDER</span>
      </div>
      {PLACEHOLDER_NEWS.map((n) => (
        <div className="qr-news__item" key={n.id}>
          <div className="qr-news__top">
            <span className={`qr-badge ${n.badge_class}`}>{n.kind}</span>
            <span className="qr-news__time mono">{n.time}</span>
          </div>
          <div className="qr-news__title">{n.title}</div>
          <div className="qr-news__preview">{n.preview}</div>
        </div>
      ))}
      <div className="qr-news__foot">
        Placeholder items — the live company announcement feed lands in a later milestone.
      </div>
    </section>
  );
}

/* ─── Crew Desk (qatar-02) ─────────────────────────────────────────── */

function CrewDeskScreen({ utc, onNavigate, pilotName }) {
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
        <Sidebar active="crewdesk" onNavigate={onNavigate} pilotName={pilotName} />

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

/* ─── Home (M2b): Company News + My Flights + check-in ────────────── */

function HomeScreen({
  utc,
  session,
  onNavigate,
  onOpenFlight,
  onBoarding,
  checkIn,
  checkedIn,
  loadFlights,
}) {
  const {
    session: platformSession,
    configReady,
    loading,
    error,
    startAuth,
    apiBase,
  } = useCrewPlatform();
  const [flights, setFlights] = useState([]);
  const [plans, setPlans] = useState([]);
  const [plansTick, setPlansTick] = useState(0);
  const [manual, setManual] = useState({
    flight_number: "QR815",
    departure_icao: "DOH",
    arrival_icao: "LHR",
    aircraft_icao: "B777-300ER",
    callsign: "QTR815",
  });
  const [result, setResult] = useState(null);
  const [checkinError, setCheckinError] = useState(null);
  const [loadBusy, setLoadBusy] = useState(false);
  const [loadMsg, setLoadMsg] = useState(null);

  // Roster: fetch when the screen mounts / platform session is up
  // (subscription-style — setState only in the async callback).
  useEffect(() => {
    if (!platformSession?.authenticated) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(
          `${apiBase}/api/crew/session/${platformSession.session_id}/flights`
        );
        if (!cancelled && resp.ok) setFlights(await resp.json());
      } catch {
        /* pass */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [platformSession?.authenticated, platformSession?.session_id, apiBase]);

  // Saved SimBrief flightplans: subscription-style fetch; REFRESH /
  // delete bump the tick to re-run it.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch(`${apiBase}/api/simbrief/flightplans`);
        if (!cancelled && resp.ok) {
          const body = await resp.json();
          setPlans(Array.isArray(body?.plans) ? body.plans : []);
        }
      } catch {
        /* pass */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiBase, plansTick]);

  async function deletePlan(key) {
    try {
      await fetch(`${apiBase}/api/simbrief/flightplans/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      setPlansTick((t) => t + 1);
    } catch {
      /* pass */
    }
  }

  // My Flights "LOAD FLIGHTS" — re-fetch the current SimBrief OFP in the
  // background; the saved-plan list refreshes when the fetch lands.
  async function doLoadFlights() {
    if (loadBusy) return;
    setLoadBusy(true);
    setLoadMsg(null);
    const r = await loadFlights?.();
    setLoadBusy(false);
    if (r?.ok) {
      setPlansTick((t) => t + 1);
      setLoadMsg({ ok: true, text: `Loaded ${r.key} from SimBrief.` });
    } else {
      setLoadMsg({ ok: false, text: r?.detail || "SimBrief fetch failed." });
    }
  }

  // Transient LOAD FLIGHTS status clears itself.
  useEffect(() => {
    if (!loadMsg) return;
    const id = setTimeout(() => setLoadMsg(null), 5000);
    return () => clearTimeout(id);
  }, [loadMsg]);

  // M2b check-in: the local record is the source of truth for the
  // "CHECKED IN" status (crewCheckin). With a platform session the M2
  // backend validation still runs as a best-effort supplement.
  async function doCheckin(f) {
    setResult(null);
    setCheckinError(null);
    const local = checkIn(f.flight_id, session);
    if (!local.ok) {
      setCheckinError(local.error);
      return;
    }
    if (!platformSession?.authenticated) return;
    try {
      const resp = await fetch(`${apiBase}/api/crew/checkin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: platformSession.session_id,
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
    <div className="qr-screen qr-home">
      <TopHeader
        utc={utc}
        center={<span className="qr-topbar__title">HOME</span>}
        right={<QrLogo small />}
      />
      <div className="qr-home__body">
        <Sidebar active="home" onNavigate={onNavigate} pilotName={session?.pilotId} />

        <div className="qr-home__col">
          <CompanyNewsPanel />

          <div className="qr-section-head">
            <h3>MY FLIGHTS</h3>
            <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <button
                className="qr-goldbtn qr-goldbtn--sm"
                onClick={doLoadFlights}
                disabled={loadBusy}
                title="Re-fetch flightplans from SimBrief (background)"
              >
                {loadBusy ? (
                  <RefreshCw size={12} className="qr-spin" />
                ) : (
                  <Download size={12} />
                )}
                {loadBusy ? "LOADING…" : "LOAD FLIGHTS"}
              </button>
              <span className="qr-label">
                CHECK-IN BEFORE YOU OPEN THE FLIGHT
              </span>
            </span>
          </div>

          {loadMsg && (
            <div className={loadMsg.ok ? "qr-notice" : "qr-error"}>
              {loadMsg.ok && <CheckCircle2 size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />}
              {loadMsg.text}
            </div>
          )}

          {!platformSession?.authenticated && (
            <div className="qr-notice">
              {configReady?.ready ? (
                <button className="qr-goldbtn" onClick={startAuth} disabled={loading}>
                  <LogIn size={15} /> {loading ? "Connecting…" : "Sign in with vAMSYS"}
                </button>
              ) : (
                "Roster syncs through the crew session — or enter a flight manually below."
              )}
              {error && <div className="qr-error">{error}</div>}
            </div>
          )}

          {platformSession?.authenticated && (
            <>
              <div className="qr-section-head qr-section-head--compact">
                <h4>FLIGHT ROSTER</h4>
                <button
                  className="qr-linkbtn"
                  onClick={() =>
                    fetch(
                      `${apiBase}/api/crew/session/${platformSession.session_id}/flights`
                    )
                      .then((r) => (r.ok ? r.json() : null))
                      .then((d) => d && setFlights(d))
                      .catch(() => {})
                  }
                >
                  REFRESH
                </button>
              </div>
              {flights.length === 0 && (
                <div className="qr-notice">
                  No flights in the roster{platformSession.local ? " (local session)" : ""} — use manual entry below.
                </div>
              )}
              {flights.map((f) => (
                <button
                  key={f.flight_id}
                  className="qr-flight-row"
                  onClick={() => onOpenFlight(f)}
                >
                  <span className="mono qr-flight-row__no">
                    {f.flight_number || f.callsign || "—"}
                  </span>
                  <span className="qr-flight-row__route mono">
                    {f.departure_icao || "?"} <ArrowRight size={12} /> {f.arrival_icao || "?"}
                  </span>
                  <span className="qr-flight-row__type">{f.aircraft_icao || "—"}</span>
                  {checkedIn(f.flight_id) ? (
                    <span className="qr-flight-row__status qr-flight-row__status--checked">
                      <CheckCircle2 size={12} /> CHECKED IN
                    </span>
                  ) : (
                    <span className="qr-flight-row__status">{f.status || "—"}</span>
                  )}
                  <span style={{ display: "flex", gap: 6 }}>
                    <span
                      className={`qr-chip ${checkedIn(f.flight_id) ? "qr-chip--done" : "qr-chip--ghost"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        doCheckin(f);
                      }}
                    >
                      {checkedIn(f.flight_id) ? "✓ CHECKED IN" : "CHECK IN"}
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

          <div className="qr-section-head qr-section-head--compact">
            <h4>SAVED FLIGHTPLANS</h4>
            <button className="qr-linkbtn" onClick={() => setPlansTick((t) => t + 1)}>
              REFRESH
            </button>
          </div>
          {plans.length === 0 ? (
            <div className="qr-notice">
              No saved SimBrief plans yet — press “LOAD FLIGHTS” above (or “Import New Plan” on the Flightplan screen) to pull your current OFP.
            </div>
          ) : (
            plans.map((p) => {
              const plan = p.flightplan || {};
              const f = {
                flight_id: p.key,
                flight_number: plan.flight_number || null,
                departure_icao: plan.origin || null,
                arrival_icao: plan.destination || null,
                aircraft_icao: plan.aircraft_icao || plan.aircraft || null,
                callsign: plan.callsign || null,
                status: "SAVED",
              };
              return (
                <button
                  key={p.key}
                  className="qr-flight-row"
                  onClick={() => onOpenFlight(f, plan)}
                >
                  <span className="mono qr-flight-row__no">{plan.flight_number || "—"}</span>
                  <span className="qr-flight-row__route mono">
                    {plan.origin || "?"} <ArrowRight size={12} /> {plan.destination || "?"}
                  </span>
                  <span className="qr-flight-row__type">
                    {plan.aircraft_icao || plan.aircraft || "—"}
                  </span>
                  {checkedIn(p.key) ? (
                    <span className="qr-flight-row__status qr-flight-row__status--checked">
                      <CheckCircle2 size={12} /> CHECKED IN
                    </span>
                  ) : (
                    <span className="qr-flight-row__status">SAVED</span>
                  )}
                  <span style={{ display: "flex", gap: 6 }}>
                    <span
                      className={`qr-chip ${checkedIn(p.key) ? "qr-chip--done" : "qr-chip--ghost"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        doCheckin(f);
                      }}
                    >
                      {checkedIn(p.key) ? "✓ CHECKED IN" : "CHECK IN"}
                    </span>
                    <span
                      className="qr-chip qr-chip--ghost"
                      onClick={(e) => {
                        e.stopPropagation();
                        deletePlan(p.key);
                      }}
                    >
                      <Trash2 size={12} />
                    </span>
                  </span>
                </button>
              );
            })
          )}

          <div className="qr-section-head qr-section-head--compact">
            <h4>MANUAL FLIGHT ENTRY</h4>
          </div>
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
                  onOpenFlight(f);
                }}
              >
                <PlaneTakeoff size={15} /> OPEN FLIGHT
              </button>
            </div>
          </div>

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

function ProfileScreen({ utc, onBack, onOpenOptimizer, crewSession, onLogout }) {
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
          <button className="qr-linkbtn" onClick={onBack}>← HOME</button>
          <h3>PILOT PROFILE</h3>
          <div className="qr-idgrid">
            <div><span className="qr-label">PILOT ID</span><strong className="mono">{crewSession?.pilotId || "—"}</strong></div>
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
            {crewSession && (
              <button className="qr-ghostbtn" onClick={onLogout}>
                <LogOut size={14} /> LOG OUT
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─── QR SmartOps (flightplan / route / edto) ──────────────────────── */

function SmartOpsScreen({ utc, flight, ofp, ofpError, importing, tab, setTab, onImportNewPlan, onBack }) {
  const { apiBase } = useCrewPlatform();
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
    (ofpData?.route_distance_nm ??
      (ofpData?.general?.route_distance ? Number(String(ofpData.general.route_distance).replace(/[,\\s]/g, "")) : null)) ??
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
        {onBack && (
          <button className="qr-linkbtn qr-smartops__back" onClick={onBack}>
            ← HOME
          </button>
        )}
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
                  ? `Generated ${ofpData.generated_at ? new Date(ofpData.generated_at).toISOString().slice(5, 16).replace("T", " ") + " Z" : ofp?.created_at ? new Date(ofp.created_at).toISOString().slice(11, 16) + " Z" : "—"} · NAV data AIRAC ${ofpData.airac || "—"}`
                  : simPlan
                    ? "Derived from entered ICAOs + a light fuel model — for display only, never presented as real OFP values."
                    : "Select a flight with a linked SimBrief OFP, or import one below."}
              </span>
              {ofpError && <span className="qr-ofp-banner__err">{ofpError}</span>}
            </div>
            <div className="qr-ofp-banner__actions">
              <button className="qr-goldbtn" onClick={onImportNewPlan} disabled={importing}>
                {importing ? "Importing…" : "Import New Plan"}
              </button>
              {ofpData?.pdf_url && (
                <a className="qr-linkbtn" href={ofpData.pdf_url} target="_blank" rel="noopener noreferrer">
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
        <MapWeatherPanel apiBase={apiBase} utc={utc} flight={flight} />
      )}

      {tab === "edto" && (
        <EdtoScreen flight={flight} ofpData={ofpData} />
      )}

      {tab === "weather" && (
        <MapWeatherPanel apiBase={apiBase} utc={utc} flight={flight} />
      )}
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
