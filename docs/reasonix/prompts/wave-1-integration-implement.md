# Wave 1 integration — implementation dispatch

**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/wave-1-integration-spec.md`
**Plan:** `docs/reasonix/plans/wave-1-integration-plan.md`

This file records the dispatch that the implementation nodes received, so the
next reader can see what each node was told and can check its work against
the instruction rather than against the diff alone.

## Node 1 — the gate's persistence

**Agent:** `loop-free-implement-1`
**Radius:** `db/migrations/0003_research_gate.sql`,
`src/product_hunter/db/research_gate.py`,
`src/product_hunter/research/persistence.py` (docstring),
`tests/test_db_research_gate.py`, `tests/test_db_migrations.py`.

The instruction named the five tables, kept the column names the
`persistence.py` docstring dictated, and gave the exact public API:
`GateBlocked`, `SqliteToolCallStore`, `SqliteGateResultStore`,
`record_stage_execution`, `record_artifact`, `record_claim`,
`load_run_evidence`, `load_run_gate_result`, `evaluate_stage_gate`,
`evaluate_run_gate`, `evaluate_promotion_gate`, `require_promotable`.

It required the `GateBlocked` message to carry only ids, names, counts, and
fixed phrases, in line with the phase-4 untrusted-text rule. It required
parameter binding for every statement, and it forbade the node from editing
`db/__init__.py`, which a parallel node was holding.

The node added one name beyond the list, `load_run_gate_result`, and reported
why: without it there is no way to get a `RunGateResult` back out of the
database, and `evaluate_promotion` compares the run result's contract
fingerprint, so the fingerprint cannot survive the round trip without it.

The node also changed two constants in `tests/test_db_migrations.py` from `2`
to `3`, because adding migration `0003` moves the schema version and the
assertions stayed exact equality. The node reported the change rather than
making it quietly.

## Node 2 — the checkpoint fence

**Agent:** `loop-free-implement-2`, which failed with an expired provider key
and was re-dispatched to `loop-free-implement-1`.
**Radius:** `src/product_hunter/db/checkpoints.py`,
`tests/test_db_checkpoints.py`.

The instruction specified the rule: both parameters `None` is an unfenced
control-plane write, one of them is a programming error, and both present
means the write is refused unless the caller holds a live lease on a job for
that run and stage. It required the lease comparison to use the same time
source and convention that `db/jobs.py` already uses, and it required the
check and the insert to share one transaction.

It required the two refusal messages to be fixed text with no job id, worker
id, run id, or stage in them, and it required the lease check to run before
the "already exists" branch, so a stale worker cannot learn that a checkpoint
row exists.

## Node 3 — the query plan

**Agent:** `loop-free-implement-1`
**Radius:** `src/product_hunter/research/query_plan.py`,
`src/product_hunter/research/predicates.py`,
`src/product_hunter/research/__init__.py`, `config/research_contract.yaml`,
`tests/test_research_query_plan.py`, and one appended test in
`tests/test_research_gate.py`.

The instruction gave the ten families and the seven variants, required the
templates to be templates with a `{topic}` placeholder rather than finished
search strings, required the per-stage mandatory mapping, and required two
predicates.

It stated the untrusted-text rule for the second predicate explicitly: a
family string arrives on a tool call and is therefore data, so the reason must
report the count of unknown families and must not quote the offending
strings. The node implemented it that way and tested it that way.

The node made one design call the brief had left open, and reported it: the
mapping is non-empty for exactly the stages that declare the `search` tool,
so every searching stage must search the thesis, the negative, and the
alternative-to family. The brief had named only the three validation stages;
the node's rule is a superset and it flagged the difference rather than
deciding it silently.

## The review dispatch

**Agent:** `loop-free-review-1`
**Radius:** read-only. The whole change, then the fix diff.

The instruction asked the reviewer to try to prove the change wrong and
listed the eight architecture rules that had to hold, plus eleven named
attack surfaces: the stale worker, the fail-closed promotion, the round trip,
the reproducibility of a verdict, the idempotency, the new predicates, the
checkpoint fence, the migration, the duplicate implementations, the ponytail
lean, and the test quality.

The reviewer returned 2 Must-fix, 6 Should-fix, and 6 Nice-to-have findings.
Every one of them came with a scratch script under the temp directory, and
the two Must-fix ones were fail-opens that the suite did not catch.

## The fix dispatch

Two nodes, on disjoint file sets, one for the gate and the migration and one
for the predicates, the query plan, the contract, and the checkpoint. Each
was given the reviewer's exact finding, the failing sequence, and the fix.

The instruction repeated the rule about tests: never weaken, skip, or delete
an assertion. It named the one exception explicitly, the test whose
simulation mechanism had to change because the lease is now judged against the
store's clock, and it required the node to report that change.

Both nodes reported the change. The second node also verified its own fix by
mutation: it re-introduced the caller-supplied timestamp, watched both lease
tests fail, and reverted it.
