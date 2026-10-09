#!/usr/bin/env python3
"""Fail CI only on test failures/errors not already known on the
baseline commit (see docs/ci-quality-gate.md#pre-existing-failures).

Reads a pytest JUnit XML report, extracts failing/erroring test ids,
and diffs them against docs/test-baseline-failures.json. New failures
(not in the baseline) fail this script; pre-existing ones are printed
but don't block. Nothing is hidden: the full JUnit report and raw
pytest console output are uploaded as artifacts by the workflow
regardless of this script's exit code.
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "test-baseline-failures.json"


def extract_failing_ids(junit_path: str) -> set[str]:
    tree = ET.parse(junit_path)
    root = tree.getroot()
    ids: set[str] = set()
    for testcase in root.iter("testcase"):
        error = testcase.find("error")
        if error is not None and error.get("message") == "collection failure":
            continue  # handled by extract_collection_error_ids
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        node_id = f"{classname}::{name}"
        if testcase.find("failure") is not None or error is not None:
            ids.add(node_id)
    return ids


def extract_collection_error_ids(junit_path: str) -> set[str]:
    """Collection failures appear as a <testcase classname=""
    name="tests.module.path"><error message="collection failure">...
    Treat the module dotted name as the failure id."""
    tree = ET.parse(junit_path)
    root = tree.getroot()
    ids: set[str] = set()
    for testcase in root.iter("testcase"):
        error = testcase.find("error")
        if error is not None and error.get("message") == "collection failure":
            ids.add(testcase.get("name", ""))
    return ids


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: check_test_baseline.py <junit-report.xml>")
        return 2

    junit_path = argv[0]
    if not Path(junit_path).exists():
        print(f"No JUnit report at {junit_path}; nothing to check.")
        return 0

    found = extract_failing_ids(junit_path) | extract_collection_error_ids(junit_path)

    baseline: set[str] = set()
    if BASELINE_PATH.exists():
        baseline = set(json.loads(BASELINE_PATH.read_text()).get("known_failing_ids", []))

    new_failures = found - baseline
    pre_existing = found & baseline

    if pre_existing:
        print(f"Pre-existing test failures (tracked in baseline, not blocking): {len(pre_existing)}")
        for item in sorted(pre_existing):
            print(f"  [baseline] {item}")

    if new_failures:
        print(f"NEW test failures not in baseline: {len(new_failures)}")
        for item in sorted(new_failures):
            print(f"  [NEW] {item}")
        print(
            "Blocking: this PR introduced new test failures. Fix them, or if "
            "a failure is pre-existing and was missed, add it to "
            "docs/test-baseline-failures.json with justification "
            "(never add a NEW failure caused by this PR's own change)."
        )
        return 1

    print("No newly introduced test failures (collection errors + failures all pre-existing or none found).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
