/**
 * MapWeatherPanel — the interactive SkyNexus-style live eWAS route map (M5-P).
 *
 * MapLibre GL with a legally-usable dark basemap (CARTO Dark Matter, ©
 * OpenStreetMap contributors / © CARTO). Renders the EXACT route from the
 * active saved SimBrief OFP (every navlog fix, in order, antimeridian-split),
 * clickable fixes with detail popovers, SkyNexus-style hazard polygon layers
 * (turbulence / icing / CAPE / fronts / jet) as NOAA GFS proxies, an FL
 * selector, a T+0..T+36 time slider with play/pause/NOW and the
 * departure→arrival band, a live cycle/validity/stale status banner, and a
 * docked distance×FL cross-section with hover linkage back to the map.
 *
 * All weather values are NOAA GFS-derived proxies (never official WAFS/eWAS)
 * and are surfaced as-is; unavailable fields read "—", never synthesized.
 * An SSE feed auto-refreshes open maps when a new completed GFS cycle lands.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  Maximize2, Plus, Minus, Play, Pause, Layers, Cloud, Wind, Zap,
  Mountain, Snowflake, Route as RouteIcon,
} from "lucide-react";
import "./wxmap.css";
import {
  mapWeatherStatus, mapLayerFeatures, hazardColor, mapRouteForMap,
  mapCrossSection, sampleForPoint, fmtFl, validTimeLabel,
} from "./weatherMappers.js";
import { mapLiveOverlay } from "./liveMappers.js";
import CrossSection from "./CrossSection.jsx";

// Legally usable dark basemap (vector style) with required attribution.
const BASE_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const ATTRIBUTION =
  '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap</a> contributors, <a href="https://carto.com/attributions" target="_blank" rel="noopener">© CARTO</a>';

// v6 spawns a module worker from a URL derived from the bundle's own
// import.meta.url (…/assets/maplibre-gl-worker.mjs), which the Vite prod
// bundle does NOT emit — so the worker 404s and the map renders blank. The
// constructor has no workerUrl option in v6; the only override is the
// module-level setWorkerUrl, which takes priority over the default resolver.
// Point it at the self-contained worker script Vite copies verbatim from
// public/ to the dist root.
if (typeof window !== "undefined" && typeof maplibregl.setWorkerUrl === "function") {
  maplibregl.setWorkerUrl(new URL("maplibre-gl-worker.mjs", window.location.origin).href);
}

const PRODUCTS = [
  { key: "turbulence", label: "Turbulence", icon: Zap },
  { key: "icing", label: "Icing", icon: Snowflake },
  { key: "cape", label: "CAPE", icon: Cloud },
  { key: "fronts", label: "Fronts", icon: Wind },
  { key: "jet", label: "Jet Stream", icon: Wind },
];

const FL_PRESETS = [50, 100, 150, 200, 250, 300, 340, 350, 380, 390, 400, 410, 430, 450];

function emptyRoute() {
  return {
    origin: null, destination: null, cruiseFl: null, callsign: null,
    aircraft: null, routeString: null, totalNm: null, pointCount: 0,
    points: [], unresolved: [], lines: [], samples: [],
    sampleFl: null, sampleOffset: null, provenance: null,
  };
}

/**
 * M3 live layer props (telemetry / simConnected / live) are additive: the
 * map renders exactly as before without them, and with them it also draws
 * the live SimConnect aircraft symbol and dims the fixes already passed.
 */
export default function MapWeatherPanel({ apiBase, utc, telemetry, simConnected, live }) {
  const mapRef = useRef(null);
  const mapElRef = useRef(null);
  const fittedRef = useRef(false);

  const [status, setStatus] = useState(null);
  const [route, setRoute] = useState(emptyRoute());
  const [routeErr, setRouteErr] = useState(null);
  const [layers, setLayers] = useState({}); // product -> mapLayerFeatures result
  const [enabled, setEnabled] = useState({ turbulence: true, icing: true, jet: true });
  const [fl, setFl] = useState(340);
  const [offset, setOffset] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selected, setSelected] = useState(null); // point object
  const [hoverIdx, setHoverIdx] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  const cycle = status?.cycle || null;

  // ── M3 live overlay (SimConnect aircraft + passed fixes) ──────────
  // Pure derivation from the live telemetry; nothing is drawn when the sim
  // is not connected (no last-known or synthesized position).
  const liveOverlay = useMemo(
    () => mapLiveOverlay(route.points, telemetry, simConnected, live),
    [route.points, telemetry, simConnected, live]
  );
  const passedKeys = useMemo(
    () => new Set(liveOverlay.passedKeys || []),
    [liveOverlay]
  );

  // ── status ────────────────────────────────────────────────────────
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await fetch(`${apiBase}/api/crew/weather/status`);
        if (r.ok && live) setStatus(mapWeatherStatus(await r.json()));
      } catch { /* backend not up yet */ }
    })();
    const id = setInterval(() => {
      fetch(`${apiBase}/api/crew/weather/status`)
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => j && live && setStatus(mapWeatherStatus(j)))
        .catch(() => {});
    }, 15000);
    return () => { live = false; clearInterval(id); };
  }, [apiBase]);

  // ── route + samples (refetch on fl/offset/refreshTick) ─────────────
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await fetch(
          `${apiBase}/api/crew/weather/route?fl=${fl}&offset=${offset}`
        );
        if (!r.ok) {
          if (live) {
            if (r.status === 404) setRouteErr("No active SimBrief flightplan — import a plan first.");
            else if (r.status === 503) setRouteErr("NOAA GFS cycle downloading — route weather arrives shortly.");
            else setRouteErr(`Route unavailable (HTTP ${r.status}).`);
          }
          return;
        }
        const data = await r.json();
        if (!live) return;
        const mapped = mapRouteForMap(data);
        setRoute(mapped);
        setRouteErr(null);
        if (mapped.cruiseFl && fl === 340) setFl(mapped.cruiseFl);
      } catch (e) {
        if (live) setRouteErr(String(e.message || e));
      }
    })();
    return () => { live = false; };
  }, [apiBase, fl, offset, refreshTick]);

  // ── hazard layers (enabled products, per fl/offset/refreshTick) ────
  useEffect(() => {
    let live = true;
    (async () => {
      const on = Object.keys(enabled).filter((k) => enabled[k]);
      if (!on.length) {
        setLayers({});
        return;
      }
      const next = {};
      await Promise.all(on.map(async (prod) => {
        try {
          const r = await fetch(
            `${apiBase}/api/crew/weather/layer/${prod}?fl=${fl}&offset=${offset}`
          );
          if (r.ok) {
            const body = await r.json();
            next[prod] = mapLayerFeatures(body, prod);
          } else if (r.status === 503) {
            next[prod] = { features: [], unavailable: "downloading", fl, offset, label: prod, unit: "", thresholds: [] };
          }
        } catch { /* ignore per-layer */ }
      }));
      if (live) setLayers(next);
    })();
    return () => { live = false; };
  }, [apiBase, fl, offset, enabled, refreshTick]);

  // ── MapLibre init (once the container has a real size) ─────────────
  // A maplibre Map built on a 0×0 container computes its projection null and
  // then crashes on the internal ResizeObserver resize (setZoom → resize →
  // _calcMatrices on null), which is uncaught inside maplibre and tears the
  // whole app down (no error boundary above it). So wait for the container to
  // be laid out (≥2px) before constructing the Map.
  const [mapReady, setMapReady] = useState(false);
  // style-loaded is a separate stage: route/hazard source effects must
  // re-run BOTH when the style finishes loading AND when data arrives —
  // a one-shot reload raced the first /route fetch and the route never
  // drew (QA #7). Tracking it as state makes the deps honest.
  const [mapStyleLoaded, setMapStyleLoaded] = useState(false);
  useEffect(() => {
    const el = mapElRef.current;
    if (!el) return undefined;
    if (el.clientWidth >= 2 && el.clientHeight >= 2) {
      setMapReady(true);
      return undefined;
    }
    const ro = new ResizeObserver((entries) => {
      const cr = entries[entries.length - 1]?.contentRect;
      if (cr && cr.width >= 2 && cr.height >= 2) {
        setMapReady(true);
        ro.disconnect();
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!mapReady) return undefined;
    const el = mapElRef.current;
    if (!el || mapRef.current) return undefined;
    // Construct on the NEXT animation frame, after a real layout+paint pass.
    // Building a Map in the same tick the container just acquired a size (or
    // with maxBounds, which forces an init-time constrainInternal) runs
    // _calcMatrices on a projection that is still null and throws — uncaught
    // inside maplibre, tearing the whole tree down.
    let raf = 0;
    let map = null;
    raf = requestAnimationFrame(() => {
      map = new maplibregl.Map({
        container: el,
        style: BASE_STYLE,
        center: [30, 40],
        zoom: 2.4,
        attributionControl: true,
      });
      map.addControl(new maplibregl.AttributionControl({ compact: true }), "bottom-right");
      mapRef.current = map;
      const onStyle = () => setMapStyleLoaded(true);
      if (map.isStyleLoaded()) onStyle();
      else map.once("load", onStyle);
      const onAttr = (e) => {
        if (e.html) e.html = ATTRIBUTION + e.html;
      };
      map.on("attribution", onAttr);
    });
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (map) map.remove();
      mapRef.current = null;
    };
  }, [mapReady]);

  // ── route sources/layers ──────────────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapStyleLoaded) return undefined;

    const lineFC = {
      type: "FeatureCollection",
      features: (route.lines || []).map((coords, i) => ({
        type: "Feature",
        properties: { segment: i, kind: "route-line" },
        geometry: { type: "LineString", coordinates: coords },
      })),
    };
    const fixesFC = {
      type: "FeatureCollection",
      features: (route.points || []).map((p) => ({
        type: "Feature",
        properties: {
          ident: p.ident, occurrence: p.occurrence != null ? p.occurrence : 0,
          is_origin: p.isOrigin, is_dest: p.isDest,
          stage: p.stage || "", fl: p.fl != null ? String(p.fl) : "",
          // M3: fixes the live aircraft has already flown past
          passed: passedKeys.has(
            `${p.ident || ""}#${p.occurrence != null ? p.occurrence : 0}`
          ) ? "true" : "false",
        },
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
      })),
    };

    const ensure = (id, type, data) => {
      if (map.getSource(id)) { map.getSource(id).setData(data); return; }
      map.addSource(id, { type: "geojson", data });
    };
    ensure("wx-route", "line", lineFC);
    ensure("wx-fixes", "point", fixesFC);
    // M3 live aircraft position (empty FC when the sim is not connected)
    ensure("wx-live-ac", "point", liveOverlay.aircraftGeoJson);
    ensure("wx-selected", "point", {
      type: "FeatureCollection",
      features: selected ? [{
        type: "Feature",
        properties: {},
        geometry: { type: "Point", coordinates: [selected.lon, selected.lat] },
      }] : [],
    });

    if (!map.getLayer("wx-route-halo")) {
      map.addLayer({
        id: "wx-route-halo", type: "line", source: "wx-route",
        paint: {
          "line-color": "#22d3ee",
          "line-width": 7,
          "line-opacity": 0.28,
          "line-blur": 1.5,
        },
      });
    }
    if (!map.getLayer("wx-route")) {
      map.addLayer({
        id: "wx-route", type: "line", source: "wx-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#fbbf24", "line-width": 2.4 },
      });
    }
    if (!map.getLayer("wx-selected")) {
      map.addLayer({
        id: "wx-selected", type: "circle", source: "wx-selected",
        paint: {
          "circle-radius": 9, "circle-color": "#000", "circle-opacity": 0.35,
          "circle-stroke-color": "#fbbf24", "circle-stroke-width": 2,
        },
      });
    }
    if (!map.getLayer("wx-fix-end")) {
      map.addLayer({
        id: "wx-fix-end", type: "circle", source: "wx-fixes",
        filter: ["any", ["==", ["get", "is_origin"], "true"], ["==", ["get", "is_dest"], "true"]],
        paint: {
          "circle-radius": 5.5, "circle-color": "#000", "circle-opacity": 0.4,
          "circle-stroke-color": "#fbbf24", "circle-stroke-width": 2,
        },
      });
    }
    if (!map.getLayer("wx-fix")) {
      map.addLayer({
        id: "wx-fix", type: "circle", source: "wx-fixes",
        filter: ["all", ["!=", ["get", "is_origin"], "true"], ["!=", ["get", "is_dest"], "true"]],
        paint: {
          // passed fixes (M3 live) are dimmed; data-driven so the paint
          // follows the live telemetry without re-adding the layer
          "circle-radius": 3,
          "circle-color": ["case", ["==", ["get", "passed"], "true"], "#64748b", "#e2e8f0"],
          "circle-opacity": ["case", ["==", ["get", "passed"], "true"], 0.55, 1],
          "circle-stroke-width": 1,
          "circle-stroke-color": "#0f172a",
        },
      });
    }
    if (!map.getLayer("wx-fix-label")) {
      map.addLayer({
        id: "wx-fix-label", type: "symbol", source: "wx-fixes",
        layout: {
          "text-field": [
            "case", ["==", ["get", "passed"], "true"],
            ["concat", ["get", "ident"], " ✓"], ["get", "ident"],
          ],
          "text-size": 11,
          "text-offset": [0.6, -0.9],
          "text-allow-overlap": false,
        },
        paint: {
          "text-color": ["case", ["==", ["get", "passed"], "true"], "#94a3b8", "#fde68a"],
          "text-halo-color": "#1a0b10",
          "text-halo-width": 1.4,
        },
      });
    }
    // M3 live aircraft symbol + FL label, drawn above the route/fixes
    if (!map.getLayer("wx-live-ac-halo")) {
      map.addLayer({
        id: "wx-live-ac-halo", type: "circle", source: "wx-live-ac",
        paint: {
          "circle-radius": 11, "circle-color": "#22d3ee", "circle-opacity": 0.22,
          "circle-stroke-color": "#22d3ee", "circle-stroke-width": 1,
        },
      });
    }
    if (!map.getLayer("wx-live-ac")) {
      map.addLayer({
        id: "wx-live-ac", type: "circle", source: "wx-live-ac",
        paint: {
          "circle-radius": 4.5, "circle-color": "#22d3ee",
          "circle-stroke-color": "#06283d", "circle-stroke-width": 1.5,
        },
      });
    }
    if (!map.getLayer("wx-live-ac-label")) {
      map.addLayer({
        id: "wx-live-ac-label", type: "symbol", source: "wx-live-ac",
        layout: {
          "text-field": ["get", "label"],
          "text-size": 11,
          "text-offset": [1.1, 0.2],
          "text-allow-overlap": true,
        },
        paint: { "text-color": "#67e8f9", "text-halo-color": "#06283d", "text-halo-width": 1.4 },
      });
    }

    // fit the route once when real points land — but ONLY once the canvas
    // has a real size. MapLibre keeps its projection matrix null until the
    // first non-zero resize, so a fitBounds fired on a 0×0 container runs
    // setZoom → resize → _calcMatrices on null and throws, tearing the whole
    // app down (uncaught inside maplibre). Defer to the next resize instead.
    const tryFit = () => {
      if (fittedRef.current) return;
      if ((route.points || []).length < 2) return;
      const cv = map.getCanvas ? map.getCanvas() : null;
      if (!cv || cv.clientWidth < 2 || cv.clientHeight < 2) return;
      const coords = route.points.map((p) => [p.lon, p.lat]);
      try {
        map.fitBounds(coords, { padding: 60, duration: 600 });
        fittedRef.current = true;
        map.off("resize", tryFit);
      } catch { /* degenerate bounds */ }
    };
    tryFit();
    map.on("resize", tryFit);

    // click-to-select on the fixes (fresh route.points in closure; this
    // effect re-runs whenever route/selected change).
    const pts = route.points || [];
    const onClick = (e) => {
      const feats = map.queryRenderedFeatures(e.point, {
        layers: ["wx-fix", "wx-fix-end"],
      });
      if (feats.length) {
        const f = feats[0].properties || {};
        // occurrence-aware match: repeated idents are distinct points
        const p = pts.find(
          (q) => q.ident === f.ident &&
                 (q.occurrence != null ? q.occurrence : 0) === (f.occurrence != null ? f.occurrence : 0)
        );
        if (p) setSelected((cur) => (cur === p ? null : p));
      } else {
        setSelected(null);
      }
    };
    map.on("click", onClick);

    return () => {
      map.off("click", onClick);
      map.off("resize", tryFit);
    };
  }, [route, selected, mapStyleLoaded, liveOverlay, passedKeys]);

  // ── hazard polygon sources/layers ─────────────────────────────────
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    const present = Object.keys(layers);

    for (const prod of present) {
      const srcId = `wx-haz-${prod}`;
      const fc = layers[prod];
      if (map.getSource(srcId)) {
        map.getSource(srcId).setData({
          type: "FeatureCollection",
          features: fc.features || [],
        });
      } else {
        map.addSource(srcId, {
          type: "geojson",
          data: { type: "FeatureCollection", features: fc.features || [] },
        });
        // empty thresholds [] is TRUTHY in JS — `|| [0,1,2]` would keep the
        // empty array and the step ramp would be all-undefined (MapLibre:
        // "'undefined' value invalid"). Fall back only when there are NO bands.
        const bands = (fc.thresholds && fc.thresholds.length) ? fc.thresholds : [0, 1, 2];
        const ramp = bands.map((_, b) => hazardColor(prod, b));
        const colorExpr = ["step", ["coalesce", ["get", "band"], 0],
          ramp[0], 1, ramp[1] || ramp[0], 2, ramp[2] || ramp[1] || ramp[0]];
        // insert BELOW the route layers: hazard fills must not paint over
        // the route line / fix dots (QA #7 layer order)
        const before = map.getLayer("wx-route-halo") ? "wx-route-halo" : undefined;
        map.addLayer({
          id: srcId, type: "fill", source: srcId,
          paint: { "fill-color": colorExpr, "fill-opacity": 0.42 },
        }, before);
        map.addLayer({
          id: `${srcId}-line`, type: "line", source: srcId,
          paint: { "line-color": colorExpr, "line-width": 1, "line-opacity": 0.9 },
        }, before);
      }
    }
  }, [layers]);

  // toggle hazard layer visibility
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !map.isStyleLoaded()) return;
    for (const prod of PRODUCTS.map((p) => p.key)) {
      const id = `wx-haz-${prod}`;
      const want = Boolean(enabled[prod]);
      if (map.getLayer(id)) {
        const vis = map.getLayoutProperty(id, "visibility");
        if ((want && vis === "none") || (!want && vis !== "none")) {
          map.setLayoutProperty(id, "visibility", want ? "visible" : "none");
        }
      }
    }
  }, [enabled, layers]);

  // ── SSE auto-refresh on new completed cycle ───────────────────────
  useEffect(() => {
    let es;
    try {
      es = new EventSource(`${apiBase}/api/crew/weather/events`);
      es.addEventListener("cycle", () => setRefreshTick((t) => t + 1));
      es.onerror = () => { /* will retry; EventSource auto-reconnects */ };
    } catch { /* EventSource unsupported */ }
    return () => { if (es) es.close(); };
  }, [apiBase]);

  // ── time playback ─────────────────────────────────────────────────
  useEffect(() => {
    if (!playing) return undefined;
    const id = setInterval(() => setOffset((o) => (o >= 36 ? 0 : o + 1)), 1200);
    return () => clearInterval(id);
  }, [playing]);

  // ── selected fix popover projection ───────────────────────────────
  const [popXY, setPopXY] = useState(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return undefined;
    const update = () => {
      if (!selected) { setPopXY(null); return; }
      const p = map.project([selected.lon, selected.lat]);
      setPopXY({ x: p.x, y: p.y });
    };
    update();
    map.on("move", update);
    return () => map.off("move", update);
  }, [selected, route]);

  const cross = useMemo(
    () => mapCrossSection(route.points, route.samples, fl),
    [route, fl]
  );

  // departure→arrival band on the slider (from std + total ETE)
  const band = useMemo(() => {
    const etes = (route.points || []).map((p) => p.ete).filter(Boolean);
    if (etes.length < 2) return null;
    const last = etes[etes.length - 1];
    const m = /^(\d{1,2}):(\d{2})$/.exec(last);
    if (!m) return null;
    return Math.round(Number(m[1]) + Number(m[2]) / 60); // arrival offset hours
  }, [route]);

  function fitRoute() {
    const map = mapRef.current;
    if (!map || route.points.length < 2) return;
    map.fitBounds(route.points.map((p) => [p.lon, p.lat]), { padding: 60, duration: 500 });
  }

  // occurrence-aware sample join (repeated fix idents are distinct)
  const selectedSample = useMemo(
    () => (selected ? sampleForPoint(route.sampleByKey, selected) : null),
    [selected, route.sampleByKey]
  );

  const wxTime = cycle ? validTimeLabel(cycle, offset) : null;

  return (
    <div className="wx-map">
      <div className="wx-map__mapwrap">
        <div ref={mapElRef} className="wx-map__canvas" />
        <div className="wx-map__tint" aria-hidden="true" />

        {/* status banner */}
        <div className="wx-map__status">
          <span className={`wx-chip wx-chip--${status?.state || "idle"}`}>
            {status ? status.label : "LOADING"}
          </span>
          <span className="mono wx-map__cycle">
            {cycle ? `CYCLE ${cycle}` : "NO CYCLE"}
          </span>
          {wxTime && <span className="mono">VALID {wxTime}</span>}
          {status?.fetchedAt && (
            <span className="mono">FET {status.fetchedAt.slice(11, 16)}Z</span>
          )}
          {status?.stale && <span className="wx-chip wx-chip--stale">STALE {status.ageHours != null ? `${status.ageHours.toFixed(1)}h` : ""}</span>}
          <span className="wx-map__src" title={status?.source}>NOAA GFS proxy</span>
        </div>

        {/* nav buttons */}
        <div className="wx-map__nav">
          <button className="wx-navbtn" onClick={() => mapRef.current?.zoomIn()} aria-label="Zoom in"><Plus size={16} /></button>
          <button className="wx-navbtn" onClick={() => mapRef.current?.zoomOut()} aria-label="Zoom out"><Minus size={16} /></button>
          <button className="wx-navbtn" onClick={fitRoute} aria-label="Fit route"><Maximize2 size={16} /></button>
          <button
            className={`wx-navbtn ${drawer ? "wx-navbtn--on" : ""}`}
            onClick={() => setDrawer((d) => !d)}
            aria-label="Layers and overlays"
          >
            <Layers size={16} />
            <span className="wx-navbtn__badge">{Object.values(enabled).filter(Boolean).length}</span>
          </button>
        </div>

        {/* layer drawer */}
        {drawer && (
          <div className="wx-drawer">
            <div className="wx-drawer__head">
              <Layers size={15} /> LAYERS &amp; OVERLAYS
            </div>
            <div className="wx-drawer__fl">
              <span className="qr-label">FLIGHT LEVEL</span>
              <div className="wx-drawer__flrow">
                {FL_PRESETS.map((v) => (
                  <button
                    key={v}
                    className={`wx-flbtn ${fl === v ? "wx-flbtn--on" : ""}`}
                    onClick={() => setFl(v)}
                  >
                    {String(v).padStart(3, "0")}
                  </button>
                ))}
              </div>
            </div>
            <div className="wx-drawer__layers">
              {PRODUCTS.map(({ key, label, icon: Icon }) => (
                <label key={key} className="wx-layer">
                  <input
                    type="checkbox"
                    checked={Boolean(enabled[key])}
                    onChange={(e) => setEnabled((s) => ({ ...s, [key]: e.target.checked }))}
                  />
                  <Icon size={15} />
                  <span>{label}</span>
                  {layers[key] && layers[key].unavailable && (
                    <em className="wx-layer__unav">unavailable</em>
                  )}
                </label>
              ))}
            </div>
            <div className="wx-drawer__legend">
              {PRODUCTS.map(({ key, label }) => {
                const l = layers[key];
                if (!l || !l.features.length) return null;
                const ramp = (l.thresholds || []).map((_, b) => hazardColor(key, b));
                return (
                  <div key={key} className="wx-legend">
                    <span className="qr-label">{label}</span>
                    {ramp.map((c, b) => (
                      <span key={b} className="wx-legend__sw" style={{ background: c }} />
                    ))}
                  </div>
                );
              })}
            </div>
            <div className="wx-drawer__foot">
              NOAA GFS 0.25° proxies — not official WAFS/eWAS.
            </div>
          </div>
        )}

        {/* fix popover */}
        {selected && popXY && (
          <div
            className="wx-pop"
            style={{ left: Math.min(popXY.x + 14, 620), top: Math.max(8, popXY.y - 10) }}
          >
            <div className="wx-pop__head">
              <strong className="mono">{selected.ident}</strong>
              {selected.stage && <span className="wx-chip">{selected.stage}</span>}
              {(() => {
                const sv = selectedSample;
                if (!sv) return <span className="wx-chip">NO WX</span>;
                // temporal provenance chip: the served valid time is always
                // shown; when the ETA couldn't be sampled (outside horizon /
                // stale plan) the honest flag takes over — never a T+24 label
                // on a T+6 sample (QA #2).
                const etaFlag = (sv.unavailable || []).find(
                  (u) => /eta|horizon|predates/i.test(u)
                );
                if (sv.valid_at_utc == null) return <span className="wx-chip">NO WX</span>;
                const vt = sv.valid_at_utc.length > 16 ? sv.valid_at_utc.slice(11, 16) + "Z" : sv.valid_at_utc;
                return etaFlag
                  ? <span className="wx-chip" title={etaFlag}>T+{sv.offset_served != null ? sv.offset_served : "—"}h ⚠</span>
                  : <span className="wx-chip" title="sampled at this fix's ETA">VALID {vt}</span>;
              })()}
              <button className="wx-pop__x" onClick={() => setSelected(null)} aria-label="Close">✕</button>
            </div>
            <div className="wx-pop__grid mono">
              <span>LAT {selected.lat?.toFixed(4)}</span>
              <span>LON {selected.lon?.toFixed(4)}</span>
              <span>{fmtFl(selected.fl)}</span>
              <span>{selected.cumNm != null ? `${Math.round(selected.cumNm)} NM` : ""}</span>
              {selected.airway && <span>AWY {selected.airway}</span>}
              {selected.fir && <span>FIR {selected.fir}</span>}
            </div>
            {(() => {
              const s = selectedSample;
              if (!s) return <div className="wx-pop__na">WX sample unavailable</div>;
              return (
                <div className="wx-pop__wx mono">
                  <span className="wx-pop__wxrow">
                    WIND {s.wind_speed_kt != null ? `${Math.round(s.wind_speed_kt)}kt @${s.wind_from_deg ?? "—"}` : "—"}
                  </span>
                  <span className="wx-pop__wxrow">
                    TAIL {s.tailwind_kt != null ? `${s.tailwind_kt >= 0 ? "+" : ""}${s.tailwind_kt}kt` : "—"}
                  </span>
                  <span className="wx-pop__wxrow">OAT {s.oat_c != null ? `${s.oat_c}°C` : "—"}</span>
                  <span className="wx-pop__wxrow">
                    TURB {s.turbulence_tier != null ? `T${s.turbulence_tier}` : "—"}
                    {s.icing_tier != null ? ` · ICE T${s.icing_tier}` : ""}
                    {s.jet_tier != null ? ` · JET T${s.jet_tier}` : ""}
                  </span>
                </div>
              );
            })()}
          </div>
        )}

        {/* time slider + profile controls */}
        <div className="wx-map__time">
          <div className="wx-time__head">
            <span className="qr-label">WX TIME</span>
            <span className="mono">{utc?.time || "00:00"}z • {offset === 0 ? "NOW" : `T+${offset}h`}</span>
          </div>
          <div className="wx-time__row">
            <button className="wx-wxbtn" onClick={() => setPlaying((p) => !p)} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause size={13} /> : <Play size={13} />}
            </button>
            <input
              className="wx-slider"
              type="range" min={0} max={36} step={1}
              value={offset}
              onChange={(e) => { setOffset(Number(e.target.value)); setPlaying(false); }}
              aria-label="Forecast hour offset"
            />
            <button className={`wx-wxbtn ${offset === 0 ? "wx-wxbtn--on" : ""}`} onClick={() => { setOffset(0); setPlaying(false); }}>NOW</button>
          </div>
          {band != null && band > 0 && (
            <div className="wx-time__band">
              <div className="wx-time__bandfill" style={{ width: `${Math.min(100, (band / 36) * 100)}%` }} />
              <span className="mono wx-time__bandlbl">DEP→ARR {band}h</span>
            </div>
          )}
        </div>

        {/* click-to-select on fixes */}
        <div className="wx-map__clickhint">
          <RouteIcon size={13} /> Tap a fix for detail · {route.pointCount} fixes
          {route.unresolved.length > 0 && (
            <span className="wx-chip wx-chip--stale">
              {route.unresolved.length} unresolved
            </span>
          )}
        </div>

        {/* M3 live route state — only when SimConnect is actually live */}
        {liveOverlay.connected && (
          <div className="wx-map__live" data-testid="wx-live-hud">
            <span className="wx-chip wx-chip--live">LIVE {liveOverlay.label}</span>
            <span className="mono">
              PASSED {liveOverlay.passedIdents.length}/{route.pointCount}
            </span>
            <span className="mono">
              REM {liveOverlay.remainingNm != null ? `${liveOverlay.remainingNm} NM` : "—"}
            </span>
          </div>
        )}

        {routeErr && (
          <div className="wx-map__err">
            <Mountain size={14} /> {routeErr}
          </div>
        )}
      </div>

      {/* cross-section — sibling of the map (NOT inside the mapwrap, whose
          absolute inset:0 canvas would paint over and pointer-intercept it) */}
      <div className="wx-map__cross">
        <CrossSection
          columns={cross.columns}
          maxDistNm={cross.maxDistNm}
          cruiseFl={fl}
          hoverIdx={hoverIdx}
          onHover={(i) => {
            setHoverIdx(i);
            if (i != null && route.points[i]) setSelected(route.points[i]);
          }}
        />
      </div>
    </div>
  );
}
