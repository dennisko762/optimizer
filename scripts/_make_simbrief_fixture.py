"""Build the committed test fixture from a real SimBrief v2 payload.

The fixture preserves the true API shape (navlog as a list of per-waypoint
rows, ISO timestamps, plan_takeoff/plan_landing fuel keys, list-typed
alternate) that the flightplan parser and UI mappers consume. Heavy
non-UI sections (NOTAMs, METARs/TAFs, ATIS, prefiles, images, wind_data
stacks) are trimmed so the fixture stays small. The source payload is read
from $LIVE_OFP (default %LOCALAPPDATA%/Temp/simbrief_live.json) — this is a
one-off dev tooling script, run manually, not part of the test suite.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SRC = Path(os.environ.get(
    "LIVE_OFP",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp", "simbrief_live.json"),
))
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "simbrief" / "live_ofp_v2.json"

# Sections the flightplan view does not need at all.
DROP_SECTIONS = {
    "crew", "database_updates", "impacts", "images", "links", "map_data",
    "notams", "sigmets", "tracks", "weather", "tlr", "etops",
    "apoc_prefile", "fms_downloads", "isfp_prefile", "ivao_prefile",
    "pilotedge_prefile", "poscon_prefile", "prefile", "skylite_prefile",
    "vatsim_prefile", "offset_data", "offset_length", "alternate_navlog",
    "enroute_station", "takeoff_altn", "text",
}
# Keys trimmed inside the airport-style sections.
AIP_TRIM = {"notam", "atis", "metar", "taf", "metar_time", "taf_time",
            "metar_category", "metar_visibility", "metar_ceiling"}
# Keys trimmed inside navlog rows (keep everything the parser/UI uses).
NAVLOG_DROP = {"wind_data", "fir_units", "fir_valid_levels", "fir_crossing",
               "shear", "tropopause_feet", "ground_height", "mora"}


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    out = {k: v for k, v in raw.items() if k not in DROP_SECTIONS}

    # Anonymize pilot-identifying values (public repo — the fixture must not
    # leak a real registration / callsign / vAMSYS static id).
    ac = out.get("aircraft")
    if isinstance(ac, dict):
        ac["reg"] = "D-TEST"
    gen = out.get("general")
    if isinstance(gen, dict):
        gen["flight_number"] = "1234"
        gen["icao_airline"] = "TST"
    atc = out.get("atc")
    if isinstance(atc, dict):
        atc["callsign"] = "TST1234"
        atc["section18"] = ""
    fetch_sec = out.get("fetch")
    if isinstance(fetch_sec, dict):
        fetch_sec["userid"] = ""
    par = out.get("params")
    if isinstance(par, dict):
        par["static_id"] = ""
        par["user_id"] = ""
    for key in ("origin", "destination", "enroute_altn"):
        sec = out.get(key)
        if isinstance(sec, dict):
            out[key] = {k: v for k, v in sec.items() if k not in AIP_TRIM}
    for alt in out.get("alternate") or []:
        if isinstance(alt, dict):
            for k in list(alt.keys()):
                if k in AIP_TRIM:
                    alt.pop(k, None)
    nav = out.get("navlog")
    if isinstance(nav, list):
        out["navlog"] = [
            {k: v for k, v in row.items() if k not in NAVLOG_DROP}
            for row in nav if isinstance(row, dict)
        ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.1f} KiB), navlog rows: {len(out.get('navlog') or [])}")


if __name__ == "__main__":
    main()
