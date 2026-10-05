"""Lufthansa Virtual adapter — aggregates SimBrief OFP, vAMSYS VA,
and Open-Meteo wind data into a unified flight data model."""

from .simbrief_fetcher import SimBriefOFPFetcher
from .vamsys_client import VamsysClient
from .weather_integration import OpenMeteoWindService
from .unified_flight_data import UnifiedFlightData, build_unified_flight_data

__all__ = [
    "SimBriefOFPFetcher",
    "VamsysClient",
    "OpenMeteoWindService",
    "UnifiedFlightData",
    "build_unified_flight_data",
]
