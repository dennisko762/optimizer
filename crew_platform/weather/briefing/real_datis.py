"""Real-world D-ATIS provider interface — disabled unless an authorized
provider is configured.

Task requirement: this is an INTERFACE only. No provider implementation
ships here (no scraping, no unlicensed third-party feed, and explicitly
NEVER deriving/labelling a METAR summary as ATIS). An authorized provider
is wired in later by setting ``DATIS_PROVIDER`` to a registered name and
supplying its own credentials; until then every call returns the fallback
state with label ``METAR briefing`` — never a fabricated D-ATIS value.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .models import Product, utc_now_iso

FALLBACK_LABEL = "METAR briefing"


class DatisProvider(Protocol):
    """Contract an authorized real-world D-ATIS provider must implement."""

    name: str

    def fetch(self, icao: str) -> Product:
        """Return a normalized ``real_world_datis`` Product for one ICAO."""
        ...


@dataclass(frozen=True)
class DatisConfig:
    #: registered provider name, or empty = no provider configured.
    provider_name: str = field(default_factory=lambda: os.environ.get("DATIS_PROVIDER", "").strip())

    @classmethod
    def from_env(cls) -> "DatisConfig":
        return cls()


#: registry of authorized provider factories; empty until one is approved
#: and its licence terms documented. Intentionally not populated here.
_REGISTRY: dict[str, "DatisProvider"] = {}


def register_provider(provider: DatisProvider) -> None:
    """Register an authorized D-ATIS provider (called from app startup
    wiring, never from this module, once licence/access is confirmed)."""
    _REGISTRY[provider.name] = provider


def fetch_datis(icao: str, *, config: Optional[DatisConfig] = None) -> Product:
    """Real-world D-ATIS for one ICAO, or an honest fallback.

    Never returns a ``real_world_datis`` product derived from METAR text —
    when no authorized provider is configured/available this returns a
    ``METAR briefing``-labelled unavailable Product so the caller can fall
    back to displaying the real METAR under its own correct label instead.
    """
    config = config or DatisConfig.from_env()
    icao = (icao or "").strip().upper()
    if not config.provider_name:
        return Product.unavailable(
            "real_world_datis", "none", "D-ATIS", FALLBACK_LABEL,
            "No authorized real-world D-ATIS provider is configured "
            "(DATIS_PROVIDER unset) — showing METAR briefing instead.",
        )
    provider = _REGISTRY.get(config.provider_name)
    if provider is None:
        return Product.unavailable(
            "real_world_datis", config.provider_name, "D-ATIS", FALLBACK_LABEL,
            f"D-ATIS provider {config.provider_name!r} is configured but not "
            "registered/authorized in this deployment — showing METAR briefing instead.",
        )
    try:
        product = provider.fetch(icao)
    except Exception as exc:  # noqa: BLE001 — provider failure must degrade honestly
        return Product.provider_error(
            "real_world_datis", config.provider_name, "D-ATIS",
            f"Real-world D-ATIS — {config.provider_name}", str(exc),
        )
    # enforce the correct label regardless of what the provider set.
    product.label = f"Real-world D-ATIS — {config.provider_name}"
    product.retrieved_utc = product.retrieved_utc or utc_now_iso()
    return product
