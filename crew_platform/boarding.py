"""Boarding module — session-scoped boarding state and view model.

Pure data model + API routes for the crew boarding panel.
No proprietary data sources; all values are crew-entered or derived
from the SimBrief OFP when available.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BoardingState:
    """Session-persisted boarding state for a flight."""

    flight_id: str = ""
    pax_planned: Optional[int] = None
    pax_ate: int = 0
    bags_expected: Optional[int] = None
    bags_loaded: int = 0
    contacts: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    oew_kg: Optional[float] = None
    pax_kg_each: Optional[float] = None
    bag_kg_each: Optional[float] = None
    cargo_kg: Optional[float] = None
    fuel_kg: Optional[float] = None


def ring_percent(current: float, planned: float) -> float:
    """Compute ring percentage, clamped 0..100. Returns 0 when planned <= 0."""
    if planned <= 0:
        return 0.0
    return min(100.0, max(0.0, (current / planned) * 100.0))


def compute_weights(
    oew_kg: float = 0,
    pax_count: int = 0,
    pax_kg_each: float = 0,
    bag_count: int = 0,
    bag_kg_each: float = 0,
    cargo_kg: float = 0,
    fuel_kg: float = 0,
) -> dict:
    """Compute derived weights from editable inputs."""
    pax_kg = pax_count * pax_kg_each
    bag_kg = bag_count * bag_kg_each
    zfw_kg = oew_kg + pax_kg + bag_kg + cargo_kg
    tow_kg = zfw_kg + fuel_kg
    return {"pax_kg": pax_kg, "bag_kg": bag_kg, "zfw_kg": zfw_kg, "tow_kg": tow_kg}


def build_boarding_view_model(
    flight: Optional[dict],
    ofp: Optional[dict],
    state: BoardingState,
) -> dict:
    """Build the full boarding view model from flight + OFP + session state."""
    flight = flight or {}
    ofp = ofp or {}

    header = {
        "flight_number": flight.get("flight_number") or flight.get("callsign", ""),
        "route": _build_route(flight),
        "sibt": flight.get("sibt", ""),
        "sobt": flight.get("sobt", ""),
        "block_time": flight.get("block_time", ""),
    }

    pax_planned = state.pax_planned if state.pax_planned is not None else ofp.get("pax_count", 0)
    pax_ate = state.pax_ate
    bags_expected = state.bags_expected if state.bags_expected is not None else ofp.get("bag_count", 0)
    bags_loaded = state.bags_loaded

    pax_ring = {
        "current": pax_ate,
        "planned": pax_planned,
        "percent": ring_percent(pax_ate, pax_planned),
    }
    bags_ring = {
        "current": bags_loaded,
        "planned": bags_expected,
        "percent": ring_percent(bags_loaded, bags_expected),
    }

    oew = state.oew_kg if state.oew_kg is not None else ofp.get("oew_kg", 0)
    pax_kg_each = state.pax_kg_each if state.pax_kg_each is not None else ofp.get("pax_kg_each", 84)
    bag_kg_each = state.bag_kg_each if state.bag_kg_each is not None else ofp.get("bag_kg_each", 15)
    cargo = state.cargo_kg if state.cargo_kg is not None else ofp.get("cargo_kg", 0)
    fuel = state.fuel_kg if state.fuel_kg is not None else ofp.get("fuel_kg", 0)

    weights = compute_weights(
        oew_kg=oew,
        pax_count=pax_ate,
        pax_kg_each=pax_kg_each,
        bag_count=bags_loaded,
        bag_kg_each=bag_kg_each,
        cargo_kg=cargo,
        fuel_kg=fuel,
    )
    weights.update({"oew_kg": oew, "cargo_kg": cargo, "fuel_kg": fuel,
                    "pax_kg_each": pax_kg_each, "bag_kg_each": bag_kg_each})

    # Conflict detection
    conflicts = []
    if (
        state.pax_planned is not None
        and ofp.get("pax_count") is not None
        and state.pax_planned != ofp["pax_count"]
    ):
        conflicts.append({
            "field": "pax_planned",
            "crew_value": state.pax_planned,
            "simbrief_value": ofp["pax_count"],
        })
    if (
        state.bags_expected is not None
        and ofp.get("bag_count") is not None
        and state.bags_expected != ofp["bag_count"]
    ):
        conflicts.append({
            "field": "bags_expected",
            "crew_value": state.bags_expected,
            "simbrief_value": ofp["bag_count"],
        })

    return {
        "header": header,
        "contacts": state.contacts,
        "updates": state.updates,
        "pax_ring": pax_ring,
        "bags_ring": bags_ring,
        "groups": state.groups,
        "weights": weights,
        "conflicts": conflicts,
    }


def _build_route(flight: dict) -> str:
    if not flight:
        return ""
    dep = flight.get("departure_icao", "????")
    arr = flight.get("arrival_icao", "????")
    return f"{dep} → {arr}"
