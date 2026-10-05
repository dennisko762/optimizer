import { test } from "node:test";
import assert from "node:assert/strict";
import {
  notificationToDisplay,
  mapNotifications,
  typeToIcon,
  typeToLabel,
  formatRelativeTime,
} from "./notificationMappers.js";

const NOW = 1000000;

test("notificationToDisplay maps a full event", () => {
  const event = {
    id: "abc123",
    type: "visibility",
    icao: "EDDF",
    summary: "Visibility at EDDF changed from 10.0 to 5.0 km",
    provenance: "open-meteo",
    timestamp: NOW - 120,
    observed: { visibility_km: 5.0 },
  };
  const d = notificationToDisplay(event, NOW);
  assert.equal(d.id, "abc123");
  assert.equal(d.typeIcon, "👁");
  assert.equal(d.typeLabel, "Visibility");
  assert.equal(d.icao, "EDDF");
  assert.equal(d.summary, "Visibility at EDDF changed from 10.0 to 5.0 km");
  assert.equal(d.relativeTime, "2m ago");
  assert.equal(d.provenance, "open-meteo");
});

test("notificationToDisplay returns null for null input", () => {
  assert.equal(notificationToDisplay(null), null);
  assert.equal(notificationToDisplay(undefined), null);
});

test("mapNotifications maps an array of events", () => {
  const events = [
    {
      id: "a",
      type: "wind_speed",
      icao: "KJFK",
      summary: "Wind increase",
      provenance: "open-meteo",
      timestamp: NOW - 60,
      observed: { wind_speed_kt: 25.0 },
    },
    {
      id: "b",
      type: "temperature",
      icao: "EDDF",
      summary: "Temp change",
      provenance: "open-meteo",
      timestamp: NOW - 3700,
      observed: { temp_c: 20.0 },
    },
  ];
  const mapped = mapNotifications(events, NOW);
  assert.equal(mapped.length, 2);
  assert.equal(mapped[0].id, "a");
  assert.equal(mapped[0].relativeTime, "1m ago");
  assert.equal(mapped[1].id, "b");
  assert.equal(mapped[1].relativeTime, "1h ago");
});

test("mapNotifications handles null/empty input", () => {
  assert.deepEqual(mapNotifications(null), []);
  assert.deepEqual(mapNotifications([]), []);
  assert.deepEqual(mapNotifications(undefined), []);
});

test("typeToIcon returns correct icons for known types", () => {
  assert.equal(typeToIcon("visibility"), "👁");
  assert.equal(typeToIcon("wind_direction"), "🧭");
  assert.equal(typeToIcon("wind_speed"), "💨");
  assert.equal(typeToIcon("temperature"), "🌡");
});

test("typeToIcon returns fallback for unknown type", () => {
  assert.equal(typeToIcon("unknown"), "📋");
  assert.equal(typeToIcon(undefined), "📋");
});

test("typeToLabel returns correct labels", () => {
  assert.equal(typeToLabel("visibility"), "Visibility");
  assert.equal(typeToLabel("wind_direction"), "Wind Direction");
  assert.equal(typeToLabel("wind_speed"), "Wind Speed");
  assert.equal(typeToLabel("temperature"), "Temperature");
});

test("typeToLabel returns type string for unknown", () => {
  assert.equal(typeToLabel("foo"), "foo");
});

test("formatRelativeTime returns 'just now' for < 60s", () => {
  assert.equal(formatRelativeTime(NOW, NOW), "just now");
  assert.equal(formatRelativeTime(NOW - 30, NOW), "just now");
});

test("formatRelativeTime returns minutes for < 1h", () => {
  assert.equal(formatRelativeTime(NOW - 120, NOW), "2m ago");
  assert.equal(formatRelativeTime(NOW - 3599, NOW), "59m ago");
});

test("formatRelativeTime returns hours for < 1d", () => {
  assert.equal(formatRelativeTime(NOW - 3600, NOW), "1h ago");
  assert.equal(formatRelativeTime(NOW - 7200, NOW), "2h ago");
});

test("formatRelativeTime returns days for >= 1d", () => {
  assert.equal(formatRelativeTime(NOW - 86400, NOW), "1d ago");
  assert.equal(formatRelativeTime(NOW - 172800, NOW), "2d ago");
});

test("formatRelativeTime handles missing values", () => {
  assert.equal(formatRelativeTime(undefined, NOW), "");
  assert.equal(formatRelativeTime(NOW, undefined), "");
});
