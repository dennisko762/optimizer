"""Resolve the aircraft performance config key for an optimize request.

Why this exists
---------------
``/api/optimize`` must compute fuel/Mach from the airframe actually being
flown. Feeding, say, the A320 tables to an A350-900 produces numbers that
look plausible and are wrong — a data-integrity defect, not a cosmetic one
(AGENTS.md: physically based performance data only).

Contract pinned here (the EFB client relies on it):

- ``aircraftConfig`` is OPTIONAL on the request and MAY be sent as ``null``.
  Both an absent key and an explicit ``null`` mean "resolve it yourself"
  (Pydantic defaults only apply to an absent key, so a ``str`` field would
  422 on ``null`` — that was the live-optimizer bug).
- When no explicit key is given, the key is derived from
  ``flightState.aircraft`` (a SimConnect title or an ICAO/SimBrief type
  code) through the aircraft catalog.
- If nothing resolves, or the resolved key has no YAML profile, the request
  is REFUSED with a readable message. There is no silent fallback airframe.
"""

from __future__ import annotations

from dataclasses import dataclass

from optimizer.config_loader import aircraft_config_exists
from optimizer.configs.aircraft.aircraft_catalog import (
    get_catalog_entry,
    resolve_aircraft_from_title,
)


@dataclass(frozen=True)
class ResolvedAircraftConfig:
    config_key: str
    source: str
    """Where the key came from: ``request`` or ``aircraft:<identifier>``."""
    icao_type: str | None = None
    """Catalog ICAO/SimBrief type code, when the catalog knew the airframe."""


class AircraftConfigUnresolvedError(ValueError):
    """No usable aircraft performance profile for this request."""


def resolve_aircraft_config(
    requested_key: str | None,
    *,
    aircraft: str | None = None,
) -> ResolvedAircraftConfig:
    """Resolve the config key to load, or raise with a crew-readable reason.

    Parameters
    ----------
    requested_key:
        ``aircraftConfig`` from the request body (may be ``None``).
    aircraft:
        ``flightState.aircraft`` — a SimConnect TITLE ("Airbus A350-900
        Qatar Airways") or an ICAO/SimBrief type code ("A359").
    """

    explicit = _clean(requested_key)
    identifier = _clean(aircraft)
    entry = (
        get_catalog_entry(identifier) or resolve_aircraft_from_title(identifier)
        if identifier is not None
        else None
    )

    if explicit is not None:
        if not aircraft_config_exists(explicit):
            raise AircraftConfigUnresolvedError(
                f"Aircraft config '{explicit}' has no performance profile "
                f"(expected optimizer/configs/aircraft/{explicit}.yaml). "
                "Optimization refused rather than using another airframe's data."
            )
        return ResolvedAircraftConfig(
            config_key=explicit,
            source="request",
            icao_type=entry.simbrief_code if entry is not None else None,
        )

    if identifier is None:
        raise AircraftConfigUnresolvedError(
            "Aircraft type not resolved: the request carried neither "
            "aircraftConfig nor flightState.aircraft, so no performance "
            "profile can be selected. No default airframe is substituted."
        )

    if entry is None:
        raise AircraftConfigUnresolvedError(
            f"Aircraft type '{identifier}' is not in the aircraft catalog, so "
            "no performance profile can be selected. No default airframe is "
            "substituted."
        )

    if not aircraft_config_exists(entry.config_key):
        raise AircraftConfigUnresolvedError(
            f"Aircraft type '{identifier}' maps to config '{entry.config_key}', "
            f"but optimizer/configs/aircraft/{entry.config_key}.yaml is missing. "
            "Optimization refused rather than using another airframe's data."
        )

    return ResolvedAircraftConfig(
        config_key=entry.config_key,
        source=f"aircraft:{identifier}",
        icao_type=entry.simbrief_code,
    )


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
