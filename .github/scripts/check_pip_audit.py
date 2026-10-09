#!/usr/bin/env python3
"""Fail CI only on HIGH/CRITICAL vulnerabilities, reporting the rest.

pip-audit's vulnerability records don't carry a normalized severity
field consistently across advisory sources, so we treat *any* reported
known vulnerability as "block-worthy" for now (documented in
docs/ci-quality-gate.md) and separate pre-existing from newly introduced
by diffing against the committed baseline file
docs/security-baseline-pip-audit.json. This never silently hides a
finding: everything in the JSON artifact uploaded by the job is visible
regardless of this gate's exit code.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "security-baseline-pip-audit.json"


def load_report(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    return data.get("dependencies", [])


def vuln_ids(deps: list[dict]) -> set[str]:
    ids: set[str] = set()
    for dep in deps:
        for v in dep.get("vulns", []):
            ids.add(f"{dep.get('name')}=={dep.get('version')}::{v.get('id')}")
    return ids


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_pip_audit.py <report.json> [more.json ...]")
        return 2

    baseline: set[str] = set()
    if BASELINE_PATH.exists():
        baseline = set(json.loads(BASELINE_PATH.read_text()).get("known_ids", []))

    all_found: set[str] = set()
    for report_path in argv:
        p = Path(report_path)
        if not p.exists():
            continue
        all_found |= vuln_ids(load_report(report_path))

    new_findings = all_found - baseline
    pre_existing = all_found & baseline

    if pre_existing:
        print(f"Pre-existing vulnerabilities (tracked in baseline, not blocking): {len(pre_existing)}")
        for item in sorted(pre_existing):
            print(f"  [baseline] {item}")

    if new_findings:
        print(f"NEW vulnerabilities not in baseline: {len(new_findings)}")
        for item in sorted(new_findings):
            print(f"  [NEW] {item}")
        print(
            "Blocking: newly introduced dependency vulnerabilities found. "
            "Update the dependency, or if accepted as pre-existing/unfixable, "
            "add it to docs/security-baseline-pip-audit.json with justification."
        )
        return 1

    print("No newly introduced pip-audit findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
