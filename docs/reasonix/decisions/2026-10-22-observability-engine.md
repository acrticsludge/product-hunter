# Decision note — the measurement contract and the observability engine

**Date:** 2026-10-22
**Status:** accepted, implemented.
**Spec:** `docs/reasonix/specs/wave-3-observability.md`
**Plan:** `docs/reasonix/plans/wave-3-observability.md`

Three conventions came out of this change that later waves will reuse. They
are recorded here because each of them is easy to break by accident.

---

## 1. A number has three states, not two

A metric is `measured`, `not_measured` or `not_applicable`. A metric that is
not measured holds a null value and a reason that says what was missing.

The rule that decides the cases:

| The situation | The state |
|---|---|
| the rows exist and carry the field | `measured` |
| a count over a table that exists, even when it is empty | `measured`, and the value is `0` |
| a rate whose denominator is zero | `not_measured`, reason `no denominator` |
| the source table is not in the schema | `not_measured`, reason naming the table |
| the run does not exist | `not_measured`, reason naming the run |
| every row leaves the field null | `not_measured`, reason naming the field |

**Do not** collapse a missing number into a zero. A rate that reads `0`
because nothing happened says the opposite of what happened. A count that
reads `0` because the table is empty is a true measurement, and the two must
not look alike.

**Do not** estimate, impute or default a value the engine did not read. The
only number the benchmark can produce today without a human label is
`quote_fidelity.verbatim_rate`, which is checked against the source text.

---

## 2. The engine reads, it never writes

`collect_metrics()` and `collect_trace()` issue `SELECT` statements only. The
one writer in the package is `record_comparison()`, and it writes one table,
`model_comparisons`.

The metrics are computed on demand from the authoritative tables. There is no
metrics cache table, so there is no second source of truth that can go stale
while the first one moves.

A report holds no timestamp and no random value, so two reads of the same
database produce the same bytes. That is what makes a report file
committable and diffable.

**Do not** add a cache table, and **do not** put a clock in a report.

---

## 3. A comparison gets its own table, and the benchmark keeps two datasets

`research_events` cannot hold a model comparison. It requires a
`research_runs` foreign key, and a benchmark comparison is not a research
run. Writing one would mean inventing a run to hang the event on, which
would put a row in the run registry that never ran. So the comparison has its
own table, `model_comparisons`, in `db/migrations/0008_observability.sql`.

The file is numbered `0008` because the migration sequence has to stay
contiguous and `0007` was the last file on `main`. Six other Wave 3 branches
exist, and any of them may also have written a `0008`. The migration loader
refuses a duplicate number and refuses a gap, so an unreconciled merge fails
at `db init` rather than silently. The integrator renumbers, the same way the
Wave 2 integration renumbered four `0003` files to `0004` through `0007`.

**Do not** reuse `research_events` for a comparison, and **do not** create a
research run to hold one.

A number carries two versions. The dataset version says which cases were
scored, and the evaluator version says which rules scored them. Two runs are
comparable only when both match, so `compare_runs` refuses a pair that
differs in either and says which one differed. The evaluator version moved
from `eval-v1` to `eval-v2` in this wave because the metric set changed, and
a v1 number and a v2 number are not the same measurement.

Datasets are versioned artefacts and every version stays. `ds-v1.json` holds
the thirty seed cases and `ds-v2.json` holds those thirty plus three cases
for each of the two new families. A case id is stable across dataset
versions, and `case_version` says how many times a case itself has changed.
