/**
 * BriefingPanel — aggregated aviation briefing UI (ATIS/METAR/TAF/SIGMET/
 * lightning/SIGWX). Fetches only internal /api/crew/weather/briefing/...
 * routes (never upstream services directly) and renders explicit
 * state badges so a disabled/unavailable/gated source never looks live.
 *
 * The three ATIS/briefing variants are rendered as separate rows with
 * their own label/state — `VATSIM ATIS`, `Real-world D-ATIS — <provider>`,
 * `METAR briefing` — and are never merged into one value.
 */
import { useEffect, useState } from "react";
import { CloudRain, Radio, AlertTriangle, FileWarning } from "lucide-react";
import {
  mapAtisResolution,
  mapMetarTafList,
  mapSigmetList,
  mapLightning,
} from "./briefingMappers.js";

function StateBadge({ badge, stale }) {
  const cls = stale ? "qr-badge badge--notam" : `qr-badge qr-badge--${(badge || "").toLowerCase()}`;
  return <span className={cls}>{badge}{stale ? " (last-good)" : ""}</span>;
}

function ProductRow({ title, product }) {
  return (
    <div className="qr-brief-item">
      <div style={{ flex: 1 }}>
        <div className="qr-brief-row__head">
          <strong>{title}</strong>
          <StateBadge badge={product.badge} stale={product.stale} />
        </div>
        {product.raw ? (
          <div className="mono qr-brief-raw">{product.raw}</div>
        ) : (
          <div className="qr-brief-sub">{product.detail || "No data available."}</div>
        )}
        <div className="qr-brief-sub">
          {product.station ? `${product.station} · ` : ""}
          {product.ageMin != null ? `${product.ageMin} min ago` : "age unknown"}
        </div>
      </div>
    </div>
  );
}

export default function BriefingPanel({ apiBase, station }) {
  const [atis, setAtis] = useState(null);
  const [metars, setMetars] = useState([]);
  const [tafs, setTafs] = useState([]);
  const [sigmets, setSigmets] = useState([]);
  const [sigwx, setSigwx] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!station) return undefined;
    let live = true;
    (async () => {
      try {
        const [atisR, metarR, tafR, sigmetR, sigwxR] = await Promise.all([
          fetch(`${apiBase}/api/crew/weather/briefing/atis/${station}`),
          fetch(`${apiBase}/api/crew/weather/briefing/metar?ids=${station}`),
          fetch(`${apiBase}/api/crew/weather/briefing/taf?ids=${station}`),
          fetch(`${apiBase}/api/crew/weather/briefing/sigmet`),
          fetch(`${apiBase}/api/crew/weather/briefing/sigwx`),
        ]);
        if (!live) return;
        if (atisR.ok) setAtis(mapAtisResolution(await atisR.json()));
        if (metarR.ok) setMetars(mapMetarTafList(await metarR.json()));
        if (tafR.ok) setTafs(mapMetarTafList(await tafR.json()));
        if (sigmetR.ok) setSigmets(mapSigmetList(await sigmetR.json()));
        if (sigwxR.ok) setSigwx(mapLightning(await sigwxR.json()));
        setError(null);
      } catch (e) {
        if (live) setError(String(e.message || e));
      }
    })();
    return () => { live = false; };
  }, [apiBase, station]);

  if (!station) {
    return <div className="qr-notice">Select a flight with a departure ICAO to load a briefing.</div>;
  }

  return (
    <div className="qr-briefing">
      <h3>AVIATION BRIEFING — {station}</h3>

      <div className="qr-brief-section">
        <div className="qr-brief-section__head"><Radio size={14} /> ATIS</div>
        {atis ? (
          atis.candidates.map((c) => (
            <ProductRow key={c.key} title={c.label} product={c} />
          ))
        ) : (
          <div className="qr-notice">Loading ATIS…</div>
        )}
      </div>

      <div className="qr-brief-section">
        <div className="qr-brief-section__head"><CloudRain size={14} /> METAR / TAF</div>
        {metars.map((m, i) => <ProductRow key={`m${i}`} title="METAR" product={m} />)}
        {tafs.map((t, i) => <ProductRow key={`t${i}`} title="TAF" product={t} />)}
      </div>

      <div className="qr-brief-section">
        <div className="qr-brief-section__head"><AlertTriangle size={14} /> SIGMET</div>
        {sigmets.length === 0 ? (
          <div className="qr-notice">No active SIGMETs.</div>
        ) : (
          sigmets.map((s, i) => <ProductRow key={`s${i}`} title={s.label} product={s} />)
        )}
      </div>

      <div className="qr-brief-section">
        <div className="qr-brief-section__head"><FileWarning size={14} /> SIGWX / WAFS</div>
        {sigwx && <ProductRow title={sigwx.label} product={sigwx} />}
      </div>

      <div className="qr-notice">
        Lightning (EUMETSAT MTG-LI L2 LFL, collection EO:EUM:DAT:0691) is
        route-box scoped and surfaces on the Route map when enabled — not
        duplicated here.
      </div>

      {error && <div className="qr-error">{error}</div>}
    </div>
  );
}
