/**
 * Device chrome — the Surface-class status cluster.
 *
 * Covers all three layers the task requires to be testable outside a browser:
 *
 *   A. the PURE state helpers (deviceChrome.js) — brightness maths, prefs
 *      round-trip, the battery / network / notice view models. This is where
 *      the AGENTS.md honesty rule lives: when a browser API is absent the
 *      helper must return an explicit UNKNOWN state and never invent a value.
 *
 *   B. the provider (useDeviceChrome.jsx) — real browser signal probes (with
 *      navigator/window stubbed), the airplane-mode gate that suspends the
 *      screen refresh, and the offline flag the pollers read.
 *
 *   C. the components (DeviceChrome.jsx) — the status cluster and the notice
 *      host, rendered against the real provider.
 */

import assert from "node:assert/strict";
import React from "react";
import { act, create } from "react-test-renderer";
import { afterEach, describe, test, vi } from "vitest";

import {
  BRIGHTNESS_MAX,
  BRIGHTNESS_MIN,
  DEFAULT_PREFS,
  applyNotice,
  brightnessStyle,
  dimOpacity,
  dismissNotice,
  loadPrefs,
  mapBatteryState,
  mapNetworkState,
  normalizePrefs,
  savePrefs,
} from "./deviceChrome.js";
import {
  DeviceChromeProvider,
  useDeviceChrome,
} from "./useDeviceChrome.jsx";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/* ── A. pure helpers ──────────────────────────────────────────────── */

describe("deviceChrome: brightness", () => {
  test("defaults and clamping", () => {
    assert.equal(DEFAULT_PREFS.brightness, BRIGHTNESS_MAX);
    assert.equal(normalizePrefs({}).brightness, BRIGHTNESS_MAX);
    // out of range is clamped into [BRIGHTNESS_MIN, BRIGHTNESS_MAX]
    assert.equal(normalizePrefs({ brightness: 5 }).brightness, BRIGHTNESS_MIN);
    assert.equal(normalizePrefs({ brightness: 200 }).brightness, BRIGHTNESS_MAX);
    // non-numeric falls back to the default (no invented number)
    assert.equal(normalizePrefs({ brightness: "bright" }).brightness, BRIGHTNESS_MAX);
  });

  test("dimOpacity is monotonic: full brightness → transparent, min → max dim", () => {
    assert.equal(dimOpacity(BRIGHTNESS_MAX), 0);
    assert.equal(dimOpacity(BRIGHTNESS_MIN), 0.8);
    assert.ok(dimOpacity(70) > dimOpacity(85), "darker ⇒ more dim");
    assert.ok(dimOpacity(70) < dimOpacity(60), "lighter ⇒ less dim");
  });

  test("brightnessStyle exposes the dim level as a CSS custom property", () => {
    const style = brightnessStyle(100);
    assert.equal(style["--qr-dim"], "0");
    assert.equal(brightnessStyle(40)["--qr-dim"], "0.8");
  });
});

describe("deviceChrome: prefs persistence", () => {
  test("loadPrefs with no storage returns the defaults", () => {
    const prev = globalThis.localStorage;
    delete globalThis.localStorage;
    try {
      assert.deepEqual(loadPrefs(), DEFAULT_PREFS);
    } finally {
      if (prev !== undefined) globalThis.localStorage = prev;
    }
  });

  test("savePrefs → loadPrefs round-trips; non-boolean toggles normalize to false", () => {
    const store = {};
    const fake = {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => {
        store[k] = v;
      },
      removeItem: (k) => {
        delete store[k];
      },
    };
    const prev = globalThis.localStorage;
    globalThis.localStorage = fake;
    try {
      // brightness is rounded; toggles are strict === true, so garbage
      // normalizes to false (an off switch is the honest default).
      const saved = savePrefs({ brightness: 12.4, airplaneMode: "yes", notificationsOff: 1, junk: true });
      assert.equal(saved.brightness, 40); // clamped up to BRIGHTNESS_MIN
      assert.equal(saved.airplaneMode, false); // "yes" is not the boolean true
      assert.equal(saved.notificationsOff, false); // 1 is not the boolean true
      assert.equal("junk" in saved, false); // unknown keys are dropped
      assert.deepEqual(loadPrefs(), saved);
    } finally {
      if (prev !== undefined) globalThis.localStorage = prev;
      else delete globalThis.localStorage;
    }
  });
});

describe("deviceChrome: battery honesty", () => {
  test("no Battery API → UNKNOWN, never a fabricated percentage", () => {
    const b = mapBatteryState(null);
    assert.equal(b.known, false);
    assert.equal(b.percent, null);
    assert.equal(b.label, "—");
    assert.match(b.title, /unavailable/i);
  });

  test("an out-of-range level is rejected, not clamped into a fake value", () => {
    for (const bad of [{ level: 1.5 }, { level: -0.2 }, { level: "75" }, {}]) {
      const b = mapBatteryState(bad);
      assert.equal(b.known, false, `level ${JSON.stringify(bad.level)} must be unknown`);
      assert.equal(b.percent, null);
    }
  });

  test("a real BatteryManager shape maps to a concrete percentage", () => {
    const b = mapBatteryState({ level: 0.63, charging: false });
    assert.equal(b.known, true);
    assert.equal(b.percent, 63);
    assert.equal(b.label, "63%");
    assert.equal(b.charging, false);
    const c = mapBatteryState({ level: 1, charging: true });
    assert.equal(c.percent, 100);
    assert.equal(c.charging, true);
    assert.match(c.title, /charging/i);
  });
});

describe("deviceChrome: network honesty", () => {
  test("no navigator.onLine → UNKNOWN (LINK —), never a guessed online flag", () => {
    const n = mapNetworkState({ online: undefined });
    assert.equal(n.mode, "unknown");
    assert.equal(n.online, null);
    assert.equal(n.bars, null);
    assert.equal(n.label, "LINK —");
  });

  test("navigator reports offline → OFFLINE", () => {
    const n = mapNetworkState({ online: false });
    assert.equal(n.mode, "offline");
    assert.equal(n.online, false);
    assert.equal(n.bars, 0);
  });

  test("online with connection.effectiveType maps to signal bars", () => {
    assert.equal(mapNetworkState({ online: true, effectiveType: "4g" }).bars, 4);
    assert.equal(mapNetworkState({ online: true, effectiveType: "3g" }).bars, 2);
    assert.equal(mapNetworkState({ online: true, effectiveType: "2g" }).bars, 1);
    // no connection API → online but bars unknown (null), never invented
    assert.equal(mapNetworkState({ online: true }).bars, null);
    assert.equal(mapNetworkState({ online: true }).wifi, true);
  });

  test("airplane mode always wins and reports AIRPLANE MODE", () => {
    const n = mapNetworkState({ online: true, effectiveType: "4g", airplaneMode: true });
    assert.equal(n.mode, "airplane");
    assert.equal(n.online, false);
    assert.equal(n.bars, 0);
    assert.equal(n.label, "AIRPLANE MODE");
  });
});

describe("deviceChrome: notices", () => {
  test("applyNotice prepends, caps at 4, and normalizes kind", () => {
    let s = { notices: [], suppressed: 0 };
    s = applyNotice(s, { kind: "info", text: "one" });
    s = applyNotice(s, { kind: "weird", text: "two" });
    assert.equal(s.notices.length, 2);
    assert.equal(s.notices[0].text, "two"); // newest first
    assert.equal(s.notices[1].kind, "info");
    // cap
    for (let i = 0; i < 6; i += 1) s = applyNotice(s, { text: `x${i}` });
    assert.equal(s.notices.length, 4);
  });

  test("Do Not Disturb suppresses without rendering", () => {
    const s = applyNotice({ notices: [], suppressed: 0 }, { text: "hidden" }, { notificationsOff: true });
    assert.equal(s.notices.length, 0);
    assert.equal(s.suppressed, 1);
  });

  test("an empty notice is a no-op", () => {
    const s = { notices: [], suppressed: 0 };
    assert.equal(applyNotice(s, null), s);
    assert.equal(applyNotice(s, { text: "" }), s);
  });

  test("dismissNotice removes by id only", () => {
    let s = applyNotice(null, { id: "a", text: "a" });
    s = applyNotice(s, { id: "b", text: "b" });
    s = dismissNotice(s, "a");
    assert.equal(s.notices.length, 1);
    assert.equal(s.notices[0].id, "b");
  });
});

/* ── B. provider ──────────────────────────────────────────────────── */

function Probe({ children }) {
  const ctx = useDeviceChrome();
  React.useEffect(() => {
    globalThis.__chrome = ctx;
  }, [ctx]);
  return children ?? null;
}

async function mountProvider({ onNavigate = () => {} } = {}) {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(
        DeviceChromeProvider,
        { screen: "home", onNavigate, appVersion: "v-test" },
        React.createElement(Probe, null, null)
      )
    );
  });
  return renderer;
}

describe("DeviceChromeProvider", () => {
  afterEach(() => {
    delete globalThis.__chrome;
    delete globalThis.navigator;
    delete globalThis.window;
  });

  test("with no browser APIs the indicators read UNKNOWN (never invented)", async () => {
    await mountProvider();
    const ctx = globalThis.__chrome;
    assert.equal(ctx.network.mode, "unknown");
    assert.equal(ctx.network.online, null);
    assert.equal(ctx.battery.known, false);
    assert.equal(ctx.battery.percent, null);
    assert.equal(ctx.offline, false);
  });

  test("a stubbed navigator drives real battery + network state", async () => {
    const batteryManager = { level: 0.55, charging: true };
    globalThis.navigator = {
      onLine: true,
      connection: { effectiveType: "4g" },
      getBattery: vi.fn().mockResolvedValue(batteryManager),
    };
    globalThis.window = {
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    await mountProvider();
    const ctx = globalThis.__chrome;
    assert.equal(ctx.network.mode, "online");
    assert.equal(ctx.network.bars, 4);
    assert.equal(ctx.battery.known, true);
    assert.equal(ctx.battery.percent, 55);
    assert.equal(ctx.battery.charging, true);
  });

  test("airplane mode flips offline and the refresh handler is refused", async () => {
    let calls = 0;
    await mountProvider();
    act(() => {
      // register a screen refresh handler via the context
      globalThis.__chrome.registerRefresh(() => {
        calls += 1;
        return { ok: true, text: "refetched" };
      }, "Test");
    });
    act(() => {
      globalThis.__chrome.setAirplaneMode(true);
    });
    // re-read the fresh context: setAirplaneMode produces a new value object
    const ctx = globalThis.__chrome;
    assert.equal(ctx.offline, true);
    assert.equal(ctx.network.mode, "airplane");
    assert.equal(ctx.network.online, false);
    // refresh while offline must not reach the handler
    let out;
    await act(async () => {
      out = await globalThis.__chrome.refresh();
    });
    assert.equal(out.ok, false);
    assert.match(out.text, /airplane mode/i);
    assert.equal(calls, 0, "the screen handler must not be called while offline");
    // turning airplane mode back off resumes (and re-fetches once)
    act(() => {
      globalThis.__chrome.setAirplaneMode(false);
    });
    assert.equal(globalThis.__chrome.offline, false);
    assert.equal(calls, 1, "resuming re-fetches the screen once");
  });
  test("refresh with no registered handler is a declared failure", async () => {
    await mountProvider();
    let out;
    await act(async () => {
      out = await globalThis.__chrome.refresh();
    });
    assert.equal(out.ok, false);
    assert.match(out.text, /nothing to refresh/i);
  });

  test("a throwing refresh handler is surfaced honestly, not swallowed", async () => {
    await mountProvider();
    act(() => {
      globalThis.__chrome.registerRefresh(() => {
        throw new Error("sim bridge down");
      }, "Test");
    });
    let out;
    await act(async () => {
      out = await globalThis.__chrome.refresh();
    });
    assert.equal(out.ok, false);
    assert.match(out.text, /sim bridge down/);
  });

  test("mounting without onNavigate leaves navigation entries null", async () => {
    let renderer;
    await act(async () => {
      renderer = create(
        React.createElement(
          DeviceChromeProvider,
          { screen: "home" },
          React.createElement(Probe, null, null)
        )
      );
    });
    assert.equal(globalThis.__chrome.goHome, null);
    assert.equal(globalThis.__chrome.goToSettings, null);
    await act(async () => {
      renderer.unmount();
    });
  });
});

import { DeviceStatusCluster, NoticeHost } from "./DeviceChrome.jsx";

function renderTree(renderer) {
  const node = renderer.toJSON();
  return node == null ? "null" : JSON.stringify(node);
}

async function mountCluster({ onNavigate = () => {} } = {}) {
  let renderer;
  await act(async () => {
    renderer = create(
      React.createElement(
        DeviceChromeProvider,
        { screen: "home", onNavigate, appVersion: "v-test" },
        React.createElement(Probe, null, React.createElement(DeviceStatusCluster))
      )
    );
  });
  return renderer;
}

// Walk the host tree (renderer.toJSON()) for the first node matching pred.
function findNode(node, pred) {
  if (node == null) return null;
  if (pred(node)) return node;
  const kids = Array.isArray(node.children)
    ? node.children
    : node.children
      ? [node.children]
      : [];
  for (const child of kids) {
    const hit = findNode(child, pred);
    if (hit) return hit;
  }
  return null;
}

function byLabel(node, label) {
  return findNode(node, (n) => n.type && n.type !== "#text" && n.props && n.props["aria-label"] === label);
}

async function invoke(node, prop) {
  await act(async () => {
    node.props[prop]?.();
  });
}

// The quick-settings item buttons carry no aria-label (the label lives in a
// child span), so look a menu item up by the text inside its subtree.
function subtreeText(node) {
  if (node == null) return "";
  if (typeof node === "string") return node;
  const kids = Array.isArray(node.children)
    ? node.children
    : node.children
      ? [node.children]
      : [];
  return kids.map(subtreeText).join(" ");
}

function findMenuItem(node, label) {
  return findNode(
    node,
    (n) =>
      n.props &&
      (n.props.role === "menuitem" || n.props.role === "menuitemcheckbox") &&
      subtreeText(n).includes(label)
  );
}

// BrightnessControl and QuickSettings share the "qr-qs" wrapper class and both
// carry an onKeyDown; disambiguate by the role of the popover they own.
function findQrQsWrapper(node, role) {
  let found = null;
  (function walk(n) {
    if (n == null || found) return;
    if (
      n.type === "div" &&
      n.props &&
      n.props.className === "qr-qs" &&
      n.props.onKeyDown &&
      findNode(n, (x) => x.props && x.props.role === role)
    ) {
      found = n;
      return;
    }
    const kids = Array.isArray(n.children)
      ? n.children
      : n.children
        ? [n.children]
        : [];
    kids.forEach(walk);
  })(node);
  return found;
}

describe("DeviceStatusCluster", () => {
  afterEach(() => {
    delete globalThis.navigator;
    delete globalThis.window;
  });

  test("renders HOME, brightness, refresh, quick-settings and both indicators", async () => {
    const renderer = await mountCluster();
    const tree = renderer.toJSON();
    const root = tree;
    assert.ok(root, "cluster renders");
    // data-testid anchor on the root span
    assert.equal(root.props["data-testid"], "device-chrome");
    const html = renderTree(renderer);
    assert.ok(html.includes("HOME"), "HOME button present");
    assert.ok(html.includes("LINK —") || html.includes("ONLINE") || html.includes("OFFLINE"), "network label present");
    assert.ok(html.includes("—"), "battery label present (unknown ⇒ em dash)");
  });

  test("network indicator reflects the real navigator state", async () => {
    globalThis.navigator = { onLine: false, connection: null };
    globalThis.window = { addEventListener: vi.fn(), removeEventListener: vi.fn() };
    const renderer = await mountCluster();
    const html = renderTree(renderer);
    assert.ok(html.includes("OFFLINE"), "offline navigator ⇒ OFFLINE shown");
  });

  test("a real battery API renders a numeric level, not an em dash", async () => {
    globalThis.navigator = { getBattery: () => Promise.resolve({ level: 0.6, charging: false }) };
    globalThis.window = { addEventListener: vi.fn(), removeEventListener: vi.fn() };
    const renderer = await mountCluster();
    const html = renderTree(renderer);
    assert.ok(html.includes("60"), "60% battery shown from navigator.getBattery()");
  });

  test("HOME button navigates home", async () => {
    const calls = [];
    const renderer = await mountCluster({ onNavigate: (s) => calls.push(s) });
    const home = byLabel(renderer.toJSON(), "Home");
    assert.ok(home, "Home button found");
    await invoke(home, "onClick");
    assert.deepEqual(calls, ["home"]);
  });

  test("Refresh button re-fetches the registered screen handler", async () => {
    let fetched = 0;
    const renderer = await mountCluster();
    act(() => {
      globalThis.__chrome.registerRefresh(async () => {
        fetched += 1;
        return { ok: true, text: "done" };
      }, "Test screen");
    });
    const refresh = byLabel(renderer.toJSON(), "Refresh this screen");
    assert.ok(refresh, "Refresh button found");
    await invoke(refresh, "onClick");
    assert.equal(fetched, 1, "the screen handler was invoked exactly once");
  });

  test("brightness popover steps up and down and closes on Escape", async () => {
    const renderer = await mountCluster();
    let node = renderer.toJSON();
    const brightBtn = findNode(
      node,
      (n) => n.props && typeof n.props["aria-label"] === "string" && n.props["aria-label"].startsWith("Brightness")
    );
    assert.ok(brightBtn, "brightness button found");
    await invoke(brightBtn, "onClick");
    node = renderer.toJSON();
    assert.ok(
      findNode(node, (n) => n.props && n.props["aria-label"] === "Increase brightness"),
      "increase control present"
    );
    await invoke(
      findNode(node, (n) => n.props && n.props["aria-label"] === "Increase brightness"),
      "onClick"
    );
    node = renderer.toJSON();
    await invoke(
      findNode(node, (n) => n.props && n.props["aria-label"] === "Decrease brightness"),
      "onClick"
    );
    node = renderer.toJSON();
    const wrapper = findQrQsWrapper(node, "dialog");
    assert.ok(wrapper, "brightness wrapper found");
    await act(async () => {
      wrapper.props.onKeyDown({ key: "Escape", stopPropagation() {} });
    });
    node = renderer.toJSON();
    assert.equal(
      findNode(node, (n) => n.type === "div" && n.props && n.props.role === "dialog"),
      null,
      "dialog closed after Escape"
    );
  });

  test("quick-settings opens, toggles airplane mode and navigates to Settings", async () => {
    const calls = [];
    const renderer = await mountCluster({ onNavigate: (s) => calls.push(s) });
    let node = renderer.toJSON();
    const kebab = byLabel(node, "Quick settings");
    assert.ok(kebab, "kebab found");
    await invoke(kebab, "onClick");
    node = renderer.toJSON();
    assert.ok(
      findNode(node, (n) => n.type === "div" && n.props && n.props.role === "menu"),
      "menu opened"
    );
    // Item buttons carry no aria-label (the label lives in a child span), so
    // look them up by the text inside their subtree.
    const airplane = findMenuItem(node, "Airplane mode");
    assert.ok(airplane, "airplane-mode toggle found");
    await invoke(airplane, "onClick");
    assert.equal(globalThis.__chrome.offline, true, "airplane mode ⇒ offline");
    assert.equal(globalThis.__chrome.prefs.airplaneMode, true);
    node = renderer.toJSON();
    const settings = findMenuItem(node, "Settings");
    assert.ok(settings, "Settings item found");
    await invoke(settings, "onClick");
    assert.deepEqual(calls, ["settings"]);
    node = renderer.toJSON();
    assert.equal(
      findNode(node, (n) => n.type === "div" && n.props && n.props.role === "menu"),
      null,
      "menu closed after navigation"
    );
  });

  test("quick-settings keyboard: ArrowDown/End/Escape drive focus and close", async () => {
    const renderer = await mountCluster();
    let node = renderer.toJSON();
    const kebab = byLabel(node, "Quick settings");
    await invoke(kebab, "onClick");
    node = renderer.toJSON();
    // Both BrightnessControl and QuickSettings use className="qr-qs"; the
    // quick-settings wrapper is the one owning the open role="menu" popover.
    const wrapper = findQrQsWrapper(node, "menu");
    assert.ok(wrapper, "quick-settings wrapper found");
    const key = (k) =>
      act(async () => {
        wrapper.props.onKeyDown({ key: k, preventDefault() {}, stopPropagation() {} });
      });
    await key("ArrowDown");
    await key("End");
    await key("Escape");
    node = renderer.toJSON();
    assert.equal(
      findNode(node, (n) => n.type === "div" && n.props && n.props.role === "menu"),
      null,
      "Escape closed the menu"
    );
  });

  test("outside click (backdrop mousedown) closes the quick-settings menu", async () => {
    const renderer = await mountCluster();
    let node = renderer.toJSON();
    await invoke(byLabel(node, "Quick settings"), "onClick");
    node = renderer.toJSON();
    const backdrop = findNode(
      node,
      (n) => n.props && n.props["data-testid"] === "quicksettings-backdrop"
    );
    assert.ok(backdrop, "backdrop found");
    await invoke(backdrop, "onMouseDown");
    node = renderer.toJSON();
    assert.equal(
      findNode(node, (n) => n.type === "div" && n.props && n.props.role === "menu"),
      null,
      "menu closed on outside click"
    );
  });
});

describe("NoticeHost", () => {
  test("nothing rendered when there are no notices and DND is off", async () => {
    let renderer;
    await act(async () => {
      renderer = create(
        React.createElement(
          DeviceChromeProvider,
          { screen: "home", onNavigate: () => {} },
          React.createElement(NoticeHost)
        )
      );
    });
    assert.equal(renderer.toJSON(), null);
  });

  test("Do Not Disturb shows the suppression count instead of alerts", async () => {
    let renderer;
    await act(async () => {
      renderer = create(
        React.createElement(
          DeviceChromeProvider,
          { screen: "home", onNavigate: () => {} },
          React.createElement(Probe, null, React.createElement(NoticeHost))
        )
      );
    });
    act(() => {
      globalThis.__chrome.setNotificationsOff(true);
    });
    // re-read: notify's closure depends on prefs.notificationsOff, so the
    // stale captured callback would not suppress. The fresh one does.
    act(() => {
      globalThis.__chrome.notify({ kind: "warn", text: "hidden alert" });
    });
    assert.equal(globalThis.__chrome.suppressedCount, 1);
    assert.equal(globalThis.__chrome.notices.length, 0);
    const html = renderTree(renderer);
    assert.ok(html.includes("Notifications off"), "suppression line shown");
    assert.ok(html.includes("suppressed"), "count stated");
    assert.ok(!html.includes("hidden alert"), "the alert itself is withheld");
    delete globalThis.__chrome;
  });

  test("an active alert is rendered and dismissed via its button", async () => {
    let renderer;
    await act(async () => {
      renderer = create(
        React.createElement(
          DeviceChromeProvider,
          { screen: "home", onNavigate: () => {} },
          React.createElement(Probe, null, React.createElement(NoticeHost))
        )
      );
    });
    act(() => {
      globalThis.__chrome.notify({ kind: "error", text: "link lost" });
    });
    let node = renderer.toJSON();
    const host = findNode(
      node,
      (n) => n.props && n.props["data-testid"] === "notice-host"
    );
    assert.ok(host, "notice host rendered");
    const dismiss = byLabel(node, "Dismiss alert");
    assert.ok(dismiss, "dismiss button present");
    await invoke(dismiss, "onClick");
    node = renderer.toJSON();
    assert.equal(
      findNode(node, (n) => n.props && n.props["data-testid"] === "notice-host"),
      null,
      "no notices left ⇒ host unmounted"
    );
    delete globalThis.__chrome;
  });
});
