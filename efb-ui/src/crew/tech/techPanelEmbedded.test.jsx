/**
 * TechPanel embedded mode (audit G7).
 *
 * `QatarShell` passed `<TechPanel embedded />` while the component declared
 * no props at all, so the flag was silently dropped and the panel kept its
 * own standalone "Tech" heading inside the maroon shell. These tests pin the
 * prop contract: embedded drops the standalone heading and takes the shell's
 * label/panel styling, standalone is unchanged.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

vi.mock("../useCrewPlatform.js", () => ({
  useCrewPlatform: () => ({
    apiBase: "/bridge",
    session: { session_id: "s-1", display_name: "Local Pilot" },
  }),
}));

vi.mock("./techSelection.js", () => ({
  getSelectedRegistration: () => null,
  setSelectedRegistration: vi.fn(),
}));

import TechPanel from "./TechPanel.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function textOf(renderer) {
  const out = [];
  const walk = (node) => {
    if (node == null) return;
    if (typeof node === "string" || typeof node === "number") {
      out.push(String(node));
      return;
    }
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    walk(node.children);
  };
  walk(renderer.toJSON());
  return out.join(" ").replace(/\s+/g, " ").trim();
}

async function render(props) {
  let renderer;
  await act(async () => {
    renderer = create(React.createElement(TechPanel, props));
  });
  return renderer;
}

function rootClass(renderer) {
  return String(renderer.toJSON().props.className || "");
}

beforeEach(() => {
  globalThis.fetch = vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => [],
  }));
});

test("embedded TechPanel takes the shell label and the shell panel class", async () => {
  const renderer = await render({ embedded: true });
  const body = textOf(renderer);
  assert.match(rootClass(renderer), /tech-panel--embedded/);
  assert.match(body, /TECH LOG — AIRCRAFT TECHNICAL LOG/);
  // The standalone heading pair is gone.
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("tech-header-title")
    ).length,
    0
  );
});

test("standalone TechPanel is unchanged", async () => {
  const renderer = await render({});
  assert.doesNotMatch(rootClass(renderer), /tech-panel--embedded/);
  assert.equal(
    renderer.root.findAll((n) =>
      n.props && String(n.props.className || "").includes("tech-header-title")
    ).length,
    1
  );
  assert.match(textOf(renderer), /Aircraft Technical Log/);
  assert.match(textOf(renderer), /No aircraft selected/);
});
