from __future__ import annotations

from datetime import datetime, timedelta

from optimizer.operational_data.connex_models import (
    ConnexConnectionStatus,
    ConnexPassengerGroupUplink,
    ConnexUplink,
    ResolvedConnexOperationalData,
)
from optimizer.scenario_engine.scenario_models import (
    ConnexGroupInput,
    ConnexScenarioInput,
    TimingScenarioInput,
)


def build_demo_connex_uplink(
    *,
    hub_airport: str,
    current_eta_utc: str,
) -> ConnexUplink:
    """
    Demo Connex uplink modeled after real ACARS/TELEX style Connex info.

    ACARS-style visible data:
      ETA
      ARR GATE
      ARR POS
      FLTNBR / DES / GATE / ETD / LTOP

    Airline/backend enrichment:
      affected_pax
      max_extra_fuel_kg_per_pax

    The pilot does not manually enter pax value, hotel risk, priority, etc.
    """

    hub = hub_airport.upper()

    if hub in {"EDDM", "MUC"}:
        return ConnexUplink(
            station="MUC",
            hub_airport="EDDM",
            generated_at_utc=_utc_now_hhmm(),
            source="DEMO_CONNEX_UPLINK",
            eta_utc=current_eta_utc,
            arrival_gate="G24",
            arrival_position="G24",
            groups=[
                ConnexPassengerGroupUplink(
                    group_id="MUC-GRP-001",
                    outbound_flight="LH2042",
                    destination="HAM",
                    gate="G18",
                    etd_utc=_plus_minutes_hhmm(current_eta_utc, 42),
                    ltop_utc=_minus_minutes_hhmm(current_eta_utc, 7),
                    affected_pax=6,
                    max_extra_fuel_kg_per_pax=22.0,
                    cabin_class="Y",
                    value_tier="STANDARD",
                    last_connection_of_day=False,
                ),
                ConnexPassengerGroupUplink(
                    group_id="MUC-GRP-002",
                    outbound_flight="LH2484",
                    destination="LHR",
                    gate="H44",
                    etd_utc=_plus_minutes_hhmm(current_eta_utc, 58),
                    ltop_utc=_minus_minutes_hhmm(current_eta_utc, 11),
                    affected_pax=2,
                    max_extra_fuel_kg_per_pax=55.0,
                    cabin_class="C",
                    value_tier="HIGH_VALUE",
                    last_connection_of_day=True,
                ),
            ],
            notes=[
                "Demo Connex uplink for inbound Munich hub bank.",
            ],
        )

    return ConnexUplink(
        station="FRA",
        hub_airport="EDDF",
        generated_at_utc=_utc_now_hhmm(),
        source="DEMO_CONNEX_UPLINK",
        eta_utc=current_eta_utc,
        arrival_gate="A13",
        arrival_position="A13",
        groups=[
            ConnexPassengerGroupUplink(
                group_id="FRA-GRP-001",
                outbound_flight="LH704",
                destination="MLE",
                gate="Z50",
                etd_utc=_plus_minutes_hhmm(current_eta_utc, 71),
                ltop_utc=_plus_minutes_hhmm(current_eta_utc, 30),
                affected_pax=4,
                max_extra_fuel_kg_per_pax=35.0,
                cabin_class="Y",
                value_tier="STANDARD",
                last_connection_of_day=False,
            ),
            ConnexPassengerGroupUplink(
                group_id="FRA-GRP-002",
                outbound_flight="LH760",
                destination="DEL",
                gate="B26",
                etd_utc=_plus_minutes_hhmm(current_eta_utc, 51),
                ltop_utc=_minus_minutes_hhmm(current_eta_utc, 7),
                affected_pax=3,
                max_extra_fuel_kg_per_pax=45.0,
                cabin_class="C",
                value_tier="HIGH_VALUE",
                last_connection_of_day=False,
            ),
            ConnexPassengerGroupUplink(
                group_id="FRA-GRP-003",
                outbound_flight="LH572",
                destination="JNB",
                gate="Z62",
                etd_utc=_plus_minutes_hhmm(current_eta_utc, 64),
                ltop_utc=_minus_minutes_hhmm(current_eta_utc, 12),
                affected_pax=1,
                max_extra_fuel_kg_per_pax=90.0,
                cabin_class="F",
                value_tier="VERY_HIGH_VALUE",
                last_connection_of_day=True,
            ),
        ],
        notes=[
            "Demo Connex uplink for inbound Frankfurt hub bank.",
        ],
    )


def resolve_connex_uplink(
    *,
    uplink: ConnexUplink,
    current_eta_utc: str | None = None,
) -> tuple[ConnexScenarioInput, TimingScenarioInput, ResolvedConnexOperationalData]:
    """
    Converts raw Connex uplink into scenario-engine inputs.

    Input:
      ACARS/TELEX-style Connex message + backend enrichment.

    Output:
      ConnexScenarioInput
      TimingScenarioInput
      ResolvedConnexOperationalData for UI/debug.

    Core logic:
      margin_to_ltop = LTOP - ETA

      positive margin:
        connection protected

      negative margin:
        recovery required
    """

    if not uplink.groups:
        raise ValueError("Connex uplink contains no passenger groups.")

    eta_utc = current_eta_utc or uplink.eta_utc

    if eta_utc is None:
        raise ValueError("Connex uplink resolution requires current_eta_utc or uplink.eta_utc.")

    total_pax = 0
    total_budget = 0.0
    scenario_groups: list[ConnexGroupInput] = []
    connection_statuses: list[ConnexConnectionStatus] = []
    explanation: list[str] = []

    hotel_risk = False
    last_connection_of_day = False
    longhaul_connection = False
    group_booking = False
    passenger_compensation_risk = False

    most_restrictive_ltop: str | None = None
    worst_required_recovery_min = 0.0
    worst_margin_min: float | None = None

    for group in uplink.groups:
        if group.affected_pax <= 0:
            continue

        fuel_budget_kg = group.affected_pax * group.max_extra_fuel_kg_per_pax
        margin_to_ltop_min = _calculate_margin_to_ltop_min(
            current_eta_utc=eta_utc,
            ltop_utc=group.ltop_utc,
        )
        required_recovery_min = max(-margin_to_ltop_min, 0.0)

        protected = margin_to_ltop_min >= 0
        at_risk = required_recovery_min > 0

        group_hotel_risk = _derive_hotel_risk(
            current_eta_utc=eta_utc,
            ltop_utc=group.ltop_utc,
            last_connection_of_day=group.last_connection_of_day,
            next_viable_connection_utc=group.next_viable_connection_utc,
        )

        group_longhaul = _is_longhaul_destination(group.destination)
        group_booking_flag = group.affected_pax >= 8
        group_compensation_risk = _derive_passenger_compensation_risk(
            current_eta_utc=eta_utc,
            ltop_utc=group.ltop_utc,
        )

        total_pax += group.affected_pax
        total_budget += fuel_budget_kg

        hotel_risk = hotel_risk or group_hotel_risk
        last_connection_of_day = last_connection_of_day or group.last_connection_of_day
        longhaul_connection = longhaul_connection or group_longhaul
        group_booking = group_booking or group_booking_flag
        passenger_compensation_risk = passenger_compensation_risk or group_compensation_risk

        if required_recovery_min > worst_required_recovery_min:
            worst_required_recovery_min = required_recovery_min
            most_restrictive_ltop = group.ltop_utc
            worst_margin_min = margin_to_ltop_min

        if most_restrictive_ltop is None:
            most_restrictive_ltop = group.ltop_utc
            worst_margin_min = margin_to_ltop_min

        scenario_groups.append(
            ConnexGroupInput(
                affected_pax=group.affected_pax,
                destination=group.destination,
                outbound_flight=group.outbound_flight,
                latest_transfer_arrival_utc=group.ltop_utc,
                acceptable_extra_fuel_kg_per_pax=group.max_extra_fuel_kg_per_pax,
                last_connection_of_day=group.last_connection_of_day,
                hotel_risk=group_hotel_risk,
                longhaul_connection=group_longhaul,
                group_booking=group_booking_flag,
                passenger_compensation_risk=group_compensation_risk,
            )
        )

        connection_statuses.append(
            ConnexConnectionStatus(
                group_id=group.group_id,
                outbound_flight=group.outbound_flight,
                destination=group.destination,
                gate=group.gate,
                etd_utc=group.etd_utc,
                ltop_utc=group.ltop_utc,
                affected_pax=group.affected_pax,
                max_extra_fuel_kg_per_pax=group.max_extra_fuel_kg_per_pax,
                fuel_budget_kg=round(fuel_budget_kg, 2),
                margin_to_ltop_min=round(margin_to_ltop_min, 2),
                required_recovery_min=round(required_recovery_min, 2),
                protected=protected,
                at_risk=at_risk,
                not_recoverable=False,
                hotel_risk=group_hotel_risk,
                last_connection_of_day=group.last_connection_of_day,
                longhaul_connection=group_longhaul,
                passenger_compensation_risk=group_compensation_risk,
            )
        )

        if protected:
            explanation.append(
                f"{group.outbound_flight} to {group.destination}: protected, "
                f"LTOP {group.ltop_utc}, ETA {eta_utc}, margin +{margin_to_ltop_min:.1f} min, "
                f"budget {fuel_budget_kg:.0f} kg."
            )
        else:
            explanation.append(
                f"{group.outbound_flight} to {group.destination}: at risk, "
                f"LTOP {group.ltop_utc}, ETA {eta_utc}, needs {required_recovery_min:.1f} min recovery, "
                f"budget {fuel_budget_kg:.0f} kg."
            )

    if total_pax <= 0:
        raise ValueError("Connex uplink contains no valid affected passengers.")

    weighted_avg_kg_per_pax = total_budget / total_pax if total_pax > 0 else None
    connex_protected = worst_required_recovery_min <= 0
    connex_at_risk = worst_required_recovery_min > 0

    explanation.append(
        f"Total Connex budget: {total_budget:.0f} kg for {total_pax} pax."
    )

    if connex_protected:
        explanation.append(
            "All Connex groups are currently protected. No Connex-driven speed-up is required."
        )
    else:
        explanation.append(
            f"Most restrictive LTOP: {most_restrictive_ltop}; "
            f"required recovery: {worst_required_recovery_min:.1f} min."
        )

    if hotel_risk:
        explanation.append("Hotel risk derived automatically from LTOP / late arrival / last-connection data.")

    if last_connection_of_day:
        explanation.append("At least one group is on the last connection of the day.")

    connex = ConnexScenarioInput(
        affected_pax=total_pax,
        acceptable_extra_fuel_kg_per_pax=weighted_avg_kg_per_pax,
        connex_fuel_budget_kg_override=total_budget,
        connection_groups=scenario_groups,
        hub_airport=uplink.hub_airport,
        hotel_risk=hotel_risk,
        last_connection_of_day=last_connection_of_day,
        longhaul_connection=longhaul_connection,
        group_booking=group_booking,
        passenger_compensation_risk=passenger_compensation_risk,
        passenger_soft_cost_relevant=True,
    )

    timing = TimingScenarioInput(
        current_eta_utc=eta_utc,
        target_on_block_utc=most_restrictive_ltop,
        required_time_recovery_min=round(worst_required_recovery_min, 2),
        current_delay_min=round(worst_required_recovery_min, 2),
        target_delay_min=0.0,
    )

    resolved = ResolvedConnexOperationalData(
        station=uplink.station,
        hub_airport=uplink.hub_airport,
        eta_utc=eta_utc,
        arrival_gate=uplink.arrival_gate,
        arrival_position=uplink.arrival_position,
        affected_pax_total=total_pax,
        total_fuel_budget_kg=round(total_budget, 2),
        weighted_avg_extra_fuel_kg_per_pax=(
            round(weighted_avg_kg_per_pax, 2)
            if weighted_avg_kg_per_pax is not None
            else None
        ),
        required_time_recovery_min=round(worst_required_recovery_min, 2),
        most_restrictive_ltop_utc=most_restrictive_ltop,
        margin_to_most_restrictive_ltop_min=(
            round(worst_margin_min, 2)
            if worst_margin_min is not None
            else None
        ),
        connex_protected=connex_protected,
        connex_at_risk=connex_at_risk,
        hotel_risk=hotel_risk,
        last_connection_of_day=last_connection_of_day,
        longhaul_connection=longhaul_connection,
        group_booking=group_booking,
        passenger_compensation_risk=passenger_compensation_risk,
        connections=connection_statuses,
        explanation=explanation,
    )

    return connex, timing, resolved


def _calculate_margin_to_ltop_min(
    *,
    current_eta_utc: str,
    ltop_utc: str,
) -> float:
    """
    Returns:
      positive = ETA before LTOP, connection protected
      negative = ETA after LTOP, recovery required
    """

    eta = _parse_hhmm_today(current_eta_utc)
    ltop = _parse_hhmm_today(ltop_utc)

    if eta < ltop and (ltop - eta) > timedelta(hours=12):
        eta += timedelta(days=1)

    if ltop < eta and (eta - ltop) > timedelta(hours=12):
        ltop += timedelta(days=1)

    return (ltop - eta).total_seconds() / 60.0


def _derive_hotel_risk(
    *,
    current_eta_utc: str,
    ltop_utc: str,
    last_connection_of_day: bool,
    next_viable_connection_utc: str | None,
) -> bool:
    margin = _calculate_margin_to_ltop_min(
        current_eta_utc=current_eta_utc,
        ltop_utc=ltop_utc,
    )

    if margin >= 0:
        return False

    if last_connection_of_day:
        return True

    if next_viable_connection_utc is None:
        hour = int(ltop_utc.strip().upper().replace("Z", "").split(":")[0])
        return hour >= 21 or hour <= 3

    ltop = _parse_hhmm_today(ltop_utc)
    next_conn = _parse_hhmm_today(next_viable_connection_utc)

    if next_conn < ltop:
        next_conn += timedelta(days=1)

    return (next_conn - ltop) >= timedelta(hours=6)


def _derive_passenger_compensation_risk(
    *,
    current_eta_utc: str,
    ltop_utc: str,
) -> bool:
    margin = _calculate_margin_to_ltop_min(
        current_eta_utc=current_eta_utc,
        ltop_utc=ltop_utc,
    )

    required_recovery = max(-margin, 0.0)

    return required_recovery >= 30


def _is_longhaul_destination(destination: str | None) -> bool:
    if not destination:
        return False

    # MVP heuristic.
    # Later replace by airport distance / connection schedule.
    longhaul_destinations = {
        "JFK",
        "EWR",
        "ORD",
        "IAD",
        "LAX",
        "SFO",
        "BOS",
        "YYZ",
        "YVR",
        "YUL",
        "DEL",
        "BOM",
        "BLR",
        "MLE",
        "SIN",
        "HKG",
        "NRT",
        "HND",
        "ICN",
        "PEK",
        "PVG",
        "JNB",
        "CPT",
        "GRU",
        "EZE",
        "MEX",
    }

    return destination.upper() in longhaul_destinations


def _parse_hhmm_today(value: str) -> datetime:
    cleaned = value.strip().upper().replace("Z", "")

    if len(cleaned) == 4 and ":" not in cleaned:
        cleaned = f"{cleaned[:2]}:{cleaned[2:]}"

    try:
        hour_str, minute_str = cleaned.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
    except Exception as exc:
        raise ValueError(f"Invalid HH:MM time value: {value}") from exc

    now = datetime.utcnow()

    return now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    )


def _plus_minutes_hhmm(value: str, minutes: int) -> str:
    base = _parse_hhmm_today(value)
    result = base + timedelta(minutes=minutes)
    return result.strftime("%H:%M")


def _minus_minutes_hhmm(value: str, minutes: int) -> str:
    base = _parse_hhmm_today(value)
    result = base - timedelta(minutes=minutes)
    return result.strftime("%H:%M")


def _utc_now_hhmm() -> str:
    return datetime.utcnow().strftime("%H:%M")