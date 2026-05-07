from __future__ import annotations

from .airbus_mcdu_adapter import AirbusMcduAdapter


class IniBuildsA340McduAdapter(AirbusMcduAdapter):
    """
    Parser wrapper for iniBuilds A340 MCDU text lines.

    Important:
    This parser assumes you already have 14-ish MCDU text lines.
    The low-level iniBuilds export reader is aircraft/version-specific and may require
    discovering the SimConnect client-data name or another export endpoint.
    """

    def __init__(self):
        super().__init__(
            aircraft="INIBUILDS_A340",
            source="INIBUILDS_MCDU_EXPORT",
        )
