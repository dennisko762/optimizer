/**
 * TechPanel — the TECH area of the crew shell (TechLog P1-T6).
 *
 * Shows the selected aircraft's persistent technical state: registration,
 * type, derived technical status, open defects with legal status
 * transitions, the techlog history (newest first) and the maintenance
 * history. Crew-facing: operational state only, never health percentages.
 *
 * Follows the BoardingPanel conventions: themed by the active airline via
 * CSS variables, plain fetch against apiBase, mapper-driven display
 * (techMappers.js).
 */

import { useCallback, useEffect, useState } from "react";
import { useCrewPlatform } from "../useCrewPlatform.js";
import {
  MAINTENANCE_ACTION_TYPES,
  mapMaintenanceAction,
  mapStatusResponse,
  mapTechlogEntry,
  sortTechlogNewestFirst,
  technicalStatusLabel,
} from "./techMappers.js";
import { getSelectedRegistration, setSelectedRegistration } from "./techSelection.js";

const SEVERITY_OPTIONS = ["MINOR", "MAJOR", "CRITICAL"];

/** Map a technical status value to its badge modifier. */
function statusBadgeClass(status) {
  switch (status) {
    case "SERVICEABLE":
      return "tech-status-badge--ok";
    case "DISPATCHABLE_WITH_MEL":
      return "tech-status-badge--mel";
    case "UNDER_REVIEW":
      return "tech-status-badge--review";
    case "OPEN_DEFECTS":
      return "tech-status-badge--open";
    default:
      return "";
  }
}

/** Map a defect status to its chip modifier. */
function defectChipClass(status) {
  switch (status) {
    case "OPEN":
      return "tech-chip--open";
    case "UNDER_REVIEW":
    case "DEFERRED":
    case "MEL_APPLIED":
      return "tech-chip--mel";
    case "RECTIFIED":
      return "tech-chip--ok";
    case "CLOSED":
      return "tech-chip--muted";
    default:
      return "";
  }
}

function fmtDateTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toISOString().slice(0, 16).replace("T", " ");
}

/**
 * Compact technical status card shown on the eDesk (home) area.
 *
 * Tracks the selected aircraft the same way the TECH panel does: the
 * remembered registration if it still exists in the fleet, otherwise the
 * first aircraft. Refetches on mount, so the card always reflects the
 * current selection when the user navigates back to home.
 */
export function TechStatusCard({ onOpenTech }) {
  const { apiBase } = useCrewPlatform();
  const [registration, setRegistration] = useState(null);
  // { reg, status } — a snapshot keyed by the aircraft it was fetched for,
  // so a stale snapshot from a previous selection reads as "loading".
  const [data, setData] = useState(null);

  const base = `${apiBase}/api/crew/technical`;
  const current = data && data.reg === registration ? data : null;
  const loading = Boolean(registration) && !current;

  useEffect(() => {
    let cancelled = false;
    fetch(`${base}/aircraft/`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        if (cancelled) return;
        const rows = Array.isArray(list) ? list : [];
        const remembered = rows.find((a) => a.registration === getSelectedRegistration());
        setRegistration((remembered || rows[0] || null)?.registration || null);
      })
      .catch(() => {
        if (!cancelled) setRegistration(null);
      });
    return () => {
      cancelled = true;
    };
  }, [base]);

  useEffect(() => {
    if (!registration) return;
    let cancelled = false;
    fetch(`${base}/aircraft/${encodeURIComponent(registration)}/status`)
      .then((r) => (r.ok ? r.json() : null))
      .then((raw) => {
        if (!cancelled)
          setData({ reg: registration, status: raw ? mapStatusResponse(raw) : null });
      })
      .catch(() => {
        if (!cancelled) setData({ reg: registration, status: null });
      });
    return () => {
      cancelled = true;
    };
  }, [registration, base]);

  const vm = current ? current.status : null;

  return (
    <div className="tech-status-card">
      <div className="tech-status-card__row">
        <div className="tech-status-card__id">
          <span className="tech-status-card__reg">
            {registration || "No aircraft selected"}
          </span>
          {vm?.type && <span className="tech-status-card__type">{vm.type}</span>}
        </div>
        {vm && (
          <span className={`tech-status-badge ${statusBadgeClass(vm.status)}`}>
            {vm.statusLabel}
          </span>
        )}
      </div>
      <div className="tech-status-card__row">
        <span className="tech-status-card__meta">
          {vm
            ? `${vm.openCount} open defect${vm.openCount === 1 ? "" : "s"}`
            : loading
              ? "Loading…"
              : "Select or create an aircraft in Tech"}
        </span>
        <button className="edesk-btn-small" onClick={onOpenTech}>
          Review Tech Log
        </button>
      </div>
    </div>
  );
}

/**
 * The full TECH area panel.
 */
export default function TechPanel() {
  const { apiBase, session } = useCrewPlatform();

  const [aircraft, setAircraft] = useState([]);
  const [registration, setRegistrationState] = useState(() => getSelectedRegistration());
  const [view, setView] = useState("overview"); // overview | techlog | maintenance
  // { reg, status, entries, maintenance, error } — a snapshot for one aircraft.
  const [data, setData] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(false);
  // A snapshot is only current for the aircraft it was fetched for — stale
  // snapshots (from a previous selection) are treated as "loading".
  const current = data && data.reg === registration ? data : null;
  const loading = Boolean(registration) && !current;

  // Create-aircraft form
  const [showNewAircraft, setShowNewAircraft] = useState(false);
  const [newAircraft, setNewAircraft] = useState({ registration: "", type: "" });

  // Add-defect form
  const [defect, setDefect] = useState({
    ata: "",
    system_component: "",
    pilot_report: "",
    severity: "",
  });

  // Add-maintenance form
  const [maint, setMaint] = useState({
    action_type: "GENERAL",
    description: "",
    performed_by: session?.display_name || "Crew",
    defect_id: "",
  });

  const base = `${apiBase}/api/crew/technical`;

  const loadAircraft = useCallback(async () => {
    const resp = await fetch(`${base}/aircraft/`);
    if (!resp.ok) throw new Error(`Failed to load aircraft (${resp.status})`);
    return resp.json();
  }, [base]);

  const loadSelected = useCallback(
    async (reg) => {
      if (!reg) return;
      const enc = encodeURIComponent(reg);
      const [statusResp, entriesResp, maintResp] = await Promise.all([
        fetch(`${base}/aircraft/${enc}/status`),
        fetch(`${base}/aircraft/${enc}/techlog`),
        fetch(`${base}/aircraft/${enc}/maintenance`),
      ]);
      if (!statusResp.ok) {
        throw new Error(`Aircraft '${reg}' not found (${statusResp.status})`);
      }
      const statusData = await statusResp.json();
      const entryRows = entriesResp.ok ? await entriesResp.json() : [];
      const maintRows = maintResp.ok ? await maintResp.json() : [];

      // Defects per entry (for the Tech Log view), newest first overall.
      const withDefects = await Promise.all(
        entryRows.map(async (row) => {
          const dResp = await fetch(`${base}/techlog/${row.id}/defects`);
          const defects = dResp.ok ? await dResp.json() : [];
          return { row, defects };
        })
      );
      const mapped = withDefects
        .map(({ row, defects }) => {
          const entry = mapTechlogEntry(row, defects.length);
          entry.defectNames = defects.map((d) => d.system_component || d.description || "DEFECT");
          return entry;
        })
        .filter(Boolean);

      return {
        status: mapStatusResponse(statusData),
        entries: sortTechlogNewestFirst(mapped),
        maintenance: maintRows.map(mapMaintenanceAction).filter(Boolean),
      };
    },
    [base]
  );

  // Load the fleet on mount; reload the selected aircraft when it changes.
  useEffect(() => {
    let cancelled = false;
    loadAircraft()
      .then((rows) => {
        if (cancelled) return;
        const list = Array.isArray(rows) ? rows : [];
        setAircraft(list);
        setRegistrationState((current) => {
          if (current && list.some((a) => a.registration === current)) return current;
          const remembered = list.find((a) => a.registration === getSelectedRegistration());
          return remembered ? remembered.registration : list[0]?.registration || null;
        });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [loadAircraft]);

  // Reload the selected aircraft when it changes (or on mount).
  useEffect(() => {
    if (!registration) return;
    let cancelled = false;
    loadSelected(registration)
      .then((d) => {
        if (!cancelled) setData({ reg: registration, ...d, error: null });
      })
      .catch((e) => {
        if (!cancelled)
          setData({
            reg: registration,
            status: null,
            entries: [],
            maintenance: [],
            error: e.message,
          });
      });
    return () => {
      cancelled = true;
    };
  }, [registration, loadSelected]);

  function selectAircraft(reg) {
    setSelectedRegistration(reg || null);
    setRegistrationState(reg || null);
    setActionError(null);
  }

  async function runAction(label, fn) {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(`${label} failed: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  }

  async function createAircraft() {
    const reg = (newAircraft.registration || "").trim().toUpperCase();
    if (!reg) return;
    await runAction("Create aircraft", async () => {
      const resp = await fetch(`${base}/aircraft/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          registration: reg,
          type: newAircraft.type?.trim() || null,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      setAircraft(await loadAircraft());
      selectAircraft(reg);
      setShowNewAircraft(false);
      setNewAircraft({ registration: "", type: "" });
    });
  }

  async function addDefect() {
    if (!registration) return;
    const enc = encodeURIComponent(registration);
    const component = (defect.system_component || "").trim();
    const report = (defect.pilot_report || "").trim();
    const ata = (defect.ata || "").trim();
    if (!component && !report && !ata) return;
    await runAction("Add defect", async () => {
      // A defect lives on a techlog entry: log the report first, then
      // attach the defect to that entry.
      const entryResp = await fetch(`${base}/aircraft/${enc}/techlog`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chapter: ata || null,
          system_component: component || null,
          pilot_report: report || null,
          severity: defect.severity || null,
          source: "PILOT_REPORT",
        }),
      });
      if (!entryResp.ok) {
        const body = await entryResp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${entryResp.status}`);
      }
      const entry = await entryResp.json();
      const description = component || report || (ata ? `ATA ${ata} defect` : "Defect reported");
      const defectResp = await fetch(`${base}/techlog/${entry.id}/defects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description,
          chapter: ata || null,
          system_component: component || null,
          pilot_report: report || null,
          severity: defect.severity || null,
          source: "PILOT_REPORT",
        }),
      });
      if (!defectResp.ok) {
        const body = await defectResp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${defectResp.status}`);
      }
      setDefect({ ata: "", system_component: "", pilot_report: "", severity: "" });
      setData({ reg: registration, ...(await loadSelected(registration)), error: null });
    });
  }

  async function transitionDefect(defectId, newStatus) {
    if (!registration) return;
    await runAction("Update defect status", async () => {
      const resp = await fetch(`${base}/defects/${defectId}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      setData({ reg: registration, ...(await loadSelected(registration)), error: null });
    });
  }

  async function addMaintenance() {
    if (!registration) return;
    const enc = encodeURIComponent(registration);
    if (!(maint.description || "").trim()) return;
    await runAction("Add maintenance action", async () => {
      const resp = await fetch(`${base}/aircraft/${enc}/maintenance`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_type: maint.action_type,
          description: maint.description.trim(),
          performed_by: (maint.performed_by || "").trim() || "Crew",
          defect_id: maint.defect_id ? Number(maint.defect_id) : null,
        }),
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${resp.status}`);
      }
      setMaint((m) => ({ ...m, description: "", defect_id: "" }));
      setData({ reg: registration, ...(await loadSelected(registration)), error: null });
    });
  }

  const status = current?.status || null;
  const entries = current?.entries || [];
  const maintenance = current?.maintenance || [];
  const error = current?.error || null;
  const openDefects = status?.defects || [];

  return (
    <div className="tech-panel">
      {/* Aircraft picker */}
      <div className="tech-header">
        <div className="tech-header-flight">
          <span className="tech-header-title">Tech</span>
          <span className="tech-header-sub">Aircraft Technical Log</span>
        </div>
        <div className="tech-picker">
          <select
            className="boarding-input"
            value={registration || ""}
            onChange={(e) => selectAircraft(e.target.value)}
          >
            <option value="">— select aircraft —</option>
            {aircraft.map((a) => (
              <option key={a.registration} value={a.registration}>
                {a.registration}
                {a.type ? ` (${a.type})` : ""}
              </option>
            ))}
          </select>
          <button className="edesk-btn-small" onClick={() => setShowNewAircraft((s) => !s)}>
            {showNewAircraft ? "Cancel" : "+ New"}
          </button>
        </div>
      </div>

      {showNewAircraft && (
        <div className="tech-form">
          <div className="boarding-inputs">
            <label className="boarding-input-label">
              <span>Registration</span>
              <input
                className="boarding-input"
                value={newAircraft.registration}
                onChange={(e) =>
                  setNewAircraft((p) => ({
                    ...p,
                    registration: e.target.value.toUpperCase(),
                  }))
                }
                placeholder="A7-TEST"
                maxLength={8}
              />
            </label>
            <label className="boarding-input-label">
              <span>Type</span>
              <input
                className="boarding-input"
                value={newAircraft.type}
                onChange={(e) =>
                  setNewAircraft((p) => ({ ...p, type: e.target.value.toUpperCase() }))
                }
                placeholder="A359"
              />
            </label>
          </div>
          <button className="edesk-checkin-btn" onClick={createAircraft} disabled={busy}>
            Create Aircraft
          </button>
        </div>
      )}

      {actionError && <div className="tech-error">{actionError}</div>}

      {!registration ? (
        <div className="tech-empty">
          <p>No aircraft selected.</p>
          <p>Select a registration above or create a new aircraft to start its tech log.</p>
        </div>
      ) : (
        <>
          {/* Status overview */}
          <div className="ofp-grid tech-overview">
            <div className="ofp-item">
              <span className="ofp-item__label">Registration</span>
              <strong className="ofp-item__value">{status?.registration || registration}</strong>
            </div>
            <div className="ofp-item">
              <span className="ofp-item__label">Type</span>
              <strong className="ofp-item__value">{status?.type || "—"}</strong>
            </div>
            <div className="ofp-item">
              <span className="ofp-item__label">Technical Status</span>
              <strong className="ofp-item__value">
                {status && (
                  <span className={`tech-status-badge ${statusBadgeClass(status.status)}`}>
                    {status.statusLabel}
                  </span>
                )}
              </strong>
            </div>
            <div className="ofp-item">
              <span className="ofp-item__label">Open Defects</span>
              <strong className="ofp-item__value">{status ? status.openCount : "—"}</strong>
            </div>
          </div>

          {/* View switcher */}
          <div className="tech-tabs">
            {[
              ["overview", "Open Defects"],
              ["techlog", "Tech Log"],
              ["maintenance", "Maintenance"],
            ].map(([id, label]) => (
              <button
                key={id}
                className={`edesk-btn-small ${view === id ? "tech-tab--active" : ""}`}
                onClick={() => setView(id)}
              >
                {label}
              </button>
            ))}
          </div>

          {loading && <div className="tech-loading">Loading {view}…</div>}
          {error && <div className="tech-error">{error}</div>}

          {/* Open defects + actions */}
          {view === "overview" && !loading && (
            <>
              <div className="tech-section">
                <h4>Open Defects</h4>
                {openDefects.length === 0 && (
                  <div className="tech-empty-small">No open defects.</div>
                )}
                {openDefects.map((d) => (
                  <div key={d.id} className="tech-defect-row">
                    <div className="tech-defect-id">
                      <span className="tech-defect-name">
                        {(d.system_component || d.description || `ATA ${d.ata || "?"}`).toUpperCase()}
                      </span>
                      <span className={`tech-chip ${defectChipClass(d.status)}`}>
                        {technicalStatusLabel(d.status)}
                      </span>
                    </div>
                    <div className="tech-defect-meta">
                      {d.ata && <span>ATA {d.ata}</span>}
                      {d.severity && <span>{d.severity}</span>}
                      {d.pilot_report && <span>{d.pilot_report}</span>}
                      {d.mel_reference && <span>MEL {d.mel_reference}</span>}
                    </div>
                    {(d.legalTransitions || []).length > 0 && (
                      <div className="tech-defect-actions">
                        {d.legalTransitions.map((t) => (
                          <button
                            key={t}
                            className="edesk-btn-small"
                            disabled={busy}
                            onClick={() => transitionDefect(d.id, t)}
                          >
                            {technicalStatusLabel(t)}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="tech-section">
                <h4>Report Defect</h4>
                <div className="tech-form-grid">
                  <label className="boarding-input-label">
                    <span>ATA</span>
                    <input
                      className="boarding-input"
                      value={defect.ata}
                      onChange={(e) =>
                        setDefect((p) => ({ ...p, ata: e.target.value.toUpperCase() }))
                      }
                      placeholder="21"
                      maxLength={3}
                    />
                  </label>
                  <label className="boarding-input-label">
                    <span>System / Component</span>
                    <input
                      className="boarding-input"
                      value={defect.system_component}
                      onChange={(e) =>
                        setDefect((p) => ({ ...p, system_component: e.target.value }))
                      }
                      placeholder="Pack 1 regulation fault"
                    />
                  </label>
                  <label className="boarding-input-label tech-form-wide">
                    <span>Pilot Report</span>
                    <input
                      className="boarding-input"
                      value={defect.pilot_report}
                      onChange={(e) =>
                        setDefect((p) => ({ ...p, pilot_report: e.target.value }))
                      }
                      placeholder="Pack 1 outflow valve failed to open…"
                    />
                  </label>
                  <label className="boarding-input-label">
                    <span>Severity</span>
                    <select
                      className="boarding-input"
                      value={defect.severity}
                      onChange={(e) => setDefect((p) => ({ ...p, severity: e.target.value }))}
                    >
                      <option value="">—</option>
                      {SEVERITY_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <button
                  className="edesk-checkin-btn"
                  onClick={addDefect}
                  disabled={
                    busy ||
                    (!defect.ata && !defect.system_component && !defect.pilot_report)
                  }
                >
                  Add Defect
                </button>
              </div>
            </>
          )}

          {/* Tech Log */}
          {view === "techlog" && !loading && (
            <div className="tech-section">
              <h4>Tech Log</h4>
              {entries.length === 0 && (
                <div className="tech-empty-small">No tech log entries yet.</div>
              )}
              {entries.map((e) => (
                <div key={e.id} className="tech-entry">
                  <div className="tech-entry-head">
                    <span className="tech-entry-date">{fmtDateTime(e.created_at)}</span>
                    <span className={`tech-chip ${defectChipClass(e.status)}`}>
                      {technicalStatusLabel(e.status)}
                    </span>
                  </div>
                  {(e.phase || e.source) && (
                    <div className="tech-defect-meta">
                      {e.phase && <span>{e.phase}</span>}
                      {e.source && <span>{e.source}</span>}
                      {e.chapter && <span>ATA {e.chapter}</span>}
                    </div>
                  )}
                  {(e.system_component || e.pilot_report) && (
                    <div className="tech-entry-text">
                      {(e.system_component ? e.system_component.toUpperCase() : "")}
                      {e.pilot_report ? ` — ${e.pilot_report}` : ""}
                    </div>
                  )}
                  {e.maintenance_action && (
                    <div className="tech-entry-text tech-entry-maintenance">
                      Maint: {e.maintenance_action}
                    </div>
                  )}
                  {e.defectNames.length > 0 && (
                    <div className="tech-defect-meta">
                      <span>{e.defectCount} defect(s)</span>
                      {e.defectNames.map((n, i) => (
                        <span key={i}>{n.toUpperCase()}</span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Maintenance */}
          {view === "maintenance" && !loading && (
            <>
              <div className="tech-section">
                <h4>Maintenance History</h4>
                {maintenance.length === 0 && (
                  <div className="tech-empty-small">No maintenance actions recorded.</div>
                )}
                {maintenance.map((a) => (
                  <div key={a.id} className="tech-entry">
                    <div className="tech-entry-head">
                      <span className="tech-entry-date">{fmtDateTime(a.performed_at)}</span>
                      <span className="tech-chip tech-chip--muted">
                        {technicalStatusLabel(a.action_type)}
                      </span>
                    </div>
                    <div className="tech-entry-text">{a.description}</div>
                    <div className="tech-defect-meta">
                      <span>{a.performed_by}</span>
                      {a.defect_id != null && <span>Defect #{a.defect_id}</span>}
                      <span>{a.id}</span>
                    </div>
                  </div>
                ))}
              </div>

              <div className="tech-section">
                <h4>Add Maintenance Action</h4>
                <div className="tech-form-grid">
                  <label className="boarding-input-label">
                    <span>Action</span>
                    <select
                      className="boarding-input"
                      value={maint.action_type}
                      onChange={(e) => setMaint((p) => ({ ...p, action_type: e.target.value }))}
                    >
                      {MAINTENANCE_ACTION_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {technicalStatusLabel(t)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="boarding-input-label">
                    <span>Linked Defect</span>
                    <select
                      className="boarding-input"
                      value={maint.defect_id}
                      onChange={(e) => setMaint((p) => ({ ...p, defect_id: e.target.value }))}
                    >
                      <option value="">— none —</option>
                      {openDefects.map((d) => (
                        <option key={d.id} value={d.id}>
                          #{d.id} {(d.system_component || d.description || "defect").toUpperCase()}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="boarding-input-label">
                    <span>Performed By</span>
                    <input
                      className="boarding-input"
                      value={maint.performed_by}
                      onChange={(e) => setMaint((p) => ({ ...p, performed_by: e.target.value }))}
                      placeholder="Crew"
                    />
                  </label>
                  <label className="boarding-input-label tech-form-wide">
                    <span>Description</span>
                    <input
                      className="boarding-input"
                      value={maint.description}
                      onChange={(e) => setMaint((p) => ({ ...p, description: e.target.value }))}
                      placeholder="Replaced pack 1 outflow valve, operational check OK"
                    />
                  </label>
                </div>
                <button
                  className="edesk-checkin-btn"
                  onClick={addMaintenance}
                  disabled={busy || !maint.description.trim()}
                >
                  {maint.action_type === "RECTIFICATION" && maint.defect_id
                    ? "Rectify Defect"
                    : "Add Maintenance Action"}
                </button>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
