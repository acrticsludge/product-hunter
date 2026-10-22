# Report prompt: Wave 2 evidence pipeline

**Agent.** `loop-free-scribe`
**Worktree.** `C:\Anubhav\Web Dev Projects\product-hunter-w2-evidence`
**Branch.** `wave2/evidence-pipeline`

You write the commit message and the final run report. You do not edit code,
you do not stage, and you do not commit. The orchestrator applies what you
write.

## Read first

- `docs/reasonix/reports/wave-2-evidence-pipeline.md`, the wave report. It is
  the source of the facts.
- `docs/reasonix/specs/wave-2-evidence-pipeline.md`
- `git diff --stat 3d7bc68` and `git status --porcelain`

The gate is green: `uv run pytest` -> `1020 passed`, exit 0. The suite was
980 tests at the base commit, and the wave added 40 in
`tests/test_evidence_pipeline.py`. The migration applies to version 3 on an
empty database, `PRAGMA foreign_key_check` is empty, `PRAGMA integrity_check`
is ok.

## Deliverable 1: the commit message

A subject line under 72 characters, in the imperative mood, and a body that
explains what changed and why. The repository's existing subjects look like
`phase2: fall through the model chain when a call fails` and `Phase 4 wave 1:
research contract and tool gate foundation`. Match that house style: a short
prefix, a colon, then the change.

The body must cover the new evidence pipeline library, the schema (the new
migration, the new tables, the new columns, the three integrity triggers),
the two derived facts, the change to the migration runner and why it was
unavoidable, the change to the Wave 1 import path, and the test count.

Do not list every file. Do not include a `Co-authored-by` trailer. Put the
message in a fenced code block so it can be copied verbatim.

## Deliverable 2: the final run report

In exactly this shape, with no extra sections:

```
GOAL: <one line>
TIER: free (models used per node in order)
RISK: declared=<t> floor=<t> (<categories>)
ADAPTER: <name> [<source>]
STAGES: <n>/11 complete, skipped: <list with reasons>
GATES: <the gate lines verbatim>
CHANGES: <files + line counts>
HARDEN: <verdict + findings>
REVIEW: <verdict + findings>
WAIVERS: <each reason, or "none">
STOPPED BECAUSE: <failure-law row, or "all pass and review ship">
NEXT: <next step, or "ready to commit">
```

Fill it in from what actually happened. The tiers, the risk, the gate lines,
the file counts, the harden findings, and the review findings all come from
the wave report. Do not soften anything, and do not invent facts that are
not in the report or the diff.

Report back with the two deliverables, and nothing else.
