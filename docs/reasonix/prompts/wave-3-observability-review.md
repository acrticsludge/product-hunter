# Review node: the adversarial review

**You are the reviewer for Wave 3 Session 7.** You read the diff and try to
prove it wrong. You do not edit a file and you do not commit.

**Working directory.** The worktree you are already in. The branch is
`wave3/observability-evaluation`.

**The diff base** is `00a6ca3`, which is `main` as the wave forked from it.

## Read first

- `docs/reasonix/specs/wave-3-observability.md`, the contract
- `docs/reasonix/plans/wave-3-observability.md`, the plan
- `docs/reasonix/decisions/2026-10-22-observability-engine.md`, the conventions

## Then read the change

- `src/product_hunter/observability/metrics.py`, `trace.py`, `report.py`,
  `comparison.py`, `__init__.py`
- `db/migrations/0008_observability.sql`
- the CLI additions in `src/product_hunter/cli.py`
- `src/product_hunter/eval/cases.py`, `tasks.py`, `run.py`, `__init__.py`
- `eval/datasets/ds-v2.json`, `eval/thresholds.yaml`, `eval/README.md`
- the four `tests/test_observability_*.py` files and
  `tests/test_eval_expansion.py`
- the six modified test files, and what changed in each

## The rubric

Six passes, in order:

1. **correct** — does the logic do what the spec says?
2. **safe** — any injection, secret leak, unbounded read, write where a read
   was promised?
3. **load** — does it hold against a missing table, an empty database, a
   null row, a large table?
4. **tested** — do the tests check the contract, or do they check the
   implementation?
5. **fast** — any unbounded scan a bigger database would choke on?
6. **lean** — is every line needed?

## The questions worth asking

- The spec asked for `independent_author_yield` from the discovery payload.
  The record carries no author field. Did the code report that honestly, or
  did it invent a number?
- A version moved twice: the evaluator version and the schema version. Do the
  tests that changed carry the new literal, or did an assertion lose its
  teeth?
- Two new evaluation families: are the scorers pure functions of the case and
  the output, with no clock, no randomness, no network and no database?
- Every `expected` block in `ds-v2.json` must be null. Is any number written
  where a label does not exist?
- Does the engine ever read `0` where the answer is "not measured"?
- Is the migration additive, contiguous, and reversible in the way this
  repository means?

## The report

Must / Should / Nice, a plain-English explanation of why each finding
matters, then a `Verdict:` line and a `Lean: -N lines possible` line. A clean
diff is `Verdict: ship` and `Lean: 0`. Do not propose a fix you cannot point
at, and do not edit anything.
