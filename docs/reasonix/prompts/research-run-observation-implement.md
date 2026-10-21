# Implement prompt: research run observation

**RUN_ID:** robs-20261021-ea2759ad
**Node:** IMPLEMENT (stage 4), rung 1
**You are:** the implementer. Your radius is the two files named below and nothing else.

## The request being implemented

The user asked for two calls to the `product_hunter` tool and their outputs printed verbatim:

1. `action=list`
2. `action=status`, `research_id=RH-20261021-0003`

The orchestrator has already made both calls. The product code that serves them is in place. Your job is to establish whether any code change is required, and to keep the run's two documents factually true.

## What to do

1. Read `docs/reasonix/specs/research-run-observation.md` and `docs/reasonix/plans/research-run-observation.md`.
2. Establish, by reading the code and citing file:line, that `action=list` and `action=status` run SELECT statements only, and that they do not start, resume, or cancel anything. The chain to check: `.opencode/plugins/product-hunter.ts` (how the plugin dispatches) and `src/product_hunter/registry.py` (the `ACTIONS` tuple and the `execute()` dispatcher, and the `list` / `status` branches).
3. Establish that `RH-20261021-0003` is the right id format and that a `status` call for it reads the same row the `list` call renders. Cite the id generation and the render path.
4. State plainly whether the request needs any product code change. The expected answer is no, and you should say so if you find a counterexample.
5. Check the two documents against the code. Fix any factual error you find, in place, inside these two files only. If they are right, change nothing.

## Files you may edit

- `docs/reasonix/specs/research-run-observation.md`
- `docs/reasonix/plans/research-run-observation.md`

## Rules

- The working tree carries another agent's uncommitted work in `src/`, `tests/`, `config/`, and untracked `eval/`, `src/product_hunter/research/`, `src/product_hunter/db/scheduler.py`. Do not edit, stage, or commit any of it. Reading it for context is fine.
- Do not run `uv run pytest`. Stage 5 owns the code gate.
- Do not start a worker, and do not call the `product_hunter` tool with any action other than `list` or `status`. No `create`, `resume`, `continue`, `pause`, `retry`, `cancel`, or `log`.
- Do not commit.
- If you find that a code change really is required, say so in your report and do not make it. This run's radius has no source files in it.

## What to report

- What you verified, each with a file:line citation.
- The verdict on whether a code change is required.
- Every file you edited and what changed in it, or the statement that you changed nothing.
- Anything you could not verify.
