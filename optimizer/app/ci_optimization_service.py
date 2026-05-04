from __future__ import annotations

from typing import Any

from data_fetcher.sim.sim_models import CurrentFlightState

from delay_module.eta_calculator import EtaEstimate, _plus_minutes, compute_eta_if_possible
from optimizer.api.api_models import (
    AppliedStateResponse,
    ConnexConnectionResponse,
    EfbAction,
    InterpretedScenarioResponse,
    OperationalDataResponse,
    OptimizeRequest,
    OptimizeResponse,
    StrategyResponse,
)
from optimizer.config_loader import load_aircraft_config, load_general_config
from optimizer.cost_model import (
    IropsConnectionGroup,
    reg261_amount_eur,
)

from optimizer.cost_optimizer import optimize_cost
from optimizer.operational_data.connex_models import (
    ConnexConnectionStatus,
    ConnexPassengerGroupUplink,
    ConnexUplink,
)
from optimizer.operational_data.connex_resolver import (
    build_demo_connex_uplink,
    resolve_connex_uplink,
)
from optimizer.scenario_engine.scenario_interpreter import interpret_scenario
from optimizer.scenario_engine.scenario_models import (
    ArrivalUncertaintyInput,
    CostScenarioInput,
    FixedConstraintInput,
    FlightContextInput,
    HoldingScenarioInput,
    OperationalTrigger,
    RerouteScenarioInput,
    ScenarioInput,
    ScenarioPriority,
    ScenarioType,
    TimingScenarioInput,
    VatsimScenarioInput,
    VirtualAirlineScenarioInput,
    WeatherScenarioInput,
)
from optimizer.scenario_engine.scenario_state_applier import apply_scenario_to_current_state
from performance_engine.speed_envelope import max_mach_at_altitude, climb_advisory


class CiOptimizationService:
    """
    Application layer between UI/API and the optimization engine.

    IROPs integration (new):
        For CONNEX_UPLINK actions, converts resolved ConnexConnectionStatus
        objects into IropsConnectionGroup thresholds and passes them to
        optimize_cost(). This enables the non-linear step cost function and,
        when ArrivalUncertaintyInput.sigma_min > 0, the E[IROPs(T)] integration.
    """

    def optimize(self, request: OptimizeRequest) -> OptimizeResponse:
        general_cfg = load_general_config()
        aircraft_cfg = load_aircraft_config(request.aircraft_config)

        current_state = self._to_current_flight_state(request)
        flight_context = self._to_flight_context(request)
        cost = CostScenarioInput()

        eta = self._compute_eta(request=request, current_state=current_state)

        eff_max_mach = self._effective_max_mach(aircraft_cfg, current_state.altitude_ft)

        scenario_input, operational_data, irops_groups = self._build_scenario(
            request=request,
            current_state=current_state,
            flight_context=flight_context,
            cost=cost,
            eta=eta,
            max_mach=eff_max_mach,
        )

        updated_state = apply_scenario_to_current_state(
            current_state=current_state,
            scenario_input=scenario_input,
        )

        interpreted = interpret_scenario(scenario_input)

        arrival_sigma = scenario_input.arrival_uncertainty.sigma_min

        result = optimize_cost(
            current_state=updated_state,
            interpreted_scenario=interpreted,
            general_cfg=general_cfg,
            aircraft_cfg=aircraft_cfg,
            irops_groups=irops_groups if irops_groups else None,
            arrival_sigma_min=arrival_sigma,
        )

        warnings = list(result.warnings)
        envelope = (aircraft_cfg.get("performance") or {}).get("speed_envelope") or {}
        vmo_kt = float(envelope.get("vmo_kt") or 320.0)
        mmo = float(envelope.get("mmo") or 0.82)
        cruise_cfg = (aircraft_cfg.get("performance") or {}).get("cruise") or {}
        normal_max = float(cruise_cfg.get("normal_max_mach") or mmo)

        if (
            current_state.altitude_ft is not None
            and current_state.altitude_ft > 0
            and eff_max_mach < normal_max - 0.01
        ):
            advisory = climb_advisory(
                current_state.altitude_ft,
                normal_max,
                vmo_kt=vmo_kt,
                mmo=mmo,
            )
            if advisory:
                warnings.append(advisory)

        return OptimizeResponse(
            recommendation=result.recommendation,
            currentStrategy=self._to_strategy_response(result.current_strategy),
            bestStrategy=self._to_strategy_response(result.best_strategy),
            strategies=[
                self._to_strategy_response(strategy)
                for strategy in result.strategies
            ],
            appliedState=AppliedStateResponse(
                windComponentKt=updated_state.wind_component_kt,
                isaDeviationC=updated_state.isa_deviation_c,
                remainingDistanceNm=updated_state.remaining_distance_nm,
            ),
            interpreted=InterpretedScenarioResponse(
                trigger=interpreted.trigger.value,
                scenarioType=interpreted.scenario_type.value,
                objective=interpreted.objective.value,
                priority=interpreted.priority.value,
                reasons=interpreted.reasons,
                warnings=warnings,
            ),
            operationalData=operational_data,
        )

    # ─── IROPs group building ─────────────────────────────────────────────────

    def _build_manual_connex_uplink(
        self,
        *,
        hub_airport: str,
        current_eta_utc: str,
        manual_groups: list[dict],
    ) -> ConnexUplink:
        """
        Builds a ConnexUplink from manually entered UI data.

        Each group dict has: flight, dest, ltop (HH:MM), pax, kgPerPax.
        Missing fields are skipped or filled with defaults.
        """

        groups: list[ConnexPassengerGroupUplink] = []

        for i, g in enumerate(manual_groups):
            ltop = str(g.get("ltop") or "").strip()
            if not ltop:
                continue

            try:
                pax = int(float(str(g.get("pax") or 0)))
                kg_per_pax = float(str(g.get("kgPerPax") or 0))
            except (ValueError, TypeError):
                continue

            if pax <= 0:
                continue

            flight = str(g.get("flight") or f"CNX{i+1:02d}").strip().upper()
            dest = str(g.get("dest") or "---").strip().upper()

            # Estimate ETD as LTOP + 30 min (no real ETD in manual input)
            etd = _plus_minutes(ltop, 30)

            groups.append(
                ConnexPassengerGroupUplink(
                    group_id=f"MANUAL-{i+1:02d}",
                    outbound_flight=flight,
                    destination=dest,
                    gate=None,
                    etd_utc=etd,
                    ltop_utc=ltop,
                    affected_pax=pax,
                    max_extra_fuel_kg_per_pax=kg_per_pax,
                )
            )

        if not groups:
            return build_demo_connex_uplink(
                hub_airport=hub_airport,
                current_eta_utc=current_eta_utc,
            )

        return ConnexUplink(
            station=hub_airport,
            hub_airport=hub_airport,
            generated_at_utc=current_eta_utc,
            source="MANUAL_EFB_INPUT",
            eta_utc=current_eta_utc,
            arrival_gate=None,
            arrival_position=None,
            groups=groups,
            notes=["Manually entered by crew via EFB."],
        )

    def _build_irops_groups(
        self,
        *,
        connections: list[ConnexConnectionStatus],
        current_delay_min: float,
        route_distance_nm: float | None = None,
    ) -> list[IropsConnectionGroup]:
        """
        Converts resolved ConnexConnectionStatus objects into IropsConnectionGroup
        thresholds for the non-linear cost function.

        LTOP offset derivation
        ----------------------
        The IROPs step function evaluates cost(gate_delay_min), where
        gate_delay_min uses the same reference as current_delay_min (= 0
        means "on time vs SIBT").

        LTOP for a connection group is:
            LTOP = ETA + margin_to_ltop_min
                 = (SIBT + current_delay_min) + margin_to_ltop_min

        So relative to SIBT:
            ltop_offset_min = current_delay_min + margin_to_ltop_min

        A negative ltop_offset means the connection threshold has already
        passed at the current estimated arrival — step cost is already active.

        Cost parameters
        ---------------
        Rebooking:  €200 (short/medium haul),  €300 (long haul)
        Care:       €60  (meals, ground transport)
        Hotel:      €150 (when hotel_risk or last_connection_of_day)
        Reg 261:    derived from route_distance_nm (€250 / €400 / €600)
                    applied only when passenger_compensation_risk is flagged
        """

        reg261_eur = reg261_amount_eur(route_distance_nm)
        groups: list[IropsConnectionGroup] = []

        for conn in connections:
            if conn.affected_pax <= 0:
                continue

            ltop_offset = round(current_delay_min + conn.margin_to_ltop_min, 1)

            rebooking = 300.0 if conn.longhaul_connection else 200.0
            hotel = 150.0 if (conn.hotel_risk or conn.last_connection_of_day) else 0.0
            compensation = reg261_eur if conn.passenger_compensation_risk else 0.0

            groups.append(
                IropsConnectionGroup(
                    label=f"{conn.outbound_flight} {conn.destination}",
                    ltop_offset_min=ltop_offset,
                    affected_pax=conn.affected_pax,
                    rebooking_cost_eur_per_pax=rebooking,
                    care_cost_eur_per_pax=60.0,
                    hotel_cost_eur_per_pax=hotel,
                    compensation_eur_per_pax=compensation,
                    longhaul=conn.longhaul_connection,
                )
            )

        return sorted(groups, key=lambda g: g.ltop_offset_min)

    # ─── Scenario builders ────────────────────────────────────────────────────

    def _to_current_flight_state(self, request: OptimizeRequest) -> CurrentFlightState:
        fs = request.flight_state

        return CurrentFlightState(
            aircraft=fs.aircraft,
            engine_variant=fs.engine_variant,
            altitude_ft=fs.altitude_ft,
            gross_weight_kg=fs.gross_weight_kg,
            mach=fs.mach,
            current_cost_index=fs.current_cost_index,
            remaining_distance_nm=fs.remaining_distance_nm,
            route_distance_nm=(
                fs.route_distance_nm
                if fs.route_distance_nm is not None
                else fs.remaining_distance_nm
            ),
            wind_component_kt=0.0 if fs.wind_component_kt is None else fs.wind_component_kt,
            isa_deviation_c=0.0 if fs.isa_deviation_c is None else fs.isa_deviation_c,
            fuel_remaining_kg=fs.fuel_remaining_kg,
            ground_speed_kt=fs.ground_speed_kt,
            cruise_segments=[
                (
                    segment.model_dump(by_alias=True)
                    if hasattr(segment, "model_dump")
                    else segment.dict(by_alias=True)
                )
                for segment in fs.cruise_segments
                if segment.distance_nm > 0
            ],
            total_pax=fs.pax_count or 0,
        )

    def _to_flight_context(self, request: OptimizeRequest) -> FlightContextInput:
        ctx = request.flight_context
        destination = ctx.destination

        is_hub_inbound = destination in {"EDDF", "EDDM", "FRA", "MUC"}
        hub_airport = None
        if destination == "FRA":
            hub_airport = "EDDF"
        elif destination == "MUC":
            hub_airport = "EDDM"
        elif destination in {"EDDF", "EDDM"}:
            hub_airport = destination

        return FlightContextInput(
            origin=ctx.origin,
            destination=ctx.destination,
            planned_block_time_min=ctx.planned_block_time_min,
            elapsed_flight_time_min=ctx.elapsed_flight_time_min,
            remaining_flight_time_min=ctx.remaining_flight_time_min,
            is_hub_inbound=is_hub_inbound,
            hub_airport=hub_airport,
            flight_number=ctx.flight_number,
            airline=ctx.airline,
            sibt_utc=ctx.sibt_utc,
            sobt_utc=ctx.sobt_utc,
        )

    def _compute_eta(
        self,
        *,
        request: OptimizeRequest,
        current_state: CurrentFlightState,
    ) -> EtaEstimate | None:
        """
        Computes current ETA and delay from live state + SIBT.

        Returns None if insufficient data (no SIBT, no GS, no distance).
        """
        sibt = request.flight_context.sibt_utc
        sobt = request.flight_context.sobt_utc

        return compute_eta_if_possible(
            remaining_distance_nm=current_state.remaining_distance_nm,
            ground_speed_kt=current_state.ground_speed_kt,
            sibt_utc=sibt,
            sobt_utc=sobt,
        )

    def _build_scenario(
        self,
        *,
        request: OptimizeRequest,
        current_state: CurrentFlightState,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        eta: EtaEstimate | None = None,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse, list[IropsConnectionGroup]]:
        """
        Returns (ScenarioInput, OperationalDataResponse, irops_groups).

        When eta is available, current_delay_min is auto-populated from the
        live computation — the pilot does not need to enter it manually.

        irops_groups is non-empty only for CONNEX_UPLINK.
        """

        if request.action == EfbAction.NORMAL_RECALC:
            scenario, data = self._build_normal_scenario(
                flight_context=flight_context, cost=cost
            )
            return scenario, data, []

        if request.action == EfbAction.CONNEX_UPLINK:
            return self._build_connex_scenario(
                request=request, current_state=current_state,
                flight_context=flight_context, cost=cost, eta=eta,
                max_mach=max_mach,
            )

        if request.action == EfbAction.TARGET_ON_BLOCK:
            scenario, data = self._build_target_on_block_scenario(
                request=request, flight_context=flight_context, cost=cost, eta=eta,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.REROUTE:
            scenario, data = self._build_reroute_scenario(
                request=request, flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.WEATHER_REFRESH:
            scenario, data = self._build_weather_scenario(
                request=request, current_state=current_state,
                flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.FIXED_SPEED_FL:
            scenario, data = self._build_fixed_speed_fl_scenario(
                request=request, flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.HOLDING_OR_METERING:
            scenario, data = self._build_holding_scenario(
                request=request, flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.ATC_SPEED_CONSTRAINT:
            scenario, data = self._build_atc_speed_scenario(
                request=request, flight_context=flight_context, cost=cost
            )
            return scenario, data, []

        if request.action == EfbAction.ATC_LEVEL_CONSTRAINT:
            scenario, data = self._build_atc_level_scenario(
                request=request, flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.VATSIM_EVENT_FLOW:
            scenario, data = self._build_vatsim_scenario(
                request=request, flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        if request.action == EfbAction.VA_SCORING:
            scenario, data = self._build_va_scenario(
                flight_context=flight_context, cost=cost,
                max_mach=max_mach,
            )
            return scenario, data, []

        scenario, data = self._build_ofp_drift_scenario(
            flight_context=flight_context, cost=cost,
            max_mach=max_mach,
        )
        return scenario, data, []

    def _effective_max_mach(
        self,
        aircraft_cfg: dict,
        altitude_ft: float | None,
    ) -> float:
        """
        Return the highest Mach number that is physically achievable at
        altitude_ft given the aircraft's VMO and MMO limits.

        Falls back to the aircraft's normal_max_mach (or 0.82) when
        altitude is unknown so the behaviour is unchanged in that case.
        """
        envelope = (aircraft_cfg.get("performance") or {}).get("speed_envelope") or {}
        vmo_kt = float(envelope.get("vmo_kt") or 320.0)
        mmo = float(envelope.get("mmo") or 0.82)

        cruise = (aircraft_cfg.get("performance") or {}).get("cruise") or {}
        aircraft_max = float(cruise.get("normal_max_mach") or mmo)

        if altitude_ft is None or altitude_ft <= 0:
            return min(aircraft_max, mmo)

        envelope_ceiling = max_mach_at_altitude(altitude_ft, vmo_kt=vmo_kt, mmo=mmo)
        return min(aircraft_max, envelope_ceiling)

    def _base_optimizer_kwargs(
        self,
        *,
        allow_speed_up: bool,
        allow_slow_down: bool,
        max_mach: float | None = 0.82,
    ) -> dict[str, Any]:
        return {
            "allow_speed_up": allow_speed_up,
            "allow_slow_down": allow_slow_down,
            "min_mach": None,
            "max_mach": max_mach,
            "mach_step": 0.005,
            "max_extra_fuel_kg": None,
            "max_time_loss_min": None,
            "required_time_recovery_min": None,
        }

    def _build_normal_scenario(
        self,
        *,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        scenario = ScenarioInput(
            trigger=OperationalTrigger.MANUAL_RECALCULATION,
            source="EFB normal recalculation",
            scenario_type=ScenarioType.NORMAL_COST_OPTIMIZATION,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            arrival_uncertainty=ArrivalUncertaintyInput(),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=None,
            ),
        )

        data = OperationalDataResponse(
            title="Normal Cost Recalculation",
            status="Manual re-check",
            rows=[
                ("Objective", "Minimize expected total cost"),
                ("Reason", "Crew requested a fresh CI check"),
                ("Operational data", "Current state only"),
            ],
            note="Useful when flight time is above one hour or when crew wants to verify the current CI.",
        )

        return scenario, data

    def _build_connex_scenario(
        self,
        *,
        request: OptimizeRequest,
        current_state: CurrentFlightState,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        eta: EtaEstimate | None = None,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse, list[IropsConnectionGroup]]:
        """
        Connex scenario — the only action that produces IROPs groups.

        Flow:
        1. Build / receive connex uplink (demo or real).
        2. Resolve uplink → ConnexScenarioInput + TimingScenarioInput + resolved data.
        3. Convert ConnexConnectionStatus list → IropsConnectionGroup list.
        4. Pass IROPs groups to optimize_cost() so the non-linear step function
           and E[IROPs(T)] integration are used instead of linear delay cost.

        The sigma_min for arrival uncertainty is taken from the payload
        (key: "arrivalSigmaMin") or defaults to ArrivalUncertaintyInput.sigma_min
        (5.0 min). Increase sigma for peak-traffic airports or when holding
        is expected.
        """

        current_eta_utc = str(
            request.payload.get("currentEtaUtc")
            or request.payload.get("current_eta_utc")
            or (eta.eta_utc if eta else None)
            or "16:24"
        )

        hub = flight_context.hub_airport or flight_context.destination or "EDDF"

        manual_groups = request.payload.get("manualGroups") or []
        # Filter out incomplete rows (need at least LTOP and PAX)
        valid_manual = [
            g for g in manual_groups
            if g.get("ltop") and g.get("pax") and float(str(g.get("pax") or 0)) > 0
        ]

        if valid_manual:
            uplink = self._build_manual_connex_uplink(
                hub_airport=hub,
                current_eta_utc=current_eta_utc,
                manual_groups=valid_manual,
            )
        else:
            uplink = build_demo_connex_uplink(
                hub_airport=hub,
                current_eta_utc=current_eta_utc,
            )

        connex, timing, resolved = resolve_connex_uplink(
            uplink=uplink,
            current_eta_utc=current_eta_utc,
        )

        current_delay_min = timing.current_delay_min or 0.0

        route_distance_nm = getattr(current_state, "route_distance_nm", None)

        irops_groups = self._build_irops_groups(
            connections=resolved.connections,
            current_delay_min=current_delay_min,
            route_distance_nm=route_distance_nm,
        )

        # Detect if all LTOPs are already past (no recovery possible at all)
        all_missed = bool(irops_groups) and all(
            g.ltop_offset_min <= 0 for g in irops_groups
        )

        arrival_sigma = float(
            request.payload.get("arrivalSigmaMin", 5.0)
        )

        scenario = ScenarioInput(
            trigger=OperationalTrigger.CONNEX_INFO_RECEIVED,
            source=uplink.source,
            raw_message="Connex uplink applied by application service.",
            scenario_type=ScenarioType.CONNEX_RECOVERY,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            timing=timing,
            connex=connex,
            arrival_uncertainty=ArrivalUncertaintyInput(
                expected_holding_min=None,
                recovery_absorption_factor=0.8,
                sigma_min=arrival_sigma,
            ),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=False,
                max_mach=max_mach,
            ),
        )

        connections = [
            ConnexConnectionResponse(
                outboundFlight=connection.outbound_flight,
                destination=connection.destination,
                gate=connection.gate,
                etdUtc=connection.etd_utc,
                ltopUtc=connection.ltop_utc,
                affectedPax=connection.affected_pax,
                fuelBudgetKg=connection.fuel_budget_kg,
                marginToLtopMin=connection.margin_to_ltop_min,
                requiredRecoveryMin=connection.required_recovery_min,
                protected=connection.protected,
                atRisk=connection.at_risk,
            )
            for connection in resolved.connections
        ]

        irops_summary = []
        for g in irops_groups:
            status = "already missed" if g.ltop_offset_min <= 0 else f"LTOP at +{g.ltop_offset_min:.0f} min"
            irops_summary.append((g.label, f"€{g.step_cost_eur:.0f} step ({status})"))

        data = OperationalDataResponse(
            title="Connecting Passenger Integration",
            status="Protected" if resolved.connex_protected else "At risk",
            rows=[
                ("Station", resolved.station),
                ("ETA", resolved.eta_utc or "—"),
                ("Arrival gate", resolved.arrival_gate or "—"),
                ("Affected pax", str(resolved.affected_pax_total)),
                ("Fuel budget", f"{resolved.total_fuel_budget_kg:.0f} kg"),
                ("Required recovery", f"{resolved.required_time_recovery_min:.1f} min"),
                ("Most restrictive LTOP", resolved.most_restrictive_ltop_utc or "—"),
                ("IROPs mode", f"Active — {len(irops_groups)} groups, σ={arrival_sigma:.0f} min"),
                *irops_summary,
            ],
            note=(
                "All connection LTOPs have already passed. Speed-up cannot protect these "
                "connections. Maintaining current CI is recommended."
                if all_missed else
                "IROPs step function active. Cost uses non-linear connection thresholds "
                f"with arrival uncertainty σ={arrival_sigma:.0f} min (E[cost(T)] integration)."
            ),
            connexConnections=connections,
        )

        return scenario, data, irops_groups

    def _build_target_on_block_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        eta: EtaEstimate | None = None,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        auto_delay = eta.current_delay_min if eta else None
        current_delay_min = float(
            request.payload.get("currentDelayMin")
            if request.payload.get("currentDelayMin") is not None
            else (auto_delay if auto_delay is not None else 12.0)
        )
        target_delay_min = float(request.payload.get("targetDelayMin", 0.0))
        target_on_block_utc = request.payload.get("targetOnBlockUtc")

        scenario = ScenarioInput(
            trigger=OperationalTrigger.TARGET_ON_BLOCK_UPDATED,
            source="EFB target on-block input",
            scenario_type=ScenarioType.TARGET_ON_BLOCK,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            timing=TimingScenarioInput(
                current_delay_min=current_delay_min,
                target_delay_min=target_delay_min,
                target_on_block_utc=target_on_block_utc,
            ),
            arrival_uncertainty=ArrivalUncertaintyInput(),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=False,
                max_mach=max_mach,
            ),
        )

        required = max(current_delay_min - target_delay_min, 0.0)

        data = OperationalDataResponse(
            title="Desired On-Block Time",
            status="Recovery target set",
            rows=[
                ("Current delay", f"{current_delay_min:.1f} min"),
                ("Target delay", f"{target_delay_min:.1f} min"),
                ("Required recovery", f"{required:.1f} min"),
                ("Target on-block", str(target_on_block_utc or "—")),
            ],
            note="The crew enters the target. The engine decides whether speed-up is worth the fuel.",
        )

        return scenario, data

    def _build_reroute_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        new_remaining_distance_nm = request.payload.get("newRemainingDistanceNm")
        distance_delta_nm = request.payload.get("distanceDeltaNm", 45.0)
        reason = str(request.payload.get("reason", "ATC/weather reroute"))

        new_distance = float(new_remaining_distance_nm) if new_remaining_distance_nm is not None else None
        distance_delta = float(distance_delta_nm) if distance_delta_nm is not None else None

        scenario = ScenarioInput(
            trigger=OperationalTrigger.REROUTE_RECEIVED,
            source=str(request.payload.get("source", "ATC / SimBrief refresh")),
            scenario_type=ScenarioType.REROUTE_RECOVERY,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            reroute=RerouteScenarioInput(
                reroute_received=True,
                new_remaining_distance_nm=new_distance,
                distance_delta_nm=distance_delta,
                reason=reason,
            ),
            arrival_uncertainty=ArrivalUncertaintyInput(),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="Reroute / Updated Route",
            status="New route distance",
            rows=[
                ("Reason", reason),
                ("Distance delta", f"{distance_delta:+.1f} NM" if distance_delta is not None else "—"),
                ("New remaining distance", f"{new_distance:.1f} NM" if new_distance is not None else "—"),
            ],
            note="Reroutes are strong recalculation triggers because distance, ETA and fuel burn change.",
        )

        return scenario, data

    def _build_weather_scenario(
        self,
        *,
        request: OptimizeRequest,
        current_state: CurrentFlightState,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        source = str(request.payload.get("source", "SimBrief / FMC wind uplink"))

        new_wind = float(request.payload.get(
            "newWindComponentKt",
            current_state.wind_component_kt - 25.0,
        ))

        new_isa = float(request.payload.get(
            "newIsaDeviationC",
            current_state.isa_deviation_c + 2.0,
        ))

        expected_weather_reroute_nm = float(request.payload.get("expectedWeatherRerouteNm", 0.0))

        scenario = ScenarioInput(
            trigger=OperationalTrigger.WEATHER_FORECAST_UPDATED,
            source=source,
            raw_message="Updated weather forecast applied by application service.",
            scenario_type=ScenarioType.WEATHER_UPDATE,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            weather=WeatherScenarioInput(
                weather_update_received=True,
                old_wind_component_kt=current_state.wind_component_kt,
                new_wind_component_kt=new_wind,
                old_isa_deviation_c=current_state.isa_deviation_c,
                new_isa_deviation_c=new_isa,
                expected_weather_reroute_nm=expected_weather_reroute_nm,
                updated_forecast_source=source,
            ),
            arrival_uncertainty=ArrivalUncertaintyInput(),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="Weather / Forecast Update",
            status="Updated forecast applied",
            rows=[
                ("Source", source),
                ("Wind component", f"{current_state.wind_component_kt:+.0f} kt → {new_wind:+.0f} kt"),
                ("ISA deviation", f"{current_state.isa_deviation_c:+.1f}°C → {new_isa:+.1f}°C"),
                ("Weather reroute", f"{expected_weather_reroute_nm:+.1f} NM"),
            ],
            note="Weather is a recalculation trigger. The objective remains cost/time/fuel optimization.",
        )

        return scenario, data

    def _build_fixed_speed_fl_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        fixed_mach_raw = request.payload.get("fixedMach", 0.78)
        fixed_fl_raw = request.payload.get("fixedFlightLevel", 330)

        fixed_mach = float(fixed_mach_raw) if fixed_mach_raw is not None else None
        fixed_fl = int(fixed_fl_raw) if fixed_fl_raw is not None else None

        scenario = ScenarioInput(
            trigger=OperationalTrigger.MANUAL_RECALCULATION,
            source="EFB fixed speed/FL evaluation",
            scenario_type=ScenarioType.FIXED_SPEED_FL,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            fixed_constraints=FixedConstraintInput(
                fixed_mach=fixed_mach,
                fixed_flight_level=fixed_fl,
            ),
            **self._base_optimizer_kwargs(
                allow_speed_up=False if fixed_mach is not None else True,
                allow_slow_down=False if fixed_mach is not None else True,
                max_mach=fixed_mach,
            ),
        )

        data = OperationalDataResponse(
            title="Fixed Speed / Flight Level",
            status="Constraint evaluation",
            rows=[
                ("Fixed Mach", f"M{fixed_mach:.3f}" if fixed_mach is not None else "—"),
                ("Fixed FL", f"FL{fixed_fl}" if fixed_fl is not None else "—"),
            ],
            note="This evaluates the cost/time/fuel impact of a fixed operational constraint.",
        )

        return scenario, data

    def _build_holding_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        holding = float(request.payload.get("expectedHoldingMin", 20.0))
        metering = float(request.payload.get("arrivalMeteringDelayMin", 10.0))

        sigma = float(request.payload.get("arrivalSigmaMin", max(holding / 3.0, 5.0)))

        scenario = ScenarioInput(
            trigger=OperationalTrigger.HOLDING_OR_METERING_EXPECTED,
            source="ATC / arrival flow information",
            scenario_type=ScenarioType.HOLDING_EXPECTED,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            holding=HoldingScenarioInput(
                expected_holding_min=holding,
                arrival_metering_delay_min=metering,
                holding_absorbs_recovery=True,
            ),
            arrival_uncertainty=ArrivalUncertaintyInput(
                expected_holding_min=holding,
                expected_sequencing_delay_min=metering,
                recovery_absorption_factor=1.0,
                sigma_min=sigma,
            ),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="Holding / Arrival Metering",
            status="Arrival flow applied",
            rows=[
                ("Expected holding", f"{holding:.1f} min"),
                ("Metering delay", f"{metering:.1f} min"),
                ("Arrival sigma", f"{sigma:.1f} min"),
                ("Recovery absorption", "Full (100%)"),
            ],
            note="If arrival flow absorbs cruise time savings, speed-up may waste fuel.",
        )

        return scenario, data

    def _build_atc_speed_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        assigned_mach = float(request.payload.get("assignedMach", 0.78))

        scenario = ScenarioInput(
            trigger=OperationalTrigger.ATC_SPEED_CONSTRAINT,
            source="ATC speed assignment",
            scenario_type=ScenarioType.ATC_SPEED_CONSTRAINT,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            fixed_constraints=FixedConstraintInput(assigned_mach=assigned_mach),
            allow_speed_up=False,
            allow_slow_down=False,
            min_mach=assigned_mach,
            max_mach=assigned_mach,
            mach_step=0.005,
        )

        data = OperationalDataResponse(
            title="ATC Speed Constraint",
            status="Assigned speed",
            rows=[("Assigned Mach", f"M{assigned_mach:.3f}")],
            note="This evaluates the operational impact of an assigned speed.",
        )

        return scenario, data

    def _build_atc_level_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        assigned_fl = int(request.payload.get("assignedFlightLevel", 330))

        scenario = ScenarioInput(
            trigger=OperationalTrigger.ATC_LEVEL_CONSTRAINT,
            source="ATC level assignment",
            scenario_type=ScenarioType.ATC_LEVEL_CONSTRAINT,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            fixed_constraints=FixedConstraintInput(assigned_flight_level=assigned_fl),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="ATC Level Constraint",
            status="Assigned flight level",
            rows=[("Assigned FL", f"FL{assigned_fl}")],
            note="This evaluates the impact of a level restriction.",
        )

        return scenario, data

    def _build_vatsim_scenario(
        self,
        *,
        request: OptimizeRequest,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        holding = float(request.payload.get("expectedHoldingMin", 25.0))
        metering = float(request.payload.get("arrivalMeteringDelayMin", 15.0))
        atc_reroute = bool(request.payload.get("atcReroute", False))
        oceanic_restriction = bool(request.payload.get("oceanicLevelRestriction", False))

        scenario = ScenarioInput(
            trigger=OperationalTrigger.VATSIM_EVENT_FLOW_UPDATED,
            source="VATSIM event flow",
            scenario_type=ScenarioType.VATSIM_EVENT_FLOW,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            vatsim=VatsimScenarioInput(
                enabled=True,
                event_mode=True,
                expected_holding_min=holding,
                arrival_metering_delay_min=metering,
                atc_reroute=atc_reroute,
                oceanic_level_restriction=oceanic_restriction,
            ),
            arrival_uncertainty=ArrivalUncertaintyInput(
                expected_holding_min=holding,
                expected_sequencing_delay_min=metering,
                recovery_absorption_factor=1.0,
                sigma_min=float(request.payload.get("arrivalSigmaMin", 6.0)),
            ),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="VATSIM Event Flow",
            status="Event flow applied",
            rows=[
                ("Expected holding", f"{holding:.1f} min"),
                ("Metering delay", f"{metering:.1f} min"),
                ("ATC reroute", "Yes" if atc_reroute else "No"),
                ("Oceanic/level restriction", "Yes" if oceanic_restriction else "No"),
            ],
            note="For VATSIM events, fastest cruise may not improve arrival time if holding is expected.",
        )

        return scenario, data

    def _build_va_scenario(
        self,
        *,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        scenario = ScenarioInput(
            trigger=OperationalTrigger.VA_SCORING_RISK_UPDATED,
            source="VA profile",
            scenario_type=ScenarioType.VA_SCORING,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            timing=TimingScenarioInput(
                current_delay_min=10.0,
                target_delay_min=0.0,
            ),
            virtual_airline=VirtualAirlineScenarioInput(
                enabled=True,
                va_name=flight_context.airline or "Virtual Airline",
                on_time_score_weight=1.0,
                fuel_score_weight=1.0,
                pirep_late_threshold_min=15.0,
            ),
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="VA Scoring",
            status="VA profile applied",
            rows=[
                ("VA", flight_context.airline or "Virtual Airline"),
                ("On-time weight", "1.0"),
                ("Fuel weight", "1.0"),
            ],
            note="This mode optimizes for a virtual airline scoring profile.",
        )

        return scenario, data

    def _build_ofp_drift_scenario(
        self,
        *,
        flight_context: FlightContextInput,
        cost: CostScenarioInput,
        max_mach: float = 0.82,
    ) -> tuple[ScenarioInput, OperationalDataResponse]:
        scenario = ScenarioInput(
            trigger=OperationalTrigger.MANUAL_RECALCULATION,
            source="OFP drift check",
            scenario_type=ScenarioType.OFP_DRIFT_CHECK,
            priority=ScenarioPriority.MEDIUM,
            flight_context=flight_context,
            cost=cost,
            **self._base_optimizer_kwargs(
                allow_speed_up=True,
                allow_slow_down=True,
                max_mach=max_mach,
            ),
        )

        data = OperationalDataResponse(
            title="OFP Drift Check",
            status="Not fully implemented",
            rows=[("Objective", "Check current progress vs OFP")],
            note="Requires planned vs actual fuel/time data in a later module.",
        )

        return scenario, data

    def _to_strategy_response(self, strategy) -> StrategyResponse:
        return StrategyResponse(
            costIndex=strategy.cost_index,
            mach=strategy.mach,
            label=strategy.label,
            fuelKg=strategy.performance.remaining_fuel_kg,
            timeMin=strategy.performance.remaining_time_min,
            totalCostEur=strategy.cost.total_cost_eur,
            deltaFuelKg=strategy.delta_fuel_kg,
            deltaCruiseTimeMin=strategy.delta_time_min,
            gateTimeSavedMin=strategy.gate_time_saved_min,
            deltaCostEur=strategy.delta_cost_eur,
            allowed=strategy.allowed,
            rejectionReason=strategy.rejection_reason,
        )
