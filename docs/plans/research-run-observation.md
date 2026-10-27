# Research Run Observation — Plan

**Run id:** `robs-20261021-ea2759ad`
**Spec:** `docs/reasonix/specs/research-run-observation.md`
**Risk:** declared medium, floored medium by `.loop/adapter.json`.

## 1. Shape of the work

A read-only observation. The deliverable is two tool outputs and a paper trail. The whole run touches eight new files, all under `docs/reasonix/`, and no source or test file.

That fact drives the stage plan below. The stages that exist to inspect a diff have nothing to inspect, so they run as honest no-ops rather than being skipped silently, and each one says so in its own report.

## 2. Stage plan

| Stage | Executor | What it does here |
|---|---|---|
| 0 ORIENT | orchestrator | Read the tree, declare the radius, pull memory, ask SubQ for the risk and lane |
| 1 DEFINE | orchestrator | Write the spec |
| 2 ARCHITECT | skipped | No architecture decision to take; the tool contract is already fixed by the plugin and the registry |
| 3 PLAN | orchestrator | This file |
| 4 IMPLEMENT | `loop-free-implement-1` | Confirm no code change is required, and that the two calls are the correct read surface |
| 5 VERIFY | `loop-free-verify-code-1` | Run `uv run pytest` and report the result verbatim |
| 6 HARDEN | `loop-free-harden` | Security and observability pass over the read path the run touched |
| 7 REVIEW | `loop-free-review-1` | Adversarial read of the diff, scoped to this run's files |
| 8 PRODUCTIONIZE | orchestrator | Write the handoff, harvest shortcut markers, store the outcome in memory |
| 9 SHIP | `loop-free-scribe` | Commit the docs trail by explicit path |
| 10 REPORT | `loop-free-scribe` | Final report |

Stage 5b VISUAL does not run. No UI file changed and `.loop/adapter.json` sets `visualRequired: never`.

## 3. How the deliverable is protected

The two tool outputs are the one thing the user actually asked for, so the plan keeps them out of reach of every later stage. The outputs are captured in the stage 0 record, copied into the stage 10 report, and printed in the final reply. No node edits them, and the review node is told to check that the copies in the report match the originals byte for byte.

## 4. What could go wrong, and what happens then

| Risk | Response |
|---|---|
| The code gate fails on the other agent's uncommitted work | Report the failure verbatim, name the failing files as outside this radius, and keep the run's own verdict separate. Do not touch the test. |
| A node reports `unknown` instead of pass or fail | Treated as failure. Re-run on the other rung, and stop if the ladder is exhausted. |
| A node writes outside the declared radius | Stop the run and report the breach. |
| The dirty tree gets committed by accident | The stage 9 commit names this run's eight paths explicitly, so the 15 paths already in the index, four of them source and test files, stay where they are. `git add -A` is not used. |

## 5. Deliberately not done

No worker is started, because starting one would change the state this run observes and would reach past the request. No run is resumed. The `db/product_hunter.db` file is left alone. The uncommitted work from the other agent stays uncommitted and untouched, and the stage 9 commit names only this run's eight files.
