from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from optimizer.number_utils import parse_number


# ─── IROPs: Non-linear connection cost model ──────────────────────────────────

@dataclass(frozen=True)
class IropsConnectionGroup:
    """
    One passenger connection group as a discrete cost threshold (step).

    Represents the jump in the IROPs cost function when this group misses
    their connecting flight.

    ltop_offset_min:
        Gate arrival time (minutes vs. SIBT = 0) at which this group misses
        their connection. Derived from:
            current_delay_min + margin_to_ltop_min

        Positive: the LTOP lies in the future relative to SIBT.
        Negative: the LTOP has already passed (connection already missed at
                  current_delay_min).

    Cost structure per group:
        step_cost_eur = affected_pax × (rebooking + care + hotel + compensation)

    The step is applied when gate_delay_min > ltop_offset_min.
    """

    label: str
    ltop_offset_min: float
    affected_pax: int

    rebooking_cost_eur_per_pax: float = 200.0
    care_cost_eur_per_pax: float = 60.0
    hotel_cost_eur_per_pax: float = 0.0
    compensation_eur_per_pax: float = 0.0

    longhaul: bool = False

    @property
    def step_cost_eur(self) -> float:
        """Total cost incurred when this group misses their connection."""
        return self.affected_pax * (
            self.rebooking_cost_eur_per_pax
            + self.care_cost_eur_per_pax
            + self.hotel_cost_eur_per_pax
            + self.compensation_eur_per_pax
        )


def irops_step_cost(
    *,
    gate_delay_min: float,
    connection_groups: Sequence[IropsConnectionGroup],
    soft_cost_eur_per_pax_per_min: float = 0.5,
    total_pax: int = 0,
    reg261_threshold_min: float = 180.0,
    reg261_eur_per_pax: float = 0.0,
    reg261_pax: int = 0,
) -> float:
    """
    Deterministic IROPs cost function for a given gate delay.

    Structure (three layers):

    1. Soft costs — small, approximately linear.
       Passenger dissatisfaction, minor service recovery.
       Negligible for delays < 30 min.

    2. Step costs — discrete jumps at each connection LTOP threshold.
       Cost = sum of step_cost_eur for all groups with gate_delay > their LTOP.
       This is the key non-linearity.

    3. EC Regulation 261/2004 — large step at the compensation threshold.
       Typically +3h at final destination for medium haul (€400/pax).

    Parameters
    ----------
    gate_delay_min:
        Estimated gate arrival relative to SIBT (0 = on time, positive = late).
    connection_groups:
        Per-connection threshold definitions. See IropsConnectionGroup.
    total_pax:
        Total passengers on board (for soft cost). Use 0 if unknown.
    reg261_threshold_min:
        Gate delay (vs SIBT) at which Regulation 261 compensation triggers.
        Default 180 min (3h). Set to a large value to disable.
    reg261_eur_per_pax:
        Compensation amount per affected passenger.
        €250 (≤1500 km), €400 (1500–3500 km), €600 (>3500 km).
        Set to 0 to disable.
    reg261_pax:
        Number of passengers eligible for Reg 261 compensation.
    """

    t = gate_delay_min

    soft = max(t, 0.0) * soft_cost_eur_per_pax_per_min * total_pax

    steps = sum(
        group.step_cost_eur
        for group in connection_groups
        if t > group.ltop_offset_min
    )

    reg261 = (reg261_eur_per_pax * reg261_pax) if (
        reg261_eur_per_pax > 0
        and reg261_pax > 0
        and t > reg261_threshold_min
    ) else 0.0

    return soft + steps + reg261


def expected_irops_cost(
    *,
    mean_delay_min: float,
    sigma_min: float,
    connection_groups: Sequence[IropsConnectionGroup],
    soft_cost_eur_per_pax_per_min: float = 0.5,
    total_pax: int = 0,
    reg261_threshold_min: float = 180.0,
    reg261_eur_per_pax: float = 0.0,
    reg261_pax: int = 0,
    n_integration_steps: int = 400,
) -> float:
    """
    E[IROPs cost] integrated over the arrival time distribution.

    Because the IROPs function is non-linear (step-wise), Jensen's inequality
    applies:

        E[f(T)] ≠ f(E[T])

    Evaluating the cost at the mean arrival is WRONG when the mean sits near
    a threshold. The correct value integrates over the distribution.

    Example
    -------
    mean_delay = +9 min, sigma = 5 min, LTOP threshold at +10 min.

    Deterministic evaluation:
        9 < 10 → connection not missed → IROPs step cost = 0

    Expected value:
        P(T > 10) ≈ 42%
        E[cost] includes 0.42 × step_cost_eur for that group.

    This is the correction the paper implements via distribution convolution.
    Here we use numerical integration (trapezoidal rule) over a ±4σ window.

    Parameters
    ----------
    mean_delay_min:
        Expected gate arrival offset in minutes vs SIBT.
    sigma_min:
        Standard deviation of gate arrival time.
        Captures holding, sequencing/merging, taxi-in uncertainty.
        Typical European value: 4–8 min.
        Set to 0 for deterministic fallback.
    n_integration_steps:
        Number of integration steps. 400 gives <0.1% error.
    """

    if sigma_min < 0.1 or not connection_groups:
        return irops_step_cost(
            gate_delay_min=mean_delay_min,
            connection_groups=connection_groups,
            soft_cost_eur_per_pax_per_min=soft_cost_eur_per_pax_per_min,
            total_pax=total_pax,
            reg261_threshold_min=reg261_threshold_min,
            reg261_eur_per_pax=reg261_eur_per_pax,
            reg261_pax=reg261_pax,
        )

    t_lo = mean_delay_min - 4.0 * sigma_min
    t_hi = mean_delay_min + 4.0 * sigma_min
    dt = (t_hi - t_lo) / n_integration_steps

    total = 0.0
    for i in range(n_integration_steps + 1):
        t = t_lo + i * dt
        weight = 2.0 if 0 < i < n_integration_steps else 1.0
        total += (
            irops_step_cost(
                gate_delay_min=t,
                connection_groups=connection_groups,
                soft_cost_eur_per_pax_per_min=soft_cost_eur_per_pax_per_min,
                total_pax=total_pax,
                reg261_threshold_min=reg261_threshold_min,
                reg261_eur_per_pax=reg261_eur_per_pax,
                reg261_pax=reg261_pax,
            )
            * _normal_pdf(t, mean_delay_min, sigma_min)
            * weight
        )

    return total * dt / 2.0


def _normal_pdf(x: float, mu: float, sigma: float) -> float:
    z = (x - mu) / sigma
    return math.exp(-0.5 * z * z) / (sigma * math.sqrt(2.0 * math.pi))


def reg261_amount_eur(route_distance_nm: float | None) -> float:
    """
    EC Regulation 261/2004 compensation amount based on route distance.

    ≤ 1500 km  → €250
    1500–3500 km → €400 (reduced 50% if rerouted and delay < 4h at destination)
    > 3500 km  → €600

    Distance in nautical miles.
    """
    if route_distance_nm is None:
        return 400.0

    km = route_distance_nm * 1.852

    if km <= 1500:
        return 250.0
    if km <= 3500:
        return 400.0
    return 600.0


# ─── Existing cost model (unchanged) ─────────────────────────────────────────

@dataclass
class CostBreakdown:
    fuel_cost_eur: float
    time_cost_eur: float
    delay_cost_eur: float

    irops_cost_eur: float

    connex_cost_eur: float
    curfew_cost_eur: float
    duty_cost_eur: float
    va_score_cost_eur: float

    total_cost_eur: float

    fuel_cost_per_kg_eur: float
    base_time_cost_per_hour_eur: float
    effective_time_cost_per_hour_eur: float
    delay_cost_per_min_eur: float

    economic_ci_kg_per_min: float
    economic_ci_kg_per_hour: float
    recommended_ci: int
    ci_scale_factor: float

    fuel_kg: float
    time_min: float
    delay_min: float
    connex_risk_min: float
    curfew_risk_min: float
    duty_risk_min: float

    irops_mode_active: bool = False


@dataclass
class DynamicCostFactors:
    """
    Dynamic factors from scenario interpretation.

    Multipliers increase the value of time.
    Penalty minutes create direct penalty costs.

    IROPs fields (new):
        When connection_groups is non-empty, the non-linear IROPs step function
        replaces the linear delay_cost_per_min model for the delay cost component.
        All other cost components (fuel, time, connex/curfew multipliers) remain.
    """

    connex_time_multiplier: float = 0.0
    curfew_time_multiplier: float = 0.0
    duty_time_multiplier: float = 0.0
    reactionary_time_multiplier: float = 0.0
    va_time_multiplier: float = 0.0

    predicted_delay_min: float = 0.0
    predicted_connex_risk_min: float = 0.0
    predicted_curfew_risk_min: float = 0.0
    predicted_duty_risk_min: float = 0.0

    connex_penalty_eur_per_min: float = 0.0
    curfew_penalty_eur_per_min: float = 0.0
    duty_penalty_eur_per_min: float = 0.0
    va_score_penalty_eur_per_min: float = 0.0

    fuel_price_override_eur_per_kg: float | None = None
    ets_override_eur_per_kg: float | None = None
    fuel_surcharge_override_eur_per_kg: float | None = None
    time_cost_override_eur_per_hour: float | None = None
    delay_cost_override_eur_per_min: float | None = None

    ci_override: int | None = None

    # ── IROPs: non-linear connection cost ────────────────────────────────────

    connection_groups: list[IropsConnectionGroup] = field(default_factory=list)
    """
    Per-connection LTOP thresholds and cost steps.
    When non-empty, replaces the linear delay_cost_per_min model.
    Populated by ci_optimization_service from resolve_connex_uplink().
    """

    total_pax: int = 0
    """
    Total passengers on board.
    Used for soft cost calculation (€/pax/min × pax).
    Provide from SimBrief OFP payload weight if available.
    """

    soft_cost_eur_per_pax_per_min: float = 0.5
    """
    Passenger soft cost per minute of delay.
    Dissatisfaction, minor inconvenience. Cook & Tanner reference: ~€0.50/pax/min.
    """

    arrival_uncertainty_sigma_min: float = 0.0
    """
    Standard deviation of gate arrival time (minutes).
    Captures holding, sequencing and merging, taxi-in uncertainty.
    When > 0, uses E[IROPs(T)] instead of IROPs(E[T]).
    Typical European airport: 4–8 min.
    Set via ArrivalUncertaintyInput.sigma_min.
    """

    # ── EC Regulation 261/2004 ────────────────────────────────────────────────

    reg261_threshold_min: float = 180.0
    """
    Gate delay threshold (vs SIBT) at which Reg 261 triggers.
    3h for short/medium haul. 4h for long haul to certain destinations.
    """

    reg261_eur_per_pax: float = 0.0
    """
    Compensation amount per PAX. Set from reg261_amount_eur(route_distance_nm).
    0 = disabled (when route distance not available).
    """

    reg261_pax: int = 0
    """
    Number of passengers eligible for Reg 261 (connecting pax affected).
    """


def fuel_cost_per_kg(
    general_cfg: dict,
    dynamic: DynamicCostFactors | None = None,
) -> float:
    dynamic = dynamic or DynamicCostFactors()
    fuel_cfg = general_cfg.get("fuel", {})

    return (
        _non_negative_number(
            dynamic.fuel_price_override_eur_per_kg,
            fuel_cfg.get("price_eur_per_kg", 0.0),
        )
        + _non_negative_number(
            dynamic.ets_override_eur_per_kg,
            fuel_cfg.get("ets_eur_per_kg", 0.0),
        )
        + _non_negative_number(
            dynamic.fuel_surcharge_override_eur_per_kg,
            fuel_cfg.get("surcharge_eur_per_kg", 0.0),
        )
    )


def ci_scale_factor(
    *,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> float:
    aircraft_perf_cfg = aircraft_cfg.get("performance", {})

    scale = parse_number(
        aircraft_perf_cfg.get(
            "ci_scale_factor",
            general_cfg.get("ci_scale_factor", 1.0),
        ),
        default=1.0,
    )

    if scale is None or scale <= 0:
        return 1.0

    return scale


def clamp_ci(
    value: int,
    *,
    general_cfg: dict,
    aircraft_cfg: dict,
) -> int:
    aircraft_perf_cfg = aircraft_cfg.get("performance", {})

    min_ci = int(parse_number(
        aircraft_perf_cfg.get(
            "min_ci",
            general_cfg.get("min_ci", 0),
        ),
        default=0,
    ))

    max_ci = int(parse_number(
        aircraft_perf_cfg.get(
            "max_ci",
            general_cfg.get("max_ci", 999),
        ),
        default=999,
    ))

    return max(min_ci, min(value, max_ci))


def calculate_economic_ci(
    *,
    effective_time_cost_per_hour_eur: float,
    fuel_cost_per_kg_eur: float,
    general_cfg: dict,
    aircraft_cfg: dict,
    dynamic: DynamicCostFactors | None = None,
) -> tuple[float, float, int, float]:
    dynamic = dynamic or DynamicCostFactors()

    if dynamic.ci_override is not None:
        recommended = clamp_ci(
            int(dynamic.ci_override),
            general_cfg=general_cfg,
            aircraft_cfg=aircraft_cfg,
        )
        scale = ci_scale_factor(general_cfg=general_cfg, aircraft_cfg=aircraft_cfg)
        return 0.0, 0.0, recommended, scale

    if fuel_cost_per_kg_eur <= 0:
        economic_ci_kg_per_min = 0.0
        economic_ci_kg_per_hour = 0.0
    else:
        effective_time_cost_per_min_eur = effective_time_cost_per_hour_eur / 60.0
        economic_ci_kg_per_min = effective_time_cost_per_min_eur / fuel_cost_per_kg_eur
        economic_ci_kg_per_hour = economic_ci_kg_per_min * 60.0

    scale = ci_scale_factor(general_cfg=general_cfg, aircraft_cfg=aircraft_cfg)
    recommended = clamp_ci(
        int(round(economic_ci_kg_per_min * scale)),
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
    )

    return economic_ci_kg_per_min, economic_ci_kg_per_hour, recommended, scale


def base_time_cost_per_hour(
    aircraft_cfg: dict,
    remaining_time_h: float | None = None,
) -> float:
    crew_cfg = aircraft_cfg.get("crew", {})
    cockpit_cfg = crew_cfg.get("cockpit", {})
    cabin_cfg = crew_cfg.get("cabin", {})

    cockpit_hourly = _non_negative_number(cockpit_cfg.get("hourly_cost_eur", 0.0))
    base_pilots = int(parse_number(cockpit_cfg.get("base_pilots", 2), default=2))

    pilots = base_pilots
    augmentation_rule = cockpit_cfg.get("augmentation_rule", {})
    threshold = augmentation_rule.get("threshold_hours")

    if remaining_time_h is not None and threshold is not None:
        if remaining_time_h > _non_negative_number(threshold):
            pilots = max(pilots, 3)

    cockpit_cost = pilots * cockpit_hourly
    fa_count = int(parse_number(cabin_cfg.get("default_fa", 0), default=0))
    fa_hourly = _non_negative_number(cabin_cfg.get("hourly_cost_eur", 0.0))
    cabin_cost = fa_count * fa_hourly

    maintenance = _non_negative_number(aircraft_cfg.get("maint", 0.0))
    ownership = _non_negative_number(aircraft_cfg.get("own", 0.0))
    schedule = _non_negative_number(aircraft_cfg.get("sched", 0.0))

    return cockpit_cost + cabin_cost + maintenance + ownership + schedule


def effective_time_cost_per_hour(
    *,
    aircraft_cfg: dict,
    remaining_time_h: float | None,
    dynamic: DynamicCostFactors | None = None,
) -> tuple[float, float]:
    dynamic = dynamic or DynamicCostFactors()

    if dynamic.time_cost_override_eur_per_hour is not None:
        base = dynamic.time_cost_override_eur_per_hour
    else:
        base = base_time_cost_per_hour(
            aircraft_cfg=aircraft_cfg,
            remaining_time_h=remaining_time_h,
        )

    multiplier = (
        1.0
        + dynamic.connex_time_multiplier
        + dynamic.curfew_time_multiplier
        + dynamic.duty_time_multiplier
        + dynamic.reactionary_time_multiplier
        + dynamic.va_time_multiplier
    )

    return base, base * multiplier


def delay_cost_per_min(
    general_cfg: dict,
    aircraft_family: str,
    phase: str = "enroute",
    reactionary: bool = False,
    override_eur_per_min: float | None = None,
    aircraft_cfg: dict | None = None,
) -> float:
    if override_eur_per_min is not None:
        return _non_negative_number(override_eur_per_min)

    delay_table = general_cfg.get("delay_cost_eur_per_min", {})
    family_cfg = delay_table.get(aircraft_family)

    if not family_cfg:
        family_cfg = _fallback_delay_family_cfg(
            delay_table=delay_table,
            aircraft_family=aircraft_family,
            aircraft_cfg=aircraft_cfg,
        )

    if not family_cfg:
        return 0.0

    base = _non_negative_number(family_cfg.get(phase, 0.0))

    if reactionary:
        base *= _non_negative_number(family_cfg.get("reactionary_factor", 1.0), 1.0)

    return base


def _fallback_delay_family_cfg(
    *,
    delay_table: dict,
    aircraft_family: str,
    aircraft_cfg: dict | None = None,
) -> dict | None:
    family_aliases = {
        "A300": "A330",
        "A310": "A330",
        "A380": "A340",
        "B737": "A320",
        "B747": "A340",
        "B757": "A330",
        "B767": "A330",
        "B777": "A340",
        "B787": "A350",
        "CRJ": "A320",
        "DASH8": "A320",
        "EJET": "A320",
        "MD11": "A340",
        "ATR": "A320",
    }

    family = aircraft_family or ""
    if not family and aircraft_cfg:
        family = (
            aircraft_cfg.get("family")
            or aircraft_cfg.get("aircraft", {}).get("family")
            or aircraft_cfg.get("aircraft_type", "")
        )

    alias = family_aliases.get(family)
    if alias and isinstance(delay_table.get(alias), dict):
        return delay_table[alias]

    generic = delay_table.get("GENERIC")
    if isinstance(generic, dict):
        return generic

    a320 = delay_table.get("A320")
    if isinstance(a320, dict):
        return a320

    return None


def calculate_total_strategy_cost(
    *,
    fuel_kg: float,
    time_min: float,
    general_cfg: dict,
    aircraft_cfg: dict,

    delay_min: float = 0.0,
    delay_phase: str = "enroute",
    reactionary_delay: bool = False,

    dynamic: DynamicCostFactors | None = None,
) -> CostBreakdown:
    """
    Main cost function — evaluates ONE candidate strategy.

    When dynamic.connection_groups is non-empty, the delay cost component
    uses the non-linear IROPs step function instead of the linear
    delay_cost_per_min model. This captures the discrete cost jumps at
    connection LTOP thresholds.

    When dynamic.arrival_uncertainty_sigma_min > 0, the IROPs cost is
    computed as E[cost(T)] via numerical integration over the arrival
    time distribution — the correct treatment for a non-linear cost
    function under uncertainty.

    All other cost components (fuel, operating time, connex/curfew/duty
    multipliers) are unchanged and computed in parallel.
    """

    dynamic = dynamic or DynamicCostFactors()

    fuel_kg = _non_negative_number(fuel_kg)
    time_min = _non_negative_number(time_min)
    delay_min = _non_negative_number(delay_min)

    remaining_time_h = time_min / 60.0

    fuel_unit_cost = fuel_cost_per_kg(general_cfg, dynamic=dynamic)

    base_time_cost_h, effective_time_cost_h = effective_time_cost_per_hour(
        aircraft_cfg=aircraft_cfg,
        remaining_time_h=remaining_time_h,
        dynamic=dynamic,
    )

    (
        economic_ci_kg_per_min,
        economic_ci_kg_per_hour,
        recommended_ci,
        used_ci_scale_factor,
    ) = calculate_economic_ci(
        effective_time_cost_per_hour_eur=effective_time_cost_h,
        fuel_cost_per_kg_eur=fuel_unit_cost,
        general_cfg=general_cfg,
        aircraft_cfg=aircraft_cfg,
        dynamic=dynamic,
    )

    aircraft_family = aircraft_cfg.get("family") or aircraft_cfg.get("aircraft_type", "")

    delay_min_cost = delay_cost_per_min(
        general_cfg=general_cfg,
        aircraft_family=aircraft_family,
        phase=delay_phase,
        reactionary=reactionary_delay,
        override_eur_per_min=dynamic.delay_cost_override_eur_per_min,
        aircraft_cfg=aircraft_cfg,
    )

    used_delay_min = max(delay_min + dynamic.predicted_delay_min, 0.0)

    # ── Delay / IROPs cost ────────────────────────────────────────────────────

    irops_mode_active = bool(dynamic.connection_groups)

    if irops_mode_active:
        if dynamic.arrival_uncertainty_sigma_min > 0.1:
            irops_cost_eur = expected_irops_cost(
                mean_delay_min=used_delay_min,
                sigma_min=dynamic.arrival_uncertainty_sigma_min,
                connection_groups=dynamic.connection_groups,
                soft_cost_eur_per_pax_per_min=dynamic.soft_cost_eur_per_pax_per_min,
                total_pax=dynamic.total_pax,
                reg261_threshold_min=dynamic.reg261_threshold_min,
                reg261_eur_per_pax=dynamic.reg261_eur_per_pax,
                reg261_pax=dynamic.reg261_pax,
            )
        else:
            irops_cost_eur = irops_step_cost(
                gate_delay_min=used_delay_min,
                connection_groups=dynamic.connection_groups,
                soft_cost_eur_per_pax_per_min=dynamic.soft_cost_eur_per_pax_per_min,
                total_pax=dynamic.total_pax,
                reg261_threshold_min=dynamic.reg261_threshold_min,
                reg261_eur_per_pax=dynamic.reg261_eur_per_pax,
                reg261_pax=dynamic.reg261_pax,
            )
        delay_cost = irops_cost_eur
    else:
        irops_cost_eur = 0.0
        delay_cost = used_delay_min * delay_min_cost

    # ── Other penalty components ──────────────────────────────────────────────

    fuel_cost = fuel_kg * fuel_unit_cost
    time_cost = remaining_time_h * effective_time_cost_h

    connex_cost = (
        max(dynamic.predicted_connex_risk_min, 0.0)
        * dynamic.connex_penalty_eur_per_min
    )

    curfew_cost = (
        max(dynamic.predicted_curfew_risk_min, 0.0)
        * dynamic.curfew_penalty_eur_per_min
    )

    duty_cost = (
        max(dynamic.predicted_duty_risk_min, 0.0)
        * dynamic.duty_penalty_eur_per_min
    )

    va_score_cost = (
        max(dynamic.predicted_delay_min, 0.0)
        * dynamic.va_score_penalty_eur_per_min
    )

    total = (
        fuel_cost
        + time_cost
        + delay_cost
        + connex_cost
        + curfew_cost
        + duty_cost
        + va_score_cost
    )

    return CostBreakdown(
        fuel_cost_eur=round(fuel_cost, 2),
        time_cost_eur=round(time_cost, 2),
        delay_cost_eur=round(delay_cost, 2),
        irops_cost_eur=round(irops_cost_eur, 2),

        connex_cost_eur=round(connex_cost, 2),
        curfew_cost_eur=round(curfew_cost, 2),
        duty_cost_eur=round(duty_cost, 2),
        va_score_cost_eur=round(va_score_cost, 2),

        total_cost_eur=round(total, 2),

        fuel_cost_per_kg_eur=round(fuel_unit_cost, 2),
        base_time_cost_per_hour_eur=round(base_time_cost_h, 2),
        effective_time_cost_per_hour_eur=round(effective_time_cost_h, 2),
        delay_cost_per_min_eur=round(delay_min_cost, 2),

        economic_ci_kg_per_min=round(economic_ci_kg_per_min, 2),
        economic_ci_kg_per_hour=round(economic_ci_kg_per_hour, 2),
        recommended_ci=recommended_ci,
        ci_scale_factor=round(used_ci_scale_factor, 4),

        fuel_kg=round(fuel_kg, 2),
        time_min=round(time_min, 2),
        delay_min=round(used_delay_min, 2),
        connex_risk_min=round(dynamic.predicted_connex_risk_min, 2),
        curfew_risk_min=round(dynamic.predicted_curfew_risk_min, 2),
        duty_risk_min=round(dynamic.predicted_duty_risk_min, 2),

        irops_mode_active=irops_mode_active,
    )


def _non_negative_number(
    value,
    fallback: float = 0.0,
) -> float:
    number = parse_number(value, default=fallback)
    if number is None:
        return max(fallback, 0.0)
    return max(number, 0.0)
