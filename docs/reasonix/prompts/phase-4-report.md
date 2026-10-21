# Report prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-resume-01
**Node:** REPORT (stage 10)
**You are:** the scribe. You write the final run report. You do not change
code, and you do not change the documents that already exist.

## What this wave was

Phase 4 Wave 1 built the enforcement substrate for research quality: a typed
Research Contract, a gate that turns evidence into pass or fail with stable
violation codes, tool-call provenance records, a budget model, and
deterministic tests. The point of the wave is that the worker, not an agent
instruction, decides whether a research stage happened.

The implementation was already built and green when this run resumed. The run
re-hardened it, reviewed it nine times, fixed what the reviews found, closed
the gap between the code and the documents, and committed the result.

## The files that hold the record

Read these before you write anything:

- `docs/reasonix/reports/phase-4-research-contract.md` — the wave report.
  It holds the delivery, the gate evidence, what was found and fixed, the
  deliberate deferrals, and the known residuals. It is the backbone of what
  you write.
- `docs/reasonix/handoffs/phase-4-research-contract-handoff.md` — the handoff,
  with the run history and the drift inventory.
- `docs/reasonix/prompts/phase-4-implement.md` — the ten fix rounds, in
  order, each with what it changed and what it pinned.
- `docs/reasonix/prompts/phase-4-review.md` — the nine review passes.
- `docs/reasonix/prompts/phase-4-harden.md` — the two security passes.
- `docs/reasonix/specs/phase-4-research-contract.md` — the design of record,
  now in sync with the code.

## The gate evidence

Take it from the wave report, and confirm the number by running the gate
yourself:

```text
uv run pytest
```

Report the command, the pass count, and the exit code, verbatim. If the tree
is red in a file outside `src/product_hunter/research/` and
`tests/test_research_*.py`, that is another wave's work in a shared tree.
Say so plainly and report the research package's own count separately.

## What your report must contain

1. **What the wave delivered**, in plain words, for a reader who has not read
   the code.
2. **The gate evidence.** The command, the count, the exit code, and the
   research package's own count.
3. **What the reviews found.** The two security passes and the nine review
   passes, and the fail-open paths they closed. Name the class of each one,
   because the class is what a later wave should watch for. Do not list every
   test.
4. **What was deliberately left**, and why. The budget scope naming, the
   length bounds, the logging, the hand-built run result, the hand-built
   budget spec, the untrimmed family comparison, the lean findings.
5. **The known residuals**, stated plainly. No live research run has ever
   exercised this gate, every input is a test fixture, the budget limits are
   starting values and not measured ones, the package has no caller yet, the
   persistence protocols have no implementation, and the wave is not
   committed at the time you write.
6. **What the next wave inherits**, and what it must do first.
7. **The debts.** Every `shortcut:` or `ponytail:` comment in the research
   package, with its file and line. If there are none, say there are none.

## Rules

- Do not edit any file under `src/`, `config/`, or `tests/`.
- Do not edit the five documents listed above. Read them and report from them.
- Your report is a new file: `docs/reasonix/reports/phase-4-run-report.md`.
- Write prose a tired engineer can read in one pass. No filler, no "in
  today's fast-paced world", no binary contrasts, no rule-of-three lists, no
  em-dash as a connector. Say what is true and stop.
- Do not claim something is proven that you have not read. If the wave report
  says a test pins something, name the test. If it does not, do not say it.
- Report the residuals plainly. A report that oversells is worse than no
  report, and the next engineer will trust what you write.

## Definition of done

Report:

- the file you wrote, with its line count
- the gate output verbatim
- what you took from each of the six files you read
- what you did not check
