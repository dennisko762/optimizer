/**
 * Provider gate persistence (audit G9, acceptance check 12).
 *
 * The crew session already survived a reload while the chosen airline did
 * not, so every reload dropped back to the selector. These tests cover the
 * restore-on-load and persist-on-select paths, and the maroon default theme
 * the gate opens with.
 *
 * `document` / `localStorage` are stubbed: this suite runs in the node
 * environment (no jsdom in the project toolchain).
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { beforeEach, test, vi } from "vitest";

import {
  CrewPlatformProvider,
  CrewContext,
  PROVIDER_STORAGE_KEY,
} from "./CrewPlatformContext.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PROVIDERS = [
  {
    id: "qatarvirtual",
    display_name: "Qatar Airways Virtual",
    short_code: "QRV",
    icao: "QTR",
    theme: { "--airline-primary": "#5c1a2e", "--airline-accent": "#e8a838" },
  },
  {
    id: "lhvirtual",
    display_name: "Lufthansa Virtual",
    short_code: "LHV",
    icao: "DLH",
    theme: { "--airline-primary": "#003366", "--airline-accent": "#FFCC00" },
  },
];

let store;
let cssVars;

function installDom() {
  store = new Map();
  cssVars = {};
  globalThis.window = {
    localStorage: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
    },
    location: { search: "" },
    history: { replaceState: () => {} },
  };
  globalThis.document = {
    documentElement: {
      style: {
        setProperty: (k, v) => {
          cssVars[k] = v;
        },
      },
    },
  };
}

function captured() {
  const box = {};
  const Probe = () => {
    const ctx = React.useContext(CrewContext);
    box.ctx = ctx;
    return null;
  };
  return { box, Probe };
}

async function renderProvider() {
  const { box, Probe } = captured();
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(CrewPlatformProvider, null, React.createElement(Probe))
    );
  });
  return { box, renderer };
}

beforeEach(() => {
  installDom();
  globalThis.fetch = vi.fn(async (url) => {
    const u = String(url);
    if (u.includes("/api/crew/providers")) {
      return { ok: true, status: 200, json: async () => PROVIDERS };
    }
    if (u.includes("/api/crew/config/readiness")) {
      return { ok: true, status: 200, json: async () => ({ ready: false }) };
    }
    if (u.includes("/api/crew/session/local")) {
      return { ok: true, status: 200, json: async () => ({ session_id: "local-1" }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });
});

test("the gate opens on the maroon default theme before any selection", async () => {
  const { box } = await renderProvider();
  assert.equal(box.ctx.selectedProvider, null);
  assert.equal(cssVars["--airline-bg"], "#1a0a12");
  assert.equal(cssVars["--airline-primary"], "#5c1a2e");
});

test("Qatar Airways is offered as a selectable provider", async () => {
  const { box } = await renderProvider();
  assert.deepEqual(
    box.ctx.providers.map((p) => p.id),
    ["qatarvirtual", "lhvirtual"]
  );
});

test("selecting an airline persists it and applies its theme", async () => {
  const { box } = await renderProvider();
  await act(async () => box.ctx.selectProvider("qatarvirtual"));
  assert.equal(box.ctx.selectedProvider.id, "qatarvirtual");
  assert.equal(store.get(PROVIDER_STORAGE_KEY), "qatarvirtual");
  assert.equal(cssVars["--airline-accent"], "#e8a838");

  // An unknown id is ignored rather than clearing the selection.
  await act(async () => box.ctx.selectProvider("nope"));
  assert.equal(box.ctx.selectedProvider.id, "qatarvirtual");
});

test("the stored airline is restored on the next load", async () => {
  store.set(PROVIDER_STORAGE_KEY, "lhvirtual");
  const { box } = await renderProvider();
  assert.equal(box.ctx.selectedProvider.id, "lhvirtual");
  assert.equal(cssVars["--airline-primary"], "#003366");
});

test("an unknown stored id falls back to the selector", async () => {
  store.set(PROVIDER_STORAGE_KEY, "retiredairline");
  const { box } = await renderProvider();
  assert.equal(box.ctx.selectedProvider, null);
});

test("a blocked localStorage never breaks the gate", async () => {
  globalThis.window.localStorage = {
    getItem: () => {
      throw new Error("blocked");
    },
    setItem: () => {
      throw new Error("blocked");
    },
  };
  const { box } = await renderProvider();
  assert.equal(box.ctx.selectedProvider, null);
  await act(async () => box.ctx.selectProvider("qatarvirtual"));
  assert.equal(box.ctx.selectedProvider.id, "qatarvirtual");
});
