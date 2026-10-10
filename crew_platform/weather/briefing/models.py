"""Normalized briefing product model shared by every adapter.

Task requirement: every product carries source/product, issued/valid/
retrieved UTC timestamps, explicit stale/expired state, raw data where
licensing allows, and station/geometry — and loading/unavailable/
provider-error/stale-last-good states must be distinguishable, never
fabricated.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ProductState(str, Enum):
    """Explicit lifecycle state — the UI must branch on this, not guess."""

    LOADING = "loading"
    OK = "ok"
    STALE = "stale"  # last-good value served past its normal refresh window
    UNAVAILABLE = "unavailable"  # feature-gated off / not configured / no data
    PROVIDER_ERROR = "provider_error"  # upstream reachable but errored/malformed
    DISABLED = "disabled"  # explicit feature gate off (licence/consent not confirmed)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_utc_iso(value: Any) -> Optional[str]:
    """Best-effort normalize a timestamp to ISO-8601 UTC ``...Z``.

    Returns ``None`` (never fabricated) when the input cannot be parsed.
    Accepts datetime objects, unix seconds, and common text formats
    (including the trailing 'Z' forms used by AviationWeather.gov).
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, (int, float)):
        try:
            return _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            dt = _dt.datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
    return None


@dataclass
class Product:
    """One normalized briefing product instance.

    ``kind`` is the normalized product kind (see package docstring).
    ``label`` is the exact user-facing provenance string — the three ATIS/
    briefing variants (``VATSIM ATIS``, ``Real-world D-ATIS — <provider>``,
    ``METAR briefing``) must never be conflated, so callers set this
    explicitly rather than deriving it from ``source``.
    """

    kind: str
    source: str
    product: str
    label: str
    state: ProductState
    issued_utc: Optional[str] = None
    valid_from_utc: Optional[str] = None
    valid_until_utc: Optional[str] = None
    retrieved_utc: Optional[str] = None
    stale: bool = False
    station: Optional[str] = None
    geometry: Optional[dict[str, Any]] = None
    raw: Optional[str] = None
    detail: Optional[str] = None  # human-readable reason for non-OK states
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "product": self.product,
            "label": self.label,
            "state": self.state.value,
            "issued_utc": self.issued_utc,
            "valid_from_utc": self.valid_from_utc,
            "valid_until_utc": self.valid_until_utc,
            "retrieved_utc": self.retrieved_utc,
            "stale": self.stale,
            "station": self.station,
            "geometry": self.geometry,
            "raw": self.raw,
            "detail": self.detail,
            **self.extra,
        }

    @classmethod
    def unavailable(cls, kind: str, source: str, product: str, label: str, detail: str) -> "Product":
        return cls(
            kind=kind, source=source, product=product, label=label,
            state=ProductState.UNAVAILABLE, detail=detail,
            retrieved_utc=utc_now_iso(),
        )

    @classmethod
    def disabled(cls, kind: str, source: str, product: str, label: str, detail: str) -> "Product":
        return cls(
            kind=kind, source=source, product=product, label=label,
            state=ProductState.DISABLED, detail=detail,
            retrieved_utc=utc_now_iso(),
        )

    @classmethod
    def provider_error(cls, kind: str, source: str, product: str, label: str, detail: str) -> "Product":
        return cls(
            kind=kind, source=source, product=product, label=label,
            state=ProductState.PROVIDER_ERROR, detail=detail,
            retrieved_utc=utc_now_iso(),
        )
