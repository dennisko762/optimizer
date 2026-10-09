/**
 * Tests for techMappers — node:test, pure functions only.
 *
 * Follows the boardingMappers.test.js style: TDD, no DOM or framework
 * dependencies. Mirrors the backend contract of crew_platform/technical
 * (T2–T5): defect lifecycle, status derivation, status response shape.
 */

import { describe, it } from "vitest";
import assert from "node:assert/strict";

import {
  DEFECT_STATUS_VALUES,
  DEFECT_STATUS_TRANSITIONS,
  DEFECT_TERMINAL_STATUSES,
  MAINTENANCE_ACTION_TYPES,
  normalizeStatus,
  defectDisplayName,
  technicalStatusLabel,
  mapDefect,
  mapDefects,
  openDefects,
  deriveTechnicalStatus,
  mapStatusResponse,
  mapTechlogEntry,
  sortTechlogNewestFirst,
  mapMaintenanceAction,
  mapHomeCardModel,
} from "./techMappers.js";

// ────────────────────────────────────────────────────────────────
// constants
// ────────────────────────────────────────────────────────────────
describe("domain constants", () => {
  it("lists all six defect statuses", () => {
    assert.deepEqual(DEFECT_STATUS_VALUES, [
      "OPEN",
      "UNDER_REVIEW",
      "DEFERRED",
      "MEL_APPLIED",
      "RECTIFIED",
      "CLOSED",
    ]);
  });

  it("has no transitions out of terminal statuses", () => {
    assert.deepEqual(DEFECT_STATUS_TRANSITIONS["RECTIFIED"], ["CLOSED"]);
    assert.deepEqual(DEFECT_STATUS_TRANSITIONS["CLOSED"], []);
    for (const t of DEFECT_TERMINAL_STATUSES) {
      assert.ok(DEFECT_STATUS_TRANSITIONS[t].length <= 1);
    }
  });

  it("OPEN allows every non-terminal target except staying OPEN", () => {
    assert.deepEqual(DEFECT_STATUS_TRANSITIONS["OPEN"], [
      "UNDER_REVIEW",
      "DEFERRED",
      "MEL_APPLIED",
      "RECTIFIED",
      "CLOSED",
    ]);
  });

  it("lists the four maintenance action types", () => {
    assert.deepEqual(MAINTENANCE_ACTION_TYPES, [
      "RECTIFICATION",
      "INSPECTION",
      "COMPONENT_SWAP",
      "GENERAL",
    ]);
  });
});

// ────────────────────────────────────────────────────────────────
// normalizeStatus
// ────────────────────────────────────────────────────────────────
describe("normalizeStatus", () => {
  it("upper-cases a raw status", () => {
    assert.equal(normalizeStatus("mel_applied"), "MEL_APPLIED");
  });

  it("returns empty string for null/undefined", () => {
    assert.equal(normalizeStatus(null), "");
    assert.equal(normalizeStatus(undefined), "");
  });
});

// ────────────────────────────────────────────────────────────────
// defectDisplayName
// ────────────────────────────────────────────────────────────────
describe("defectDisplayName", () => {
  it("builds 'COMPONENT - STATUS' from system_component", () => {
    const d = {
      system_component: "Pack 1 regulation fault",
      status: "open",
      ata: "21",
      description: "something else",
    };
    assert.equal(defectDisplayName(d), "PACK 1 REGULATION FAULT - OPEN");
  });

  it("falls back to description when no system_component", () => {
    const d = { description: "Nose gear leak", status: "UNDER_REVIEW" };
    assert.equal(defectDisplayName(d), "NOSE GEAR LEAK - UNDER REVIEW");
  });

  it("falls back to ATA chapter, then generic DEFECT", () => {
    assert.equal(defectDisplayName({ ata: "21", status: "OPEN" }), "ATA 21 - OPEN");
    assert.equal(defectDisplayName({ status: "OPEN" }), "DEFECT - OPEN");
  });

  it("defaults missing status to OPEN", () => {
    assert.equal(defectDisplayName({ system_component: "APU" }), "APU - OPEN");
  });

  it("returns empty string for null input", () => {
    assert.equal(defectDisplayName(null), "");
  });
});

// ────────────────────────────────────────────────────────────────
// technicalStatusLabel
// ────────────────────────────────────────────────────────────────
describe("technicalStatusLabel", () => {
  it("renders DISPATCHABLE_WITH_MEL as 'DISPATCHABLE WITH MEL'", () => {
    assert.equal(technicalStatusLabel("DISPATCHABLE_WITH_MEL"), "DISPATCHABLE WITH MEL");
  });

  it("renders SERVICEABLE as 'SERVICEABLE'", () => {
    assert.equal(technicalStatusLabel("serviceable"), "SERVICEABLE");
  });

  it("renders em dash for empty status", () => {
    assert.equal(technicalStatusLabel(""), "—");
    assert.equal(technicalStatusLabel(null), "—");
  });
});

// ────────────────────────────────────────────────────────────────
// mapDefect / mapDefects
// ────────────────────────────────────────────────────────────────
describe("mapDefect", () => {
  it("adds legal transitions and display flags", () => {
    const d = mapDefect({
      id: 1,
      description: "Pack 1 fault",
      status: "open",
      system_component: "Pack 1",
    });
    assert.equal(d.status, "OPEN");
    assert.equal(d.terminal, false);
    assert.equal(d.live, true);
    assert.deepEqual(d.legalTransitions, [
      "UNDER_REVIEW",
      "DEFERRED",
      "MEL_APPLIED",
      "RECTIFIED",
      "CLOSED",
    ]);
    assert.equal(d.displayName, "PACK 1 - OPEN");
  });

  it("marks CLOSED as terminal with no legal transitions", () => {
    const d = mapDefect({ id: 2, status: "CLOSED" });
    assert.equal(d.terminal, true);
    assert.equal(d.live, false);
    assert.deepEqual(d.legalTransitions, []);
  });

  it("returns null for null input", () => {
    assert.equal(mapDefect(null), null);
  });

  it("mapDefects handles non-array input", () => {
    assert.deepEqual(mapDefects(null), []);
    assert.deepEqual(mapDefects(undefined), []);
  });

  it("mapDefects maps each defect and preserves order", () => {
    const out = mapDefects([
      { id: 1, status: "OPEN" },
      { id: 2, status: "MEL_APPLIED" },
    ]);
    assert.equal(out.length, 2);
    assert.equal(out[0].id, 1);
    assert.equal(out[1].live, true);
  });
});

// ────────────────────────────────────────────────────────────────
// openDefects
// ────────────────────────────────────────────────────────────────
describe("openDefects", () => {
  it("keeps only live (non-terminal) defects", () => {
    const defects = [
      { status: "OPEN" },
      { status: "UNDER_REVIEW" },
      { status: "DEFERRED" },
      { status: "MEL_APPLIED" },
      { status: "RECTIFIED" },
      { status: "CLOSED" },
    ];
    const open = openDefects(defects);
    assert.equal(open.length, 4);
    assert.ok(!open.some((d) => d.status === "RECTIFIED" || d.status === "CLOSED"));
  });

  it("returns [] for non-array input", () => {
    assert.deepEqual(openDefects(null), []);
  });
});

// ────────────────────────────────────────────────────────────────
// deriveTechnicalStatus (mirrors backend status.py:derive_status)
// ────────────────────────────────────────────────────────────────
describe("deriveTechnicalStatus", () => {
  it("returns SERVICEABLE for no defects", () => {
    assert.equal(deriveTechnicalStatus([]), "SERVICEABLE");
    assert.equal(deriveTechnicalStatus(null), "SERVICEABLE");
  });

  it("OPEN beats everything", () => {
    assert.equal(
      deriveTechnicalStatus([
        { status: "OPEN" },
        { status: "MEL_APPLIED" },
        { status: "UNDER_REVIEW" },
      ]),
      "OPEN_DEFECTS"
    );
  });

  it("DEFERRED / MEL_APPLIED beat UNDER_REVIEW", () => {
    assert.equal(
      deriveTechnicalStatus([{ status: "DEFERRED" }, { status: "UNDER_REVIEW" }]),
      "DISPATCHABLE_WITH_MEL"
    );
    assert.equal(
      deriveTechnicalStatus([{ status: "MEL_APPLIED" }]),
      "DISPATCHABLE_WITH_MEL"
    );
  });

  it("UNDER_REVIEW alone", () => {
    assert.equal(deriveTechnicalStatus([{ status: "UNDER_REVIEW" }]), "UNDER_REVIEW");
  });

  it("terminal defects never influence the result", () => {
    assert.equal(
      deriveTechnicalStatus([{ status: "RECTIFIED" }, { status: "CLOSED" }]),
      "SERVICEABLE"
    );
  });

  it("accepts raw status strings", () => {
    assert.equal(deriveTechnicalStatus(["OPEN", "CLOSED"]), "OPEN_DEFECTS");
  });
});

// ────────────────────────────────────────────────────────────────
// mapStatusResponse (T5 GET /aircraft/{reg}/status)
// ────────────────────────────────────────────────────────────────
describe("mapStatusResponse", () => {
  it("maps the T5 status response to the compact card model", () => {
    const status = {
      registration: "A7-TEST",
      type: "A359",
      current_technical_status: "OPEN_DEFECTS",
      open_defects: 2,
      open_defects_list: [
        {
          id: 1,
          ata: "21",
          system_component: "Pack 1 regulation fault",
          status: "OPEN",
          description: "pack fault",
        },
        {
          id: 2,
          ata: "32",
          system_component: "Nose gear leak",
          status: "MEL_APPLIED",
          description: "leak",
        },
      ],
    };
    const vm = mapStatusResponse(status);
    assert.equal(vm.registration, "A7-TEST");
    assert.equal(vm.type, "A359");
    assert.equal(vm.status, "OPEN_DEFECTS");
    assert.equal(vm.statusLabel, "OPEN DEFECTS");
    assert.equal(vm.openCount, 2);
    assert.equal(vm.defects[0].displayName, "PACK 1 REGULATION FAULT - OPEN");
  });

  it("falls back to derivation when the response lacks a status", () => {
    const vm = mapStatusResponse({
      registration: "A7-TEST",
      open_defects_list: [{ status: "DEFERRED" }],
    });
    assert.equal(vm.status, "DISPATCHABLE_WITH_MEL");
    assert.equal(vm.openCount, 1);
  });

  it("returns an empty model for null input", () => {
    const vm = mapStatusResponse(null);
    assert.equal(vm.registration, "");
    assert.equal(vm.status, "SERVICEABLE");
    assert.equal(vm.openCount, 0);
    assert.deepEqual(vm.defects, []);
  });
});

// ────────────────────────────────────────────────────────────────
// mapTechlogEntry / sortTechlogNewestFirst
// ────────────────────────────────────────────────────────────────
describe("mapTechlogEntry", () => {
  it("normalises status and carries defect count", () => {
    const e = mapTechlogEntry({ id: 7, status: "open", created_at: "2026-10-05T10:00:00Z" }, 2);
    assert.equal(e.status, "OPEN");
    assert.equal(e.defectCount, 2);
  });

  it("returns null for null input", () => {
    assert.equal(mapTechlogEntry(null), null);
  });
});

describe("sortTechlogNewestFirst", () => {
  it("sorts entries newest first by created_at", () => {
    const entries = [
      { id: 1, created_at: "2026-10-05T08:00:00Z" },
      { id: 2, created_at: "2026-10-05T12:00:00Z" },
      { id: 3, created_at: "2026-10-05T10:00:00Z" },
    ];
    const out = sortTechlogNewestFirst(entries);
    assert.deepEqual(out.map((e) => e.id), [2, 3, 1]);
  });

  it("does not mutate the input array", () => {
    const entries = [
      { id: 1, created_at: "2026-10-05T08:00:00Z" },
      { id: 2, created_at: "2026-10-05T12:00:00Z" },
    ];
    const copy = entries.map((e) => e.id);
    sortTechlogNewestFirst(entries);
    assert.deepEqual(entries.map((e) => e.id), copy);
  });

  it("breaks ties on id descending", () => {
    const entries = [
      { id: 1, created_at: "2026-10-05T10:00:00Z" },
      { id: 5, created_at: "2026-10-05T10:00:00Z" },
    ];
    const out = sortTechlogNewestFirst(entries);
    assert.deepEqual(out.map((e) => e.id), [5, 1]);
  });

  it("returns [] for non-array input", () => {
    assert.deepEqual(sortTechlogNewestFirst(null), []);
  });
});

// ────────────────────────────────────────────────────────────────
// mapMaintenanceAction
// ────────────────────────────────────────────────────────────────
describe("mapMaintenanceAction", () => {
  it("normalises action_type", () => {
    const a = mapMaintenanceAction({ id: "MA-A7-TEST-0001-ab12cd", action_type: "rectification" });
    assert.equal(a.action_type, "RECTIFICATION");
  });

  it("returns null for null input", () => {
    assert.equal(mapMaintenanceAction(null), null);
  });
});

// ────────────────────────────────────────────────────────────────
// mapHomeCardModel
// ────────────────────────────────────────────────────────────────
describe("mapHomeCardModel", () => {
  it("prefers the /status response when present", () => {
    const vm = mapHomeCardModel(
      { registration: "A7-TEST", type: "A359" },
      { registration: "A7-TEST", current_technical_status: "SERVICEABLE", open_defects_list: [] }
    );
    assert.equal(vm.status, "SERVICEABLE");
    assert.equal(vm.openCount, 0);
  });

  it("falls back to aircraft-only model when no status", () => {
    const vm = mapHomeCardModel({ registration: "A7-TEST", type: "A359" }, null);
    assert.equal(vm.registration, "A7-TEST");
    assert.equal(vm.status, "");
    assert.equal(vm.openCount, 0);
  });

  it("returns empty model when nothing is known", () => {
    const vm = mapHomeCardModel(null, null);
    assert.equal(vm.registration, "");
  });
});
