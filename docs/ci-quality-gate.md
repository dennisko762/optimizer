# CI Quality Gate

This repository enforces a mandatory CI quality gate on every pull request
via GitHub Actions. This document records the required checks, the
established coverage/security baselines, how to reproduce them locally,
and the triage rules for pre-existing vs newly introduced findings.

## Required check names (stable, used by branch protection)

Workflow `CI` (`.github/workflows/ci.yml`):

| Check name              | What it does                                              | Blocking? |
|--------------------------|------------------------------------------------------------|-----------|
| `python-tests`           | `pytest` across `tests/`, baseline-aware (see below)        | yes |
| `python-lint-ruff`        | `ruff check .` (pyflakes + syntax rule set, see below)      | yes |
| `python-typecheck-mypy`   | `mypy` over the application packages                        | **no — do not add to branch-protection required checks** (see Baseline; `continue-on-error: true` still reports its own check-run as `failure` when findings exist, by GitHub design, so branch protection must not require it yet) |
| `python-coverage-gate`    | `diff-cover` — changed-code coverage ≥ 80%                  | yes |
| `python-dependency-audit` | `pip-audit` on `requirements.txt` + `requirements-efb.txt`  | yes (new HIGH/CRIT only) |
| `node-build`              | `npm run build` (Vite) in `efb-ui/`                         | yes |
| `node-lint-eslint`        | `npm run lint` (ESLint) in `efb-ui/`                        | yes |
| `node-tests`              | `node --test` with coverage in `efb-ui/`                    | yes |
| `node-coverage-gate`      | `diff-cover` on the Node lcov report — changed-code ≥ 80%   | yes |
| `node-dependency-audit`   | `npm audit` in `efb-ui/`                                     | yes (new HIGH/CRIT only) |

Workflow `CodeQL` (`.github/workflows/codeql.yml`):

| Check name            | What it does |
|-------------------------|---------------|
| `codeql-python`         | CodeQL static analysis, Python, `security-extended` query pack |
| `codeql-javascript-typescript` | CodeQL static analysis, JS/TS, `security-extended` query pack |

Both block on newly introduced HIGH/CRITICAL findings via GitHub's default
code-scanning alert severity handling; CodeQL results appear as code
scanning alerts on the PR, not as a pass/fail gate by themselves — branch
protection should require the check **and** reviewers should check the
Security tab for new alerts (recorded as a manual step until this repo
is public long enough to enable "Require no new code scanning alerts").

Workflow `Gitleaks` (`.github/workflows/gitleaks.yml`):

| Check name  | What it does |
|--------------|---------------|
| `gitleaks`   | Scans the PR's new commits (`base_sha..HEAD`) and, on push to `main`, the full history. Always `--redact`s matched values. |

Workflow `Dependency Review` (`.github/workflows/dependency-review.yml`):

| Check name            | What it does |
|------------------------|---------------|
| `dependency-review`    | GitHub's manifest-diff dependency review, fails on newly introduced `high`+ advisories across the PR's changed manifests. |

## Coverage policy

- **Metric**: diff-aware coverage via [`diff-cover`](https://github.com/Bachmann1234/diff-cover),
  run against `origin/<base-branch>`. Only lines touched by the PR are
  scored, so legacy low-coverage files don't block unrelated PRs.
- **Threshold**: `--fail-under=80` — changed executable code in a PR
  must be ≥ 80% covered by the existing test suite (Python: pytest +
  `coverage.py`, cobertura XML; Node: `node --test
  --experimental-test-coverage` lcov output).
- **No-regression rule**: `python-tests` and `node-tests` always run and
  publish a full (non-diff) coverage report as an artifact
  (`python-coverage`, `node-coverage`). Reviewers compare the PR's
  artifact against the commit immediately before the PR's first commit
  to confirm overall project coverage did not drop. This is checked by
  a human reviewer today; automating a stored-baseline-diff is a good
  follow-up once the repo has a stable `main` coverage history to diff
  against (coverage trend requires several merged data points to be
  meaningful).
- **Overall baseline (audited 2026-10-08, commit `86e632c`)**: total
  statement coverage ≈ 65% (see raw run below). This is the floor PRs
  must not regress below; it is **not** the per-PR bar, which is the
  80% diff-aware threshold above.

  ```
  TOTAL  9307 statements, 3249 missed, 65% coverage
  (performance_engine/*_bak.py and sim_bridge/main.py, trajectory_engine/opentop_adapter.py
  are the biggest drags — unused backup files and a hardware-adapter module that is not
  exercised by the current unit-test suite)
  ```
- **Exclusions**: `tests/*`, `*_bak.py` (dead backup files kept in-tree),
  `.venv/*`. Configured in `pyproject.toml` under `[tool.coverage.run]`.
  `efb-ui/node_modules`, `dist`, and `*.test.js` files are excluded by
  construction (Node's test coverage only instruments `src/`, test files
  measure but don't count against themselves).
- **Never relaxed to pass**: if a PR's diff-coverage is under 80%, the
  fix is to add tests, not to lower the threshold or widen the
  exclusion list. Any exclusion-list change must be justified in the PR
  description and reviewed.

## Pre-existing failures (baseline — commit `86e632c`, audited 2026-10-08)

These exist on `main` **before** this quality gate was added. CI reports
them on every PR (so a PR that fixes one is visible), but they do not,
by themselves, block a PR that didn't touch the broken area. A PR is
only blocked by a *regression it introduces*, never by baseline noise it
didn't create.

1. **Two test modules fail to import**: `tests/test_gefs_ensemble.py`
   and `tests/test_wind_reconciliation.py` import
   `optimizer.wind.wind_profile` and `optimizer.wind.wind_interpolator`,
   neither of which exists in `optimizer/wind/` on `main` (only
   `gefs_ensemble.py`, `reconciliation.py`, `uncertainty.py` are
   present). `pytest --continue-on-collection-errors` lets the rest of
   the suite run; these two collection errors show up in every run's
   log until the missing modules are added or the tests are removed.
2. **3 tests in `tests/test_techlog_aircraft_api.py` are not
   idempotent across repeated local runs**: `test_create_aircraft`,
   `test_create_duplicate_409`, and `test_data_survives_restart` can
   fail with `409 Conflict` instead of `201` if a prior local run left
   `techlog.db` state behind the test didn't clean up (observed in a
   local reproduction; in a clean CI container/checkout each run starts
   from scratch so the suite passes clean — 330 passed / 4 failed with
   a *dirty* local venv run, 333 passed with a fresh environment). CI's
   `actions/checkout` starts from a clean workspace each run, so this
   should not reproduce in CI; if it does, the test fixture needs a
   `tmp_path`-scoped DB rather than process-cwd-relative `data/techlog.db`.
3. **`tests/performance_engine/test_ci_mach_physics_invariants.py::...::
   test_docs_exist_and_mention_main_sources`** fails independent of the
   above (documentation-content assertion, not an import/state issue) —
   tracked as a pre-existing doc-content gap, not introduced by this PR.
4. **`ruff check .`**: this PR ran `ruff check . --fix` once to clear
   every `F401` (unused-import) finding (40 auto-fixes, zero behavior
   change — import removal only). Two real findings remain, deliberately
   **excluded** from the gate's `select` list rather than hidden: `F841`
   (unused variables in `crew_platform/technical/routes.py`,
   `data_fetcher/sim/fmc_bridge.py`,
   `optimizer/api/simbrief_routes.py`, `optimizer/cost_optimizer.py` +
   its `_bak.py` twin, `optimizer/wind/uncertainty.py`,
   `src/adapters/lufthansa_virtual/simbrief_fetcher.py`,
   `strategy/strategy_generator.py`) and one genuine pre-existing bug,
   `F821` undefined name `_replace_flight_icaos` at
   `crew_platform/routes.py:390` (a real `NameError` if that code path
   executes — flagged here for a follow-up app-code fix, not patched by
   this CI-only task). The full default `ruff` rule set additionally
   reports 531 findings repo-wide (`UP045`, `I001`, `BLE001`, etc.) —
   not yet adopted; `pyproject.toml` documents the narrower `select`
   list deliberately so the lint gate is meaningful (and green) today
   rather than red by default. Broadening the rule set, and fixing the
   excluded F841/F821 findings, are follow-ups to be ratcheted in
   gradually.
5. **`mypy`**: advisory-only (`continue-on-error: true`); currently
   reports type errors across the application packages (mostly
   SQLAlchemy `Column[T]` vs plain `T` assignment mismatches in
   `crew_platform/technical/routes.py`, two `sys._MEIPASS` PyInstaller
   attributes mypy doesn't know about, one real `name-defined` bug in
   `crew_platform/routes.py:390`). Flip to blocking once the backlog is
   cleared.
6. **Dependency audit baseline**: `pip-audit` reports 73 unique
   pre-existing advisory IDs (126 counting duplicate CVE aliases) across
   `requirements.txt`/`requirements-efb.txt` on the audited commit (ML
   tooling pins like `torch`, `transformers`, `protobuf`, `starlette`
   are the main contributors); `npm audit` reports 15 pre-existing
   HIGH/CRITICAL advisory IDs in `efb-ui` (`vite`, `postcss`, `nanoid`,
   `brace-expansion`, `source-map-js` — all dev-time/build tooling, not
   shipped to the packaged EFB build). Both are recorded verbatim in
   `docs/security-baseline-pip-audit.json` and
   `docs/security-baseline-npm-audit.json` so CI only blocks genuinely
   **new** HIGH/CRITICAL findings a PR introduces. See "Dependency audit
   triage" below for the regeneration procedure.

## Local reproduction

### Python

```bash
python -m venv .venv && source .venv/Scripts/activate   # or .venv/bin/activate on Linux/macOS
pip install -r requirements.txt -r requirements-efb.txt -r requirements-dev.txt

# tests + coverage (matches python-tests)
python -m pytest tests -v --continue-on-collection-errors \
  --cov --cov-report=xml --cov-report=term-missing

# lint (matches python-lint-ruff)
ruff check .

# types (matches python-typecheck-mypy, advisory)
mypy --ignore-missing-imports crew_platform optimizer performance_engine \
  strategy trajectory_engine delay_module data_fetcher sim_bridge src

# diff-aware coverage gate (matches python-coverage-gate)
git fetch origin main --depth=1
diff-cover coverage.xml --compare-branch=origin/main --fail-under=80

# dependency audit (matches python-dependency-audit)
pip-audit -r requirements.txt --format json --output pip-audit-requirements.json || true
pip-audit -r requirements-efb.txt --format json --output pip-audit-efb.json || true
python .github/scripts/check_pip_audit.py pip-audit-requirements.json pip-audit-efb.json
```

### Node (`efb-ui/`)

```bash
cd efb-ui
npm ci
npm run build                 # matches node-build
npm run lint                  # matches node-lint-eslint
node --test --experimental-test-coverage \
  --test-reporter=lcov --test-reporter-destination=lcov.info \
  "src/**/*.test.js"          # matches node-tests
git fetch origin main --depth=1
diff-cover lcov.info --compare-branch=origin/main --fail-under=80   # node-coverage-gate
npm audit --json > npm-audit.json || true
node ../.github/scripts/check_npm_audit.js npm-audit.json            # node-dependency-audit
```

### Gitleaks

```bash
# Windows amd64 / Linux amd64 binaries at https://github.com/gitleaks/gitleaks/releases
gitleaks git --redact --no-banner                       # full history
gitleaks git --redact --no-banner --log-opts="origin/main..HEAD"  # PR diff only
```

### actionlint / yamllint (workflow syntax)

```bash
# https://github.com/rhysd/actionlint/releases
actionlint .github/workflows/*.yml
yamllint -d "{extends: default, rules: {line-length: disable, truthy: disable, document-start: disable}}" \
  .github/workflows/*.yml
```

## Test-failure baseline (pre-existing vs new)

`python-tests` always runs the full suite (`|| true` so a pre-existing
failure never aborts the job before coverage/report upload), then a
second step, `.github/scripts/check_test_baseline.py`, parses the
JUnit XML and diffs failing/erroring test ids against
`docs/test-baseline-failures.json` — the same new-vs-pre-existing
pattern used for the dependency audits. Only a failure **not** in that
baseline file fails the job. Regenerate the baseline deliberately (never
silently) after confirming a listed failure is still pre-existing and
unrelated to your change:

```bash
python -m pytest tests -v --continue-on-collection-errors --junitxml=pytest-report.xml || true
# inspect pytest-report.xml / console output, then hand-edit
# docs/test-baseline-failures.json with justification in the PR body —
# never add an id caused by your own PR's change.
```



`pip-audit` and `npm audit` report every known advisory for the resolved
dependency tree, including ones nobody has fixed yet. To avoid either
(a) blocking every PR on inherited debt, or (b) silently hiding
findings, each audit job:

1. Runs the real scanner and uploads the **full, unfiltered** JSON
   report as a build artifact (`pip-audit-reports`, `npm-audit-report`)
   — always, regardless of pass/fail, so nothing is hidden.
2. Diffs the found vulnerability IDs against a committed baseline file
   (`docs/security-baseline-pip-audit.json`,
   `docs/security-baseline-npm-audit.json`) containing the IDs known at
   the time this gate was added.
3. **Fails the job only if new IDs (not in the baseline) appear** —
   i.e. only vulnerabilities introduced by this PR's dependency changes.
   Pre-existing ones are printed (so they stay visible) but don't block.

To accept a new pre-existing finding into the baseline (e.g. after
triage decides it's unfixable right now and is being tracked
separately), regenerate deliberately — never hand-edit silently:

```bash
# Python
pip-audit -r requirements.txt --format json --output /tmp/root.json
pip-audit -r requirements-efb.txt --format json --output /tmp/efb.json
python .github/scripts/_gen_pip_baseline.py docs/security-baseline-pip-audit.json /tmp/root.json /tmp/efb.json

# Node
cd efb-ui && npm audit --json > npm-audit.json
node ../.github/scripts/_gen_npm_baseline.js ../docs/security-baseline-npm-audit.json npm-audit.json
```

Any baseline regeneration must be called out explicitly in the PR
description with the reason (e.g. "upgrading X introduces transitive Y
which has no fix yet, tracked as accepted risk").

## Security model

- **Default permissions**: every workflow sets top-level
  `permissions: contents: read`. Jobs that need more (CodeQL's
  `security-events: write` to publish SARIF) declare it at the **job**
  level, not the workflow level, and nothing else.
- **Pinned actions**: every third-party action reference is pinned to a
  full commit SHA (not a mutable tag), with the human-readable tag kept
  as a trailing comment for auditability.
- **No `pull_request_target`**: all workflows use `pull_request`, which
  runs with a read-only, secret-less `GITHUB_TOKEN` and the PR head's
  code — a malicious fork PR cannot escalate to repo secrets or write
  access. `persist-credentials: false` on every checkout additionally
  prevents the ambient token from being reused by subsequent steps or
  dependency install scripts.
- **No secret-dependent PR checks**: none of the PR-triggered jobs read
  a repository secret. Gitleaks runs via a checksum-verified public
  binary, not the commercial Action (which expects a license key);
  CodeQL's `security-events: write` only governs where results are
  published (this repo's own code-scanning tab), not an external
  credential.
- **Bounded timeouts**: every job sets `timeout-minutes`.
- **Concurrency cancellation**: every workflow cancels superseded runs
  for the same PR/ref via `concurrency: { group, cancel-in-progress }`.
- **Fork safety**: because nothing requires secrets or write access,
  checks run identically for same-repo and fork-origin PRs — there is
  no "skip on fork" special case to get wrong.

## Coder → Reviewer → QA → human-merge workflow

1. **Coder** implements, adds/updates tests, runs the Local
   Reproduction steps above, pushes, opens (or updates) exactly one PR,
   and requests review from the `reviewer` profile/role via the
   tracking Kanban card. Coder never merges or marks the card complete.
2. **Reviewer** independently reviews the diff (architecture, security,
   permissions, maintainability, regression risk) and the PR's
   **latest-head** `gh pr checks` output — not a stale run. Findings go
   back to Coder on the same card; on approval, Reviewer hands off
   explicitly to `qa` on the same card.
3. **QA** independently re-executes/validates: acceptance criteria,
   fork-safety assumptions, coverage/security/dependency gate behavior,
   and re-checks any prior Reviewer findings. Failures go back to Coder
   on the same branch/PR; Reviewer and QA repeat after fixes land.
4. Only after independent Reviewer + QA approval does the card move to
   **READY FOR HUMAN MERGE** — a human performs the actual merge. No
   agent role merges, enables auto-merge, or bypasses a required check
   at any point in this pipeline.

Required for a PR to reach "ready for human merge": all required checks
green on the latest commit SHA (verified via `gh pr checks`, not
memory), coverage/security policies green or explicitly
accepted-and-documented, independent Reviewer + QA sign-off recorded on
the Kanban card, and no open blocking finding.
