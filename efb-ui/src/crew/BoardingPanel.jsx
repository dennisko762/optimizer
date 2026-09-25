/**
 * BoardingPanel — crew-facing boarding status for the selected flight.
 *
 * Shows flight header, contacts, boarding-time updates, pax/bags progress
 * rings, boarding groups, and weight/fuel grid. Themed by the active airline
 * via CSS variables. All data is crew-entered or derived from SimBrief OFP.
 */

import { useState, useEffect } from "react";
import { useCrewPlatform } from "./useCrewPlatform.js";
import {
  ringPercent,
} from "./boardingMappers.js";

/**
 * SVG progress ring component.
 */
function ProgressRing({ label, current, planned, percent, color }) {
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (percent / 100) * circumference;

  return (
    <div className="boarding-ring">
      <svg viewBox="0 0 120 120" className="boarding-ring-svg">
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          stroke="var(--airline-surface)"
          strokeWidth="8"
        />
        <circle
          cx="60"
          cy="60"
          r={radius}
          fill="none"
          stroke={color || "var(--airline-accent)"}
          strokeWidth="8"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 60 60)"
          style={{ transition: "stroke-dashoffset 0.5s ease" }}
        />
        <text
          x="60"
          y="54"
          textAnchor="middle"
          fill="var(--airline-text)"
          fontSize="18"
          fontWeight="bold"
        >
          {Math.round(percent)}%
        </text>
        <text
          x="60"
          y="72"
          textAnchor="middle"
          fill="var(--airline-text-secondary)"
          fontSize="11"
        >
          {current} / {planned}
        </text>
      </svg>
      <span className="boarding-ring-label">{label}</span>
    </div>
  );
}

/**
 * Editable number input for boarding fields.
 */
function BoardingInput({ label, value, onChange, min = 0, step = 1 }) {
  return (
    <label className="boarding-input-label">
      <span>{label}</span>
      <input
        type="number"
        className="boarding-input"
        value={value}
        onChange={(e) => onChange(Math.max(min, parseFloat(e.target.value) || 0))}
        min={min}
        step={step}
      />
    </label>
  );
}

export default function BoardingPanel({ flightId, onClose }) {
  const { session, apiBase } = useCrewPlatform();
  const [state, setState] = useState({
    paxAte: 0,
    paxPlanned: 0,
    bagsLoaded: 0,
    bagsExpected: 0,
    contacts: [],
    updates: [],
    groups: [],
    oewKg: 0,
    paxKgEach: 84,
    bagKgEach: 15,
    cargoKg: 0,
    fuelKg: 0,
  });
  const [loading] = useState(false);
  const [error, setError] = useState(null);

  // Fetch boarding state on mount
  useEffect(() => {
    if (!session?.session_id || !flightId) return;
    let cancelled = false;
    fetch(
      `${apiBase}/api/crew/boarding?session_id=${session.session_id}&flight_id=${flightId}`
    )
      .then((r) => {
        if (!r.ok) throw new Error("Failed to load boarding");
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        const w = data.weights ?? {};
        setState((prev) => ({
          ...prev,
          paxAte: data.pax_ring?.current ?? 0,
          paxPlanned: data.pax_ring?.planned ?? 0,
          bagsLoaded: data.bags_ring?.current ?? 0,
          bagsExpected: data.bags_ring?.planned ?? 0,
          contacts: data.contacts ?? [],
          updates: data.updates ?? [],
          groups: data.groups ?? [],
          header: data.header ?? {},
          weights: w,
          conflicts: data.conflicts ?? [],
          oewKg: w.oew_kg ?? prev.oewKg,
          paxKgEach: w.pax_kg_each ?? prev.paxKgEach,
          bagKgEach: w.bag_kg_each ?? prev.bagKgEach,
          cargoKg: w.cargo_kg ?? prev.cargoKg,
          fuelKg: w.fuel_kg ?? prev.fuelKg,
        }));
      })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [session?.session_id, flightId, apiBase]);

  function pushUpdate(patch) {
    if (!session?.session_id || !flightId) return;
    setState((prev) => {
      const newState = { ...prev, ...patch };
      fetch(`${apiBase}/api/crew/boarding/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: session.session_id,
          flight_id: flightId,
          pax_ate: newState.paxAte,
          pax_planned: newState.paxPlanned,
          bags_loaded: newState.bagsLoaded,
          bags_expected: newState.bagsExpected,
          oew_kg: newState.oewKg,
          pax_kg_each: newState.paxKgEach,
          bag_kg_each: newState.bagKgEach,
          cargo_kg: newState.cargoKg,
          fuel_kg: newState.fuelKg,
        }),
      })
        .then((r) => r.json())
        .then((data) => {
          setState((s) => ({
            ...s,
            weights: data.weights ?? s.weights,
            conflicts: data.conflicts ?? [],
          }));
        })
        .catch(() => {});
      return newState;
    });
  }

  const paxPercent = ringPercent(state.paxAte, state.paxPlanned);
  const bagsPercent = ringPercent(state.bagsLoaded, state.bagsExpected);

  const header = state.header || {};
  const weights = state.weights || {};
  const conflicts = state.conflicts || [];

  if (loading) {
    return (
      <div className="boarding-panel">
        <div className="boarding-loading">Loading boarding data…</div>
      </div>
    );
  }

  if (!session?.session_id) {
    return (
      <div className="boarding-panel">
        <div className="boarding-empty">
          <p>No crew session active.</p>
          <p>
            Sign in through vAMSYS from the eDesk tile to load your flight,
            then enter boarding actuals here.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="boarding-panel">
      {/* Flight Header */}
      <div className="boarding-header">
        <div className="boarding-header-flight">
          <span className="boarding-flight-number">
            {header.flightNumber || header.flight_number || "—"}
          </span>
          <span className="boarding-route">{header.route || ""}</span>
        </div>
        <div className="boarding-header-times">
          {header.sobt && <span>SOBT {header.sobt}</span>}
          {header.sibt && <span>SIBT {header.sibt}</span>}
          {header.blockTime || header.block_time ? (
            <span>Block {header.blockTime || header.block_time} min</span>
          ) : null}
        </div>
        {onClose && (
          <button className="boarding-close-btn" onClick={onClose}>
            ✕
          </button>
        )}
      </div>

      {/* Conflict badges */}
      {conflicts.length > 0 && (
        <div className="boarding-conflicts">
          {conflicts.map((c, i) => (
            <div key={i} className="boarding-conflict-badge">
              ⚠ {c.field}: crew={c.crew_value ?? c.crewValue}, SimBrief=
              {c.simbrief_value ?? c.simbriefValue}
            </div>
          ))}
        </div>
      )}

      {/* Progress Rings */}
      <div className="boarding-rings">
        <ProgressRing
          label="Passengers"
          current={state.paxAte}
          planned={state.paxPlanned}
          percent={paxPercent}
          color="var(--airline-accent)"
        />
        <ProgressRing
          label="Bags"
          current={state.bagsLoaded}
          planned={state.bagsExpected}
          percent={bagsPercent}
          color="var(--airline-primary)"
        />
      </div>

      {/* Boarding Inputs */}
      <div className="boarding-inputs">
        <BoardingInput
          label="Pax Aboard"
          value={state.paxAte}
          onChange={(v) => pushUpdate({ paxAte: v })}
        />
        <BoardingInput
          label="Pax Planned"
          value={state.paxPlanned}
          onChange={(v) => pushUpdate({ paxPlanned: v })}
        />
        <BoardingInput
          label="Bags Loaded"
          value={state.bagsLoaded}
          onChange={(v) => pushUpdate({ bagsLoaded: v })}
        />
        <BoardingInput
          label="Bags Expected"
          value={state.bagsExpected}
          onChange={(v) => pushUpdate({ bagsExpected: v })}
        />
      </div>

      {/* Boarding Groups */}
      {state.groups.length > 0 && (
        <div className="boarding-groups">
          <h4>Boarding Groups</h4>
          {state.groups.map((g, i) => (
            <div
              key={i}
              className={`boarding-group ${g.inProgress ? "boarding-group-active" : ""}`}
            >
              <span>Group {i + 1}</span>
              <span>{g.boardingTime}</span>
              <span>{g.plannedPax} pax</span>
            </div>
          ))}
        </div>
      )}

      {/* Weight & Fuel Grid */}
      <div className="boarding-weights">
        <h4>Weight &amp; Fuel (kg)</h4>
        <div className="boarding-weight-grid">
          <BoardingInput
            label="OEW (empty weight)"
            value={state.oewKg}
            step={50}
            onChange={(v) => pushUpdate({ oewKg: v })}
          />
          <BoardingInput
            label="Pax Weight (each)"
            value={state.paxKgEach}
            step={1}
            onChange={(v) => pushUpdate({ paxKgEach: v })}
          />
          <BoardingInput
            label="Bag Weight (each)"
            value={state.bagKgEach}
            step={1}
            onChange={(v) => pushUpdate({ bagKgEach: v })}
          />
          <BoardingInput
            label="Cargo"
            value={state.cargoKg}
            step={50}
            onChange={(v) => pushUpdate({ cargoKg: v })}
          />
          <BoardingInput
            label="Fuel"
            value={state.fuelKg}
            step={50}
            onChange={(v) => pushUpdate({ fuelKg: v })}
          />
          <div className="boarding-weight-item boarding-weight-item--derived">
            <span>ZFW</span>
            <span>{Math.round(weights.zfw_kg ?? 0)} kg</span>
          </div>
          <div className="boarding-weight-item boarding-weight-item--derived">
            <span>TOW</span>
            <span>{Math.round(weights.tow_kg ?? 0)} kg</span>
          </div>
        </div>
      </div>

      {error && <div className="boarding-error">{error}</div>}
    </div>
  );
}
