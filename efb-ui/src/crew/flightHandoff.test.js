import { test } from "node:test";
import assert from "node:assert/strict";
import { flightToOptimizerContext, applyHandoffContext, applyHandoffState } from "./flightHandoff.js";

const LH = { id: "lhvirtual", icao: "DLH", short_code: "LHV" };
const ETD = { id: "etihadvirtual", icao: "ETD", short_code: "ETD" };

test("maps a roster flight onto the optimizer context", () => {
  const flight = {
    flight_id: "f1",
    flight_number: "LH704",
    departure_icao: "EDDF",
    arrival_icao: "KJFK",
    aircraft_icao: "B77W",
  };
  const ctx = flightToOptimizerContext(flight, LH);
  assert.deepEqual(ctx.flightContextPatch, {
    origin: "EDDF",
    destination: "KJFK",
    flightNumber: "LH704",
    airline: "DLH",
  });
  assert.deepEqual(ctx.flightStatePatch, { aircraft: "B77W" });
});

test("uses the provider icao as the airline code", () => {
  const flight = { flight_number: "ET601", departure_icao: "OMDB", arrival_icao: "EKDH" };
  const ctx = flightToOptimizerContext(flight, ETD);
  assert.equal(ctx.flightContextPatch.airline, "ETD");
});

test("returns null for a missing flight", () => {
  assert.equal(flightToOptimizerContext(null, LH), null);
  assert.equal(flightToOptimizerContext(undefined, LH), null);
});

test("nulls out absent flight fields instead of leaving holes", () => {
  const ctx = flightToOptimizerContext({ flight_id: "x" }, LH);
  assert.deepEqual(ctx.flightContextPatch, {
    origin: null,
    destination: null,
    flightNumber: null,
    airline: "DLH",
  });
  assert.deepEqual(ctx.flightStatePatch, { aircraft: null });
});

test("still maps airline when provider is unknown", () => {
  const ctx = flightToOptimizerContext({ flight_number: "AA1" }, null);
  assert.equal(ctx.flightContextPatch.airline, null);
});

test("applyHandoffContext fills only present fields, keeps base for absent", () => {
  const base = { origin: "", destination: "", flightNumber: "", airline: "", sibtUtc: "" };
  const mapped = flightToOptimizerContext(
    { flight_number: "LH704", departure_icao: "EDDF" },
    { icao: "DLH" }
  );
  const out = applyHandoffContext(base, mapped);
  assert.equal(out.origin, "EDDF");
  assert.equal(out.flightNumber, "LH704");
  assert.equal(out.airline, "DLH");
  assert.equal(out.destination, ""); // absent → base preserved
  assert.equal(out.sibtUtc, "");
});

test("applyHandoffContext returns base unchanged when no handoff", () => {
  const base = { origin: "" };
  assert.equal(applyHandoffContext(base, null), base);
});

test("applyHandoffState seeds aircraft only when present", () => {
  const base = { aircraft: "", mach: "" };
  const mapped = flightToOptimizerContext({ aircraft_icao: "B77W" }, { icao: "DLH" });
  const out = applyHandoffState(base, mapped);
  assert.equal(out.aircraft, "B77W");
  assert.equal(out.mach, "");
});

