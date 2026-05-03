# Dynamic CI Assistant
### Intelligent In-Flight Cost Index Optimization for Virtual Aviation

---

## What It Is

Dynamic CI Assistant is a real-time flight economics tool designed for virtual pilots flying on simulators. It connects directly to the simulator, reads live aircraft telemetry, and continuously calculates whether the current Cost Index is still optimal — given everything that has changed since the OFP was generated.

It does what real airline dispatchers and flight ops systems do in the background on every flight: evaluate speed strategy against time, fuel, connections, and cost — and present the crew with a clear, justified recommendation.

---

## System Requirements

| Component | Requirement |
|---|---|
| Simulator | Microsoft Flight Simulator (MSFS 2020 / 2024) |
| Sim PC OS | Windows 10 / 11 (64-bit) |
| Sim PC RAM | 4 GB available (beyond MSFS) |
| Client devices | Any modern browser (iOS Safari, Chrome, Edge, Firefox) |
| Network | Local WiFi or Tailscale for remote access |
| SimBrief | Free SimBrief account (for OFP sync) |

No installation required on client devices. No cloud subscription. No account required.

---

## How It Deploys

```
┌─────────────────────────────────┐
│  Sim PC (Windows)               │
│                                 │
│  ┌───────────────────────────┐  │
│  │  efb.exe                  │  │
│  │  · SimConnect bridge      │  │
│  │  · Optimization engine    │  │
│  │  · Web interface server   │  │
│  └───────────────────────────┘  │
│            ▲  port 7070         │
└────────────┼────────────────────┘
             │
    ┌────────┴──────────────────────────┐
    │  Any device / any OS / browser    │
    │  iPad · Android · Mac · Windows   │
    │  http://<sim-pc-ip>:7070          │
    └───────────────────────────────────┘
```

A single executable runs on the sim PC. Every other device — tablet, laptop, phone — connects via browser. Nothing to install on client devices.

For access outside the local network, Tailscale is set up automatically on first launch.

---

## Module Overview

The system is organized into six functional layers. Each layer has a single responsibility and communicates through defined interfaces.

---

### 1 · Sim Connector

**Purpose:** Live aircraft data acquisition.

Maintains a continuous, low-latency connection to the running simulator. Polls aircraft state at configurable intervals and makes the most recent snapshot available to all other modules. Handles reconnection automatically when the simulator is restarted or a new flight is loaded.

Provides: altitude, Mach, gross weight, fuel remaining, ground speed, wind component, ISA deviation, position.

*Client devices may connect from any network location. The connector layer abstracts whether data is sourced locally or forwarded across a network.*

---

### 2 · Performance Engine

**Purpose:** Aircraft-type-aware performance modeling.

Given current flight conditions — altitude, weight, Mach, wind, ISA — the engine computes time and fuel consumption for any candidate speed over the remaining route. It holds performance data for each supported aircraft type and selects the appropriate model automatically based on the aircraft identifier.

The engine evaluates complete remaining-cruise profiles, not point-in-time snapshots. This is what makes speed trade-offs meaningful: not just "faster costs more fuel now" but "exactly how much more fuel, over exactly how many minutes, at the current weight and temperature."

Supported families: Boeing 777 series (200ER, 300ER, 200LR, F), 747-400/8, MD-11, Airbus A330 series (200, 300), A340 series (300, 600).

---

### 3 · Cost Model

**Purpose:** Translate physical quantities into economic outcomes.

Converts fuel delta and time delta into a single comparable cost figure. Accounts for fuel price, ETS (emissions), fuel surcharge, and time cost — either from the airline's configured profile or from defaults. The economic Cost Index is derived from this model, not entered manually.

This layer is what separates an "advisory system" from a genuine optimization tool: recommendations are expressed in euros, not just in Mach numbers.

---

### 4 · Scenario Engine

**Purpose:** Interpret what is actually happening operationally and define the correct optimization objective.

A speed recommendation cannot be correct without knowing *why* it is being requested. The same aircraft, at the same conditions, with a 15-minute delay, should be treated completely differently depending on whether there is a connection bank at the destination, an ATC flow restriction ahead, or a fuel budget constraint.

The Scenario Engine receives a trigger (Connex uplink, ATC constraint, weather update, manual recalculation, etc.) and produces a fully interpreted operational context: what the objective is, what constraints apply, what the derived priority is, and why. The Cost Optimizer then works against this context rather than a generic target.

Supported trigger types:
- Normal cost recalculation
- Connex uplink (passenger connection data)
- Target on-block / recovery
- Reroute / route distance update
- Weather forecast refresh
- Fixed speed or flight level constraint
- Holding / arrival metering
- ATC speed assignment
- ATC level assignment
- VATSIM event flow
- VA scoring profile
- OFP drift check

---

### 5 · Connex Module

**Purpose:** Evaluate inbound passenger connections and quantify their impact on the speed decision.

For hub operations, the question is not just "are we on time" — it is "which outbound flights are at risk, how many passengers are affected, and is there fuel budget to recover." This module processes connection data (outbound flights, LTOP times, passenger loads, per-passenger fuel cost) and produces a structured risk and budget assessment.

The output feeds directly into the Scenario Engine and Cost Model, so the optimizer can weigh the cost of missing a connection against the cost of the fuel required to prevent it.

---

### 6 · ETA & Delay Module

**Purpose:** Continuous arrival estimate and proactive recalculation trigger.

Computes a rolling ETA from live position and ground speed, compares it against the scheduled in-block time, and classifies the delay status (ON TIME / MINOR / SIGNIFICANT / CRITICAL). When the delay changes materially — or when the status escalates — the interface surfaces a prompt to re-run optimization with the updated delay as input.

This is the proactive layer of the system: pilots do not need to manually decide when to recalculate. The module watches for the conditions that make a recalculation warranted and brings it to their attention.

---

### 7 · SimBrief Integration

**Purpose:** One-tap OFP data import.

Fetches the active OFP from the pilot's SimBrief account and populates all relevant fields: aircraft type, route, planned block time, scheduled times, cruise altitude, planned Mach, fuel figures, Cost Index, wind component, ISA deviation, PAX count, and destination coordinates. Eliminates manual data entry entirely at the start of each flight.

---

### 8 · EFB Interface

**Purpose:** The pilot-facing layer.

A responsive web application optimized for tablet use. Displays the current recommendation, the justification, the full strategy table, live telemetry, ETA status, and all input fields required to describe the operational situation. Designed for single-hand operation in portrait or landscape.

The interface does not expose the calculation engine — it presents conclusions, reasons, and deltas. Pilots see what to do and why, not how the number was arrived at.

---

## Security & Privacy

- No data leaves the local network unless Tailscale remote access is explicitly enabled.
- SimBrief credentials are not stored; only the pilot ID is saved locally in the browser.
- No telemetry, analytics, or usage data is collected.
- No account or registration required.

---

## Licensing

Proprietary. All rights reserved. This document describes the product at a functional level for evaluation and media purposes. Internal architecture, algorithms, and performance data are not disclosed.

© 2025 Dennis Korolevych / Pulsation IT
