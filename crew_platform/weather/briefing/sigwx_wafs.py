"""SIGWX/WAFS adapter state — optional, disabled until authorized.

NOAA's WAFS/WIFS (World Area Forecast System / WAFS Internet File Service)
significant-weather charts require an authorized NOAA account; there is no
anonymous/open endpoint. This module defines the adapter's config/state
shape only — it never claims anonymous access, and :func:`fetch_sigwx`
always returns a disabled/unavailable Product unless an operator has set
both the feature gate and real WIFS credentials.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .models import Product, ProductState

SOURCE = "NOAA WAFS/WIFS"
LABEL = "SIGWX (WAFS)"


@dataclass(frozen=True)
class SigwxConfig:
    #: NOAA WIFS account credentials — there is no anonymous SIGWX/WAFS access.
    wifs_username: str = field(default_factory=lambda: os.environ.get("NOAA_WIFS_USERNAME", "").strip())
    wifs_password_set: bool = field(
        default_factory=lambda: bool(os.environ.get("NOAA_WIFS_PASSWORD", "").strip())
    )
    #: explicit feature gate — OFF by default; this adapter is not implemented
    #: against the live WIFS endpoint, only its state is modeled here.
    enabled: bool = field(default_factory=lambda: os.environ.get("SIGWX_WAFS_ENABLED", "0").strip() == "1")

    @classmethod
    def from_env(cls) -> "SigwxConfig":
        return cls()

    @property
    def has_credentials(self) -> bool:
        return bool(self.wifs_username and self.wifs_password_set)


def fetch_sigwx(*, config: Optional[SigwxConfig] = None) -> Product:
    """Always unavailable/disabled — see module docstring.

    Never returns ``ok``: this is a documented placeholder for a future,
    explicitly authorized WIFS integration, not a working adapter.
    """
    config = config or SigwxConfig.from_env()
    if not config.enabled:
        return Product.disabled(
            "sigwx", SOURCE, LABEL, LABEL,
            "SIGWX/WAFS is disabled — NOAA WIFS requires an authorized "
            "account; there is no anonymous/open access to enable here.",
        )
    if not config.has_credentials:
        return Product.unavailable(
            "sigwx", SOURCE, LABEL, LABEL,
            "SIGWX_WAFS_ENABLED=1 but NOAA_WIFS_USERNAME/NOAA_WIFS_PASSWORD "
            "are not configured — an authorized NOAA WIFS account is required.",
        )
    # Even with credentials present, no live WIFS client is implemented in
    # this layer yet — surface that honestly rather than fabricating data.
    return Product.unavailable(
        "sigwx", SOURCE, LABEL, LABEL,
        "NOAA WIFS credentials are configured but the live SIGWX/WAFS "
        "client is not yet implemented in this deployment.",
    )
