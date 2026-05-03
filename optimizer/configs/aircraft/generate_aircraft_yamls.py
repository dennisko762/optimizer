from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml

from optimizer.configs.aircraft.aircraft_catalog import AIRCRAFT_CATALOG, AircraftCatalogEntry, cabin_hourly_cost_eur, cockpit_hourly_cost_eur, cockpit_pilots_for_config, default_cabin_crew, easa_min_cabin_crew, normalize_aircraft_code, reference_fuel_flow, scaled_hourly_costs, typical_service_range




SIMBRIEF_INPUTS_URL = "http://www.simbrief.com/api/inputs.list.json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate aircraft YAML configs for optimizer."
    )

    parser.add_argument(
        "--output-dir",
        default="optimizer/configs/aircraft",
        help="Where aircraft YAML files should be written.",
    )

    parser.add_argument(
        "--include-simbrief",
        action="store_true",
        help="Fetch SimBrief supported aircraft list and generate estimated stubs for unknown types.",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing YAML files.",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries: dict[str, AircraftCatalogEntry] = dict(AIRCRAFT_CATALOG)

    unknown_simbrief_codes: list[str] = []

    if args.include_simbrief:
        simbrief_codes = fetch_simbrief_aircraft_codes()

        for code in simbrief_codes:
            normalized = normalize_aircraft_code(code)

            if normalized is None:
                continue

            if normalized not in entries:
                unknown_simbrief_codes.append(normalized)
                entries[normalized] = make_unknown_entry(normalized)

    written = 0
    skipped = 0

    for code, entry in sorted(entries.items(), key=lambda item: item[0]):
        yaml_data = build_yaml(entry, estimated=code in unknown_simbrief_codes)

        file_path = output_dir / f"{entry.config_key}.yaml"

        if file_path.exists() and not args.overwrite:
            skipped += 1
            continue

        with file_path.open("w", encoding="utf-8") as file:
            yaml.safe_dump(
                yaml_data,
                file,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
            )

        written += 1

    print(f"Generated aircraft YAMLs in: {output_dir}")
    print(f"Written: {written}")
    print(f"Skipped existing: {skipped}")

    if unknown_simbrief_codes:
        print()
        print("Unknown SimBrief aircraft generated as estimated stubs:")
        for code in sorted(set(unknown_simbrief_codes)):
            print(f"  - {code}")


def fetch_simbrief_aircraft_codes() -> list[str]:
    """
    Fetch currently supported SimBrief aircraft codes.

    The public SimBrief/Navigraph endpoint is intended for integrations and
    can change over time. Therefore this parser is intentionally defensive.
    """

    try:
        with urlopen(SIMBRIEF_INPUTS_URL, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch SimBrief inputs list: {exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse SimBrief inputs list JSON.") from exc

    codes = extract_aircraft_codes_from_any_json(data)

    if not codes:
        raise RuntimeError(
            "No aircraft codes found in SimBrief inputs list. "
            "The API schema may have changed."
        )

    return sorted(set(codes))


def extract_aircraft_codes_from_any_json(data: Any) -> list[str]:
    """
    Defensive extraction.

    SimBrief's inputs.list.json schema can contain nested lists/dicts.
    We search for likely aircraft type/code fields.
    """

    codes: list[str] = []

    likely_keys = {
        "type",
        "icao",
        "code",
        "aircraft",
        "aircraft_type",
        "aircraft_icao",
        "aircraft_code",
    }

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, inner in value.items():
                lowered = str(key).lower()

                if lowered in likely_keys and isinstance(inner, str):
                    normalized = normalize_aircraft_code(inner)
                    if normalized and 3 <= len(normalized) <= 5:
                        codes.append(normalized)

                walk(inner)

        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)

    return codes


def make_unknown_entry(code: str) -> AircraftCatalogEntry:
    """
    Unknown SimBrief types get a safe placeholder.

    They are generated so the UI/backend does not fail, but they are marked as
    estimated and unsupported in the YAML notes.
    """

    return AircraftCatalogEntry(
        simbrief_code=code,
        config_key=code.lower(),
        aircraft_type=code,
        family=code,
        seats=168,
        mtow_kg=None,
        mlw_kg=None,
        aircraft_category="jet",
        passenger=True,
        reference_fuel_flow_kgph=None,
        ci_min=0,
        ci_max=100,
        ci_step=5,
        notes="Estimated placeholder generated from SimBrief supported type list.",
    )


def build_yaml(entry: AircraftCatalogEntry, *, estimated: bool) -> dict[str, Any]:
    min_fa = easa_min_cabin_crew(entry.seats)
    default_fa = default_cabin_crew(entry.seats, entry.aircraft_category)
    service_range = typical_service_range(entry.seats, entry.aircraft_category)

    maint, own, sched = scaled_hourly_costs(entry)

    yaml_data: dict[str, Any] = {
        "aircraft_type": entry.aircraft_type,
        "simbrief_code": entry.simbrief_code,
        "family": entry.family,
        "seats": entry.seats,
        "type": "p" if entry.passenger else "f",
        "certification": {
            "mtow_kg": entry.mtow_kg,
            "mlw_kg": entry.mlw_kg,
        },
        "crew": {
            "cockpit": {
                "base_pilots": cockpit_pilots_for_config(entry),
                "augmentation_rule": {
                    "trigger": "block_time_hours",
                    "threshold_hours": 10.0,
                },
                "hourly_cost_eur": cockpit_hourly_cost_eur(entry),
            },
            "cabin": {
                "min_by_easa": min_fa,
                "typical_service_range": service_range,
                "default_fa": default_fa,
                "hourly_cost_eur": cabin_hourly_cost_eur(entry),
            },
        },
        "maint": maint,
        "own": own,
        "sched": sched,
        "performance": {
            "min_ci": entry.ci_min,
            "max_ci": entry.ci_max,
            "ci_step": entry.ci_step,
            "ci_normalization_factor": 21500,
        },
        "ff": reference_fuel_flow(entry),
        "support_status": {
            "estimated": estimated,
            "has_curated_catalog_entry": not estimated,
            "performance_model": "estimated_baseline_scaling",
        },
        "notes": {
            "important": [
                "This file is generated for simulator/VA optimization use.",
                "Cost values are estimates scaled from the A320 baseline until operator/VA data is provided.",
                "Cabin crew minimum uses EASA ORO.CC.100 one cabin crew member per 50 or fraction of 50 installed passenger seats.",
                "Actual airline seating, crew service levels, MTOW/MLW and costs can differ by operator and airframe.",
            ],
            "sources": {
                "crew_regulation": "EASA ORO.CC.100 one-per-50-or-fraction installed passenger seats.",
                "cost_model": "A320 baseline scaled by MTOW/seats; replace with VA/operator-provided cost data.",
                "performance": "Reference fuel flow is approximate unless backed by curated/OpenAP/performance data.",
            },
        },
    }

    if entry.notes:
        yaml_data["notes"]["catalog_note"] = entry.notes

    return yaml_data


if __name__ == "__main__":
    main()