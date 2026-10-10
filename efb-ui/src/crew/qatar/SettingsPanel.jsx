/**
 * SettingsPanel — the EFB's own Setup screen (card t_97dcde51).
 *
 * Why it lives here and not in the optimizer showcase: SimBrief and
 * Navigraph are *EFB* integrations, so the crew configures them inside the
 * EFB shell (Sidebar → Settings), on the bridge they are actually flying.
 *
 * Honesty rules this panel obeys:
 * - every status pill is driven by a live backend read, never by local
 *   optimism after a save;
 * - "Test SimBrief" performs a REAL fetch and reports the real outcome;
 * - a client secret is write-only — it is sent once and never read back,
 *   so the field renders empty with a "set (hidden)" marker beside it.
 */

// eslint-disable-next-line no-unused-vars -- classic JSX transform in component tests.
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { CheckCircle2, ExternalLink, LogIn, LogOut, RefreshCw, XCircle } from "lucide-react";

import { useNavigraph } from "./useNavigraph.js";
import { mapNavigraphStatus } from "./navigraphMappers.js";
import {
  NAVIGRAPH_PORTAL_URL,
  fetchSettings,
  fetchSimbriefReadiness,
  mapSettingsView,
  mapSimbriefPill,
  saveSettings,
  testSimbrief,
  validateNavigraphClientId,
  validateNavigraphClientSecret,
  validateSimbriefUser,
} from "./settingsApi.js";

function Result({ result, testId }) {
  if (!result) return null;
  return (
    <div
      className={`qr-settings__result ${
        result.ok ? "qr-settings__result--ok" : "qr-settings__result--err"
      }`}
      data-testid={testId}
    >
      {result.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
      <span>{result.message}</span>
    </div>
  );
}

export default function SettingsPanel({ apiBase, sessionId, onSimbriefConfigured }) {
  const [state, setState] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [readiness, setReadiness] = useState(null);

  const [simbriefUser, setSimbriefUser] = useState("");
  const [simbriefResult, setSimbriefResult] = useState(null);
  const [testResult, setTestResult] = useState(null);
  const [savingSimbrief, setSavingSimbrief] = useState(false);
  const [testing, setTesting] = useState(false);

  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [navResult, setNavResult] = useState(null);
  const [savingNav, setSavingNav] = useState(false);

  const navigraph = useNavigraph({ apiBase });
  const navView = useMemo(
    () => mapNavigraphStatus(navigraph.status),
    [navigraph.status]
  );
  const view = useMemo(() => mapSettingsView(state), [state]);
  const pill = useMemo(() => mapSimbriefPill(readiness), [readiness]);

  const reloadSettings = useCallback(async () => {
    const result = await fetchSettings(apiBase, sessionId);
    setLoadError(result.ok ? null : result.error);
    if (result.ok) {
      setState(result.state);
      setSimbriefUser(result.state?.simbrief?.user || "");
    }
    return result;
  }, [apiBase, sessionId]);

  const reloadReadiness = useCallback(async () => {
    setReadiness(await fetchSimbriefReadiness(apiBase));
  }, [apiBase]);

  // Both reads are awaited through an async boundary (and guarded by a
  // cancel flag) so an unmounted screen never writes state — the same
  // pattern useNavigraph uses for its own mount fetches.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!cancelled) await reloadSettings();
      if (!cancelled) await reloadReadiness();
    })();
    return () => {
      cancelled = true;
    };
  }, [reloadSettings, reloadReadiness]);

  async function saveSimbrief() {
    const check = validateSimbriefUser(simbriefUser);
    if (!check.valid) {
      setSimbriefResult({ ok: false, message: check.error });
      return;
    }
    setSavingSimbrief(true);
    setTestResult(null);
    try {
      const result = await saveSettings(apiBase, sessionId, {
        simbrief_user: check.value,
      });
      if (!result.ok) {
        setSimbriefResult({
          ok: false,
          message: result.fieldErrors?.simbrief_user || result.message,
        });
        return;
      }
      setState(result.state);
      setSimbriefResult({
        ok: true,
        message: "SimBrief username saved — it takes effect immediately.",
      });
      // Re-read the live readiness instead of assuming success, then let
      // the shell refetch the OFP so the Flightplan tile stops showing the
      // "not configured" error without a page reload.
      await reloadReadiness();
      await onSimbriefConfigured?.();
    } finally {
      setSavingSimbrief(false);
    }
  }

  async function runSimbriefTest() {
    setTesting(true);
    try {
      setTestResult(await testSimbrief(apiBase));
    } finally {
      setTesting(false);
    }
  }

  async function saveNavigraph() {
    const idCheck = validateNavigraphClientId(clientId);
    const values = {};
    if (!idCheck.valid && !(view.navigraph.clientIdSet && !clientId.trim())) {
      setNavResult({ ok: false, message: idCheck.error });
      return;
    }
    if (idCheck.valid) values.navigraph_client_id = idCheck.value;

    if (clientSecret.trim()) {
      const secretCheck = validateNavigraphClientSecret(clientSecret);
      if (!secretCheck.valid) {
        setNavResult({ ok: false, message: secretCheck.error });
        return;
      }
      values.navigraph_client_secret = secretCheck.value;
    } else if (!view.navigraph.clientSecretSet) {
      setNavResult({
        ok: false,
        message:
          "A Navigraph client secret is required before the account can be linked.",
      });
      return;
    }

    setSavingNav(true);
    try {
      if (Object.keys(values).length === 0) {
        setNavResult({
          ok: false,
          message: "Nothing to save — both Navigraph fields are unchanged.",
        });
        return;
      }
      const result = await saveSettings(apiBase, sessionId, values);
      if (!result.ok) {
        const fieldError =
          result.fieldErrors?.navigraph_client_id ||
          result.fieldErrors?.navigraph_client_secret;
        setNavResult({ ok: false, message: fieldError || result.message });
        return;
      }
      setState(result.state);
      // The secret is write-only: drop it from component state at once.
      setClientSecret("");
      setClientId("");
      setNavResult({
        ok: true,
        message: "Navigraph credentials saved — you can link the account now.",
      });
      await navigraph.refreshStatus();
    } finally {
      setSavingNav(false);
    }
  }

  const device = navigraph.signIn;

  return (
    <div className="qr-settings" data-testid="efb-settings">
      {loadError && (
        <div className="qr-notice" data-testid="settings-load-error">
          Settings unavailable — {loadError}
        </div>
      )}
      {view.notice && (
        <div className="qr-notice" data-testid="settings-env-notice">
          {view.notice}
        </div>
      )}

      {/* ── SimBrief ─────────────────────────────────────────────── */}

      <section className="qr-settings__section">
        <div className="qr-settings__head">
          <h3>SIMBRIEF</h3>
          <span className={`qr-badge ${pill.badgeClass}`} data-testid="simbrief-pill">
            {pill.label}
          </span>
          <button className="qr-linkbtn" onClick={reloadReadiness}>
            <RefreshCw size={13} /> RECHECK
          </button>
        </div>
        <p className="qr-settings__hint">
          The bridge pulls your current OFP from SimBrief using your SimBrief
          account name. Nothing else is stored, and no SimBrief password is
          needed.
        </p>
        <label className="qr-settings__field">
          <span className="qr-label">SIMBRIEF USERNAME OR PILOT ID</span>
          <input
            type="text"
            value={simbriefUser}
            placeholder="e.g. your SimBrief account name"
            data-testid="simbrief-input"
            onChange={(e) => setSimbriefUser(e.target.value)}
          />
        </label>
        <div className="qr-settings__actions">
          <button
            className="qr-goldbtn"
            disabled={savingSimbrief}
            data-testid="simbrief-save"
            onClick={saveSimbrief}
          >
            {savingSimbrief ? "SAVING…" : "SAVE"}
          </button>
          <button
            className="qr-ghostbtn"
            disabled={testing}
            data-testid="simbrief-test"
            onClick={runSimbriefTest}
          >
            {testing ? "TESTING…" : "TEST"}
          </button>
        </div>
        <Result result={simbriefResult} testId="simbrief-save-result" />
        <Result result={testResult} testId="simbrief-test-result" />
      </section>

      {/* ── Navigraph ────────────────────────────────────────────── */}

      <section className="qr-settings__section">
        <div className="qr-settings__head">
          <h3>NAVIGRAPH</h3>
          <span
            className={`qr-badge ${
              navView.authenticated
                ? "badge--ok"
                : navView.configured
                  ? "badge--dispatch"
                  : "badge--notam"
            }`}
            data-testid="navigraph-pill"
          >
            {navView.authenticated
              ? "LINKED"
              : navView.configured
                ? "NOT LINKED"
                : "NOT CONFIGURED"}
          </span>
          <button className="qr-linkbtn" onClick={navigraph.refreshAll}>
            <RefreshCw size={13} /> REFRESH
          </button>
        </div>
        <p className="qr-settings__hint">
          {navView.headline}. Charts, enroute tiles and FMS data need both an
          application credential pair and your own Navigraph account.{" "}
          <a href={NAVIGRAPH_PORTAL_URL} target="_blank" rel="noreferrer">
            Get a client ID at the Navigraph developer portal{" "}
            <ExternalLink size={11} />
          </a>
        </p>

        <label className="qr-settings__field">
          <span className="qr-label">
            NAVIGRAPH CLIENT ID
            {view.navigraph.clientIdSet ? " — SET (VALUE HIDDEN)" : ""}
          </span>
          <input
            type="text"
            value={clientId}
            placeholder={
              view.navigraph.clientIdSet
                ? "Leave blank to keep the stored client ID"
                : "Paste the client ID from the developer portal"
            }
            data-testid="navigraph-client-id"
            onChange={(e) => setClientId(e.target.value)}
          />
        </label>
        <label className="qr-settings__field">
          <span className="qr-label">
            NAVIGRAPH CLIENT SECRET
            {view.navigraph.clientSecretSet ? " — SET (VALUE HIDDEN)" : ""}
          </span>
          <input
            type="password"
            value={clientSecret}
            autoComplete="new-password"
            placeholder={
              view.navigraph.clientSecretSet
                ? "Leave blank to keep the stored secret"
                : "Paste the client secret"
            }
            data-testid="navigraph-client-secret"
            onChange={(e) => setClientSecret(e.target.value)}
          />
        </label>
        <div className="qr-settings__actions">
          <button
            className="qr-goldbtn"
            disabled={savingNav}
            data-testid="navigraph-save"
            onClick={saveNavigraph}
          >
            {savingNav ? "SAVING…" : "SAVE CREDENTIALS"}
          </button>
          {navView.configured && !navView.authenticated && (
            <button
              className="qr-goldbtn"
              data-testid="navigraph-link"
              onClick={navigraph.startSignIn}
            >
              <LogIn size={14} /> LINK NAVIGRAPH ACCOUNT
            </button>
          )}
          {navView.authenticated && (
            <button
              className="qr-ghostbtn"
              data-testid="navigraph-signout"
              onClick={navigraph.signOut}
            >
              <LogOut size={14} /> SIGN OUT
            </button>
          )}
        </div>
        <Result result={navResult} testId="navigraph-save-result" />

        {device?.status === "pending" && device.user_code && (
          <div className="qr-settings__device" data-testid="navigraph-device">
            <div>
              Open{" "}
              <a
                href={device.verification_uri_complete || device.verification_uri}
                target="_blank"
                rel="noreferrer"
              >
                {device.verification_uri_complete || device.verification_uri}
              </a>{" "}
              and enter this code:
            </div>
            <strong className="mono qr-settings__code">{device.user_code}</strong>
            <span className="qr-settings__hint">
              Waiting for Navigraph to confirm — this screen updates itself.
            </span>
          </div>
        )}
        {device && !["pending", "authorized"].includes(device.status) && (
          <div className="qr-notice" data-testid="navigraph-device-state">
            Navigraph sign-in {device.status}
            {device.detail ? ` — ${device.detail}` : ""}
          </div>
        )}

        <div className="qr-configlist" data-testid="navigraph-capabilities">
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
              Navigraph capability status unavailable — the bridge did not answer.
            </div>
          )}
        </div>
      </section>

      {/* ── where settings live ──────────────────────────────────── */}

      <section className="qr-settings__section">
        <h3>WHERE THESE SETTINGS ARE STORED</h3>
        <p className="qr-settings__hint">
          Saved settings are written to the bridge&apos;s gitignored{" "}
          <code>.env</code> file and applied immediately — no restart, and
          nothing is committed to the repository. Secrets are write-only: once
          saved they are never sent back to this screen.
          {view.remoteWritesAllowed
            ? " Remote (tablet) writes are enabled on this bridge."
            : " Settings can only be changed from the machine running the bridge."}
        </p>
      </section>
    </div>
  );
}
