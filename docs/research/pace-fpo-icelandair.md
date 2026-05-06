# Pace FPO / Icelandair Research Notes

## Source summary

This document summarizes findings from a thesis/paper about Pacelab/Pace Flight Profile Optimizer (FPO) and its planned implementation at Icelandair.

Pace FPO is an onboard Electronic Flight Bag application that recalculates an economical vertical flight profile after takeoff using live aircraft data, live weather data, manufacturer performance data, and operational constraints.

It is not merely a Cost Index calculator. It is a remaining-flight vertical profile optimizer.

## Core product concept

Pace FPO calculates the most cost-efficient speeds and altitudes for the remaining flight and displays an alternative economy vertical flight profile to the crew.

The crew can accept or reject the recommended profile. If accepted, the crew manually enters the recommended values into the FMC.

The system continuously recalculates in the background based on live/current conditions.

## Why it exists

The Operational Flight Plan is usually created before the flight and may be based on assumptions that become outdated:

- forecast winds
- forecast temperatures
- planned cruise altitude
- planned aircraft weight
- expected passengers/cargo/luggage
- departure delay
- reroutes or directs
- ATC altitude restrictions

Pace FPO improves on the OFP/FMC by using richer and more current data.

## Difference from FMC

The FMC optimizes based on current onboard data and has limited re-optimization capability.

Pace FPO looks at the remaining route as a whole and calculates the least expensive way to complete the flight.

Important product principle:

> Optimize the remaining flight holistically, not only the aircraft's current point.

## Inputs

### Live aircraft / avionics inputs

- latitude
- longitude
- altitude
- weight
- speed
- wind
- temperature
- center of gravity

### Operational / flight-plan inputs

- OFP route
- planned waypoints
- planned altitudes
- planned cost index
- planned weather assumptions
- departure time / delay
- reroutes
- deviations from OFP

### Weather inputs

- live weather data uploaded via ACARS/FMC
- real winds at every flight level
- real temperatures at every flight level
- real tropopause altitude at all waypoints
- turbulence forecast data

### Static / built-in data

- manufacturer aircraft performance data
- aircraft-specific performance model
- route and waypoint data
- possible aircraft limitations

### Manual crew inputs

- closed airspace
- military airspace
- altitude constraints
- ATC constraints
- start and end points for constraints
- reroute/direct route waypoint changes

## Icelandair implementation detail

Icelandair planned to implement Pace FPO using an IP broadband connection to aircraft avionics and Spectralux Envoy ACARS.

Initial implementation allowed weather forecast uploads before each flight, with four forecast updates per day.

Later implementation planned on-demand weather data packages via ACARS, especially useful for in-flight reroutes or direct routings.

## Cost concept

Total flight cost includes:

- fuel cost
- trip time
- missed passenger connection cost

Cost Index relates trip-time cost to fuel cost.

Formula:

CI = time-related cost / fuel cost

The paper explains CI as a way to express trip-time cost in fuel-equivalent units.

Example:

- CI 10 means a 2-minute delay is equivalent to 20 kg fuel
- CI 200 means a 2-minute delay is equivalent to 400 kg fuel

Implementation idea:

time_cost_kg = CI * time_minutes

Possible total objective for our optimizer:

total_cost_kg_equivalent =
    fuel_burn_kg
  + time_cost_kg
  + missed_connection_penalty_kg
  + operational_delay_penalty_kg

Important: the paper does not reveal Pace's proprietary internal optimization algorithm.

## Optimization behavior described

The paper says Pace FPO calculates:

- most cost-efficient speeds
- most cost-efficient altitudes
- least expensive remaining vertical flight profile
- exact location between OFP waypoints where altitude changes should happen

It uses:

- live aircraft data
- richer weather data
- manufacturer performance data
- remaining route as a whole

The exact algorithm is not disclosed.

## Recalculation triggers

The optimizer should recalculate when:

- aircraft has taken off
- current aircraft state differs from OFP
- actual weight differs from planned weight
- actual altitude differs from planned altitude
- weather/wind/temp data changes
- new ACARS weather package is received
- departure delay occurs
- missed passenger connection risk changes
- reroute or direct routing is received
- ATC imposes altitude constraints
- NAT OTS or oceanic flight-level restrictions affect cruise level
- turbulence layer affects planned optimum altitude
- temperature/tropopause layers affect optimum altitude
- crew manually changes constraints

## Outputs

The app should not only output an optimum Cost Index.

It should output:

- recommended Cost Index
- recommended economical speed / Mach
- recommended cruise altitude
- recommended step-climb or step-descent points
- exact point between waypoints where a vertical change should occur
- vertical economy flight profile
- alternative economy flight profile
- estimated fuel saving
- estimated time impact
- estimated net cost saving
- turbulence vertical profile warning/chart
- recommended values to enter into the FMC

## Limitations mentioned

- Pace FPO only optimizes the vertical flight profile.
- Lateral route changes/directs must be entered manually and recalculated.
- ATC, military, closed-airspace, and altitude constraints require pilot input.
- Crew must define start and end points of constraints.
- In-flight rerouting increases crew workload because new route waypoints must be entered.
- The exact proprietary optimization algorithm is not disclosed.

## Design consequence for our project

Our tool should be designed as a Remaining Flight Cost Optimizer, not only a CI calculator.

Architecture:

Inputs:
- SimBrief/OFP
- live SimConnect state
- aircraft performance profile
- weather/wind/temp/tropopause model
- CI/time-cost model
- delay/connection model
- ATC/manual constraints

Optimization:
- build remaining route
- generate candidate vertical profiles
- generate candidate speed/CI schedules
- simulate fuel/time for each profile
- apply constraints
- convert delay/time/connection penalties into fuel-equivalent cost
- select minimum-cost profile

Outputs:
- CI
- Mach/speed
- flight level profile
- step climb/descent points
- TOD impact
- fuel/time/cost savings
- FMC-enterable recommendation