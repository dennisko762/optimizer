from __future__ import annotations

import math
from dataclasses import dataclass


# ─── Thresholds ───────────────────────────────────────────────────────────────

DELAY_THRESHOLD_MINOR_MIN: float = 5.0
DELAY_THRESHOLD_SIGNIFICANT_MIN: float = 15.0
DELAY_THRESHOLD_CRITICAL_MIN: float = 30.0

RECALCULATE_TRIGGER_MIN: float = 5.0


# ─── Output model ─────────────────────────────────────────────────────────────

@dataclass
class EtaEstimate:
    """
    Full ETA and delay picture derived from live SimConnect data + SimBrief SIBT.

    This is the central piece missing from the original architecture:
    the system can now detect delay automatically and trigger recalculation
    without the pilot entering it manually.
    """

    remaining_distance_nm: float
    ground_speed_kt: float
    remaining_time_min: float

    eta_utc: str
    sibt_utc: str
    sobt_utc: str | None

    delay_min: float
    delay_status: str

    should_recalculate: bool
    recalculate_reason: str | None

    # For direct use in scenario payloads
    current_delay_min: float
    target_delay_min: float

    # For display
    delay_label: str


@dataclass
class DelayTriggerConfig:
    recalculate_threshold_min: float = RECALCULATE_TRIGGER_MIN
    target_delay_min: float = 0.0
    last_known_delay_min: float | None = None


# ─── Core computation ──────────────────────────────────────────────────────────

def compute_eta(
    *,
    remaining_distance_nm: float,
    ground_speed_kt: float,
    sibt_utc: str,
    sobt_utc: str | None = None,
    config: DelayTriggerConfig | None = None,
) -> EtaEstimate:
    """
    Derives current ETA and delay from live SimConnect data + SimBrief schedule.

    Replaces the manual entry of current_delay_min in every scenario payload.

    Parameters
    ----------
    remaining_distance_nm:
        Live from SimConnect (or calculated via great-circle to destination).
    ground_speed_kt:
        Live from SimConnect.
    sibt_utc:
        Scheduled in-block time from SimBrief OFP (e.g. "09:10").
    sobt_utc:
        Scheduled off-block time (optional, for context only).
    config:
        Thresholds controlling recalculation trigger.

    Returns
    -------
    EtaEstimate with all fields ready to inject into ScenarioInput.timing.
    """

    cfg = config or DelayTriggerConfig()

    if ground_speed_kt <= 0:
        raise ValueError(
            f"ground_speed_kt must be positive, got {ground_speed_kt:.1f} kt."
        )

    if remaining_distance_nm <= 0:
        raise ValueError(
            f"remaining_distance_nm must be positive, got {remaining_distance_nm:.1f} NM."
        )

    remaining_time_min = (remaining_distance_nm / ground_speed_kt) * 60.0

    now_min = _utc_now_minutes()
    eta_min = now_min + remaining_time_min
    sibt_min = _hhmm_to_minutes(sibt_utc)

    # Handle overnight wrap by placing the scheduled in-block time on the
    # calendar day closest to the computed ETA, not closest to "now".
    # Long-haul flights can depart before midnight and arrive the next UTC day.
    sibt_min = _unwrap_minutes(sibt_min, reference=eta_min)

    delay_min = eta_min - sibt_min
    eta_str = _minutes_to_hhmm(eta_min)

    delay_status = _classify_delay(delay_min)
    delay_label = _build_delay_label(delay_min)

    current_delay_min = max(round(delay_min, 1), 0.0)
    target_delay_min = max(cfg.target_delay_min, 0.0)

    should_recalculate, reason = _should_trigger_recalculation(
        delay_min=delay_min,
        last_known_delay_min=cfg.last_known_delay_min,
        threshold_min=cfg.recalculate_threshold_min,
    )

    return EtaEstimate(
        remaining_distance_nm=round(remaining_distance_nm, 1),
        ground_speed_kt=round(ground_speed_kt, 1),
        remaining_time_min=round(remaining_time_min, 1),
        eta_utc=eta_str,
        sibt_utc=sibt_utc,
        sobt_utc=sobt_utc,
        delay_min=round(delay_min, 1),
        delay_status=delay_status,
        should_recalculate=should_recalculate,
        recalculate_reason=reason,
        current_delay_min=current_delay_min,
        target_delay_min=target_delay_min,
        delay_label=delay_label,
    )


def compute_eta_if_possible(
    *,
    remaining_distance_nm: float | None,
    ground_speed_kt: float | None,
    sibt_utc: str | None,
    sobt_utc: str | None = None,
    config: DelayTriggerConfig | None = None,
) -> EtaEstimate | None:
    """
    Safe wrapper — returns None instead of raising if inputs are missing.
    Use in the service layer where data may be incomplete.
    """

    if remaining_distance_nm is None or remaining_distance_nm <= 0:
        return None

    if ground_speed_kt is None or ground_speed_kt <= 0:
        return None

    if sibt_utc is None:
        return None

    try:
        return compute_eta(
            remaining_distance_nm=remaining_distance_nm,
            ground_speed_kt=ground_speed_kt,
            sibt_utc=sibt_utc,
            sobt_utc=sobt_utc,
            config=config,
        )
    except (ValueError, Exception):
        return None


# ─── Trigger logic ────────────────────────────────────────────────────────────

def _should_trigger_recalculation(
    *,
    delay_min: float,
    last_known_delay_min: float | None,
    threshold_min: float,
) -> tuple[bool, str | None]:
    """
    Decides whether to prompt the pilot for a recalculation.

    Triggers when:
    1. Delay appeared for the first time (was 0, now > threshold).
    2. Delay changed by >= threshold since last calculation.
    3. Delay crossed into a worse status band.
    """

    if delay_min <= 0:
        return False, None

    if last_known_delay_min is None:
        if delay_min >= threshold_min:
            return True, (
                f"Delay of {delay_min:.0f} min detected for the first time. "
                f"Recalculation recommended."
            )
        return False, None

    change = abs(delay_min - last_known_delay_min)

    if change >= threshold_min:
        direction = "increased" if delay_min > last_known_delay_min else "decreased"
        return True, (
            f"Delay {direction} by {change:.0f} min "
            f"({last_known_delay_min:+.0f} → {delay_min:+.0f} min). "
            f"Recalculation recommended."
        )

    prev_status = _classify_delay(last_known_delay_min)
    curr_status = _classify_delay(delay_min)
    status_order = ["ON_TIME", "MINOR", "SIGNIFICANT", "CRITICAL"]

    if status_order.index(curr_status) > status_order.index(prev_status):
        return True, (
            f"Delay status escalated: {prev_status} → {curr_status} "
            f"({delay_min:+.0f} min). Recalculation recommended."
        )

    return False, None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _classify_delay(delay_min: float) -> str:
    if delay_min < DELAY_THRESHOLD_MINOR_MIN:
        return "ON_TIME"
    if delay_min < DELAY_THRESHOLD_SIGNIFICANT_MIN:
        return "MINOR"
    if delay_min < DELAY_THRESHOLD_CRITICAL_MIN:
        return "SIGNIFICANT"
    return "CRITICAL"


def _build_delay_label(delay_min: float) -> str:
    if delay_min < -0.5:
        return f"{abs(delay_min):.0f} min early"
    if delay_min < DELAY_THRESHOLD_MINOR_MIN:
        return "on time"
    return f"+{delay_min:.0f} min"


def _hhmm_to_minutes(value: str) -> float:
    """"09:10" or "0910" → 550.0 minutes since midnight."""
    text = str(value).strip().upper().replace("Z", "").replace("UTC", "").strip()

    if ":" in text:
        parts = text.split(":")
        return int(parts[0]) * 60.0 + int(parts[1])

    if len(text) == 4 and text.isdigit():
        return int(text[:2]) * 60.0 + int(text[2:])

    raise ValueError(f"Cannot parse time: {value!r}")


def _minutes_to_hhmm(minutes: float) -> str:
    total = int(round(minutes)) % (24 * 60)
    h = total // 60
    m = total % 60
    return f"{h:02d}:{m:02d}"


def _utc_now_minutes() -> float:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.hour * 60.0 + now.minute + now.second / 60.0


def _plus_minutes(hhmm: str, minutes: int) -> str:
    """Adds minutes to a HH:MM string. "19:10" + 30 → "19:40"."""
    try:
        base = _hhmm_to_minutes(hhmm)
        return _minutes_to_hhmm(base + minutes)
    except (ValueError, TypeError):
        return hhmm


def _unwrap_minutes(scheduled_min: float, reference: float) -> float:
    """
    Adjusts scheduled_min for overnight wrap so it's comparable to reference
    (current time). Handles the case where the arrival is after midnight.
    """
    diff = scheduled_min - reference

    if diff < -12 * 60:
        return scheduled_min + 24 * 60

    if diff > 12 * 60:
        return scheduled_min - 24 * 60

    return scheduled_min
