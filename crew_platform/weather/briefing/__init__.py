"""Aggregated aviation briefing layer: ATIS, METAR/TAF, SIGMET, lightning.

Server-side only — browsers never poll upstream services directly. Each
adapter in this package normalizes its upstream into :class:`models.Product`
and is fetched/cached/scheduled from :mod:`briefing_service`. Nothing here
fabricates data: an adapter that cannot reach its upstream (no credentials,
feature gate off, network failure) returns an explicit ``unavailable``/
``provider_error`` state, never a guessed value.

Product kinds: ``vatsim_atis``, ``real_world_datis``, ``metar``, ``taf``,
``sigmet``, ``sigwx``, ``winds_aloft`` (reused from the existing GFS M5-P
module, see :mod:`crew_platform.weather.service`), ``lightning``.
"""
from __future__ import annotations

from .models import Product, ProductState

__all__ = ["Product", "ProductState"]
