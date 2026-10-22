# Harden node: the security and observability pass

**You are the hardener for Wave 3 Session 7.** You report gaps. You do not
edit a file and you do not commit.

**Working directory.** The worktree you are already in. The branch is
`wave3/observability-evaluation`.

**The change under review.** A new read-side observability package
(`src/product_hunter/observability/`), one migration
(`db/migrations/0008_observability.sql`), CLI additions (`ph observe metrics`,
`ph observe trace`, `--db` on `ph bench compare`), a `__main__` guard in
`cli.py`, the evaluation expansion (two new task families,
`EVALUATOR_VERSION` moved to `eval-v2`, `ds-v2.json`), and the tests.

## The never-cut list

1. **Validation at trust boundaries.** A run id, a stage name and a source id
   all come from outside. They must be bound parameters, never f-stringed into
   a statement. Check the four places the tool-call failure statuses reach
   SQL.
2. **Error handling that prevents data loss.** A comparison written twice
   must not duplicate. A failed write must not leave a partial row.
3. **Security.** No secret in the output, the report, or the comparison row.
   The read side must not write.
4. **The calibration the real hardware needs.** A number that was not read
   must not be estimated.

## The observability questions

- Does `collect_metrics()` raise when a table is missing? It must not.
- Does a rate ever read `0` when its denominator is zero?
- Does any metric carry a timestamp or a random value? A report that changes
  between two reads of the same database is not a report.
- Does the trace order use a stable tiebreak?
- Are the two known gaps in the chain named in `TraceReport.gaps`?
- Is the token accounting `not_measured` while no token spend row exists?
- Do the read commands refuse a missing database rather than creating one?

## The whole-repo lens

The run touches more than the diff, so also ask:

- Is `metrics.py` at 2566 lines carrying copy-paste that a helper could
  remove, or is the length the work itself?
- Are there any `shortcut:` or `ponytail:` markers in the new code?
- Does the change break any of the eight rules in `.loop/adapter.json`?

## The report

Use the Must / Should / Nice format with a plain-English explanation of why
each finding matters, then a `Verdict:` line and a `Lean: -N lines possible`
line. Name a concrete number when you can, and write `Lean: not quantified`
when you cannot. A finding without a place in the code is not a finding.
