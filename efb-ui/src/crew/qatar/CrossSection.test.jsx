/**
 * CrossSection — component coverage for the docked distance × FL
 * cross-section of the Route map.
 *
 * Covers the planned-profile geometry, the per-fix hazard dots, the
 * hover/touch/keyboard selection contract (the readout and the map
 * highlight share one hovered index) and the honest "—" readout for
 * fixes without samples.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { test } from "vitest";

import CrossSection from "./CrossSection.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const COLUMNS = [
  { ident: "EDDF", cumNm: 0, fl: 0, stage: "DEP", isOrigin: true, windKt: 8, windFrom: 250, oatC: 15, ete: "00:00" },
  { ident: "ERNAS", cumNm: 120, fl: 360, windKt: 65, windFrom: 270, tailwindKt: 42, oatC: -52, turbTier: 2, iceTier: 1, ete: "00:24" },
  { ident: "TUVLU", cumNm: 260, fl: 360, windKt: 70, windFrom: 300, tailwindKt: -18, oatC: -54, turbTier: 0, iceTier: 0 },
  { ident: "LKPR", cumNm: 380, fl: 0, stage: "ARR", isDest: true, ete: "01:05" },
];

/** Controlled harness: hoverIdx lives in the parent, exactly as the map does. */
function Harness({ columns = COLUMNS, initial = null }) {
  const [hoverIdx, setHoverIdx] = React.useState(initial);
  return React.createElement(CrossSection, {
    columns,
    maxDistNm: 380,
    cruiseFl: 360,
    hoverIdx,
    onHover: setHoverIdx,
  });
}

/** Flattened rendered text (react-test-renderer splits interpolations). */
function flatten(node) {
  if (node == null || node === false) return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(flatten).join("");
  return flatten(node.children);
}

function render(props) {
  let renderer;
  const focused = [];
  act(() => {
    renderer = create(React.createElement(Harness, props), {
      createNodeMock: () => ({ focus: () => focused.push(1) }),
    });
  });
  // JSON keeps the class/aria attributes, the flattened text keeps the copy
  const text = () => {
    const json = renderer.toJSON();
    return `${flatten(json)}\n${JSON.stringify(json)}`;
  };
  return { renderer, focused, text };
}

function targets(renderer) {
  return renderer.root.findAll(
    (n) => n.type === "rect" && typeof n.props.onKeyDown === "function",
    { deep: true }
  );
}

test("the profile, idents and hazard dots are drawn for every fix", () => {
  const { renderer, text } = render();
  const out = text();

  assert.match(out, /Route cross-section/);
  for (const ident of ["EDDF", "ERNAS", "TUVLU", "LKPR"]) {
    assert.match(out, new RegExp(ident));
  }
  // FL grid ticks incl. the SFC label
  assert.match(out, /SFC/);
  assert.match(out, /FL400/);
  // planned profile polyline, one point per fix
  const profile = renderer.root.findAll((n) => n.type === "polyline");
  assert.equal(profile.length, 1);
  assert.equal(profile[0].props.points.split(" ").length, COLUMNS.length);
  // hazard dots only where a tier is actually > 0 (turb + ice on ERNAS)
  const dots = renderer.root.findAll((n) => n.type === "circle" && n.props.className === "wx-cross__dot");
  assert.equal(dots.length, 2);
  // one non-overlapping hover target per fix
  assert.equal(targets(renderer).length, COLUMNS.length);
  // nothing hovered yet
  assert.match(out, /Hover a fix for wind \/ temp \/ hazards/);
});

test("hovering a fix publishes the index and shows its wind, temp and hazards", () => {
  const { renderer, text } = render();
  act(() => { targets(renderer)[1].props.onMouseEnter(); });
  const out = text();

  assert.match(out, /ERNAS/);
  assert.match(out, /FL360/);
  assert.match(out, /TAIL 42 kt/);
  assert.match(out, /-52°C/);
  assert.match(out, /TURB 2/);
  assert.match(out, /ICE 1/);
  assert.match(out, /ETA 00:24/);

  // a headwind fix is labelled HEAD, not TAIL
  act(() => { targets(renderer)[2].props.onMouseEnter(); });
  assert.match(text(), /HEAD 18 kt/);

  // a fix without samples reads "—", never a fabricated value
  act(() => { targets(renderer)[3].props.onMouseEnter(); });
  const arr = text();
  assert.match(arr, /ARR/);
  assert.match(arr, /—/);
  assert.ok(!arr.includes("TURB"), "no hazard chip for a fix without a tier");
});

test("touch, keyboard and blur all drive the same hovered fix", () => {
  const { renderer, focused, text } = render();
  let prevented = 0;
  const ev = () => ({ preventDefault: () => { prevented += 1; } });

  // tap-select on a touch device
  act(() => { targets(renderer)[0].props.onTouchStart(ev()); });
  assert.match(text(), /EDDF/);
  assert.equal(prevented, 1);

  // focus selects, Enter/Space re-selects, arrows step and move focus
  act(() => { targets(renderer)[0].props.onFocus(); });
  act(() => { targets(renderer)[0].props.onKeyDown({ key: "Enter", ...ev() }); });
  act(() => { targets(renderer)[0].props.onKeyDown({ key: " ", ...ev() }); });
  act(() => { targets(renderer)[0].props.onKeyDown({ key: "ArrowRight", ...ev() }); });
  assert.match(text(), /ERNAS/);
  assert.ok(focused.length >= 1, "arrow navigation must move DOM focus");

  act(() => { targets(renderer)[1].props.onKeyDown({ key: "ArrowLeft", ...ev() }); });
  assert.match(text(), /EDDF/);
  // an unhandled key changes nothing; an arrow off the ends is ignored
  act(() => { targets(renderer)[0].props.onKeyDown({ key: "ArrowLeft", ...ev() }); });
  act(() => { targets(renderer)[0].props.onKeyDown({ key: "Tab", ...ev() }); });
  assert.match(text(), /EDDF/);

  // blurring and leaving the chart clear the readout
  act(() => { targets(renderer)[0].props.onBlur(); });
  assert.match(text(), /Hover a fix/);
  act(() => { targets(renderer)[2].props.onMouseEnter(); });
  const svg = renderer.root.findAll((n) => n.type === "svg")[0];
  act(() => { svg.props.onMouseLeave(); });
  assert.match(text(), /Hover a fix/);
});

test("an empty route draws no profile and no columns", () => {
  const { renderer, text } = render({ columns: [] });
  assert.equal(renderer.root.findAll((n) => n.type === "polyline").length, 0);
  assert.equal(targets(renderer).length, 0);
  assert.match(text(), /Hover a fix/);
  // the FL grid still renders so the empty chart is readable
  assert.match(text(), /SFC/);
});
