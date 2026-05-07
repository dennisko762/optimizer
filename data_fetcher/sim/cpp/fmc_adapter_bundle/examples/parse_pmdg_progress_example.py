import json

from optimizer.adapters import FmcAdapterRegistry

lines = [
    "    KAL902 PROGRESS 1/4",
    " TO      DTG  ETA   FUEL",
    "DINRO    372 2314z  84.9",
    " NEXT",
    "UDROS    469 2325z  83.2",
    " DEST",
    "RKSI    5026 0822z  15.3",
    " ECON SPD    TO STEP CLB",
    ".842        0000z/ 765nm",
    "",
    "",
    "------------------------",
    "<POS REPORT     POS REF>",
    "2037",
]

registry = FmcAdapterRegistry()
snapshot = registry.parse_first(lines, cdu_index=0)

if snapshot is None:
    raise RuntimeError("No adapter could parse these lines")

print(json.dumps(snapshot.to_dict(), indent=2))
