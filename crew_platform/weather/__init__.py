"""Crew EFB live-weather domain.

SkyNexus-style NOAA GFS 0.25 deg ingest for the Qatar Crew PAD Route map:
pressure-level fields ingested as bounded NOMADS g2sub subsets, interpolated
to ISA flight-level slabs, and reduced to documented aviation hazard proxies
(turbulence, icing, jet-stream, thermal fronts) exposed as GeoJSON.

Everything here is a *derived proxy from public NOAA GFS model output* — it
is NOT official WAFS/eWAS. See ``hazards.py`` for the cited formulas and the
explicit proxy disclaimer.
"""

from __future__ import annotations

from . import levels, hazards, polygonize, sampler  # noqa: F401

__all__ = ["levels", "hazards", "polygonize", "sampler"]
