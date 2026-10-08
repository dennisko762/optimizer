## What

<!-- Summarize the change. Link the Kanban task / tracking issue. -->

## Why

<!-- What problem does this solve, or what capability does it add? -->

## Quality gate checklist

- [ ] All required checks are green on the latest commit (`gh pr checks`)
- [ ] `python-coverage-gate` / `node-coverage-gate` ≥ 80% on changed code, or exception justified below
- [ ] No newly introduced HIGH/CRITICAL findings (`python-dependency-audit`, `node-dependency-audit`, `dependency-review`, CodeQL)
- [ ] `gitleaks` clean (no new secrets)
- [ ] Pre-existing failures this PR touches are called out explicitly (not silently fixed-and-hidden, not silently left broken without a note)
- [ ] Docs updated if this PR changes required checks, baselines, or the review workflow (`docs/ci-quality-gate.md`)

## Pre-existing findings touched by this PR (if any)

<!-- e.g. "Fixes the test_create_duplicate_409 flake described in
     docs/ci-quality-gate.md#pre-existing-failures" or "N/A" -->

## Review routing

Coder → `reviewer` → `qa` → human merge. See
`docs/ci-quality-gate.md#coder--reviewer--qa--human-merge-workflow`.
Do not merge without independent Reviewer + QA sign-off recorded on the
tracking Kanban card.
