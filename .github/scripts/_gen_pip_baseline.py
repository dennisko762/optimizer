import json
import sys

out_path = sys.argv[1]
report_paths = sys.argv[2:]

ids = []
for rp in report_paths:
    data = json.load(open(rp, encoding="utf-8"))
    for dep in data.get("dependencies", []):
        for v in dep.get("vulns", []):
            ids.append(f"{dep.get('name')}=={dep.get('version')}::{v.get('id')}")

ids = sorted(set(ids))
print(f"{len(ids)} vulnerability ids collected from {report_paths}")

with open(out_path, "w") as f:
    json.dump(
        {
            "_comment": (
                "Pre-existing pip-audit findings on the audited baseline "
                "commit, tracked here so CI only blocks NEW vulnerabilities "
                "introduced by a PR. See docs/ci-quality-gate.md. Regenerate "
                "with .github/scripts/_gen_pip_baseline.py after accepting a "
                "dependency upgrade that resolves an entry."
            ),
            "baseline_commit": "86e632c8438083cadc5d4967db72d2b0399bb2d3",
            "known_ids": ids,
        },
        f,
        indent=2,
    )
    f.write("\n")
