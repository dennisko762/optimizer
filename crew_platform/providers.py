"""Airline provider registry and theme definitions.

Each provider profile controls:
- airline display name and short code
- primary, accent, background, surface and text colors
- terminology and capability flags

No flight or optimization logic may branch on CSS/theme values.
Provider capability flags control features.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ThemeColors:
    """CSS color palette for an airline theme."""

    primary: str
    accent: str
    background: str
    surface: str
    text: str
    text_secondary: str = "#a0a0a0"


@dataclass(frozen=True)
class ProviderProfile:
    """An airline provider profile.

    Controls visual branding and capability flags.
    Logo and background image paths are user-supplied/licensed assets,
    not bundled in the repository.
    """

    id: str
    display_name: str
    short_code: str
    icao: str
    theme: ThemeColors
    callsign_prefix: Optional[str] = None
    logo_asset_path: Optional[str] = None
    background_image_path: Optional[str] = None
    # Capability flags — control feature visibility
    supports_vamsys: bool = True
    supports_simbrief: bool = True
    supports_live_optimizer: bool = True
    supports_remote_checkin: bool = False  # No remote write endpoints available
    terminology: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Built-in airline profiles
# ---------------------------------------------------------------------------

LH_VIRTUAL = ProviderProfile(
    id="lhvirtual",
    display_name="Lufthansa Virtual",
    short_code="LHV",
    icao="DLH",
    callsign_prefix="DLH",
    theme=ThemeColors(
        primary="#003366",
        accent="#FFCC00",
        background="#0a0e14",
        surface="#111820",
        text="#e8ecf0",
    ),
    terminology={"crew_id": "Crew ID", "flight_deck": "Cockpit"},
)

EMIRATES_VIRTUAL = ProviderProfile(
    id="emiratesvirtual",
    display_name="Emirates Virtual",
    short_code="EKV",
    icao="UAE",
    callsign_prefix="UAE",
    theme=ThemeColors(
        primary="#D71920",
        accent="#C4A747",
        background="#0b0c10",
        surface="#141619",
        text="#f0ece8",
    ),
    terminology={"crew_id": "Staff ID", "flight_deck": "Flight Deck"},
)

ETIHAD_VIRTUAL = ProviderProfile(
    id="etihadvirtual",
    display_name="Etihad Virtual",
    short_code="ETD",
    icao="ETD",
    callsign_prefix="ETD",
    theme=ThemeColors(
        primary="#BD8B2E",
        accent="#1C3A5F",
        background="#0c0d11",
        surface="#131520",
        text="#eae8e0",
    ),
    terminology={"crew_id": "Crew Number", "flight_deck": "Flight Deck"},
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, ProviderProfile] = {
    p.id: p for p in [LH_VIRTUAL, EMIRATES_VIRTUAL, ETIHAD_VIRTUAL]
}


def get_provider(provider_id: str) -> ProviderProfile:
    """Return the provider profile for the given id.

    Raises KeyError if the provider is not registered.
    """
    try:
        return _PROVIDERS[provider_id]
    except KeyError:
        raise KeyError(
            f"Unknown provider '{provider_id}'. "
            f"Available: {', '.join(sorted(_PROVIDERS))}"
        )


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles."""
    return list(_PROVIDERS.values())


def provider_theme_css_vars(provider: ProviderProfile) -> dict[str, str]:
    """Return a dict of CSS variable name -> value for the provider theme."""
    t = provider.theme
    return {
        "--airline-primary": t.primary,
        "--airline-accent": t.accent,
        "--airline-bg": t.background,
        "--airline-surface": t.surface,
        "--airline-text": t.text,
        "--airline-text-secondary": t.text_secondary,
    }
