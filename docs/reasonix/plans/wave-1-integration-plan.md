# Wave 1 integration plan

**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/wave-1-integration-spec.md`

This plan records what was actually done, in the order it was done, and why
each step was taken in that order. The order is adapted to the repository
state, not to the dependency order the original wave plan assumed.

## Step 0 — orient, before anything else

The first thing the run did was look. `git status` was clean, the branch was
`main`, the log held 30 commits across the two waves, and the worktree list
showed one worktree. The baseline suite was **980 passed**.

The run report of every wave was read, and the repository structure and the
specification were mapped. The result was the change map that drove
everything else.

### The dependency order, adapted

The assumed order was:

```
DB foundation -> recovery/fencing -> worker runtime -> scheduler
  -> model routing -> Research Contract / Tool Gate -> evaluation
  -> OpenCode interface -> documentation/tooling
```

The repository was already past most of it. The database foundation, the
recovery and fencing, the worker runtime, the scheduler, the model routing, the
evaluation, and the OpenCode interface all existed and were tested. What was
missing sat at two specific seams:

| Responsibility | State found | Action |
|---|---|---|
| SQLite authority | Built and tested | Keep |
| Recovery and fencing | Built for jobs; **checkpoints were unfenced** | Fence the checkpoint |
| Worker runtime | Built; **no stage handler exists** | Keep; the handler is the next wave |
| Scheduler | Built; nothing runs it | Keep |
| Model routing | Built and configurable; **the README claimed it did not drive extraction** | Correct the document |
| Research Contract and Tool Gate | A complete library with **no persistence and no caller** | Give it a home and a fail-closed primitive |
| Query families | Free-form strings a model could invent | Make them worker-owned |
| Evaluation | Built | Keep |
| OpenCode interface | Built | Keep |
| Documentation | Describes a state the code had left | Rewrite against the code |

## Step 1 — reconcile the working tree

Two findings from the orientation changed the shape of the run.

**The Wave 2 sessions were writing into the shared working tree.** While the
run was in progress, a Wave 2 session checked out `wave2/clustering` in the
main directory and began writing `db/migrations/0004_clustering.sql`,
`src/product_hunter/clustering/`, `src/product_hunter/db/clustering.py`, and
`config/clustering.yaml` into it.

The instruction was to stay on `main`. The main directory was not on `main`
and was being dirtied by another session, so the run created a dedicated
worktree at `product-hunter-main` on the `main` branch, moved the session
into it, and copied only its own files across. The other session's files were
left exactly where they were. `main` was free at commit `3d7bc68`, so the
branch pointer did not move and the other session's work was never at risk.

**The gates were green but the review was not.** The first review found two
fail-open bugs that a passing suite did not catch, which is the argument for
running the review at all.

## Step 2 — close the three gaps

Three implementation nodes ran, each on a disjoint file set.

### Gap A — the gate had no persistence and no caller

`src/product_hunter/research/` was a complete, well-tested library that
nothing outside the package imported. The package's own docstring said the
migration was "a plan in a docstring and the file does not exist".

The fix: migration `0003_research_gate.sql` with the five tables the gate
needs, and `src/product_hunter/db/research_gate.py` with the SQLite authority
for them — the two stores, the evidence loader, the gate evaluators, and
`require_promotable()`, which raises `GateBlocked` when a run may not be
promoted.

The module lives in `db/` rather than `research/` because `research/` is
deliberately a pure-data package that never touches a database, and `db/` is
where every other SQLite access lives. The dependency runs one way: `db`
imports `research`, never the reverse.

### Gap B — a checkpoint was not fenced

Every job write was fenced by the lease epoch, but `save_checkpoint()` was
not. A worker that had lost its lease could still write a checkpoint over a
newer worker's state.

The fix: optional `worker_id` and `lease_epoch` on `save_checkpoint()`, a
refusal unless the caller holds a live lease on a job for that run and stage,
and the check and the write sharing one transaction. A write with neither
parameter is the control plane and stays unfenced, which keeps the registry
caller working.

### Gap C — the query families were the model's to invent

The family name on a tool call was a free-form string, and the only check
counted distinct strings. A stage that ran four searches with four invented
names passed, and nothing required the negative or alternative-to families
that spec §7A calls mandatory.

The fix: `src/product_hunter/research/query_plan.py` owns the ten families,
the eight controlled wording variants, the mandatory templates, and the
per-stage mapping. Two new gate predicates enforce it, and the shipped
contract asks for both on every stage that searches.

## Step 3 — review, and fix what it found

The adversarial review ran twice. Round 1 read the whole change and returned
**2 Must-fix, 6 Should-fix, and 6 Nice-to-have** findings, each with a scratch
script that demonstrated it. The two Must-fix findings were the ones that
mattered:

- The database gate never evaluated the contract budget, so a run that made
  2027 searches against a limit of 160 was promoted.
- The evidence round trip merged every attempt's evidence into the latest
  attempt, so a stage that re-ran and produced nothing was judged as if it
  had done the earlier attempt's work. A verdict failed in memory and passed
  after the round trip.

Both were fail-opens, and neither showed up in the test suite.

Round 2 checked that each closure actually held, by re-running the round-1
attacks. It confirmed eight of the fourteen as closed and found four
residuals, plus one new Must-fix:

- **The run-level verdict was lost across the round trip.** `evaluate_run()`
  produces run-level violations with no stage, and the run-level floor is what
  the promotion check reads. The rebuilt verdict was built from the per-stage
  rows only, so a run that failed the run-level floor was promoted once its
  verdict came back from the database. The schema already allowed the
  run-level row the code never wrote.

Round 2 also found that a contract change permanently bricked a run, that the
gate verdict write path took no lease fence, that `model_calls` was the sum of
two overlapping sources, and that the attempt was not required on the call and
artifact write path. A second fix round closed all of them, plus four
nice-to-haves and two de-duplications the de-duplication pass had missed.

The lesson both rounds taught is the same one: **a green suite is not evidence
that a fail-closed path is closed.** Both Must-fix findings were invisible to
the tests, and one of them was invisible twice — the round-1 suite stayed
green with the run-level floor silently dropped until a reviewer wrote a
script for it.

## Step 4 — document against the code

The documentation described a state the code had left behind. Three claims
were wrong and were corrected against the code rather than from memory:

- the README said the model router "does not drive extraction yet", which
  `cli.py` contradicted,
- the architecture document said the router routes only the `ph models`
  command, which was also wrong,
- the architecture document recorded a live hazard that `.gitignore` hides
  `src/product_hunter/runtime/`. The pattern is `/runtime/`, anchored to the
  repository root, and all six files of the package are tracked. The hazard
  was an artefact of reading the pattern without the slash.

The specification checklist was written from the code, with one correction to
the draft: `opencode.json` is tracked and declares three MCP servers, and the
extraction severity scale of spec §13 is implemented.

## Step 5 — commit and verify

The integration commit lands on `main` with an explicit path list. Nothing is
pushed. The final verification runs the full suite from the committed state
and re-checks the CLI, the database initialisation, the worker lifecycle, the
recovery behaviour, the model routing configuration, the gate validation, and
the secret scan.
