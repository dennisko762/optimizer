from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from performance_engine.ci_mach import CiMachRequest, optimize_ci_mach


def main() -> None:
    scenarios = [
        ("B77W CI 0 FL350 zero wind", 0, 0),
        ("B77W CI 100 FL350 zero wind", 100, 0),
        ("B77W CI 180 FL350 zero wind", 180, 0),
        ("B77W CI 180 FL350 80 kt headwind", 180, -80),
        ("B77W CI 180 FL350 80 kt tailwind", 180, 80),
    ]
    for label, ci, wind in scenarios:
        result = optimize_ci_mach(
            CiMachRequest(
                aircraft_variant="B77W",
                cost_index=ci,
                gross_weight_kg=240000.0,
                flight_level=350.0,
                wind_component_kt=wind,
                remaining_distance_nm=1000.0,
                live_fuel_flow_kg_h=10464.0,
                fuel_flow_reference_mach=0.839,
            ),
            include_candidates=False,
        )
        print(
            f"{label}: M{result.recommended_mach:.3f}, "
            f"TAS {result.recommended_tas_kt:.0f} kt, GS {result.ground_speed_kt:.0f} kt, "
            f"FF {result.fuel_flow_kg_h:.0f} kg/h, fuel {result.fuel_kg_per_nm:.2f} kg/NM"
        )


if __name__ == "__main__":
    main()
