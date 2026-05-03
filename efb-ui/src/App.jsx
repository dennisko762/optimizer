import React, { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  CloudSun,
  DownloadCloud,
  Fuel,
  Gauge,
  Loader2,
  Map,
  Plane,
  RadioTower,
  Route,
  Settings,
  SlidersHorizontal,
  Users,
  Wifi,
  WifiOff,
} from "lucide-react";

// Empty string = same-origin (when served by FastAPI).
// Override via VITE_API_URL env var for separate deployments.
const API_BASE_URL = import.meta.env.VITE_API_URL ?? "";

const ACTIONS = [
  {
    id: "NORMAL_RECALC",
    label: "Normal",
    short: "NORM",
    icon: Activity,
    description: "Recalculate current economic speed.",
  },
  {
    id: "CONNEX_UPLINK",
    label: "Connex",
    short: "CNX",
    icon: Users,
    description: "Apply AOC passenger connection uplink.",
  },
  {
    id: "TARGET_ON_BLOCK",
    label: "On-Block",
    short: "TIME",
    icon: Clock3,
    description: "Evaluate recovery to desired on-block.",
  },
  {
    id: "REROUTE",
    label: "Reroute",
    short: "RTE",
    icon: Route,
    description: "Recalculate after route distance change.",
  },
  {
    id: "WEATHER_REFRESH",
    label: "Weather",
    short: "WX",
    icon: CloudSun,
    description: "Apply new wind / ISA forecast.",
  },
  {
    id: "FIXED_SPEED_FL",
    label: "Speed/FL",
    short: "SPD",
    icon: Gauge,
    description: "Evaluate fixed Mach or flight level.",
  },
  {
    id: "HOLDING_OR_METERING",
    label: "Arrival",
    short: "ARR",
    icon: RadioTower,
    description: "Account for holding / sequencing.",
  },
];

const EMPTY_FLIGHT_STATE = {
  aircraft: "",
  engineVariant: "",
  aircraftRegistration: "",
  altitudeFt: "",
  grossWeightKg: "",
  mach: "",
  currentCostIndex: "",
  remainingDistanceNm: "",
  routeDistanceNm: "",
  windComponentKt: "",
  isaDeviationC: "",
  fuelRemainingKg: "",
  groundSpeedKt: "",
  paxCount: "",
};

const EMPTY_FLIGHT_CONTEXT = {
  origin: "",
  destination: "",
  plannedBlockTimeMin: "",
  flightNumber: "",
  airline: "",
  sibtUtc: "",
  sobtUtc: "",
  destinationLat: null,
  destinationLon: null,
};

const EMPTY_TELEMETRY_PATCH = {
  altitudeFt: null,
  grossWeightKg: null,
  mach: null,
  windComponentKt: null,
  isaDeviationC: null,
  fuelRemainingKg: null,
  groundSpeedKt: null,
  remainingDistanceNm: null,
};

function getInitialPayload(action, eta = null) {
  switch (action) {
    case "CONNEX_UPLINK":
      return {
        currentEtaUtc: eta?.etaUtc ?? "18:50",
        manualGroups: [{ id: 1, flight: "", dest: "", ltop: "", pax: "", kgPerPax: "" }],
      };
    case "TARGET_ON_BLOCK":
      return {
        currentDelayMin: eta?.currentDelayMin ?? 12,
        targetDelayMin: 0,
        targetOnBlockUtc: "",
      };
    case "REROUTE":
      return { distanceDeltaNm: 45, newRemainingDistanceNm: "", reason: "ATC/weather reroute" };
    case "WEATHER_REFRESH":
      return {
        source: "SimBrief / FMC wind uplink",
        newWindComponentKt: -35,
        newIsaDeviationC: 2,
        expectedWeatherRerouteNm: 0,
      };
    case "FIXED_SPEED_FL":
      return { fixedMach: 0.78, fixedFlightLevel: 330 };
    case "HOLDING_OR_METERING":
      return { expectedHoldingMin: 20, arrivalMeteringDelayMin: 10 };
    default:
      return {};
  }
}

// ─── ETA computation (pure JS, no API needed) ─────────────────────────────────

function computeLiveEta(remainingNm, groundSpeedKt, mach, altFt, isaDev, windKt) {
  const nm = parseFlexibleNumber(remainingNm);
  if (!nm || nm <= 0) return null;

  const gsRaw = parseFlexibleNumber(groundSpeedKt);
  const gsEstimated = !gsRaw ? estimateGroundSpeedKt(mach, altFt, isaDev, windKt) : null;
  const gs = gsRaw || gsEstimated;
  const gsIsEstimated = !gsRaw && !!gsEstimated;

  if (!gs || gs <= 0) return null;

  const remainingTimeMin = (nm / gs) * 60;

  const now = new Date();
  const nowMin = now.getUTCHours() * 60 + now.getUTCMinutes() + now.getUTCSeconds() / 60;
  const etaMin = nowMin + remainingTimeMin;

  const etaH = Math.floor(etaMin / 60) % 24;
  const etaM = Math.round(etaMin % 60) % 60;
  const etaUtc = `${String(etaH).padStart(2, "0")}:${String(etaM).padStart(2, "0")}`;

  return {
    remainingTimeMin: Math.round(remainingTimeMin * 10) / 10,
    etaUtc,
    nowMin,
    etaMin,
    gsIsEstimated,
    groundSpeedKt: Math.round(gs),
  };
}

function computeEta(remainingNm, groundSpeedKt, sibtUtc, lastDelayMin, mach, altFt, isaDev, windKt) {
  const liveEta = computeLiveEta(remainingNm, groundSpeedKt, mach, altFt, isaDev, windKt);
  if (!liveEta || !sibtUtc) return null;

  const parts = sibtUtc.split(":");
  if (parts.length < 2) return null;
  let sibtMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);

  // Overnight wrap
  if (sibtMin - liveEta.nowMin < -12 * 60) sibtMin += 24 * 60;
  if (sibtMin - liveEta.nowMin > 12 * 60) sibtMin -= 24 * 60;

  const delayMin = liveEta.etaMin - sibtMin;

  let status = "ON_TIME";
  if (delayMin >= 30) status = "CRITICAL";
  else if (delayMin >= 15) status = "SIGNIFICANT";
  else if (delayMin >= 5) status = "MINOR";

  const delayLabel =
    delayMin < -0.5
      ? `${Math.abs(delayMin).toFixed(0)} min early`
      : delayMin < 5
      ? "on time"
      : `+${Math.round(delayMin)} min`;

  const change = lastDelayMin !== null ? Math.abs(delayMin - lastDelayMin) : null;
  const statusOrder = ["ON_TIME", "MINOR", "SIGNIFICANT", "CRITICAL"];
  const prevStatus = lastDelayMin !== null ? classifyDelay(lastDelayMin) : null;

  const shouldRecalculate =
    delayMin >= 5 &&
    (lastDelayMin === null || change >= 5 || (prevStatus && statusOrder.indexOf(status) > statusOrder.indexOf(prevStatus)));

  const recalculateReason =
    !shouldRecalculate
      ? null
      : lastDelayMin === null
      ? `Delay of ${Math.round(delayMin)} min detected`
      : change >= 5
      ? `Delay changed by ${Math.round(change)} min (now ${delayLabel})`
      : `Status escalated to ${status} (${delayLabel})`;

  return {
    remainingTimeMin: liveEta.remainingTimeMin,
    etaUtc: liveEta.etaUtc,
    sibtUtc,
    delayMin: Math.round(delayMin * 10) / 10,
    delayStatus: status,
    delayLabel,
    currentDelayMin: Math.max(Math.round(delayMin), 0),
    shouldRecalculate,
    recalculateReason,
    gsIsEstimated: liveEta.gsIsEstimated,
    groundSpeedKt: liveEta.groundSpeedKt,
  };
}

function estimateGroundSpeedKt(mach, altFt, isaDev, windKt) {
  const m = parseFlexibleNumber(mach);
  if (!m || m <= 0) return null;
  const alt = parseFlexibleNumber(altFt) || 35000;
  const isa = parseFlexibleNumber(isaDev) || 0;
  // ISA temperature: below tropopause 36089 ft
  const tempC = (alt < 36089 ? 15 - 0.00198 * alt : -56.5) + isa;
  const sos = 661.5 * Math.sqrt((tempC + 273.15) / 288.15);
  const tas = m * sos;
  const wind = parseFlexibleNumber(windKt) || 0;
  return Math.round(tas + wind);
}

function classifyDelay(delayMin) {
  if (delayMin >= 30) return "CRITICAL";
  if (delayMin >= 15) return "SIGNIFICANT";
  if (delayMin >= 5) return "MINOR";
  return "ON_TIME";
}

function delayColor(status) {
  if (status === "CRITICAL" || status === "SIGNIFICANT") return "var(--red)";
  if (status === "MINOR") return "var(--amber)";
  return "var(--green)";
}

function delayBg(status) {
  if (status === "CRITICAL" || status === "SIGNIFICANT") return "var(--red-soft)";
  if (status === "MINOR") return "var(--amber-soft)";
  return "var(--green-soft)";
}

function normalizeUtcClock(value) {
  if (typeof value !== "string") return null;
  const match = value.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;

  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;

  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function shiftUtcClock(utcClock, deltaMin) {
  const normalized = normalizeUtcClock(utcClock);
  const minutes = parseFlexibleNumber(deltaMin);
  if (!normalized || minutes === null) return null;

  const [hours, mins] = normalized.split(":").map(Number);
  const total = ((hours * 60 + mins + Math.round(minutes)) % 1440 + 1440) % 1440;
  const shiftedHours = Math.floor(total / 60);
  const shiftedMinutes = total % 60;
  return `${String(shiftedHours).padStart(2, "0")}:${String(shiftedMinutes).padStart(2, "0")}`;
}

function resolvePlannedEta(flightContext) {
  const sibtUtc = normalizeUtcClock(flightContext?.sibtUtc);
  if (sibtUtc) {
    return {
      etaUtc: sibtUtc,
      sourceLabel: "SimBrief SIBT",
    };
  }

  const sobtUtc = normalizeUtcClock(flightContext?.sobtUtc);
  const plannedBlockTimeMin = parseFlexibleNumber(flightContext?.plannedBlockTimeMin);
  const derivedEtaUtc = shiftUtcClock(sobtUtc, plannedBlockTimeMin);
  if (derivedEtaUtc) {
    return {
      etaUtc: derivedEtaUtc,
      sourceLabel: "SimBrief SOBT + block",
    };
  }

  return null;
}

function formatTelemetryAge(dataAgeMs, collectorStatus) {
  const age = parseFlexibleNumber(dataAgeMs);
  if (collectorStatus === "warming_up") return "waiting for telemetry";
  if (collectorStatus !== "connected") return "telemetry offline";
  if (age === null) return "waiting for telemetry";
  if (age < 1000) return "live telemetry";
  return `live telemetry · ${(age / 1000).toFixed(1)}s old`;
}

function formatFlightLevelValue(altitudeFt) {
  const altitude = parseFlexibleNumber(altitudeFt);
  if (altitude === null) return "—";
  return `FL${Math.round(altitude / 100)}`;
}

// ─── Utility helpers (unchanged) ─────────────────────────────────────────────

function isFiniteNumber(value) {
  return parseFlexibleNumber(value) !== null;
}

function parseFlexibleNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;

  const text = String(value).trim().replace(/\s+/g, "");
  if (!text) return null;

  let normalized = text;

  if (normalized.includes(",") && normalized.includes(".")) {
    normalized =
      normalized.lastIndexOf(",") > normalized.lastIndexOf(".")
        ? normalized.replaceAll(".", "").replace(",", ".")
        : normalized.replaceAll(",", "");
  } else if (normalized.includes(",")) {
    const parts = normalized.split(",");
    normalized =
      parts.length > 2
        ? parts.join("")
        : parts[1]?.length === 3 && /^[-+]?\d+$/.test(parts[0])
        ? parts.join("")
        : normalized.replace(",", ".");
  }

  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function formatNumber(value, decimals = 0) {
  if (!isFiniteNumber(value)) return "—";
  return parseFlexibleNumber(value).toLocaleString(undefined, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

function formatSigned(value, decimals = 0, suffix = "") {
  if (!isFiniteNumber(value)) return "—";
  const number = parseFlexibleNumber(value);
  const prefix = number > 0 ? "+" : "";
  return `${prefix}${formatNumber(number, decimals)}${suffix}`;
}

function removeEmptyValues(obj) {
  const cleaned = {};
  for (const [key, value] of Object.entries(obj ?? {})) {
    if (value === null || value === undefined || value === "") continue;
    cleaned[key] = value;
  }
  return cleaned;
}

function pick(obj, paths, fallback = null) {
  for (const path of paths) {
    const keys = path.split(".");
    let current = obj;
    let found = true;

    for (const key of keys) {
      if (current && key in current) current = current[key];
      else {
        found = false;
        break;
      }
    }

    if (found && current !== undefined && current !== null) return current;
  }
  return fallback;
}

function normalizeStrategy(strategy) {
  if (!strategy) return null;

  return {
    costIndex: pick(strategy, ["costIndex", "cost_index"], null),
    mach: pick(strategy, ["mach"], null),
    fuelKg: pick(strategy, ["fuelKg", "performance.remainingFuelKg", "performance.remaining_fuel_kg"], null),
    timeMin: pick(strategy, ["timeMin", "performance.remainingTimeMin", "performance.remaining_time_min"], null),
    totalCostEur: pick(strategy, ["totalCostEur", "cost.totalCostEur", "cost.total_cost_eur"], null),
    deltaFuelKg: pick(strategy, ["deltaFuelKg", "delta_fuel_kg"], null),
    deltaCruiseTimeMin: pick(strategy, ["deltaCruiseTimeMin", "delta_time_min"], null),
    gateTimeSavedMin: pick(strategy, ["gateTimeSavedMin", "gate_time_saved_min"], null),
    deltaCostEur: pick(strategy, ["deltaCostEur", "delta_cost_eur"], null),
    allowed: pick(strategy, ["allowed"], null),
    label: pick(strategy, ["label"], null),
  };
}

function normalizeResult(raw) {
  if (!raw) {
    return {
      recommendation: null,
      currentStrategy: null,
      bestStrategy: null,
      strategies: [],
      reasons: [],
      warnings: [],
      scenarioType: null,
      objective: null,
      priority: null,
      operationalData: null,
    };
  }

  const interpreted = pick(raw, ["interpretedScenario", "interpreted", "scenario"], {});
  const strategyListRaw = pick(raw, ["strategies"], []);

  return {
    recommendation: pick(raw, ["recommendation"], null),
    currentStrategy: normalizeStrategy(pick(raw, ["currentStrategy", "current_strategy"], null)),
    bestStrategy: normalizeStrategy(pick(raw, ["bestStrategy", "best_strategy"], null)),
    strategies: Array.isArray(strategyListRaw) ? strategyListRaw.map(normalizeStrategy) : [],
    reasons: pick(interpreted, ["reasons"], []),
    warnings: pick(interpreted, ["warnings"], []),
    scenarioType: pick(interpreted, ["scenarioType", "scenario_type"], null),
    objective: pick(interpreted, ["objective"], null),
    priority: pick(interpreted, ["priority"], null),
    operationalData: pick(raw, ["operationalData", "operational_data"], null),
  };
}

// ─── Small components (unchanged) ────────────────────────────────────────────

function StatusPill({ tone = "neutral", children }) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}

function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function TextInput({ label, value, onChange, placeholder = "" }) {
  return (
    <Field label={label}>
      <input
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

function NumberInput({ label, value, onChange, step = "any", placeholder = "" }) {
  return (
    <Field label={label}>
      <input
        type="text"
        inputMode="decimal"
        data-step={step}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </Field>
  );
}

function Tile({ label, value, sub, highlight, className = "" }) {
  return (
    <div className={`tile ${className}`.trim()} style={highlight ? { borderColor: highlight, background: delayBg(highlight === "var(--red)" ? "CRITICAL" : highlight === "var(--amber)" ? "MINOR" : "ON_TIME") } : undefined}>
      <div className="tile__label">{label}</div>
      <div className="tile__value" style={highlight ? { color: highlight } : undefined}>{value}</div>
      {sub && <div className="tile__sub">{sub}</div>}
    </div>
  );
}

function PageButton({ active, icon: Icon, label, onClick }) {
  return (
    <button className={`page-button ${active ? "page-button--active" : ""}`} onClick={onClick}>
      <Icon size={18} />
      <span>{label}</span>
    </button>
  );
}

function ActionSelector({ selectedAction, onChange }) {
  const selected = ACTIONS.find((a) => a.id === selectedAction);
  return (
    <div className="action-selector">
      <select value={selectedAction} onChange={(e) => onChange(e.target.value)}>
        {ACTIONS.map((a) => (
          <option key={a.id} value={a.id}>{a.label}</option>
        ))}
      </select>
      <ChevronDown size={18} />
      {selected && <div className="action-selector__description">{selected.description}</div>}
    </div>
  );
}

function emptyToNull(value) {
  return value === "" || value === undefined ? null : value;
}

function numberOrNull(value) {
  return parseFlexibleNumber(value);
}

function buildOptimizeRequest({ selectedAction, aircraftConfig, flightState, flightContext, payload }) {
  const cleanedPayload = {};
  for (const [key, value] of Object.entries(payload ?? {})) {
    if (value === "" || value === undefined || value === null) {
      cleanedPayload[key] = null;
    } else if (typeof value === "number") {
      cleanedPayload[key] = value;
    } else {
      const numeric = parseFlexibleNumber(value);
      cleanedPayload[key] = numeric !== null ? numeric : value;
    }
  }

  return {
    action: selectedAction,
    aircraftConfig: aircraftConfig || null,
    flightState: {
      aircraft: emptyToNull(flightState.aircraft),
      engineVariant: emptyToNull(flightState.engineVariant),
      aircraftRegistration: emptyToNull(flightState.aircraftRegistration),
      altitudeFt: numberOrNull(flightState.altitudeFt),
      grossWeightKg: numberOrNull(flightState.grossWeightKg),
      mach: numberOrNull(flightState.mach),
      currentCostIndex: numberOrNull(flightState.currentCostIndex),
      remainingDistanceNm: numberOrNull(flightState.remainingDistanceNm),
      routeDistanceNm: numberOrNull(flightState.routeDistanceNm),
      windComponentKt: numberOrNull(flightState.windComponentKt),
      isaDeviationC: numberOrNull(flightState.isaDeviationC),
      fuelRemainingKg: numberOrNull(flightState.fuelRemainingKg),
      groundSpeedKt: numberOrNull(flightState.groundSpeedKt),
      paxCount: numberOrNull(flightState.paxCount),
    },
    flightContext: {
      origin: emptyToNull(flightContext.origin),
      destination: emptyToNull(flightContext.destination),
      plannedBlockTimeMin: numberOrNull(flightContext.plannedBlockTimeMin),
      flightNumber: emptyToNull(flightContext.flightNumber),
      airline: emptyToNull(flightContext.airline),
      sibtUtc: emptyToNull(flightContext.sibtUtc),
      sobtUtc: emptyToNull(flightContext.sobtUtc),
    },
    payload: cleanedPayload,
  };
}

// ─── Main App ─────────────────────────────────────────────────────────────────


// ─── Manual Connex Input Component ───────────────────────────────────────────

function ConnexGroupsInput({ payload, updatePayload, eta }) {
  const groups = payload.manualGroups ?? [];

  function addGroup() {
    const newId = (groups[groups.length - 1]?.id ?? 0) + 1;
    updatePayload("manualGroups", [
      ...groups,
      { id: newId, flight: "", dest: "", ltop: "", pax: "", kgPerPax: "" },
    ]);
  }

  function removeGroup(id) {
    updatePayload("manualGroups", groups.filter((g) => g.id !== id));
  }

  function updateGroup(id, field, value) {
    updatePayload(
      "manualGroups",
      groups.map((g) => (g.id === id ? { ...g, [field]: value } : g))
    );
  }

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
        <TextInput
          label="ETA UTC"
          value={payload.currentEtaUtc}
          onChange={(v) => updatePayload("currentEtaUtc", v)}
        />
        {eta && (
          <div className="tile" style={{ background: "var(--blue-soft)", borderColor: "rgba(72,166,255,0.3)" }}>
            <div className="tile__label">Computed ETA</div>
            <div className="tile__value" style={{ fontSize: 15 }}>{eta.etaUtc}</div>
            <div className="tile__sub">{eta.delayLabel}</div>
          </div>
        )}
      </div>

      <div style={{
        fontSize: 10, fontWeight: 900, letterSpacing: "0.15em",
        color: "var(--dim)", textTransform: "uppercase", marginBottom: 6
      }}>
        Connections
      </div>

      {/* Column headers */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "60px 50px 64px 46px 60px 28px",
        gap: 4, marginBottom: 4,
      }}>
        {["Flight", "Dest", "LTOP UTC", "PAX", "kg/PAX", ""].map((h) => (
          <div key={h} style={{
            fontSize: 9, fontWeight: 900, letterSpacing: "0.12em",
            color: "var(--dim)", textTransform: "uppercase"
          }}>{h}</div>
        ))}
      </div>

      {/* Group rows */}
      {groups.map((g) => (
        <div key={g.id} style={{
          display: "grid",
          gridTemplateColumns: "60px 50px 64px 46px 60px 28px",
          gap: 4, marginBottom: 5, alignItems: "center",
        }}>
          <input
            style={{ height: 34, border: "1px solid var(--line-soft)", background: "#05070a", color: "var(--text)", padding: "0 7px", fontFamily: "var(--mono)", fontSize: 12 }}
            value={g.flight} placeholder="LH704"
            onChange={(e) => updateGroup(g.id, "flight", e.target.value.toUpperCase())}
          />
          <input
            style={{ height: 34, border: "1px solid var(--line-soft)", background: "#05070a", color: "var(--text)", padding: "0 7px", fontFamily: "var(--mono)", fontSize: 12 }}
            value={g.dest} placeholder="MLE"
            onChange={(e) => updateGroup(g.id, "dest", e.target.value.toUpperCase())}
          />
          <input
            style={{ height: 34, border: "1px solid var(--line-soft)", background: "#05070a", color: "var(--text)", padding: "0 7px", fontFamily: "var(--mono)", fontSize: 12 }}
            value={g.ltop} placeholder="19:10"
            onChange={(e) => updateGroup(g.id, "ltop", e.target.value)}
          />
          <input
            style={{ height: 34, border: "1px solid var(--line-soft)", background: "#05070a", color: "var(--text)", padding: "0 7px", fontFamily: "var(--mono)", fontSize: 12 }}
            type="text" inputMode="numeric"
            value={g.pax} placeholder="8"
            onChange={(e) => updateGroup(g.id, "pax", e.target.value)}
          />
          <input
            style={{ height: 34, border: "1px solid var(--line-soft)", background: "#05070a", color: "var(--text)", padding: "0 7px", fontFamily: "var(--mono)", fontSize: 12 }}
            type="text" inputMode="numeric"
            value={g.kgPerPax} placeholder="35"
            onChange={(e) => updateGroup(g.id, "kgPerPax", e.target.value)}
          />
          <button
            onClick={() => removeGroup(g.id)}
            disabled={groups.length <= 1}
            style={{
              height: 34, width: 28, border: "1px solid var(--line-soft)",
              background: "transparent", color: "var(--dim)", cursor: "pointer",
              fontSize: 14, display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >×</button>
        </div>
      ))}

      <button
        onClick={addGroup}
        style={{
          marginTop: 4, height: 32, border: "1px solid var(--line-soft)",
          background: "transparent", color: "var(--muted)", cursor: "pointer",
          fontSize: 11, fontWeight: 900, letterSpacing: "0.08em",
          textTransform: "uppercase", padding: "0 12px", display: "flex",
          alignItems: "center", gap: 6,
        }}
      >
        + Add Connection
      </button>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState("data");
  const [selectedAction, setSelectedAction] = useState("NORMAL_RECALC");
  const [aircraftConfig, setAircraftConfig] = useState("");
  const [flightState, setFlightState] = useState(EMPTY_FLIGHT_STATE);
  const [flightContext, setFlightContext] = useState(EMPTY_FLIGHT_CONTEXT);
  const [payload, setPayload] = useState(getInitialPayload("NORMAL_RECALC"));
  const [simbriefUsername, setSimbriefUsername] = useState(
    () => localStorage.getItem("simbriefUsername") ?? ""
  );
  const [simbriefLoading, setSimbriefLoading] = useState(false);
  const [simbriefError, setSimbriefError] = useState("");
  const [simbriefLastSync, setSimbriefLastSync] = useState(null);
  const [simbriefWarnings, setSimbriefWarnings] = useState([]);
  const [apiStatus, setApiStatus] = useState("checking");
  const [loading, setLoading] = useState(false);
  const [rawResult, setRawResult] = useState(null);
  const [error, setError] = useState("");

  // ── ETA state ──────────────────────────────────────────────────────────────
  const [etaDismissed, setEtaDismissed] = useState(false);
  const lastEtaDelayRef = useRef(null);
  const simPollInFlightRef = useRef(false);
  const [simConnectStatus, setSimConnectStatus] = useState("unknown");
  const [liveTelemetry, setLiveTelemetry] = useState({
    patch: EMPTY_TELEMETRY_PATCH,
    collectorStatus: "warming_up",
    dataAgeMs: null,
    lastSampleUtc: null,
  });

  const plannedEta = useMemo(
    () => resolvePlannedEta(flightContext),
    [flightContext.plannedBlockTimeMin, flightContext.sibtUtc, flightContext.sobtUtc]
  );
  const telemetryPatch = liveTelemetry.patch;
  const hasLiveTelemetry = simConnectStatus === "connected" && liveTelemetry.collectorStatus === "connected";

  const liveEta = useMemo(() => {
    if (!hasLiveTelemetry) return null;

    return computeLiveEta(
      telemetryPatch.remainingDistanceNm,
      telemetryPatch.groundSpeedKt,
      telemetryPatch.mach,
      telemetryPatch.altitudeFt,
      telemetryPatch.isaDeviationC,
      telemetryPatch.windComponentKt,
    );
  }, [
    hasLiveTelemetry,
    telemetryPatch.remainingDistanceNm,
    telemetryPatch.groundSpeedKt,
    telemetryPatch.mach,
    telemetryPatch.altitudeFt,
    telemetryPatch.isaDeviationC,
    telemetryPatch.windComponentKt,
  ]);

  const eta = useMemo(() => {
    if (!liveEta) return null;

    const result = computeEta(
      telemetryPatch.remainingDistanceNm,
      telemetryPatch.groundSpeedKt,
      plannedEta?.etaUtc ?? null,
      lastEtaDelayRef.current,
      telemetryPatch.mach,
      telemetryPatch.altitudeFt,
      telemetryPatch.isaDeviationC,
      telemetryPatch.windComponentKt,
    );
    if (result) lastEtaDelayRef.current = result.delayMin;
    return result;
  }, [
    liveEta,
    telemetryPatch.remainingDistanceNm,
    telemetryPatch.groundSpeedKt,
    telemetryPatch.mach,
    telemetryPatch.altitudeFt,
    telemetryPatch.isaDeviationC,
    telemetryPatch.windComponentKt,
    plannedEta?.etaUtc,
  ]);

  // Reset dismiss when status improves or ETA recomputes
  useEffect(() => {
    if (!eta || eta.delayStatus === "ON_TIME") setEtaDismissed(false);
  }, [eta?.delayStatus]);

  const showDelayPrompt = eta?.shouldRecalculate && !etaDismissed;

  // ── Result ─────────────────────────────────────────────────────────────────
  const result = useMemo(() => normalizeResult(rawResult), [rawResult]);
  const best = result.bestStrategy;
  const current = result.currentStrategy;

  // ── API health check ───────────────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;
    async function checkHealth() {
      try {
        const response = await fetch(`${API_BASE_URL}/health`);
        if (mounted) setApiStatus(response.ok ? "online" : "offline");
      } catch {
        if (mounted) setApiStatus("offline");
      }
    }
    checkHealth();
    const interval = setInterval(checkHealth, 7000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  // ── SimConnect polling ────────────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;

    async function pollSimConnect() {
      if (simPollInFlightRef.current) return;
      simPollInFlightRef.current = true;

      try {
        const telemetryUrl = new URL(`${API_BASE_URL}/api/simconnect/telemetry`, window.location.origin);
        if (flightContext.destinationLat != null && flightContext.destinationLon != null) {
          telemetryUrl.searchParams.set("destinationLat", String(flightContext.destinationLat));
          telemetryUrl.searchParams.set("destinationLon", String(flightContext.destinationLon));
        }
        if (flightContext.destination) {
          telemetryUrl.searchParams.set("destination", flightContext.destination);
        }

        const response = await fetch(telemetryUrl, {
          signal: AbortSignal.timeout(1500),
        });
        if (!response.ok) return;

        const data = await response.json();
        if (!mounted) return;

        if (data.connected && data.flightStatePatch) {
          setSimConnectStatus("connected");
          setLiveTelemetry((prev) => ({
            ...prev,
            patch: {
              ...prev.patch,
              ...removeEmptyValues(data.flightStatePatch),
            },
            collectorStatus: data.collectorStatus ?? "connected",
            dataAgeMs: data.dataAgeMs ?? null,
            lastSampleUtc: data.lastSampleUtc ?? null,
          }));
          // Merge live values into flightState — only overwrite non-null values
          setFlightState((prev) => {
            const patch = data.flightStatePatch;
            
            const updated = { ...prev };
            if (patch.altitudeFt         != null) updated.altitudeFt         = String(patch.altitudeFt);
            if (patch.grossWeightKg      != null) updated.grossWeightKg      = String(Math.round(patch.grossWeightKg));
            if (patch.mach               != null) updated.mach               = String(patch.mach.toFixed(3));
            if (patch.fuelRemainingKg    != null) updated.fuelRemainingKg    = String(Math.round(patch.fuelRemainingKg));
            if (patch.groundSpeedKt      != null) updated.groundSpeedKt      = String(Math.round(patch.groundSpeedKt));
            if (patch.isaDeviationC      != null) updated.isaDeviationC      = String(patch.isaDeviationC.toFixed(1));
            if (patch.windComponentKt    != null) updated.windComponentKt    = String(Math.round(patch.windComponentKt));
            if (patch.remainingDistanceNm != null) updated.remainingDistanceNm = String(Math.round(patch.remainingDistanceNm));
            return updated;
          });
        } else {
          setSimConnectStatus("disconnected");
          setLiveTelemetry((prev) => ({
            ...prev,
            collectorStatus: data.collectorStatus ?? "disconnected",
            dataAgeMs: data.dataAgeMs ?? prev.dataAgeMs,
            lastSampleUtc: data.lastSampleUtc ?? prev.lastSampleUtc,
          }));
        }
      } catch {
        if (mounted) {
          setSimConnectStatus("disconnected");
          setLiveTelemetry((prev) => ({
            ...prev,
            collectorStatus: "disconnected",
          }));
        }
      } finally {
        simPollInFlightRef.current = false;
      }
    }

    pollSimConnect();
    const interval = setInterval(pollSimConnect, 1_000);
    return () => { mounted = false; clearInterval(interval); };
  }, [flightContext.destination, flightContext.destinationLat, flightContext.destinationLon]);

  // ── State updaters ─────────────────────────────────────────────────────────
  function updateFlightState(key, value) {
    setFlightState((p) => ({ ...p, [key]: value }));
  }

  function updateFlightContext(key, value) {
    setFlightContext((p) => ({ ...p, [key]: value }));
  }

  function updatePayload(key, value) {
    setPayload((p) => ({ ...p, [key]: value }));
  }

  function changeAction(action) {
    setSelectedAction(action);
    setPayload(getInitialPayload(action, eta));
    setEtaDismissed(false);
  }

  function applyEtaDelay() {
    if (!eta) return;
    if (selectedAction === "TARGET_ON_BLOCK") {
      updatePayload("currentDelayMin", eta.currentDelayMin);
    } else if (selectedAction === "CONNEX_UPLINK") {
      updatePayload("currentEtaUtc", eta.etaUtc);
    } else {
      changeAction("TARGET_ON_BLOCK");
    }
    setEtaDismissed(true);
  }

  // ── SimBrief sync ──────────────────────────────────────────────────────────
  async function syncSimBrief() {
    setSimbriefLoading(true);
    setSimbriefError("");
    setSimbriefWarnings([]);

    try {
      const username = encodeURIComponent(simbriefUsername.trim());
      if (!username) throw new Error("SimBrief username is required.");

      const response = await fetch(`${API_BASE_URL}/api/simbrief/sync?username=${username}`);
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || `HTTP ${response.status}`);
      }

      const data = await response.json();
      const flightStatePatch = data.flightStatePatch ?? {};
      const flightContextPatch = data.flightContextPatch ?? {};
      const aircraftInfo = data.aircraftInfo ?? {};

      const { destinationLat, destinationLon, ...restStatePatch } = flightStatePatch;
      setFlightState((p) => ({ ...p, ...removeEmptyValues(restStatePatch) }));
      setFlightContext((p) => ({
        ...p,
        ...removeEmptyValues(flightContextPatch),
        ...(destinationLat != null ? { destinationLat } : {}),
        ...(destinationLon != null ? { destinationLon } : {}),
      }));

      if (destinationLat != null && destinationLon != null) {
        fetch(`${API_BASE_URL}/api/simconnect/destination`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ lat: destinationLat, lon: destinationLon }),
        }).catch(() => {});
      }

      if (aircraftInfo.aircraftConfig) setAircraftConfig(aircraftInfo.aircraftConfig);

      setSimbriefWarnings(data.warnings ?? []);
      setSimbriefLastSync(new Date().toLocaleTimeString());
    } catch (err) {
      setSimbriefError(err instanceof Error ? err.message : String(err));
    } finally {
      setSimbriefLoading(false);
    }
  }

  // ── Optimize ───────────────────────────────────────────────────────────────
  async function runOptimization() {
    setLoading(true);
    setError("");

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 20000);

    try {
      const requestBody = buildOptimizeRequest({ selectedAction, aircraftConfig, flightState, flightContext, payload });

      if (!requestBody.aircraftConfig) throw new Error("Missing aircraft config. Sync SimBrief first.");
      if (!requestBody.flightState.aircraft) throw new Error("Missing aircraft. Sync SimBrief first.");
      if (requestBody.flightState.altitudeFt === null) throw new Error("Missing altitude.");
      if (requestBody.flightState.grossWeightKg === null) throw new Error("Missing gross weight.");
      if (requestBody.flightState.mach === null) throw new Error("Missing Mach.");
      if (requestBody.flightState.remainingDistanceNm === null) throw new Error("Missing remaining distance.");

      const response = await fetch(`${API_BASE_URL}/api/optimize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify(requestBody),
      });

      const text = await response.text();
      if (!response.ok) throw new Error(text || `HTTP ${response.status}`);

      setRawResult(text ? JSON.parse(text) : null);
    } catch (err) {
      if (err.name === "AbortError") {
        setError("Optimization timed out after 20 seconds.");
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  }

  // ── Payload fields ─────────────────────────────────────────────────────────
  function renderPayloadFields() {
    switch (selectedAction) {
      case "CONNEX_UPLINK":
        return <ConnexGroupsInput payload={payload} updatePayload={updatePayload} eta={eta} />;

      case "TARGET_ON_BLOCK":
        return (
          <div className="form-grid form-grid--3">
            <NumberInput
              label="Current Delay"
              value={payload.currentDelayMin}
              onChange={(v) => updatePayload("currentDelayMin", v)}
            />
            <NumberInput label="Target Delay" value={payload.targetDelayMin} onChange={(v) => updatePayload("targetDelayMin", v)} />
            <TextInput label="Target On-Block" value={payload.targetOnBlockUtc} onChange={(v) => updatePayload("targetOnBlockUtc", v)} placeholder="optional" />
          </div>
        );

      case "REROUTE":
        return (
          <div className="form-grid form-grid--3">
            <NumberInput label="Distance Δ NM" value={payload.distanceDeltaNm} onChange={(v) => updatePayload("distanceDeltaNm", v)} />
            <NumberInput label="New Remain NM" value={payload.newRemainingDistanceNm} onChange={(v) => updatePayload("newRemainingDistanceNm", v)} placeholder="optional" />
            <TextInput label="Reason" value={payload.reason} onChange={(v) => updatePayload("reason", v)} />
          </div>
        );

      case "WEATHER_REFRESH":
        return (
          <div className="form-grid form-grid--2">
            <NumberInput label="New Wind KT" value={payload.newWindComponentKt} onChange={(v) => updatePayload("newWindComponentKt", v)} />
            <NumberInput label="New ISA" value={payload.newIsaDeviationC} onChange={(v) => updatePayload("newIsaDeviationC", v)} />
            <NumberInput label="WX Reroute NM" value={payload.expectedWeatherRerouteNm} onChange={(v) => updatePayload("expectedWeatherRerouteNm", v)} />
            <TextInput label="Source" value={payload.source} onChange={(v) => updatePayload("source", v)} />
          </div>
        );

      case "FIXED_SPEED_FL":
        return (
          <div className="form-grid form-grid--2">
            <NumberInput label="Fixed Mach" value={payload.fixedMach} step="0.001" onChange={(v) => updatePayload("fixedMach", v)} />
            <NumberInput label="Fixed FL" value={payload.fixedFlightLevel} onChange={(v) => updatePayload("fixedFlightLevel", v)} />
          </div>
        );

      case "HOLDING_OR_METERING":
        return (
          <div className="form-grid form-grid--2">
            <NumberInput label="Holding Min" value={payload.expectedHoldingMin} onChange={(v) => updatePayload("expectedHoldingMin", v)} />
            <NumberInput label="Metering Min" value={payload.arrivalMeteringDelayMin} onChange={(v) => updatePayload("arrivalMeteringDelayMin", v)} />
          </div>
        );

      default:
        return <div className="hint">No additional input required.</div>;
    }
  }

  const limitedStrategies = [...result.strategies]
    .filter(Boolean)
    .sort((a, b) => {
      const aCost = isFiniteNumber(a.totalCostEur) ? Number(a.totalCostEur) : Infinity;
      const bCost = isFiniteNumber(b.totalCostEur) ? Number(b.totalCostEur) : Infinity;
      return aCost - bCost;
    })
    .slice(0, 6);
  const telemetrySummary = formatTelemetryAge(liveTelemetry.dataAgeMs, liveTelemetry.collectorStatus);
  const plannedEtaLabel = plannedEta?.etaUtc ?? "—";

  // ── Render ─────────────────────────────────────────────────────────────────
  return (
    <div className="tablet-shell">
      <div className="efb-device">

        {/* Topbar */}
        <header className="efb-topbar">
          <div className="efb-brand">
            <Plane size={18} />
            <div>
              <div className="efb-brand__small">{flightState.aircraftRegistration || "REG ----"}</div>
              <div className="efb-brand__title">Dynamic CI</div>
            </div>
          </div>

          <div className="efb-flight">
            <div>
              <span>FLT</span>
              <strong>{flightContext.flightNumber || "----"}</strong>
            </div>
            <div>
              <span>RTE</span>
              <strong>{flightContext.origin || "----"} → {flightContext.destination || "----"}</strong>
            </div>
            <div>
              <span>A/C</span>
              <strong>{flightState.aircraft || "----"}</strong>
            </div>
            {/* ETA badge in header — only when computed */}
            {eta && (
              <div style={{ borderColor: delayColor(eta.delayStatus) + "66" }}>
                <span>ETA</span>
                <strong style={{ color: delayColor(eta.delayStatus) }}>
                  {eta.etaUtc} · {eta.delayLabel}
                </strong>
              </div>
            )}
          </div>

          <div className="efb-status" style={{ display: "flex", gap: 6 }}>
            {simConnectStatus === "connected" && (
              <StatusPill tone="good"><Activity size={12} /> SIM</StatusPill>
            )}
            {apiStatus === "online" ? (
              <StatusPill tone="good"><Wifi size={12} /> Online</StatusPill>
            ) : apiStatus === "checking" ? (
              <StatusPill><Loader2 size={12} className="spin" /> API</StatusPill>
            ) : (
              <StatusPill tone="bad"><WifiOff size={12} /> Offline</StatusPill>
            )}
          </div>
        </header>

        {/* Tabs */}
        <nav className="efb-tabs">
          <PageButton active={page === "ci"} icon={Gauge} label="CI" onClick={() => setPage("ci")} />
          <PageButton active={page === "flight"} icon={Plane} label="Flight" onClick={() => setPage("flight")} />
          <PageButton active={page === "data"} icon={DownloadCloud} label="Data" onClick={() => setPage("data")} />
          <PageButton active={page === "table"} icon={SlidersHorizontal} label="Table" onClick={() => setPage("table")} />
          <PageButton active={page === "settings"} icon={Settings} label="Setup" onClick={() => setPage("settings")} />
        </nav>

        <main className="efb-content">

          {/* ── CI page ── */}
          {page === "ci" && (
            <section className="page page--ci">
              <div className="primary-card recommendation-card">
                <div className="card-title-row">
                  <div>
                    <span className="eyebrow">Recommendation</span>
                    <h2>Cost Index Recalculation</h2>
                  </div>
                  {best?.allowed
                    ? <StatusPill tone="good"><CheckCircle2 size={12} /> Valid</StatusPill>
                    : <StatusPill>Awaiting</StatusPill>}
                </div>

                <div className="recommendation-display">
                  <div>
                    <span>Current</span>
                    <strong>CI {flightState.currentCostIndex || current?.costIndex || "—"}</strong>
                    <small>M{formatNumber(flightState.mach || current?.mach, 3)}</small>
                  </div>
                  <div className="recommendation-separator" />
                  <div>
                    <span>Recommended</span>
                    <strong>CI {best?.costIndex ?? "—"}</strong>
                    <small>{best?.mach != null ? `M${formatNumber(best.mach, 3)}` : "—"}</small>
                  </div>
                </div>

                <div className="mini-kpi-grid">
                  <Tile label="Fuel Δ" value={best ? formatSigned(best.deltaFuelKg, 0, " kg") : "—"} />
                  <Tile label="Gate Δ" value={best ? formatSigned(best.gateTimeSavedMin, 1, " min") : "—"} />
                  <Tile label="Cost Δ" value={best ? formatSigned(best.deltaCostEur, 0, " €") : "—"} />
                </div>

                <div className="advisory-box">
                  {result.recommendation || "Select an operational trigger and run optimization."}
                </div>
              </div>

              <div className="secondary-card">
                <div className="card-title-row compact">
                  <div>
                    <span className="eyebrow">Trigger</span>
                    <h3>Operational Input</h3>
                  </div>
                </div>

                <ActionSelector selectedAction={selectedAction} onChange={changeAction} />

                {/* ── Delay prompt ── */}
                {showDelayPrompt && (
                  <div style={{
                    display: "flex", alignItems: "center", gap: 10,
                    margin: "8px 0",
                    border: `1px solid ${delayColor(eta.delayStatus)}55`,
                    background: delayBg(eta.delayStatus),
                    padding: "9px 12px",
                    fontSize: 12,
                  }}>
                    <AlertTriangle size={14} style={{ color: delayColor(eta.delayStatus), flexShrink: 0 }} />
                    <span style={{ color: delayColor(eta.delayStatus), flex: 1, lineHeight: 1.4 }}>
                      {eta.recalculateReason}
                    </span>
                    <button
                      onClick={applyEtaDelay}
                      style={{
                        border: `1px solid ${delayColor(eta.delayStatus)}88`,
                        background: "transparent",
                        color: delayColor(eta.delayStatus),
                        padding: "4px 10px",
                        fontSize: 11,
                        fontWeight: 900,
                        letterSpacing: "0.06em",
                        cursor: "pointer",
                        whiteSpace: "nowrap",
                      }}
                    >
                      Apply +{eta.currentDelayMin} min
                    </button>
                    <button
                      onClick={() => setEtaDismissed(true)}
                      style={{ border: "none", background: "transparent", color: "var(--dim)", cursor: "pointer", padding: "4px 6px", fontSize: 14 }}
                    >
                      ✕
                    </button>
                  </div>
                )}

                <div className="payload-area">{renderPayloadFields()}</div>

                <button className="main-action-button" onClick={runOptimization} disabled={loading}>
                  {loading ? <Loader2 size={18} className="spin" /> : <Gauge size={18} />}
                  Recalculate CI
                </button>

                {error && (
                  <div className="error-line">
                    <AlertTriangle size={14} />
                    {error}
                  </div>
                )}
              </div>
            </section>
          )}

          {/* ── Flight page ── */}
          {page === "flight" && (
            <section className="page page--flight">
              <div className="flight-arrival-grid">
                <Tile
                  className="flight-arrival-tile"
                  label="Live ETA"
                  value={liveEta?.etaUtc ?? "—"}
                  sub={
                    liveEta
                      ? `${telemetrySummary}${liveEta.gsIsEstimated ? ` · GS ${liveEta.groundSpeedKt} est` : ` · GS ${liveEta.groundSpeedKt} kt`}`
                      : telemetrySummary
                  }
                  highlight={eta ? delayColor(eta.delayStatus) : undefined}
                />
                <Tile
                  className="flight-arrival-tile"
                  label="Planned ETA"
                  value={plannedEtaLabel}
                  sub={plannedEta?.sourceLabel ?? "Sync SimBrief to load planned arrival"}
                />
                <Tile
                  className="flight-arrival-tile"
                  label="Arrival Status"
                  value={eta?.delayLabel ?? "—"}
                  sub={
                    eta
                      ? `${eta.delayStatus.replaceAll("_", " ")} vs planned`
                      : plannedEta
                      ? "waiting for live telemetry"
                      : "need SimBrief planned ETA"
                  }
                  highlight={eta ? delayColor(eta.delayStatus) : undefined}
                />
              </div>

              <div className="flight-telemetry-grid">
                <Tile
                  label="Altitude"
                  value={formatFlightLevelValue(telemetryPatch.altitudeFt)}
                  sub={
                    isFiniteNumber(telemetryPatch.altitudeFt)
                      ? `${formatNumber(telemetryPatch.altitudeFt)} ft · ${telemetrySummary}`
                      : telemetrySummary
                  }
                />
                <Tile label="Mach" value={isFiniteNumber(telemetryPatch.mach) ? `M${formatNumber(telemetryPatch.mach, 3)}` : "—"} />
                <Tile label="Ground Speed" value={isFiniteNumber(telemetryPatch.groundSpeedKt) ? `${formatNumber(telemetryPatch.groundSpeedKt)} kt` : "—"} />
                <Tile label="Remaining" value={isFiniteNumber(telemetryPatch.remainingDistanceNm) ? `${formatNumber(telemetryPatch.remainingDistanceNm)} nm` : "—"} />
                <Tile label="Gross Weight" value={isFiniteNumber(telemetryPatch.grossWeightKg) ? `${formatNumber(telemetryPatch.grossWeightKg)} kg` : "—"} />
                <Tile label="Fuel" value={isFiniteNumber(telemetryPatch.fuelRemainingKg) ? `${formatNumber(telemetryPatch.fuelRemainingKg)} kg` : "—"} />
                <Tile label="Wind" value={formatSigned(telemetryPatch.windComponentKt, 0, " kt")} />
                <Tile label="ISA" value={formatSigned(telemetryPatch.isaDeviationC, 0, "°C")} />
              </div>

              <div className="secondary-card">
                <div className="card-title-row compact">
                  <div>
                    <span className="eyebrow">Operational Inputs</span>
                    <h3>Schedule / Aircraft Options</h3>
                  </div>
                </div>

                <div className="form-grid form-grid--2">
                  <TextInput
                    label="Engine Variant"
                    value={flightState.engineVariant}
                    onChange={(v) => updateFlightState("engineVariant", v)}
                    placeholder="e.g. GE90-115BL"
                  />
                  <NumberInput label="Current CI" value={flightState.currentCostIndex} onChange={(v) => updateFlightState("currentCostIndex", v)} />
                  <TextInput
                    label="SIBT UTC"
                    value={flightContext.sibtUtc}
                    onChange={(v) => updateFlightContext("sibtUtc", v)}
                    placeholder="HH:MM"
                  />
                  <TextInput
                    label="SOBT UTC"
                    value={flightContext.sobtUtc}
                    onChange={(v) => updateFlightContext("sobtUtc", v)}
                    placeholder="HH:MM"
                  />
                </div>

                <div className="hint">
                  Aircraft, flight number and route stay in the header. Altitude and the telemetry values above come only from the live telemetry module.
                </div>
              </div>
            </section>
          )}

          {/* ── Data page ── */}
          {page === "data" && (
            <section className="page page--data">
              <div className="secondary-card">
                <div className="card-title-row">
                  <div>
                    <span className="eyebrow">External Data</span>
                    <h2>SimBrief Sync</h2>
                  </div>
                  {simbriefLastSync
                    ? <StatusPill tone="good">Synced {simbriefLastSync}</StatusPill>
                    : <StatusPill>Manual</StatusPill>}
                </div>

                <div className="simbrief-row">
                  <TextInput label="SimBrief Username" value={simbriefUsername} onChange={(v) => { setSimbriefUsername(v); localStorage.setItem("simbriefUsername", v); }} />
                  <button className="main-action-button secondary" onClick={syncSimBrief} disabled={simbriefLoading}>
                    {simbriefLoading ? <Loader2 size={18} className="spin" /> : <DownloadCloud size={18} />}
                    Sync OFP
                  </button>
                </div>

                <div className="hint">
                  Imports: aircraft, route, altitude, mach, CI, fuel, wind, ISA, SIBT, PAX count.
                </div>

                {simbriefWarnings.length > 0 && (
                  <div className="warning-list">
                    {simbriefWarnings.map((w) => <div key={w}>• {w}</div>)}
                  </div>
                )}

                {simbriefError && (
                  <div className="error-line">
                    <AlertTriangle size={14} />
                    {simbriefError}
                  </div>
                )}
              </div>

              <div className="secondary-card ops-card">
                <div className="card-title-row compact">
                  <div>
                    <span className="eyebrow">AOC Preview</span>
                    <h3>Operational Message</h3>
                  </div>
                </div>
                <div className="ops-display">
                  <div>AOC MSG DISPLAY</div>
                  <div>{selectedAction}</div>
                  <div>FLT {flightContext.flightNumber}</div>
                  <div>RTE {flightContext.origin} {flightContext.destination}</div>
                  <div>A/C {flightState.aircraft}</div>
                  {eta && <div>ETA {eta.etaUtc} SIBT {eta.sibtUtc} {eta.delayLabel.toUpperCase()}</div>}
                  <div>CI RECALC READY</div>
                </div>
              </div>
            </section>
          )}

          {/* ── Table page ── */}
          {page === "table" && (
            <section className="page page--table">
              <div className="secondary-card">
                <div className="card-title-row">
                  <div>
                    <span className="eyebrow">Candidates</span>
                    <h2>Strategy Table</h2>
                  </div>
                  <StatusPill>{result.strategies.length} Rows</StatusPill>
                </div>

                <div className="strategy-list">
                  {limitedStrategies.length === 0 && (
                    <div className="hint center">No candidates yet. Run optimization.</div>
                  )}

                  {limitedStrategies.map((strategy, index) => {
                    const isBest =
                      best &&
                      String(strategy.costIndex) === String(best.costIndex) &&
                      Number(strategy.mach) === Number(best.mach);

                    return (
                      <div
                        className={`strategy-row ${isBest ? "strategy-row--best" : ""}`}
                        key={`${strategy.costIndex}-${strategy.mach}-${index}`}
                      >
                        <div>
                          <strong>CI {strategy.costIndex ?? "—"}</strong>
                          <span>M{formatNumber(strategy.mach, 3)}</span>
                        </div>
                        <div>
                          <span>Fuel</span>
                          <strong>{formatNumber(strategy.fuelKg)} kg</strong>
                        </div>
                        <div>
                          <span>Time</span>
                          <strong>{formatNumber(strategy.timeMin, 1)} min</strong>
                        </div>
                        <div>
                          <span>Cost Δ</span>
                          <strong>{formatSigned(strategy.deltaCostEur, 0, " €")}</strong>
                        </div>
                        {isBest && <StatusPill tone="good">Best</StatusPill>}
                      </div>
                    );
                  })}
                </div>
              </div>
            </section>
          )}

          {/* ── Settings page ── */}
          {page === "settings" && (
            <section className="page page--settings">
              <div className="secondary-card">
                <div className="card-title-row">
                  <div>
                    <span className="eyebrow">Setup</span>
                    <h2>Backend / Config</h2>
                  </div>
                </div>

                <div className="form-grid form-grid--2">
                  <TextInput label="Aircraft Config" value={aircraftConfig} onChange={setAircraftConfig} />
                  <TextInput label="Airline / VA" value={flightContext.airline} onChange={(v) => updateFlightContext("airline", v)} />
                  <NumberInput label="Planned Block min" value={flightContext.plannedBlockTimeMin} onChange={(v) => updateFlightContext("plannedBlockTimeMin", v)} />
                  <NumberInput label="Ground Speed kt" value={flightState.groundSpeedKt} onChange={(v) => updateFlightState("groundSpeedKt", v)} />
                  <NumberInput label="PAX Count" value={flightState.paxCount} onChange={(v) => updateFlightState("paxCount", v)} placeholder="auto from SimBrief" />
                </div>

                <div className="hint">
                  SIBT and PAX count are automatically imported on SimBrief sync. Ground speed and PAX can be set manually if SimConnect is unavailable.
                </div>
              </div>
            </section>
          )}

        </main>
      </div>
    </div>
  );
}
