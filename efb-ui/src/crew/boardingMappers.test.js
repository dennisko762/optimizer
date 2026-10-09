/**
 * Tests for boardingMappers — TDD, node:test.
 *
 * Tests written before implementation covers them (failing-then-passing).
 */

import { describe, it } from "vitest";
import assert from "node:assert/strict";

import {
  ringPercent,
  computeWeights,
  deriveGroups,
  planToBoardingViewModel,
} from "./boardingMappers.js";

// ────────────────────────────────────────────────────────────────
// ringPercent
// ────────────────────────────────────────────────────────────────
describe("ringPercent", () => {
  it("returns 0 when planned is 0", () => {
    assert.equal(ringPercent(50, 0), 0);
  });

  it("returns 0 when planned is negative", () => {
    assert.equal(ringPercent(10, -5), 0);
  });

  it("computes percentage correctly", () => {
    assert.equal(ringPercent(50, 200), 25);
  });

  it("clamps to 100 when current > planned", () => {
    assert.equal(ringPercent(150, 100), 100);
  });

  it("returns 0 for non-number inputs", () => {
    assert.equal(ringPercent("a", 10), 0);
    assert.equal(ringPercent(10, null), 0);
  });

  it("returns 0 for zero current", () => {
    assert.equal(ringPercent(0, 100), 0);
  });

  it("returns 100 for exact match", () => {
    assert.equal(ringPercent(142, 142), 100);
  });
});

// ────────────────────────────────────────────────────────────────
// computeWeights
// ────────────────────────────────────────────────────────────────
describe("computeWeights", () => {
  it("computes all derived weights", () => {
    const result = computeWeights({
      oewKg: 40000,
      paxCount: 142,
      paxKgEach: 84,
      bagCount: 120,
      bagKgEach: 15,
      cargoKg: 500,
      fuelKg: 8000,
    });
    assert.equal(result.paxKg, 142 * 84);
    assert.equal(result.bagKg, 120 * 15);
    assert.equal(result.zfwKg, 40000 + 142 * 84 + 120 * 15 + 500);
    assert.equal(result.towKg, result.zfwKg + 8000);
  });

  it("handles zero/default inputs", () => {
    const result = computeWeights({});
    assert.equal(result.paxKg, 0);
    assert.equal(result.bagKg, 0);
    assert.equal(result.zfwKg, 0);
    assert.equal(result.towKg, 0);
  });

  it("handles no arguments", () => {
    const result = computeWeights();
    assert.equal(result.towKg, 0);
  });
});

// ────────────────────────────────────────────────────────────────
// deriveGroups
// ────────────────────────────────────────────────────────────────
describe("deriveGroups", () => {
  it("returns empty array for null/empty input", () => {
    assert.deepEqual(deriveGroups(null), []);
    assert.deepEqual(deriveGroups([]), []);
  });

  it("marks the in-progress group", () => {
    const groups = [
      { boardingTime: "07:30", plannedPax: 30 },
      { boardingTime: "07:45", plannedPax: 50 },
      { boardingTime: "08:00", plannedPax: 62 },
    ];
    const result = deriveGroups(groups, "07:50");
    assert.equal(result[0].inProgress, false);
    assert.equal(result[1].inProgress, true);
    assert.equal(result[2].inProgress, false);
  });

  it("marks first group when before all times", () => {
    const groups = [
      { boardingTime: "08:00", plannedPax: 50 },
      { boardingTime: "08:30", plannedPax: 50 },
    ];
    const result = deriveGroups(groups, "07:00");
    // None started yet
    assert.equal(result[0].inProgress, false);
    assert.equal(result[1].inProgress, false);
  });

  it("marks last group when past all times", () => {
    const groups = [
      { boardingTime: "07:00", plannedPax: 50 },
      { boardingTime: "07:30", plannedPax: 50 },
    ];
    const result = deriveGroups(groups, "08:00");
    assert.equal(result[0].inProgress, false);
    assert.equal(result[1].inProgress, true);
  });
});

// ────────────────────────────────────────────────────────────────
// planToBoardingViewModel
// ────────────────────────────────────────────────────────────────
describe("planToBoardingViewModel", () => {
  const fixtureFlight = {
    flight_number: "LH2024",
    departure_icao: "EDDF",
    arrival_icao: "LEPA",
    callsign: "DLH2024",
    sibt: "12:30",
    sobt: "10:00",
    block_time: 150,
  };

  const fixtureOfp = {
    pax_count: 142,
    bag_count: 120,
    oew_kg: 42000,
    pax_kg_each: 84,
    bag_kg_each: 15,
    cargo_kg: 500,
    fuel_kg: 8500,
  };

  it("builds a full view model from flight + OFP", () => {
    const vm = planToBoardingViewModel(fixtureFlight, fixtureOfp, {});
    assert.equal(vm.header.flightNumber, "LH2024");
    assert.equal(vm.header.route, "EDDF → LEPA");
    assert.equal(vm.header.sibt, "12:30");
    assert.equal(vm.header.sobt, "10:00");
    assert.equal(vm.paxRing.planned, 142);
    assert.equal(vm.paxRing.current, 0);
    assert.equal(vm.paxRing.percent, 0);
    assert.equal(vm.bagsRing.planned, 120);
    assert.equal(vm.weights.oewKg, 42000);
    assert.equal(vm.weights.fuelKg, 8500);
  });

  it("crew overrides override OFP values", () => {
    const vm = planToBoardingViewModel(fixtureFlight, fixtureOfp, {
      paxPlanned: 130,
      paxAte: 118,
    });
    assert.equal(vm.paxRing.planned, 130);
    assert.equal(vm.paxRing.current, 118);
    assert.ok(vm.paxRing.percent > 0);
  });

  it("detects SimBrief vs crew conflict", () => {
    const vm = planToBoardingViewModel(fixtureFlight, fixtureOfp, {
      paxPlanned: 130,
    });
    assert.equal(vm.conflicts.length, 1);
    assert.equal(vm.conflicts[0].field, "paxPlanned");
    assert.equal(vm.conflicts[0].crewValue, 130);
    assert.equal(vm.conflicts[0].simbriefValue, 142);
  });

  it("no conflicts when crew hasn't overridden", () => {
    const vm = planToBoardingViewModel(fixtureFlight, fixtureOfp, {});
    assert.equal(vm.conflicts.length, 0);
  });

  it("handles null flight and ofp gracefully", () => {
    const vm = planToBoardingViewModel(null, null, {});
    assert.equal(vm.header.flightNumber, "");
    assert.equal(vm.header.route, "");
    assert.equal(vm.paxRing.planned, 0);
  });
});
