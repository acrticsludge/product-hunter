# Research Run Observation — Definition

**Run id:** `robs-20261021-ea2759ad`
**Date:** 2026-10-21
**Lane:** tooling and observability. No UI file and no API surface is touched, so no `DESIGN.md` is required and no frontend or backend skill set applies.
**Source of truth:** the `product_hunter` tool surface (`.opencode/plugins/product-hunter.ts`, which shells out to `python -m product_hunter.registry`) and the durable state in `db/product_hunter.db`, table `research_runs`.
**Risk:** declared medium. `.loop/adapter.json` sets `floorRisk: medium` and no adapter may lower it. SubQ Snap advised `low` (P(low) = 0.95, confidence 0.90); the floor wins, so the run is recorded at medium.

## 1. The request

Two calls to the `product_hunter` tool, then the two outputs printed verbatim:

1. `action=list`
2. `action=status`, `research_id=RH-20261021-0003`

Nothing else is asked for.

## 2. In scope

- The two tool calls, in that order.
- The two outputs reproduced without paraphrase, trimming, reordering, or added annotation inside the output blocks.
- A docs trail under `docs/reasonix/` so the run leaves evidence behind, per the loop contract.

## 3. Out of scope

- Product code. No file under `src/`, `tests/`, `config/`, or `db/` is edited.
- Starting a worker, resuming, pausing, retrying, or cancelling any run. The tool never executes a research stage. It does not record these two reads either: a successful `list` or `status` leaves no event row and no log line, so the read trail is empty. Only a refused command against a named run writes an event, and neither action reaches that path.
- Any write to `db/product_hunter.db`. Both branches run SELECTs only, but the registry opens the file read-write rather than with `read_only=True`, so a stray write is not structurally impossible. `read_only` exists in `connect()` (`src/product_hunter/db/connection.py:9,21`) and is simply not used here.
- The uncommitted work already sitting in the tree from another agent: `README.md`, `config/model_routing.yaml`, `src/product_hunter/cli.py`, `config.py`, `db/`, `llm/`, `tests/test_cli_extract.py`, `tests/test_model_routing.py`, and the untracked `eval/`, `research/`, `scheduler.py` and new test files. The index is not clean either: it held two phase 2 documents at stage 0 and held 15 paths by the time of writing, four of them source and test files, so a bare `git commit` would sweep another agent's work into this run's commit. The full list is in the handoff. This run does not read that work as its own output, does not touch it, and does not commit it.

## 4. Acceptance criteria

1. Both tool outputs appear in the final reply, verbatim, in call order.
2. The repository code gate (`uv run pytest`, from `.loop/adapter.json`) runs in this session and passes.
3. Every file this run writes sits inside the radius declared at stage 0.
4. The final report states what was skipped and what was not checked.

## 5. Reading of the observed state

The facts below were verified against the code, not inferred from the tool output.

`list` and `status` are reads. The plugin spawns nothing but the registry (`.opencode/plugins/product-hunter.ts:101-105`), and each of the two branches runs SELECTs only: `list` calls `list_runs()` (`src/product_hunter/registry.py:1036`, function at `:325`) and `status` calls `get_run()` (`:1089`, function at `:340`). Both build the view from `_RUN_SQL` (`:232-241`) through `_view()` (`:265-287`). The writing functions, `create_run()`, `continue_run()`, `resume_run()`, `pause_run()`, `retry_run()` and `cancel_run()`, are reached from the other branches only (`:1016-1143`), and the registry starts no worker. A refused command leaves a `RESEARCH_REQUEST_REFUSED` event (`:917-956`, event written at `:944`), but only for an id that names a run, and neither action reaches that with one. The public id is `RH-YYYYMMDD-NNNN` (`:432-442`), so `RH-20261021-0003` is a well formed id, and both actions read the same row of `research_runs` through `_RUN_SQL`. Line numbers were checked against HEAD `39d81b9`; another agent is committing to this repository while the run is live, so the function names are the durable part of each citation.

A run that is `QUEUED` at stage `PLAN` with 0% progress is the normal state of a newly created run, not a stalled one. `create_run()` inserts exactly that row (`src/product_hunter/registry.py:526-536`), and `tests/test_registry.py:117-145` asserts `status == "QUEUED"`, `current_stage == "PLAN"`, `progress_percent == 0`, `worker_active is False`.

`worker: not running` means no job row for that run is `leased` with an unexpired lease (`src/product_hunter/registry.py:251-262`). The worker entry point is `python -m product_hunter.runtime`, and it takes its handlers from `PH_WORKER_HANDLERS` as `module:attribute` (`src/product_hunter/runtime/__main__.py:41`). With no handler registered it starts idle and claims nothing, which `README.md:194-198` states outright. So this run sits queued because nothing is wired to execute its stages yet, and it will stay that way until a handler is registered.
