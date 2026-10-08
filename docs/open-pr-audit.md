# Open PR audit (dispatch-time + fresh re-check)

Audited 2026-10-08 against `dennisko762/optimizer` (now public; branch
protection currently absent — 404 `Branch not protected`, not a billing
limitation). This PR does **not** modify any of the branches below; it
only adds repo-wide CI workflows that will start running against their
latest heads once merged to `main`.

| PR | Title | Head branch | Base | Mergeable | Notes |
|----|-------|--------------|------|-----------|-------|
| #14 | fix(efb-m3): resolve the real aircraft config so the live optimizer computes | `wt/m3-simconnect` | `main` | MERGEABLE | Active milestone work, independent of the TechLog chain. |
| #13 | feat(navigraph): M4 subscription-gated charts, airspace, NOTAM and risk data | `wt/m4-navigraph` | `main` | MERGEABLE | **Active rework** under task `t_3fcf4f14` — not touched by this task. |
| #11 | feat(techlog): TechLog P1-T5 — aircraft technical status derivation | `efb/t_8cbfcd0e-...` | `efb/t_dfd3189c-...` (T4, not `main`) | MERGEABLE | Stacked PR; see dependency chain below. |
| #9 | feat(crew): TechLog P1-T2 — persistent Aircraft entity | `efb/t_94e78224-...` | `main` | **CONFLICTING** | Stacked PR; conflicts with current `main` because `main` has since absorbed later TechLog commits directly (see chain below). |
| #2 | feat(techlog): add SQLite + SQLAlchemy + Alembic persistence baseline | `efb/t_9b54c699-...` | `main` | **CONFLICTING** | Earliest link in the TechLog chain; same conflict cause as #9. |

## Real dependency order (from base/head refs, not title order)

Computed via `git merge-base --is-ancestor` across the actual commit
graph, not assumed from PR numbers:

```
main (86e632c, audited tip)
  -> PR #2  head 52d4798  (SQLite/SQLAlchemy/Alembic baseline)
       -> PR #9  head 30b7bef  (persistent Aircraft entity)
            -> (T4 "maintenance actions model", merged to main as a
               direct commit rather than kept open as its own PR)
                 -> PR #11 head on efb/t_8cbfcd0e (T5 status derivation),
                    based on the T4 branch, not on main directly
```

PR #2 and PR #9 are **not** ancestors of each other's current branch
tip and both show `CONFLICTING` against present-day `main` — `main`
has already absorbed equivalent/later TechLog persistence work via
direct merges (T2/T3 commits are present on `main` at `2547747` and
earlier per `git log --oneline main`), so these two PRs are most likely
superseded duplicates of work already on `main` rather than still-needed
diffs. That determination requires a content diff, which is deliberately
out of scope for this task (no branch edits) — flagged for the
follow-up fan-out task instead.

PR #14 and PR #13 are independent milestone branches (M3 SimConnect, M4
Navigraph) based directly on `main`, unrelated to the TechLog chain.

## Status checks before this PR

All 5 open PRs reported **zero** status checks prior to this PR (no
`.github/workflows` existed on `main` until two narrow, unpinned
starter workflows — `python-package.yml`, `node.js.yml`, added directly
to `main` out-of-band during this task and superseded here, see below).
None of them have been re-evaluated against the new mandatory gate in
this task — that is explicit downstream work (see
`t_ff1b296c` — "After human gate merge, process every open PR through
mandatory gates").

## Note: two ad-hoc workflows appeared on `main` mid-task

While this task was in progress, two generic GitHub-template workflows
(`Add GitHub Actions workflow for Python package`,
`Add Node.js CI workflow configuration`) were pushed directly to `main`
outside this PR. They used unpinned action tags (`actions/checkout@v4`,
`@v3`), had no `permissions:` block (default read-write token), no
concurrency cancellation, no coverage/security gates, and non-matching
job names. This PR's branch is rebased on top of that commit and
**removes** those two files, replacing them with the hardened,
documented gate described in `docs/ci-quality-gate.md`. Flagged here
rather than silently dropped.
