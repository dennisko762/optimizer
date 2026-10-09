import assert from "node:assert/strict";
import React, { useEffect } from "react";
import { act, create } from "react-test-renderer";
import { afterEach, beforeEach, test, vi } from "vitest";

import { useNavigraph } from "./useNavigraph.js";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let current;
let renderer;

function Probe(props) {
  const value = useNavigraph(props);
  useEffect(() => {
    current = value;
  }, [value]);
  return null;
}

function response(body, status = 200) {
  return { status, json: vi.fn().mockResolvedValue(body) };
}

async function mount(props = {}) {
  await act(async () => {
    renderer = create(React.createElement(Probe, props));
  });
}

beforeEach(() => {
  current = undefined;
  renderer = undefined;
  globalThis.fetch = vi.fn().mockImplementation((url) => {
    if (url.endsWith("/status")) return Promise.resolve(response({ status: "ok", datatypes: {} }));
    if (url.includes("/notams")) return Promise.resolve(response({ status: "ok", data: [] }));
    if (url.endsWith("/risks")) return Promise.resolve(response({ status: "ok", data: {} }));
    if (url.endsWith("/navdata")) return Promise.resolve(response({ status: "ok", data: [] }));
    return Promise.resolve(response({ status: "ok" }));
  });
});

afterEach(async () => {
  if (renderer) await act(async () => renderer.unmount());
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("loads status, NOTAM, risk and navdata with canonical station codes", async () => {
  await mount({ apiBase: "https://crew.example", stations: [" doh ", "LHR", "DOH", "bad-code"] });

  assert.deepEqual(current.stations, ["DOH", "LHR"]);
  assert.equal(current.status.status, "ok");
  assert.equal(current.notams.status, "ok");
  assert.equal(current.risks.status, "ok");
  assert.equal(current.navdata.status, "ok");
  assert.ok(
    fetch.mock.calls.some(([url]) => url === "https://crew.example/api/crew/navigraph/notams?icao=DOH%2CLHR")
  );
});

test("declares no_stations without issuing a NOTAM request", async () => {
  await mount({ stations: ["", "invalid station"] });

  assert.equal(current.notams.status, "no_stations");
  assert.equal(current.notams.summary.total, 0);
  assert.equal(fetch.mock.calls.some(([url]) => url.includes("/notams?")), false);
});

test("disabled hook performs no I/O and refresh methods stay inert", async () => {
  await mount({ enabled: false, stations: ["DOH"] });

  assert.equal(fetch.mock.calls.length, 0);
  assert.equal(await current.refreshStatus(), null);
  assert.equal(await current.refreshNotams(), null);
  assert.equal(await current.refreshRisks(), null);
});

test("HTTP and network failures become offline envelopes", async () => {
  await mount({ enabled: true });
  fetch.mockReset();
  fetch
    .mockResolvedValueOnce({ status: 503, json: vi.fn().mockRejectedValue(new Error("bad json")) })
    .mockRejectedValueOnce(new Error("network down"));

  await act(async () => current.refreshStatus());
  assert.deepEqual(current.status, { status: "offline", data: null, detail: "HTTP 503" });
  await act(async () => current.refreshStatus());
  assert.equal(current.status.detail, "network down");
});

test("refreshAll exposes busy state and refreshes every endpoint", async () => {
  await mount({ enabled: true, stations: ["DOH"] });
  fetch.mockClear();
  const releases = [];
  fetch.mockImplementation(
    () => new Promise((resolve) => releases.push(() => resolve(response({ status: "ok", data: [] }))))
  );

  let pending;
  act(() => {
    pending = current.refreshAll();
  });
  assert.equal(current.busy, true);
  assert.equal(releases.length, 4);
  releases.forEach((release) => release());
  await act(async () => pending);
  assert.equal(current.busy, false);
  assert.equal(fetch.mock.calls.length, 4);
});

test("device sign-in polls to authorization and refreshes data", async () => {
  vi.useFakeTimers();
  await mount({ enabled: true, apiBase: "/bridge", stations: ["DOH"] });
  fetch.mockClear();
  fetch.mockImplementation((url) => {
    if (url.endsWith("/auth/device")) {
      return Promise.resolve(response({
        status: "pending",
        user_code: "ABCD-EFGH",
        verification_uri: "https://identity.example/device",
        interval: 2,
      }));
    }
    if (url.includes("/auth/device/poll")) return Promise.resolve(response({ status: "authorized" }));
    return Promise.resolve(response({ status: "ok", data: [] }));
  });

  await act(async () => current.startSignIn());
  assert.equal(current.signIn.user_code, "ABCD-EFGH");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  assert.equal(current.signIn.status, "authorized");
  assert.ok(fetch.mock.calls.some(([url]) => url.includes("user_code=ABCD-EFGH")));
  assert.ok(fetch.mock.calls.some(([url]) => url.endsWith("/status")));
});

test("slow_down polling backs off and sign-out clears state", async () => {
  vi.useFakeTimers();
  await mount({ enabled: false, apiBase: "/bridge" });
  let polls = 0;
  fetch.mockImplementation((url) => {
    if (url.endsWith("/auth/device")) {
      return Promise.resolve(response({ status: "pending", user_code: "A+B", interval: 2 }));
    }
    if (url.includes("/auth/device/poll")) {
      polls += 1;
      return Promise.resolve(response({ status: polls === 1 ? "slow_down" : "expired" }));
    }
    return Promise.resolve(response({ status: "ok", data: [] }));
  });

  await act(async () => current.startSignIn());
  await act(async () => vi.advanceTimersByTimeAsync(2000));
  assert.equal(current.signIn.status, "slow_down");
  await act(async () => vi.advanceTimersByTimeAsync(7000));
  assert.equal(current.signIn.status, "expired");

  await act(async () => current.signOut());
  assert.equal(current.signIn, null);
  assert.ok(fetch.mock.calls.some(([url, init]) => url.endsWith("/auth/signout") && init.method === "POST"));
});
