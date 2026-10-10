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

// eslint-disable-next-line no-unused-vars -- Vitest uses the classic JSX transform in component tests.
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  WifiOff,
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
  mapRouteView,
  mapEdtoView,
  mapNotification,
  mapOverviewCards,
  mapTimesRows,
  defaultInboxMessages,
  formatInboxStamp,
  projectMap,
  icaoLatlon,
} from "./qatarMappers.js";
import {
  mapSimStatus,
  mapLiveStrip,
  mapLiveTiming,
  mapApplyTargets,
  mapRouteLive,
  buildLiveOptimizeRequest,
  formatApiError,
} from "./liveMappers.js";
import { useSimTelemetry } from "./useSimTelemetry.js";
import { useNavigraph } from "./useNavigraph.js";
import {
  annotateAirspace,
  mapNavigraphEnvelope,
  mapNavigraphStatus,
  mapNotamMessages,
  mapNotamSummary,
  mapRiskView,
  projectTilePixels,
  routeTileGrid,
  tileGridViewBox,
  tileUrl,
  NAVIGRAPH_TILE_LAYERS,
} from "./navigraphMappers.js";
import CrewLogin from "./CrewLogin.jsx";
import MapWeatherPanel from "./MapWeatherPanel.jsx";
import BriefingPanel from "./BriefingPanel.jsx";
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

/* ─── M3: SimConnect live layer ────────────────────────────────────── */

/**
 * SIM CONNECTED / DISCONNECTED chip for the top header. Purely driven by
 * mapSimStatus — no own state, no invented "connected" case.
 */
function SimChip({ status }) {
  const s = status || { status: "unknown", label: "SIM —", sub: null };
  const Icon = s.status === "connected" ? RefreshCw : s.status === "disconnected" ? WifiOff : AlertTriangle;
  return (
    <span className={`qr-simchip qr-simchip--${s.status}`} title={s.sub || s.label}>
      <i className="qr-simchip__dot" />
      <Icon size={12} />
      {s.label}
      {s.sub && <span className="qr-simchip__sub">{s.sub}</span>}
    </span>
  );
}

/**
 * Live FL / MACH / GS / WIND / FUEL / FLOW strip (Flightplan screen).
 *
 * Every cell renders "—" when SimConnect did not supply the value — there is
 * no static fallback anywhere (AGENTS.md: live optimization must source fuel
 * flow from SimConnect, never a modelled value).
 */
function LiveStrip({ live, timing, connected }) {
  if (!connected) {
    return (
      <div className="qr-notice qr-notice--center">
        Live data unavailable — SimConnect is not connected. Flightplan values are plan-only (SimBrief OFP).
      </div>
    );
  }
  const dev = live?.deviation || null;
  return (
    <div className="qr-livestrip">
      <div className="qr-livecell">
        <span className="qr-label">FL</span>
        <Dash v={live?.flightLevel != null ? `FL${live.flightLevel}` : null} />
        <span className="qr-livecell__sub">
          {live?.altDeviation != null ? `${live.altDeviation >= 0 ? "+" : ""}${live.altDeviation} vs plan` : "vs plan —"}
        </span>
      </div>
      <div className="qr-livecell">
        <span className="qr-label">MACH</span>
        <Dash v={live?.mach != null ? live.mach.toFixed(3) : null} />
        <span className="qr-livecell__sub">
          {live?.groundSpeedKt != null ? `GS ${Math.round(live.groundSpeedKt)} kt` : "GS —"}
        </span>
      </div>
      <div className="qr-livecell">
        <span className="qr-label">WIND</span>
        <Dash
          v={
            live?.windComponentKt != null
              ? `${live.windComponentKt >= 0 ? "TW" : "HW"} ${Math.abs(Math.round(live.windComponentKt))}`
              : null
          }
          unit=" kt"
        />
        <span className="qr-livecell__sub">along track</span>
      </div>
      <div className="qr-livecell">
        <span className="qr-label">FUEL</span>
        <Dash v={live?.fuelRemainingKg != null ? (live.fuelRemainingKg / 1000).toFixed(1) : null} unit=" t" />
        <span className="qr-livecell__sub">on board</span>
      </div>
      <div className="qr-livecell">
        <span className="qr-label">FUEL FLOW</span>
        <Dash v={live?.fuelFlowKgH != null ? Math.round(live.fuelFlowKgH) : null} unit=" kg/h" />
        <span className="qr-livecell__sub">
          {live?.fuelFlowKgH == null
            ? "live data unavailable"
            : live?.fuelFlowSource
              ? String(live.fuelFlowSource).toUpperCase()
              : "SIMCONNECT"}
        </span>
      </div>
      <div className="qr-livecell qr-livecell--dev">
        <span className="qr-label">ETE / FUEL Δ</span>
        <span className="mono qr-value">
          {timing?.ete || "—"}
          {timing?.deltaMin != null && (
            <span className={`qr-unit ${timing.deltaMin > 0 ? "qr-value--neg" : "qr-value--pos"}`}>
              {" "}
              {timing.deltaMin >= 0 ? "+" : ""}
              {timing.deltaMin} min
            </span>
          )}
        </span>
        <span className="qr-livecell__sub">
          {dev != null ? (
            <span className={dev.kg >= 0 ? "qr-value--pos" : "qr-value--neg"}>
              {dev.kg >= 0 ? "+" : ""}
              {dev.t} t vs planned LDG
            </span>
          ) : (
            "fuel deviation —"
          )}
        </span>
      </div>
    </div>
  );
}

/**
 * Optimizer recommendation card with the Apply-into-the-sim button.
 *
 * `rec` comes from mapApplyTargets(/api/optimize response). The button is
 * disabled whenever the recommendation is not applyable or the sim is not
 * connected — no dead buttons, and the backend's error text is shown verbatim.
 */
function RecommendPanel({ rec, connected, onApply, applyState, busy, error }) {
  if (!connected) return null;
  const applyable = Boolean(rec?.applyable);
  return (
    <div className="qr-recommend">
      <div className="qr-recommend__head">
        <Gauge size={15} />
        <h3>OPTIMIZER RECOMMENDATION</h3>
      </div>
      {busy && <div className="qr-recommend__summary">Optimizing against live sim state…</div>}
      {error && <div className="qr-recommend__status qr-recommend__status--err">{error}</div>}
      {!busy && !error && !rec && (
        <div className="qr-recommend__summary">
          No recommendation yet — live telemetry is still warming up.
        </div>
      )}
      {rec?.line && <div className="qr-recommend__line mono">{rec.line}</div>}
      {rec?.summary && <div className="qr-recommend__summary">{rec.summary}</div>}
      {rec?.recommendation && <div className="qr-recommend__summary">{rec.recommendation}</div>}
      {rec?.aircraftConfig && (
        <div className="qr-recommend__summary mono">
          PERF {String(rec.aircraftConfig).toUpperCase()}
          {rec.aircraftConfigSource ? ` · from ${rec.aircraftConfigSource}` : ""}
        </div>
      )}
      {rec && (
        <div className="qr-recommend__actions">
          <button
            className="qr-goldbtn"
            disabled={!applyable || applyState?.busy}
            onClick={() =>
              onApply({
                flightLevel: rec.targets?.flightLevel ?? null,
                mach: rec.targets?.mach ?? null,
                reason: rec.line || null,
              })
            }
          >
            {applyState?.busy ? "Applying…" : "Apply to Sim"}
          </button>
          {!applyable && (
            <span className="qr-recommend__status">
              Current profile is already optimal (or the best strategy is not allowed).
            </span>
          )}
          {applyState && !applyState.busy && applyState.applied && (
            <span className="qr-recommend__status qr-recommend__status--ok">
              <CheckCircle2 size={13} /> Target set in the sim.
            </span>
          )}
          {applyState && !applyState.busy && applyState.error && (
            <span className="qr-recommend__status qr-recommend__status--err">
              <XCircle size={13} /> {applyState.error}
            </span>
          )}
        </div>
      )}
      {Array.isArray(rec?.warnings) && rec.warnings.length > 0 && (
        <div className="qr-recommend__summary">{rec.warnings.join(" · ")}</div>
      )}
    </div>
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
          defaultStation={lastPlan?.origin || flight?.departure_icao || null}
          flight={flight}
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

export function CrewDeskScreen({ utc, onNavigate, pilotName, defaultStation, flight }) {
  const { session, apiBase } = useCrewPlatform();
  const [tab, setTab] = useState("inbox");
  const [notifications, setNotifications] = useState([]);
  const [selectedMsg, setSelectedMsg] = useState(null);
  // Last refresh outcome: { ok, at } or { ok: false, text }. Drives the
  // explicit stale/error marker required by DESIGN.md (G11).
  const [refreshState, setRefreshState] = useState(null);

  // M4: NOTAMs for the active flight's stations, folded into the inbox.
  const stations = useMemo(
    () => [flight?.departure_icao, flight?.arrival_icao].filter(Boolean),
    [flight?.departure_icao, flight?.arrival_icao]
  );
  const navigraph = useNavigraph({ apiBase, stations });
  const notamMessages = useMemo(
    () => mapNotamMessages(navigraph.notams),
    [navigraph.notams]
  );
  const notamStatus = useMemo(
    () => mapNotamSummary(navigraph.notams),
    [navigraph.notams]
  );

  async function loadNotifications() {
    if (!session?.session_id) {
      setRefreshState({
        ok: false,
        text: "No crew session — sign in before refreshing the inbox.",
      });
      return;
    }
    try {
      const resp = await fetch(
        `${apiBase}/api/crew/notifications?session_id=${session.session_id}`
      );
      if (!resp.ok) {
        // Honest failure: the list on screen is whatever was last loaded,
        // and it is marked as such — never silently "refreshed" (G11).
        setRefreshState({
          ok: false,
          text: `Refresh failed (HTTP ${resp.status}) — showing last loaded messages.`,
        });
        return;
      }
      setNotifications((await resp.json()).map(mapNotification));
      setRefreshState({ ok: true, at: Date.now() });
    } catch (e) {
      setRefreshState({
        ok: false,
        text: `Refresh failed (${String(e?.message || e)}) — offline? Showing last loaded messages.`,
      });
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

  // Live NOTAMs come first; the rest of the inbox is unchanged.
  const baseMessages = notifications.length
    ? notifications
    : defaultInboxMessages(flight || null).filter(
        // Drop the static NOTAM placeholder once real NOTAMs exist.
        (m) => !(notamMessages.length && m.kind === "NOTAM")
      );
  const messages = [...notamMessages, ...baseMessages];

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

        {/* Tech Log is a full-width area, not a third inbox column: it
            replaces the message list AND the detail panel (G7). */}
        {tab === "techlog" ? (
          <section className="qr-crewdesk__full">
            <TechPanel embedded />
          </section>
        ) : (
          <>
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
          {refreshState && (
            <div
              className={`qr-inbox__refresh ${refreshState.ok ? "" : "qr-inbox__refresh--err"}`}
              data-testid="inbox-refresh-state"
            >
              <span className={`qr-badge ${refreshState.ok ? "badge--ok" : "badge--notam"}`}>
                {refreshState.ok ? "REFRESHED" : "STALE"}
              </span>
              <span className="qr-inbox__refreshtext">
                {refreshState.ok
                  ? `Last refresh ${new Date(refreshState.at).toISOString().slice(11, 16)}z`
                  : refreshState.text}
              </span>
            </div>
          )}
          <div className="qr-inbox__notam">
            <span className={`qr-badge ${notamStatus.badgeClass}`}>NOTAM</span>
            <span className="qr-inbox__notamtext">{notamStatus.text}</span>
            <button className="qr-linkbtn" onClick={navigraph.refreshNotams}>
              RELOAD
            </button>
          </div>
          <div className="qr-inbox__list">
            {tab === "trash" ? (
              <div className="qr-empty">Trash is empty.</div>
            ) : tab === "preflight" ? (
              <PreflightBriefing onOpenTech={() => setTab("techlog")} />
            ) : tab === "weather" ? (
              <WeatherBriefing apiBase={apiBase} station={defaultStation} />
            ) : (
              messages.map((m) => (
                <button
                  key={m.id}
                  className={`qr-msg ${selectedMsg?.id === m.id ? "qr-msg--selected" : ""} ${
                    m.severity === "critical" ? "qr-msg--critical" : ""
                  }`}
                  onClick={() => setSelectedMsg(m)}
                >
                  <div className="qr-msg__top">
                    <span className={`qr-badge ${m.badge_class}`}>{m.kind}</span>
                    <span className="qr-msg__time mono">
                      {formatInboxStamp(m.timestamp) || "NO TIMESTAMP"}
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
              <div className="qr-detail__meta">
                {selectedMsg.sender}
                {formatInboxStamp(selectedMsg.timestamp)
                  ? ` · ${formatInboxStamp(selectedMsg.timestamp)}`
                  : ""}
              </div>
              <p className={selectedMsg.navigraph ? "qr-detail__notam mono" : ""}>
                {selectedMsg.body}
              </p>
            </>
          ) : (
            <div className="qr-detail__empty">
              <Mail size={44} />
              <h2>No message selected.</h2>
              <span>Choose a message on the left.</span>
            </div>
          )}
        </section>
          </>
        )}
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

function WeatherBriefing({ apiBase, station }) {
  return <BriefingPanel apiBase={apiBase} station={station} />;
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

export function ProfileScreen({ utc, onBack, onOpenOptimizer, crewSession, onLogout }) {
  const { session, selectedProvider, configReady, logout, apiBase } = useCrewPlatform();
  const navigraph = useNavigraph({ apiBase });
  const navView = useMemo(() => mapNavigraphStatus(navigraph.status), [navigraph.status]);
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
            {/* The QR SmartOps interface is the Qatar reference shell for
                every provider in this build. Stating that explicitly keeps
                the AIRLINE row honest against the rendered branding (G9). */}
            <div>
              <span className="qr-label">INTERFACE</span>
              <strong>QR SmartOps (Qatar Airways reference shell)</strong>
            </div>
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

          <h3>NAVIGRAPH SUBSCRIPTION</h3>
          <div className="qr-nav-gate">
            <div className="qr-nav-gate__head">
              <span className={navView.authenticated ? "qr-cfg-ok" : "qr-cfg-miss"}>
                {navView.authenticated ? "✓" : "✗"}
              </span>
              <span>{navView.headline}</span>
              {navigraph.busy && <span className="qr-chip">…</span>}
            </div>
            {navView.rows.map((row) => (
              <div key={row.id} className="qr-configrow">
                <span className={row.allowed ? "qr-cfg-ok" : "qr-cfg-miss"}>
                  {row.allowed ? "✓" : "✗"}
                </span>
                <span>{row.label}</span>
                <span className={`qr-badge ${row.badgeClass}`}>{row.statusLabel}</span>
              </div>
            ))}
            {navView.rows.length === 0 && (
              <div className="qr-notice">
                Navigraph status unavailable — the crew platform did not answer.
              </div>
            )}
            {navigraph.signIn?.status === "pending" && navigraph.signIn.user_code && (
              <div className="qr-nav-gate__device">
                <div>
                  Open{" "}
                  <a
                    href={
                      navigraph.signIn.verification_uri_complete ||
                      navigraph.signIn.verification_uri
                    }
                    target="_blank"
                    rel="noreferrer"
                  >
                    {navigraph.signIn.verification_uri}
                  </a>{" "}
                  and enter this code:
                </div>
                <strong className="mono qr-nav-gate__code">
                  {navigraph.signIn.user_code}
                </strong>
              </div>
            )}
            {navigraph.signIn && !["pending", "authorized"].includes(navigraph.signIn.status) && (
              <div className="qr-notice">
                Navigraph sign-in {navigraph.signIn.status}
                {navigraph.signIn.detail ? ` — ${navigraph.signIn.detail}` : ""}
              </div>
            )}
            <div className="qr-nav-gate__actions">
              {!navView.authenticated && navView.configured && (
                <button className="qr-goldbtn" onClick={navigraph.startSignIn}>
                  <LogIn size={14} /> SIGN IN TO NAVIGRAPH
                </button>
              )}
              {navView.authenticated && (
                <button className="qr-ghostbtn" onClick={navigraph.signOut}>
                  <LogOut size={14} /> NAVIGRAPH SIGN OUT
                </button>
              )}
              <button className="qr-ghostbtn" onClick={navigraph.refreshAll}>
                <RefreshCw size={14} /> REFRESH
              </button>
            </div>
            {navView.rateLimit && (
              <div className="qr-nav-gate__meta mono">
                RATE {navView.rateLimit.rpm}/min
                {navView.rateLimit.backing_off
                  ? ` • backing off ${navView.rateLimit.backoff_seconds_remaining}s`
                  : ""}
                {navView.cache ? ` • CACHE ${navView.cache.entries} entries` : ""}
              </div>
            )}
          </div>

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

  /* ── M3: live SimConnect telemetry + optimizer apply ───────────── */

  // Destination coordinates let the backend derive live remaining distance
  // when no SimBrief route profile is synced.
  const destPos = useMemo(() => (hero.arrival ? icaoLatlon(hero.arrival) : null), [hero.arrival]);
  const { telemetry, reachable, apply, applyState } = useSimTelemetry({
    apiBase,
    destinationLat: destPos?.[0],
    destinationLon: destPos?.[1],
  });

  const simStatus = useMemo(() => mapSimStatus(telemetry, { reachable }), [telemetry, reachable]);
  const simConnected = simStatus.status === "connected";

  const live = useMemo(
    () => mapLiveStrip(telemetry, { fuelLandingT: fuel.landing, cruiseAlt: null }),
    [telemetry, fuel.landing]
  );
  const timing = useMemo(
    () => mapLiveTiming(telemetry, { plannedBlockMin: hero.block_min, sta: hero.eet }),
    [telemetry, hero.block_min, hero.eet]
  );

  // Optimizer re-run against live state. Fires only when the sim is
  // connected — never against invented values (AGENTS.md).
  const [optimizeResult, setOptimizeResult] = useState(null);
  const [optimizeBusy, setOptimizeBusy] = useState(false);
  const [optimizeError, setOptimizeError] = useState(null);
  const lastOptimizeKeyRef = useRef(null);

  // qatar-03 renders ATO/ACT as tappable chips the crew fills in. They are
  // crew-entered actual times — never derived or back-filled from the plan.
  const [actuals, setActuals] = useState({});
  const setActual = useCallback((key, value) => {
    setActuals((prev) => ({ ...prev, [key]: value.replace(/[^\d:]/g, "") }));
  }, []);

  const runOptimize = useCallback(async () => {
    const body = buildLiveOptimizeRequest(telemetry, { flight, ofpData });
    if (!body) {
      // Live state is incomplete (no altitude/weight/Mach/distance yet).
      // Clear instead of leaving a stale recommendation or error on screen;
      // the panel then shows its "telemetry warming up" line.
      setOptimizeResult(null);
      setOptimizeError(null);
      return;
    }
    setOptimizeBusy(true);
    setOptimizeError(null);
    try {
      const resp = await fetch(`${apiBase}/api/optimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({}));
        setOptimizeError(formatApiError(detail, resp.status));
        setOptimizeResult(null);
        return;
      }
      setOptimizeResult(await resp.json());
    } catch (e) {
      setOptimizeError(String(e?.message || e));
      setOptimizeResult(null);
    } finally {
      setOptimizeBusy(false);
    }
  }, [apiBase, telemetry, flight, ofpData]);

  // Re-optimize when the live state changed materially (FL / Mach / weight /
  // remaining distance), not on every 5 s poll.
  useEffect(() => {
    if (!simConnected) {
      lastOptimizeKeyRef.current = null;
      return;
    }
    const p = telemetry?.flightStatePatch || {};
    const key = [
      p.altitudeFt != null ? Math.round(p.altitudeFt / 500) : "x",
      p.mach != null ? p.mach.toFixed(2) : "x",
      p.grossWeightKg != null ? Math.round(p.grossWeightKg / 2000) : "x",
      p.remainingDistanceNm != null ? Math.round(p.remainingDistanceNm / 50) : "x",
    ].join("|");
    if (key === lastOptimizeKeyRef.current) return;
    lastOptimizeKeyRef.current = key;
    runOptimize();
  }, [simConnected, telemetry, runOptimize]);

  const recommendation = useMemo(
    () => (optimizeResult ? mapApplyTargets(optimizeResult, { liveFlightLevel: live.flightLevel }) : null),
    [optimizeResult, live.flightLevel]
  );

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
            <SimChip status={simStatus} />
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

      {tab === "flightplan" && (
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

          <LiveStrip live={live} timing={timing} connected={simConnected} />

          <RecommendPanel
            rec={recommendation}
            connected={simConnected}
            onApply={apply}
            applyState={applyState}
            busy={optimizeBusy}
            error={optimizeError}
          />

          <div className="qr-fuelrow">
            <div className="qr-fuelcell">
              <span className="qr-label">DEVIATION</span>
              <Dash
                v={
                  live.deviation
                    ? `${live.deviation.kg >= 0 ? "+" : ""}${live.deviation.t} t`
                    : fuel.deviation
                }
              />
              <span className="qr-fuelcell__sub">
                {live.deviation ? "live vs planned LDG" : simConnected ? "live data unavailable" : "No active check"}
              </span>
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
                    <td className="mono">
                      <input
                        className="qr-wpt__chip mono"
                        value={actuals[`${i}:ato`] || ""}
                        onChange={(e) => setActual(`${i}:ato`, e.target.value)}
                        placeholder="--:--"
                        aria-label={`ATO ${w.ident || `WP${i - 1}`}`}
                        maxLength={5}
                        inputMode="numeric"
                      />
                    </td>
                    <td className="mono">
                      <input
                        className="qr-wpt__chip mono"
                        value={actuals[`${i}:act`] || ""}
                        onChange={(e) => setActual(`${i}:act`, e.target.value)}
                        placeholder="--:--"
                        aria-label={`ACT ${w.ident || `WP${i - 1}`}`}
                        maxLength={5}
                        inputMode="numeric"
                      />
                    </td>
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

      {tab === "overview" && (
        <OverviewScreen
          hero={hero}
          overview={mapOverviewCards(hero, {
            ofpData,
            simPlan,
            distanceNm,
            simConnected,
            waypointCount: waypoints?.rows?.length ?? null,
            live,
          })}
        />
      )}

      {tab === "times" && (
        <TimesScreen
          hero={hero}
          times={mapTimesRows(hero, { simConnected, timing })}
        />
      )}

      {tab === "briefing" && (
        <div className="qr-briefscreen">
          <div className="qr-screenhead">
            <h2>BRIEFING</h2>
            <span className="qr-screenhead__sub">
              ATIS · METAR · TAF · SIGMET · SIGWX for {hero.departure || "—"} /{" "}
              {hero.arrival || "—"}
            </span>
          </div>
          <BriefingPanel apiBase={apiBase} station={hero.departure || null} />
          {hero.arrival && hero.arrival !== hero.departure && (
            <BriefingPanel apiBase={apiBase} station={hero.arrival} />
          )}
        </div>
      )}

      {tab === "route" && (
        <MapWeatherPanel
          variant="route"
          apiBase={apiBase}
          utc={utc}
          flight={flight}
          telemetry={telemetry}
          simConnected={simConnected}
          live={live}
        />
      )}

      {tab === "edto" && (
        <EdtoScreen flight={flight} ofpData={ofpData} />
      )}

      {tab === "runways" && (
        <RunwaysScreen hero={hero} ofpData={ofpData} />
      )}

      {/* The Weather tab is the same map instance contract as Route — the
          M3 live props are identical (G3) — with the station briefing
          instead of the route IA panels (G2: distinct screens). */}
      {tab === "weather" && (
        <MapWeatherPanel
          variant="weather"
          apiBase={apiBase}
          utc={utc}
          flight={flight}
          telemetry={telemetry}
          simConnected={simConnected}
          live={live}
        />
      )}
    </div>
  );
}

/* ─── Overview (qatar-03 tab set) ──────────────────────────────────── */

function HeroStrip({ hero, title, sub }) {
  return (
    <div className="qr-screenhead">
      <h2>{title}</h2>
      <span className="qr-screenhead__sub">
        {hero?.flight_number || "—"} · {hero?.departure || "—"} → {hero?.arrival || "—"}
        {sub ? ` · ${sub}` : ""}
      </span>
    </div>
  );
}

export function OverviewScreen({ hero, overview }) {
  const view = overview || { cards: [], provenance: "no flightplan source" };
  return (
    <div className="qr-overview">
      <HeroStrip hero={hero} title="OVERVIEW" sub={view.provenance} />
      <div className="qr-overview__grid">
        {view.cards.map((c) => (
          <div className="qr-overview__card" key={c.id}>
            <span className="qr-label">{c.label}</span>
            <Dash v={c.value} />
            <span className="qr-overview__sub">{c.sub}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─── Times (qatar-03 tab set) ─────────────────────────────────────── */

export function TimesScreen({ hero, times }) {
  const view = times || { rows: [], connected: false };
  return (
    <div className="qr-times">
      <HeroStrip
        hero={hero}
        title="TIMES"
        sub={view.connected ? "live SimConnect timing" : "planned times only"}
      />
      <table className="qr-times__table">
        <thead>
          <tr>
            <th>EVENT</th>
            <th>PLANNED</th>
            <th>ACTUAL / LIVE</th>
            <th>SOURCE</th>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => (
            <tr key={r.id}>
              <td>{r.label}</td>
              <td className="mono">{r.planned || "—"}</td>
              <td className="mono">{r.actual || "—"}</td>
              <td className="qr-times__sub">{r.sub}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!view.connected && (
        <div className="qr-notice">
          Actual times require a connected SimConnect session — planned values
          are shown as planned, never as actuals.
        </div>
      )}
    </div>
  );
}

/* ─── Runways ──────────────────────────────────────────────────────── */

export function RunwaysScreen({ hero, ofpData }) {
  const depRwy = ofpData?.origin?.plan_rwy || ofpData?.origin?.rwy || null;
  const arrRwy = ofpData?.destination?.plan_rwy || ofpData?.destination?.rwy || null;
  return (
    <div className="qr-runways">
      <HeroStrip hero={hero} title="RUNWAYS" sub={ofpData ? "from OFP" : "no OFP linked"} />
      <div className="qr-overview__grid">
        <div className="qr-overview__card">
          <span className="qr-label">DEPARTURE {hero?.departure || "—"}</span>
          <Dash v={depRwy} />
          <span className="qr-overview__sub">
            {depRwy ? "planned departure runway (OFP)" : "no runway in the flightplan source"}
          </span>
        </div>
        <div className="qr-overview__card">
          <span className="qr-label">ARRIVAL {hero?.arrival || "—"}</span>
          <Dash v={arrRwy} />
          <span className="qr-overview__sub">
            {arrRwy ? "planned arrival runway (OFP)" : "no runway in the flightplan source"}
          </span>
        </div>
      </div>
      <div className="qr-notice">
        Runway data comes from the linked OFP. Navigraph airport/runway data is
        a licensed datatype and is reported as NOT CONFIGURED in Profile until a
        subscription is present — no runway geometry is invented here.
      </div>
    </div>
  );
}

/* ─── Route (qatar-04) ─────────────────────────────────────────────── */

/**
 * Legacy SVG route view (pre-M5-P). The active Route tab renders
 * `MapWeatherPanel` (MapLibre); this component is kept as the exported
 * standalone SVG fallback. The M3 live state it pioneered — SimConnect
 * aircraft symbol + passed-waypoint marking — is ALSO wired into the
 * active MapLibre Route tab (see `mapLiveOverlay` in liveMappers.js and
 * the `wx-live-ac` / `passed` layers in MapWeatherPanel.jsx), so the live
 * route behaviour is reachable from the UI regardless of this component.
 */
export function RouteScreen({ flight, ofpData, distanceNm, utc, telemetry, simConnected, live, timing }) {
  const { apiBase } = useCrewPlatform();
  const [wxPlaying, setWxPlaying] = useState(false);
  const [wxOffset, setWxOffset] = useState(0);
  const [altMode, setAltMode] = useState("AUTO");
  // M4: Navigraph enroute chart underlay. OFF by default so the maroon
  // qatar-04 look is unchanged until the crew asks for the chart.
  const [chartLayer, setChartLayer] = useState(null);

  const stations = useMemo(
    () => [flight?.departure_icao, flight?.arrival_icao].filter(Boolean),
    [flight?.departure_icao, flight?.arrival_icao]
  );
  const navigraph = useNavigraph({ apiBase, stations });
  const tileGate = navigraph.status?.datatypes?.tile || null;
  const tilesAvailable = Boolean(tileGate?.allowed);

  // WX TIME playback: Play steps the forecast offset forward one hour per
  // 1.5 s and wraps at +12 h; Pause freezes it. The offset is the hour the
  // overlay represents (0 = NOW).
  useEffect(() => {
    if (!wxPlaying) return undefined;
    const id = setInterval(() => {
      setWxOffset((v) => (v >= 12 ? -12 : v + 1));
    }, 1500);
    return () => clearInterval(id);
  }, [wxPlaying]);

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

  // M3 live overlay: aircraft position projected onto the planned route +
  // the set of waypoints already passed. Everything comes from SimConnect;
  // without a live position the overlay simply is not drawn.
  const livePos = useMemo(() => {
    const rs = telemetry?.rawSummary || {};
    const lat = rs.latitude ?? null;
    const lon = rs.longitude ?? null;
    if (!simConnected || lat == null || lon == null) return null;
    return { latitude: lat, longitude: lon };
  }, [telemetry, simConnected]);

  const routeLive = useMemo(
    () => mapRouteLive(view.points, livePos),
    [view.points, livePos]
  );
  const passedIdents = useMemo(
    () => new Set(routeLive.passedIdents || []),
    [routeLive.passedIdents]
  );
  const acXy = useMemo(
    () => (livePos ? projectMap(livePos.latitude, livePos.longitude) : null),
    [livePos]
  );

  // M4 chart-underlay geometry. Only computed when a Navigraph layer is on;
  // the tile count is capped in routeTileGrid so one route view never fans
  // out more than a dozen tile requests.
  const chartGrid = useMemo(
    () => (chartLayer ? routeTileGrid(view.points, { maxTiles: 12, maxZoom: 6 }) : null),
    [chartLayer, view.points]
  );
  const chartVb = useMemo(() => tileGridViewBox(chartGrid), [chartGrid]);
  const chartPts = useMemo(() => {
    if (!chartGrid) return [];
    return (view.points || [])
      .map((p) => ({ ...p, xy: projectTilePixels(p.lat, p.lon, chartGrid) }))
      .filter((p) => p.xy);
  }, [chartGrid, view.points]);
  const chartAcXy = useMemo(
    () =>
      chartGrid && livePos
        ? projectTilePixels(livePos.latitude, livePos.longitude, chartGrid)
        : null,
    [chartGrid, livePos]
  );
  const navStatus = useMemo(
    () => mapNavigraphEnvelope({ status: tileGate?.status || "not_configured" }),
    [tileGate?.status]
  );

  // AIRAC provenance for the waypoint/airspace data (qatar-04 WP table).
  const airspace = useMemo(
    () => annotateAirspace(view.points, navigraph.navdata),
    [view.points, navigraph.navdata]
  );

  return (
    <div className="qr-route">
      <div className="qr-route__map">
        {chartLayer && chartGrid ? (
          <svg
            viewBox={`0 0 ${chartVb.w} ${chartVb.h}`}
            className="qr-route__svg"
            preserveAspectRatio="xMidYMid meet"
          >
            {/* Navigraph enroute chart tiles (proxied + cached by the backend) */}
            {chartGrid.tiles.map((t) => (
              <image
                key={`${t.z}/${t.x}/${t.y}`}
                href={tileUrl(apiBase, chartLayer, t, true)}
                x={(t.x - chartGrid.x0) * 256}
                y={(t.y - chartGrid.y0) * 256}
                width={256}
                height={256}
              />
            ))}
            {chartPts.length > 1 && (
              <polyline
                points={chartPts.map((p) => `${p.xy.x},${p.xy.y}`).join(" ")}
                fill="none"
                className="qr-svg-route"
                strokeWidth="3"
              />
            )}
            {chartPts.map((p, i) => (
              <g key={`c${i}`}>
                <circle cx={p.xy.x} cy={p.xy.y} r="3" className="qr-svg-wp" />
                {p.ident && (
                  <text x={p.xy.x + 6} y={p.xy.y - 6} className="qr-wp-label mono">
                    {p.ident}
                  </text>
                )}
              </g>
            ))}
            {chartAcXy && (
              <path
                d={`M ${chartAcXy.x} ${chartAcXy.y - 9} L ${chartAcXy.x + 8} ${chartAcXy.y + 8} L ${chartAcXy.x} ${chartAcXy.y + 3} L ${chartAcXy.x - 8} ${chartAcXy.y + 8} Z`}
                className="qr-svg-aircraft"
              />
            )}
          </svg>
        ) : (
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
            const passed = p.ident ? passedIdents.has(p.ident) : false;
            return (
              <g key={i}>
                {isEnd ? (
                  <>
                    <circle cx={p.xy.x} cy={p.xy.y} r="6" fill="none" className="qr-svg-end" strokeWidth="2" />
                    <circle cx={p.xy.x} cy={p.xy.y} r="2" className="qr-svg-end-fill" />
                  </>
                ) : (
                  <>
                    <circle cx={p.xy.x} cy={p.xy.y} r="2.5" className={`qr-svg-wp ${passed ? "qr-svg-wp--passed" : ""}`} />
                    <path
                      d={`M ${p.xy.x - 5} ${p.xy.y - 12} L ${p.xy.x + 5} ${p.xy.y - 12} L ${p.xy.x} ${p.xy.y - 4} Z`}
                      className={`qr-svg-wp-tri ${passed ? "qr-svg-tri--passed" : ""}`}
                    />
                  </>
                )}
                {p.ident && (
                  <text x={p.xy.x + 8} y={p.xy.y - 8} className={`qr-wp-label mono ${passed ? "qr-wp-label--passed" : ""}`}>
                    {p.ident}
                    {p.fl ? ` FL${p.fl}` : ""}
                    {passed ? " ✓" : ""}
                  </text>
                )}
              </g>
            );
          })}

          {/* live aircraft symbol (SimConnect position) */}
          {acXy && (
            <g>
              <circle cx={acXy.x} cy={acXy.y} r="11" className="qr-svg-aircraft-halo" />
              <path
                d={`M ${acXy.x} ${acXy.y - 8} L ${acXy.x + 7} ${acXy.y + 7} L ${acXy.x} ${acXy.y + 3} L ${acXy.x - 7} ${acXy.y + 7} Z`}
                className="qr-svg-aircraft"
              />
              <text x={acXy.x + 14} y={acXy.y + 4} className="qr-wp-label mono">
                {live?.flightLevel != null ? `FL${live.flightLevel}` : "LIVE"}
              </text>
            </g>
          )}
        </svg>
        )}

        <div className="qr-route__layers">
          <span className="qr-chip">Layers</span>
          {NAVIGRAPH_TILE_LAYERS.map((l) => (
            <button
              key={l.id}
              className={`qr-wxbtn ${chartLayer === l.id ? "qr-wxbtn--on" : ""}`}
              disabled={!tilesAvailable}
              title={
                tilesAvailable
                  ? `Navigraph ${l.label} chart`
                  : tileGate?.detail || "Navigraph enroute tiles not available"
              }
              onClick={() => setChartLayer(chartLayer === l.id ? null : l.id)}
            >
              {l.label}
            </button>
          ))}
          {!tilesAvailable && (
            <span className={`qr-badge ${navStatus.badgeClass || "badge--notam"}`}>
              {navStatus.label || "NOT SUBSCRIBED"}
            </span>
          )}
          {chartLayer && !chartGrid && (
            <span className="qr-chip">route unknown — no chart grid</span>
          )}
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
          {simConnected ? (
            routeLive.remainingNm != null ? (
              <>
                {" "}&nbsp;•&nbsp; LIVE REM {routeLive.remainingNm} NM
                {timing?.ete ? ` • ETE ${timing.ete}` : ""}
              </>
            ) : (
              <> &nbsp;•&nbsp; live position unavailable</>
            )
          ) : (
            <> &nbsp;•&nbsp; SIM DISCONNECTED — plan only</>
          )}
        </div>
        <div className="qr-route__alt">LHR / LGW</div>
        <div className="qr-route__airac">
          <span className={`qr-badge ${airspace.badgeClass}`}>AIRSPACE</span>
          <span className="mono">
            {airspace.available
              ? `AIRAC ${airspace.cycle || "—"}${airspace.entitled ? "" : " (outdated)"}`
              : airspace.statusLabel}
          </span>
          {!airspace.available && airspace.detail && (
            <span className="qr-route__airacnote">{airspace.detail}</span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ─── EDTO + Risks (qatar-05) ──────────────────────────────────────── */

export function EdtoScreen({ flight, ofpData }) {
  const { apiBase } = useCrewPlatform();
  const staticView = useMemo(() => mapEdtoView(flight, ofpData), [flight, ofpData]);
  const navigraph = useNavigraph({ apiBase });
  // M4: live operational risk + NAT tracks replace the static snapshot when
  // the feed is reachable; otherwise the snapshot stays, clearly labelled.
  const view = useMemo(
    () => mapRiskView(navigraph.risks, staticView),
    [navigraph.risks, staticView]
  );
  const hero = view.hero || {};
  const routeString =
    view.route_string ||
    (hero.departure && hero.arrival ? `${hero.departure} DCT ${hero.arrival}` : null);
  return (
    <div className="qr-edto">
      {/* qatar-05: a FULL-WIDTH route summary bar across the top, with the
          risk content full width underneath — not a 50/50 split with a
          near-empty map half (G10). */}
      <div className="qr-edto__summary" data-testid="edto-summary">
        <div className="qr-edto__summarycell">
          <span className="qr-label">ROUTE</span>
          <strong className="mono">
            {hero.departure || "—"} – {hero.arrival || "—"}
          </strong>
        </div>
        <div className="qr-edto__summarycell qr-edto__summarycell--wide">
          <span className="qr-label">ROUTE STRING</span>
          <strong className="mono">{routeString || "—"}</strong>
        </div>
        <div className="qr-edto__summarycell">
          <span className="qr-label">DISTANCE</span>
          <strong className="mono">
            {view.distance_nm != null ? `${view.distance_nm} NM` : "—"}
          </strong>
        </div>
        <div className="qr-edto__summarycell">
          <span className="qr-label">FLIGHT TIME (PLAN)</span>
          <strong className="mono">{hero.eet || "—"}</strong>
        </div>
        <div className="qr-edto__summarycell">
          <span className="qr-label">ALTERNATE(S)</span>
          <strong className="mono">{hero.alternate || "—"}</strong>
        </div>
      </div>

      <div className="qr-edto__risks">
        <section className="qr-risk-section">
          <div className="qr-risk-section__head">
            <h3>OPERATIONAL RISK INFORMATION</h3>
            <span className={`qr-badge ${view.badgeClass || "badge--dispatch"}`}>
              {view.statusLabel || "STATIC"}
            </span>
            <span className="qr-risk-section__src">{view.sourceLabel}</span>
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
              <div className="qr-risk-row__thumb" title="Country risk map — licensed imagery not configured">
                <span className="qr-risk-row__thumblbl">NO MAP</span>
              </div>
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
              {/* qatar-05 renders four lines per track. */}
              <div className="qr-nat__head">
                <strong>{t.name} {t.direction}</strong>
                <span className="mono qr-nat__valid">{t.valid}</span>
                {t.tmi && <span className="qr-badge badge--ok">TMI {t.tmi}</span>}
              </div>
              <div className="mono qr-nat__track">{t.track}</div>
              <div className="mono qr-nat__levels">
                {Array.isArray(t.levels) && t.levels.length > 0
                  ? t.levels.join(" ")
                  : "FL band not published in this message"}
              </div>
              <div className="mono qr-nat__decoded">
                {t.decoded || "coordinates not decodable from this message"}
              </div>
            </div>
          ))}
        </section>
      </div>
    </div>
  );
}
