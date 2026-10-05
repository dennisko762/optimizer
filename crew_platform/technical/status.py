"""Derivation of an aircraft's ``current_technical_status``.

The status is **derived** from the aircraft's live (not closed) defect set —
it is never set by hand.  This module is the single source of that rule and
the seam where the later Dispatchability Engine plugs in: it exposes a small
pure function, :func:`derive_status`, that the routes call to keep
``aircraft.current_technical_status`` current on every defect state change
and after maintenance rectification.

Precedence (highest wins — the first matching rule applies):

1. Any defect with status ``OPEN``                → ``OPEN_DEFECTS``
2. Any defect with status ``DEFERRED`` or
   ``MEL_APPLIED``                                 → ``DISPATCHABLE_WITH_MEL``
3. Any defect with status ``UNDER_REVIEW``        → ``UNDER_REVIEW``
4. Otherwise                                       → ``SERVICEABLE``

Notes
-----
- Order of precedence is exactly the order listed above: OPEN beats
  DEFERRED/MEL_APPLIED, which beat UNDER_REVIEW, which beats "no live
  defects".
- ``RECTIFIED`` and ``CLOSED`` defects are terminal and never influence the
  derived status (an aircraft with only closed defects is ``SERVICEABLE``).
- MEL/CDL as a standalone domain (its own tables, dispatchability rules) is
  later scope — do NOT add ``mel_items`` tables or dispatchability logic here.
"""

from __future__ import annotations

from typing import Iterable, Union

# Derived technical status values.
TECHNICAL_STATUS_VALUES = (
    "OPEN_DEFECTS",
    "DISPATCHABLE_WITH_MEL",
    "UNDER_REVIEW",
    "SERVICEABLE",
)

# Defect statuses that indicate the aircraft can still fly with the MEL in
# effect.
_MEL_ACTIVE_STATUSES = ("DEFERRED", "MEL_APPLIED")

# Defect statuses that are live (non-terminal).
_LIVE_STATUSES = ("OPEN", "UNDER_REVIEW", "DEFERRED", "MEL_APPLIED")


def _defect_status(item: "object") -> str:
    """Normalise a defect object or raw status string to an upper-case string."""
    raw = getattr(item, "status", item)
    return (raw or "").upper()


def derive_status(defects: "Iterable[object]") -> str:
    """Derive an aircraft's technical status from its live defect set.

    Parameters
    ----------
    defects:
        Iterable of :class:`~crew_platform.technical.models.Defect` objects
        (anything with a ``status`` attribute) or plain status strings.
        Terminal defects (``RECTIFIED`` / ``CLOSED``) may be present or
        absent — they never affect the result.

    Returns
    -------
    str
        One of :data:`TECHNICAL_STATUS_VALUES`, per the precedence documented
        in the module docstring.
    """
    statuses = [_defect_status(d) for d in defects]
    if "OPEN" in statuses:
        return "OPEN_DEFECTS"
    if any(s in _MEL_ACTIVE_STATUSES for s in statuses):
        return "DISPATCHABLE_WITH_MEL"
    if "UNDER_REVIEW" in statuses:
        return "UNDER_REVIEW"
    return "SERVICEABLE"


def live_defects(defects: "Iterable[object]") -> list:
    """Return the subset of defects that are live (non-terminal).

    Used by the status endpoint to report ``open_defects`` — i.e. defects
    that still affect the aircraft's technical status.
    """
    return [
        d for d in defects if _defect_status(d) in _LIVE_STATUSES
    ]
