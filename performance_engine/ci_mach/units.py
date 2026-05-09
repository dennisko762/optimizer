from __future__ import annotations

FT_TO_M = 0.3048
KT_TO_MPS = 0.5144444444444445
MPS_TO_KT = 1.0 / KT_TO_MPS
LB_TO_KG = 0.45359237
KG_TO_LB = 1.0 / LB_TO_KG
G0_MPS2 = 9.80665


def ft_to_m(value_ft: float) -> float:
    return value_ft * FT_TO_M


def flight_level_to_ft(flight_level: float) -> float:
    return flight_level * 100.0


def kt_to_mps(value_kt: float) -> float:
    return value_kt * KT_TO_MPS


def mps_to_kt(value_mps: float) -> float:
    return value_mps * MPS_TO_KT


def lb_to_kg(value_lb: float) -> float:
    return value_lb * LB_TO_KG


def kg_to_lb(value_kg: float) -> float:
    return value_kg * KG_TO_LB


def kg_per_h_to_kg_per_s(value_kg_h: float) -> float:
    return value_kg_h / 3600.0


def kg_per_s_to_kg_per_h(value_kg_s: float) -> float:
    return value_kg_s * 3600.0


def lb_per_h_to_kg_per_min(value_lb_h: float) -> float:
    return lb_to_kg(value_lb_h) / 60.0
