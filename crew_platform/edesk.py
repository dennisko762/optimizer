"""eDesk — flight selection, OFP pairing, and local check-in validation.

The eDesk handles:
- Display of pilot identity and crew ID
- Flight selection from the pilot's roster
- SimBrief OFP pairing with the selected flight
- Local check-in validation (airline, callsign, route, date)

Remote check-in uses the documented vAMSYS Pilot API v3 write path
(POST /dispatch-url, Phoenix dispatch) — see the dispatch endpoint in
crew_platform.routes. Local validation always happens here first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from crew_platform.providers import ProviderProfile
from crew_platform.vamsys_pilot_auth import PilotFlight, PilotIdentity


# ---------------------------------------------------------------------------
# Check-in validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckInValidationResult:
    """Result of local check-in validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CheckInRecord:
    """A local check-in record with timestamps."""

    flight_id: str
    flight_number: Optional[str]
    departure_icao: Optional[str]
    arrival_icao: Optional[str]
    aircraft_icao: Optional[str]
    callsign: Optional[str]
    pilot_id: str
    provider_id: str
    checked_in_at_utc: str
    validation: CheckInValidationResult
    # Remote check-in uses the documented v3 write path (POST /dispatch-url)
    remote_checkin_status: str = "dispatch_url"
    remote_checkin_reason: str = (
        "Remote check-in uses the documented vAMSYS Pilot API v3 write path "
        "(POST /dispatch-url, Phoenix dispatch). The pilot opens the returned "
        "URL to complete the dispatch form. Local validation still happens "
        "here in eDesk; the 'Remote Check-In (Phoenix)' button triggers the "
        "dispatch flow for authenticated vAMSYS sessions."
    )


def validate_checkin(
    *,
    flight: PilotFlight,
    pilot: PilotIdentity,
    provider: ProviderProfile,
    simbrief_departure: Optional[str] = None,
    simbrief_arrival: Optional[str] = None,
    simbrief_callsign: Optional[str] = None,
    simbrief_aircraft: Optional[str] = None,
    simbrief_flight_date: Optional[str] = None,
) -> CheckInValidationResult:
    """Validate a flight for local check-in.

    Checks:
    - Airline ICAO matches provider
    - Callsign prefix matches provider
    - Route matches SimBrief if available
    - Flight date is current or future
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Airline validation
    if pilot.airline_icao and pilot.airline_icao != provider.icao:
        errors.append(
            f"Pilot airline ({pilot.airline_icao}) does not match "
            f"selected provider ({provider.icao} / {provider.display_name})"
        )

    # 2. Callsign prefix validation
    if flight.callsign and provider.callsign_prefix:
        if not flight.callsign.upper().startswith(provider.callsign_prefix.upper()):
            errors.append(
                f"Flight callsign ({flight.callsign}) does not start with "
                f"expected prefix ({provider.callsign_prefix})"
            )

    # 3. Route validation against SimBrief
    if simbrief_departure and flight.departure_icao:
        if simbrief_departure.upper() != flight.departure_icao.upper():
            errors.append(
                f"SimBrief departure ({simbrief_departure}) does not match "
                f"flight departure ({flight.departure_icao})"
            )

    if simbrief_arrival and flight.arrival_icao:
        if simbrief_arrival.upper() != flight.arrival_icao.upper():
            errors.append(
                f"SimBrief arrival ({simbrief_arrival}) does not match "
                f"flight arrival ({flight.arrival_icao})"
            )

    # 4. Callsign cross-check
    if simbrief_callsign and flight.callsign:
        sb_clean = re.sub(r"\s+", "", simbrief_callsign.upper())
        fl_clean = re.sub(r"\s+", "", flight.callsign.upper())
        if sb_clean != fl_clean:
            warnings.append(
                f"SimBrief callsign ({simbrief_callsign}) differs from "
                f"flight callsign ({flight.callsign})"
            )

    # 5. Aircraft cross-check
    if simbrief_aircraft and flight.aircraft_icao:
        if simbrief_aircraft.upper() != flight.aircraft_icao.upper():
            warnings.append(
                f"SimBrief aircraft ({simbrief_aircraft}) differs from "
                f"flight aircraft ({flight.aircraft_icao})"
            )

    # 6. Flight date validation
    if simbrief_flight_date:
        try:
            flight_dt = datetime.fromisoformat(simbrief_flight_date)
            now = datetime.now(timezone.utc)
            if flight_dt.date() < now.date():
                errors.append(
                    f"Flight date ({simbrief_flight_date}) is in the past"
                )
        except ValueError:
            warnings.append(
                f"Could not parse flight date: {simbrief_flight_date}"
            )

    return CheckInValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def create_checkin(
    *,
    flight: PilotFlight,
    pilot: PilotIdentity,
    provider: ProviderProfile,
    validation: CheckInValidationResult,
) -> CheckInRecord:
    """Create a local check-in record.

    Requires a passing validation result. Does NOT perform any remote
    check-in — that functionality is explicitly unsupported.
    """
    if not validation.valid:
        raise ValueError(
            "Cannot check in with validation errors: "
            + "; ".join(validation.errors)
        )

    return CheckInRecord(
        flight_id=flight.flight_id,
        flight_number=flight.flight_number,
        departure_icao=flight.departure_icao,
        arrival_icao=flight.arrival_icao,
        aircraft_icao=flight.aircraft_icao,
        callsign=flight.callsign,
        pilot_id=pilot.pilot_id,
        provider_id=provider.id,
        checked_in_at_utc=datetime.now(timezone.utc).isoformat(),
        validation=validation,
    )
