# Wave 2 clustering - re-harden report

**Date:** 2026-10-21
**Node:** `loop-free-harden`, second pass (after fix round)
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave2-clustering`
**Branch:** `wave2/clustering`
**Spec:** `docs/reasonix/specs/wave-2-clustering.md` (section 2 rules, section 12 error taxonomy)
**Files in radius:** `db/migrations/0003_clustering.sql`, `src/product_hunter/db/clustering.py`, `tests/test_clustering_store.py`, `tests/test_db_migrations.py`

## Verdict

`ship`

All six first-pass findings are closed, both deliberate omissions are sound on their merits, the fixes opened no new finding, and the full gate is green (1065 passed, exit 0).

## 1. The six first-pass findings

### Must-fix (migration)

**1. Migration declared no foreign keys - CLOSED.**

All eight references are in `db/migrations/0003_clustering.sql`:

- `clusters.merged_into` ? `clusters(cluster_id)` (line 46)
- `clusters.split_from` ? `clusters(cluster_id)` (line 47)
- `cluster_versions.cluster_id` ? `clusters(cluster_id)` (line 58)
- `cluster_members.cluster_id` ? `clusters(cluster_id)` (line 79)
- `cluster_members.pain_id` ? `pain_points(pain_id)` (line 80)
- `cluster_lineage.from_cluster_id` ? `clusters(cluster_id)` (line 100)
- `cluster_lineage.to_cluster_id` ? `clusters(cluster_id)` (line 101)
- `cluster_decisions.run_id` ? `clustering_runs(run_id)` (line 113)

FKs are enforced because `PRAGMA foreign_keys = ON` is applied on every connection (`src/product_hunter/db/connection.py:35`), and each migration file runs in one `BEGIN IMMEDIATE`/`COMMIT` (`src/product_hunter/db/migrations.py:70-78`). `PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_keys` returns `1` after migrate (`tests/test_db_migrations.py:62-71`).

Write order satisfies the constraints in every path:
- `apply_result` records the run (`clustering.py:265`) before writing decisions (`clustering.py:301`), so `cluster_decisions.run_id` has a parent.
- `_insert_cluster` inserts the cluster row before `_write_version` (`clustering.py:484-512`), so `cluster_versions.cluster_id` has a parent.
- `merge_clusters` inserts the successor (`clustering.py:894`) before setting `merged_into` on the predecessors (`clustering.py:942`), and closes the predecessors' memberships before opening the successor's (`clustering.py:931-963`).
- `split_cluster` closes the parent's memberships (`clustering.py:1000-1004`) before opening the children's, so the partial index is never hit by a child open.

Tests: `test_a_membership_for_a_pain_point_the_store_does_not_hold_is_refused` (550), `test_a_membership_for_a_cluster_that_does_not_exist_is_refused` (666), `test_a_lineage_edge_for_a_cluster_that_does_not_exist_is_refused` (686), `test_a_decision_for_a_run_that_does_not_exist_is_refused` (702), `test_a_version_row_for_a_cluster_that_does_not_exist_is_refused` (720).

**2. Status column unconstrained - CLOSED.**

`CHECK (status IN ('active','merged','split','dissolved'))` added to `clusters.status` (line 37) and `cluster_versions.status` (line 61), matching the four `ClusterStatus` values (`src/product_hunter/clustering/clusters.py:21-27`).

Tests: `test_a_cluster_row_with_a_bad_status_is_refused` (618), `test_a_version_row_with_a_bad_status_is_refused` (634).

**3. No database backstop for one open membership per pain point - CLOSED.**

`CREATE UNIQUE INDEX idx_cluster_members_one_open ON cluster_members(pain_id) WHERE valid_to IS NULL` added (lines 95-96).

Write order satisfies it: `merge_clusters` closes each predecessor's open memberships before opening the successor's (931-946); `split_cluster` closes the parent's open memberships before opening the children's (1000-1004); `apply_result` runs `_close_moved` before any open (403-419) and `_close_members_of` only re-selects rows still open (463-477), so no membership is closed twice. The partial index holds everywhere: a pain point is opened in at most one cluster per apply (`_assignments_by_pain` rejects a pain point in two clusters, 358-363), merge/split dedupe members (`set(...)` at 884 and 986-988), and opens follow closes.

Tests: `test_a_second_open_membership_for_one_pain_point_is_refused` (576), `test_a_closed_membership_does_not_block_a_new_one` (594), `test_a_merge_leaves_the_predecessors_alone` (472), `test_a_split_leaves_the_parent_alone` (506).

### Should-fix (store)

**4. Adjudicator's confidence thrown away - CLOSED.**

`confidence REAL` column added to `cluster_decisions` (line 121). `_write_decisions` writes `decision.confidence` on every decision (`clustering.py:616`); held rows get `NULL` confidence (`clustering.py:~646`, column 10 of the held insert is a `NULL` literal).

Bounded and reaches the row: `ClusterDecision.confidence` is `Field(default=None, ge=0, le=1)` (`src/product_hunter/clustering/clusters.py:148`); the adjudicator's `Adjudication.confidence` is `Field(ge=0, le=1)` (`src/product_hunter/clustering/adjudication.py:42`); placement confidence is clamped `max(0.0, min(1.0, score))` (`src/product_hunter/clustering/engine.py:615`); a new cluster carries `1.0` (engine.py:312).

Test: `test_a_decision_row_carries_the_confidence_of_the_adjudication` (738) asserts `row["confidence"] == 0.75`.

**5. Held reason dropped - CLOSED.**

`detail TEXT` column added to `cluster_decisions` (line 123). `_write_held` writes `held.detail` into the `detail` column (`clustering.py:~658`, held insert value 8 ? column 12).

`detail` is never raw untrusted text. `held.detail` is only ever:
- a fixed phrase (`"no adjudicator was configured"`, `"the adjudication call budget is exhausted"`, `"the adjudicator could not tell the two apart"`),
- `type(exc).__name__` only - the exception type name, never its message (engine.py:435),
- `""` (cache hit or successful adjudication, engine.py:419, 438).

Decisions carry `detail = None` always (`clustering.py:618`), so no model answer and no quote reaches a row. This matches spec section 12: a persisted detail carries ids/counts/fixed phrases, never raw untrusted text.

Test: `test_a_held_row_carries_the_detail_and_no_confidence` (754) asserts `row["detail"] == "the adjudicator could not tell the two apart"` and `row["confidence"] is None`.

**6. `params_json` written with `default=str` - CLOSED.**

`default=str` removed. `_params_json` is now `json.dumps(run.params, sort_keys=True)` (`clustering.py:1205`); a non-serialisable value raises `ClusterStoreError` instead of being stringified (1206-1210). A failed write leaves no row (`ON CONFLICT(run_id) DO NOTHING` at 195, `store.run("run-1") is None` after the failure).

Test: `test_the_parameters_of_a_run_that_cannot_be_written_fail_the_write` (769).

## 2. The two deliberate omissions, judged on their merits

**Adjudicator's free-text reason and the text sent to the adjudicator are not persisted - AGREE, sound.**

Spec section 12: a persisted detail never carries the raw text of a quote or of a model answer; the adjudicator's reason is part of the model answer. The input is reconstructible without storing it: a decision row names `pain_a` and `pain_b`, and the canonical text of a pain point is a pure function of its row in `pain_points`. Nothing in the store writes the reason or the prompt text, so nothing can leak them. Confirmed: `cluster_decisions` has no `reason` column, decisions carry `detail = None`, and held rows carry only fixed phrases or exception type names.

**No foreign key on `cluster_decisions.cluster_id` - AGREE, sound.**

That column holds the engine's provisional cluster id, not the durable id the store allocates; a reference to `clusters(cluster_id)` would reject decision rows whose provisional id was not adopted, and decisions must stay writable even when a pain point has left the store (hence `pain_a`/`pain_b` are also deliberately unconstrained). The column stays free (migration line 118, no `REFERENCES` clause).

## 3. New findings opened by the fixes

None. An anchored review of `apply_result`, `merge_clusters`, `split_cluster`, the migration, and the new columns found no new constraint violation, no new write-order hazard, no new untrusted-text path, and no new silent-failure mode.

Note (not a finding): `split_cluster` does not validate that its `groups` are disjoint before inserting; if a caller passed the same pain point in two groups, the partial unique index would raise `sqlite3.IntegrityError` and roll back the transaction. The index is the correct backstop, and no caller in this wave passes overlapping groups (the store methods are exercised only by tests, which use disjoint groups). This is pre-existing behaviour, not introduced by the fix, and the database now catches it rather than silently duplicating an open membership.

## 4. Anything else still open in the wave

None.

Cross-checks on the fix round's scope:
- Idempotent apply: unchanged - decisions/held/versions use `ON CONFLICT ... DO NOTHING`, clusters skip when `stored == wanted` (310, 326 pass).
- Re-run / version bump: unaffected - membership change bumps the version; identical re-run changes nothing (340 passes).
- Merge / split: write only `clusters`, `cluster_versions`, `cluster_lineage`, `cluster_members`; none of the new columns are touched there (472, 506 pass).
- Evidence lineage: read-only, unaffected (537 passes).
- Space refusal: unchanged (`_refuse_another_space`, 326-340 pass).
- Observability: decision provenance is recoverable via `run_id` ? `clustering_runs` (`config_version`, `algorithm_version`, `space_id`, provider/model/versions); no additional gap beyond the two deliberate non-persistences above.
- No secrets, credentials, or MCP config in this wave's radius; nothing is logged or rendered from caller-supplied strings.

## 5. Gate

`uv run pytest` on the full tree: `1065 passed in 15.09s`, exit code 0. The clustering store and migration tests (`tests/test_clustering_store.py`, `tests/test_db_migrations.py`) pass: exit code 0, 42 tests.

## Bottom line

The fix round closed all six findings, did not introduce anything new, and both things it declined to do (persist the adjudicator reason/input; FK `cluster_decisions.cluster_id`) are the right calls. There is nothing left to fix in this wave.
