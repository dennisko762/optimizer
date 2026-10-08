/**
 * CrossSection — docked vertical cross-section (distance × FL) for the
 * SkyNexus-style route map (M5-P).
 *
 * Pure SVG: the planned climb/cruise/descent profile derived from the OFP
 * per-fix FLs, one column per navlog fix, per-fix wind/temp/turb/icing readout
 * on hover. Hovering a column highlights the same route point on the map
 * (via onHover) and shows its ETA. No fabricated values — a missing sample
 * renders "—".
 */

import { useMemo, useRef } from "react";
import { fmtFl, fmtWind } from "./weatherMappers.js";

const W = 1000; // internal svg width (viewBox), scaled to container
const H = 190;
const PAD = { l: 46, r: 14, t: 26, b: 24 };

function xFor(cum, max) {
  if (!max) return PAD.l;
  return PAD.l + (cum / max) * (W - PAD.l - PAD.r);
}
function yFor(fl, maxFl) {
  return PAD.t + (1 - Math.min(fl, maxFl) / maxFl) * (H - PAD.t - PAD.b);
}

const TIER_COLORS = ["#e8a838", "#f97316", "#ef4444"];

export default function CrossSection({ columns, maxDistNm, cruiseFl, hoverIdx, onHover }) {
  // refs to the per-fix focus targets (keyboard navigation must move focus)
  const targetRefs = useRef([]);
  const maxFl = Math.max(460, (cruiseFl || 340) + 60);
  // Hover-target width = the column pitch (clamped), so adjacent columns do
  // NOT overlap — with ~170 fixes a fixed 32px target would overlap ~6x and
  // make hover select the wrong fix.
  const pitch = Math.max(
    6,
    (W - PAD.l - PAD.r) / Math.max(columns.length, 1)
  );

  const gridFl = [0, 100, 200, 300, 400, 500].filter((f) => f <= maxFl + 20);
  const maxTick = Math.max(maxFl, Math.ceil((maxFl + 20) / 100) * 100);

  // Planned profile: per-fix FL (ground fixes = FL0) along cumulative distance.
  // MUST share the same max as the fix columns (maxTick) — using a different
  // scale here drew the profile line off the fix dots (misregistration).
  const profilePts = useMemo(() => {
    if (!columns.length) return "";
    const pts = columns.map((c) => {
      const x = xFor(c.cumNm || 0, maxDistNm);
      const y = yFor(c.fl || 0, maxTick);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    return pts.join(" ");
  }, [columns, maxDistNm, maxTick]);

  return (
    <div className="wx-cross" role="group" aria-label="Route cross-section, distance by flight level">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="wx-cross__svg"
        preserveAspectRatio="none"
        onMouseLeave={() => onHover?.(null)}
      >
        {/* FL grid */}
        {gridFl.map((f) => (
          <g key={f}>
            <line
              x1={PAD.l} y1={yFor(f, maxTick)}
              x2={W - PAD.r} y2={yFor(f, maxTick)}
              className="wx-cross__grid"
            />
            <text x={PAD.l - 6} y={yFor(f, maxTick) + 3} className="wx-cross__tick" textAnchor="end">
              {f === 0 ? "SFC" : `FL${f}`}
            </text>
          </g>
        ))}
        {/* planned profile */}
        {profilePts && (
          <polyline points={profilePts} fill="none" className="wx-cross__profile" />
        )}
        {/* fix columns */}
        {columns.map((c, i) => {
          const x = xFor(c.cumNm || 0, maxDistNm);
          const y = yFor(c.fl || 0, maxTick);
          const active = hoverIdx === i;
          return (
            <g key={`${c.ident}-${i}`}>
              <line
                x1={x} y1={PAD.t} x2={x} y2={H - PAD.b}
                className={active ? "wx-cross__col wx-cross__col--active" : "wx-cross__col"}
              />
              {/* per-fix hazard dots */}
              {c.turbTier != null && c.turbTier > 0 && (
                <circle cx={x} cy={y - 10} r={3.5} fill={TIER_COLORS[Math.min(c.turbTier, 2)]} className="wx-cross__dot" />
              )}
              {c.iceTier != null && c.iceTier > 0 && (
                <circle cx={x} cy={y + 10} r={3.5} fill="#38bdf8" className="wx-cross__dot" />
              )}
              {(c.isOrigin || c.isDest) && (
                <circle cx={x} cy={y} r={4} fill="none" className="wx-cross__end" />
              )}
              <circle cx={x} cy={y} r={2.5} className="wx-cross__fix" />
              <text
                x={x} y={PAD.t - 8}
                className={active ? "wx-cross__ident wx-cross__ident--active" : "wx-cross__ident"}
                textAnchor="middle"
              >
                {c.ident}
              </text>
              {/* hover target (width = column pitch, non-overlapping);
                  onTouchStart gives tap-select on touch devices,
                  tabIndex/keydown let keyboard users step through fixes */}
              <rect
                x={x - pitch / 2} y={PAD.t} width={pitch} height={H - PAD.t - PAD.b}
                fill="transparent"
                tabIndex={0}
                role="button"
                aria-label={`Fix ${c.ident}${c.stage ? ` ${c.stage}` : ""} — show wind and hazards`}
                onMouseEnter={() => onHover?.(i)}
                onTouchStart={(e) => { e.preventDefault(); onHover?.(i); }}
                onFocus={() => onHover?.(i)}
                onBlur={() => onHover?.(null)}
                onKeyDown={(e) => {
                  const move = (n) => {
                    if (n < 0 || n >= columns.length) return;
                    e.preventDefault();
                    onHover?.(n);
                    targetRefs.current[n]?.focus();
                  };
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onHover?.(i); }
                  else if (e.key === "ArrowRight") move(i + 1);
                  else if (e.key === "ArrowLeft") move(i - 1);
                }}
                ref={(el) => { targetRefs.current[i] = el; }}
              />
            </g>
          );
        })}
      </svg>

      <div className="wx-cross__readout">
        {hoverIdx != null && columns[hoverIdx] ? (
          (() => {
            const c = columns[hoverIdx];
            return (
              <span>
                <strong className="mono">{c.ident}</strong>
                {c.stage && <span className="wx-cross__stage">{c.stage}</span>}
                <span className="wx-cross__kv">{fmtFl(c.fl)}</span>
                <span className="wx-cross__kv">{fmtWind(c.windKt, c.windFrom)}</span>
                {c.tailwindKt != null && (
                  <span className={c.tailwindKt >= 0 ? "wx-cross__tail--tail" : "wx-cross__tail--head"}>
                    {c.tailwindKt >= 0 ? "TAIL" : "HEAD"} {Math.abs(Math.round(c.tailwindKt))} kt
                  </span>
                )}
                {c.oatC != null && <span className="wx-cross__kv">{c.oatC}°C</span>}
                {c.turbTier != null && c.turbTier > 0 && (
                  <span className="wx-cross__kv wx-cross__kv--turb">TURB {c.turbTier}</span>
                )}
                {c.iceTier != null && c.iceTier > 0 && (
                  <span className="wx-cross__kv wx-cross__kv--ice">ICE {c.iceTier}</span>
                )}
                {c.ete && <span className="wx-cross__kv">ETA {c.ete}</span>}
              </span>
            );
          })()
        ) : (
          <span className="wx-cross__hint">Hover a fix for wind / temp / hazards</span>
        )}
      </div>
    </div>
  );
}
