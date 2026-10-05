"""One-off: seed one saved SimBrief plan into the running backend's store.

Builds the plan view from the M2 fixture with the backend's own service code
and persists it through the real plan store — so the running app's
SAVED FLIGHTPLANS list has a real flight row for the M2b check-in demo.
"""
import json
import os
import sys

# Make the repo root importable regardless of where the script lives.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from optimizer.api import flightplan_service as fps

raw = json.load(open(os.path.join(
    os.path.dirname(__file__), "..", "tests", "fixtures", "simbrief", "live_ofp_v2.json"
), encoding="utf-8"))
view = fps.build_flightplan(raw)
env = fps.save_plan(view)
print("saved key:", env["key"])
print("store at:", fps._store_path())
