# Wave 3, Session 3: source registry and community expansion

## Run status

Stopped at stage 7 (REVIEW). The review node could not be dispatched on
either rung of the free model chain, so no review verdict exists and no
commit was made. The working tree on `wave3/source-expansion` holds all of
the changes at the base commit `00a6ca3`. Nothing was pushed.

## Goal

Expand Product Hunter's source registry and community-discovery capabilities:
a community registry, source metadata and type classification, source
reliability metadata, community and topic metadata, source health tracking,
a source discovery workflow, a source approval workflow, source admission
checks, source-specific collection configuration, source-specific rate
limits, source-specific query capabilities, source retirement and
deactivation, and source revalidation. The goal is to discover high-value
communities where people repeatedly describe real problems, not to scrape
volume.

## Assumption recorded at stage 0

The deliverable path in the task (`docs/resonix/reports/...`) does not exist
in this repository; the docs root is `docs/reasonix/`. This report is written
there. The same applies to the spec and plan documents, which live under
`docs/reasonix/specs/` and `docs/reasonix/plans/`.

## Models (free tier)

- Stage 4 implement: `loop-free-implement-1` on `longcat-2.5-preview-free`
- Stage 4 fix round: `loop-free-implement-1` on `longcat-2.5-preview-free`
- Stage 4 harden fix round: `loop-free-implement-1` on `longcat-2.5-preview-free`
- Stage 5 verify: `loop-free-verify-code-1` on `longcat-2.5-preview-free`
- Stage 6 harden: `loop-free-harden` on `step-5-preview-free`
- Stage 7 review: `loop-free-review-1` on `step-5-preview-free` (failed:
  endpoint unavailable), then `loop-free-review-2` on
  `longcat-2.5-preview-free` (failed: model unavailable). Both rungs failed.
  No degraded verdict was accepted.

## Risk

- Declared: `high`. Reason: `config/sources.yaml` is an adapter high-risk
  path, so the run floors at high and stages 2, 6 and 7 are non-skippable.
  No auth, schema, payment, secret or infrastructure change beyond the
  registry.
- Adapter floor: `medium` (`Product Hunter`, `floorRisk: medium`).

## Adapter

`Product Hunter` (repo), 8 forbidden rules in force.

## Stages

- 0 ORIENT: complete (risk high, radius declared, 23 files).
- 1 DEFINE: complete (`docs/reasonix/specs/wave-3-source-expansion.md`).
- 2 ARCHITECT: complete.
- 3 PLAN: complete (`docs/reasonix/plans/wave-3-source-expansion.md`).
- 4 IMPLEMENT: complete, plus two fix rounds.
- 5 VERIFY: complete, code gate green.
- 6 HARDEN: complete, plus one harden fix round.
- 7 REVIEW: **failed to dispatch on both rungs.** No verdict, no findings.
- 8 PRODUCTIONIZE: not run.
- 9 SHIP: not run (requires a review verdict of `ship`).
- 10 REPORT: this file.

Skipped by choice: none.

## Gates

Code gate, `uv run pytest` (declared in `.loop/adapter.json`):

```
2191 passed in 44.06s
```

Exit code 0. Verified independently by `loop-free-verify-code-1`.

Visual gate: not required (`visualRequired: never`, no UI files touched).

## Changes

Files created:

- `config/communities.yaml` (36 lines)
- `db/migrations/0008_source_expansion.sql` (42 lines)
- `src/product_hunter/communities.py` (225 lines)
- `src/product_hunter/source_policy.py` (237 lines)
- `src/product_hunter/source_expansion.py` (749 lines)
- `tests/test_communities.py` (185 lines)
- `tests/test_source_policy.py` (247 lines)
- `tests/test_source_expansion.py` (520 lines)

Files changed:

- `config/sources.yaml` (+55 / -1)
- `src/product_hunter/sources.py` (+294 / -13)
- `src/product_hunter/discovery/queries.py` (+44 / -4)
- `src/product_hunter/discovery/__init__.py` (+2 / 0)
- `src/product_hunter/collection/orchestrator.py` (+2 / 0)
- `src/product_hunter/db/store.py` (+1 / -1)
- `src/product_hunter/cli.py` (+102 / 0)
- `tests/test_sources.py` (+165 / 0)
- `tests/test_cli_db_status.py` (+4 / -2)
- `tests/test_db_migrations.py` (+4 / -2)
- `tests/test_discovery_queries.py` (restored to wave-2 content, no net change)

The registry still holds exactly three sources: `hn_algolia`, `github_issues`,
`blog_http`. `hn_algolia` and `github_issues` are `active` and permitted.
`blog_http` is `pending_review` and disabled. No source was enabled that was
not enabled before, and no terms status was set by the implementation.

## Harden findings

First pass (`loop-free-harden` on `step-5-preview-free`): 0 must, 2 should,
1 nice.

- Should: `communities.py` load path never invoked `validate_against`, so a
  community naming an unknown source loaded without an error.
- Should: `_registry_entry` hardcoded `schemes: ["https"]`, so an approval
  for an HTTP collector produced an admitted but unfetchable source.
- Nice: approval did not validate domains before writing the audit row.

All three were fixed in the harden fix round and re-verified:

- H1: `load_communities(path=None, *, registry=None)` now calls
  `validate_against(registry or load_registry())`; the CLI passes the
  registry it already holds.
- H2: `ReviewPolicy` carries `schemes` (default `["https"]`); the entry
  writes `list(policy.schemes)`.
- H3: `_registry_entry` became `_entry_dict` plus `_render_entry`;
  `approve_candidate` validates the entry with
  `SourcesFile(version=1, sources=[entry])` before any write, and on
  `ValidationError` returns `reason_code="invalid_entry"` with the first
  message as the detail.

Post-fix gate: 2191 passed in 44.06s.

## Review findings

Not available. Both review rungs failed to dispatch:

- `loop-free-review-1` (`step-5-preview-free`): upstream request failed,
  endpoint unavailable.
- `loop-free-review-2` (`longcat-2.5-preview-free`): model unavailable.

No verdict, no findings. Per the failure law ("If both fail, stop and report.
Never accept a degraded verdict."), the review was not retried and no
degraded verdict was substituted.

A SubQ Snap call was made at stage 7 to calibrate the severity weighting
before the review:

- Model: `subq-snap-preview` (1 successful call, 1 failed 422 on the first
  attempt with the wrong body shape, then the corrected call succeeded).
- `severity`: `should` (probability 0.692, confidence 0.538).
- `gate_sufficiency`: noul 0.429.
- Tokens: 568 input, 0 output. Cache hits: 0. Errors: 1 (422, fail-fast).
- Reading: a typical finding at this stage is a `Should`, not a `Must`,
  but the gate evidence alone is not sufficient, so the unverified items
  below carry real weight.

## Waivers

None. No mobile waiver, no desktop-only or design-only waiver, no prelaunch
waiver.

## Unverified items (risks the report must name)

- No collector ever ran against a populated store; the discovery pass
  `discover_candidates` has only been exercised in tests.
- The new CLI commands `source-candidates list` and
  `source-candidates discover` were exercised by hand against a scratch
  database, not by a test.
- `config/sources.yaml` is an adapter high-risk path and was edited by an
  agent. The registry still holds exactly three sources and no source was
  newly enabled, but the file is the source of truth and a human should
  read the diff before it is committed.
- `tests/test_discovery_queries.py` was edited once during the run and then
  restored byte for byte. The restoration is verified (`git diff --stat`
  shows no change). The wave-2 assertion that a candidate refuses to be
  built with `status="approved"` passes again.
- `docs/reasonix/ARCHITECTURE.md` was not updated in this run; the new
  tables `source_candidates` and `source_reviews` and the new modules
  `communities.py`, `source_policy.py` and `source_expansion.py` are not yet
  described there.
- The `enabled` field of `SourceConfig` is now derived from `status`. Every
  reader of `source.enabled` still works, and the shipped YAML sets
  `enabled` and `status` consistently, but a hand edit that contradicts
  them is refused at load.

## Policy assumptions

- The registry file stays hand-edited. The approval workflow returns the
  registry entry as pasteable text and never writes
  `config/sources.yaml`.
- `enabled` is derived from `status`. `enabled` is still accepted on input
  for compatibility; disagreement is refused.
- A discovered source is a proposal. Only a human-reviewed entry admits a
  source, and the terms status, allowed use, domains, collector, rate
  limit and robots policy all have to be present.
- Unknown and unreviewed sources fail closed, at load time and at admission
  time.
- The reliability tier is the one the scoring layer already uses
  (`tier_a`, `tier_b`, `tier_c`, `tier_d`, `unrated`) with the weights in
  `config/scoring_policy.yaml`. The registry derives it; it is never set.
- The community registry is a separate file because a community is a
  different entity from a source, and the database already has a
  `communities` table beside a `sources` table.
- The source type vocabulary changed: `code host` became `code_host`. Nothing
  reads the old spelling.
- The `source_candidates.status` column now holds the reviewer's decision
  (`proposed`, `approved`, `rejected`), not the candidate's own state. The
  candidate model's `status` field is always `proposed`; the decision lives
  in `SourceCandidate.decision`.

## Stopped because

STALLED node: the review stage could not be dispatched on either rung of
the free model chain (`loop-free-review-1` endpoint unavailable,
`loop-free-review-2` model unavailable). Per the failure law, both rungs
failed, so the run stopped rather than accepting a degraded verdict. The
commit gate requires a review verdict of `ship`; with no verdict, no commit
was made and no commit hash exists.

## Next

Retry the review node once the provider is available (re-enter stage 7),
or the operator reviews the diff manually and commits with explicit paths.
The changes are on branch `wave3/source-expansion` at base commit
`00a6ca3`, uncommitted. The declared radius is 23 files and all of them are
inside it. The code gate is green and the harden pass is clean.
