# Stage 4 IMPLEMENT — Phase 2 evaluation infrastructure

**Node:** `loop-free-implement-1`
**Date:** 2026-10-21
**Spec:** `docs/reasonix/specs/phase-2-evaluation-infrastructure.md`
**Architecture:** `docs/reasonix/plans/phase-2-evaluation-architecture.md`
**Plan:** `docs/reasonix/plans/phase-2-evaluation-infrastructure.md`

---

## The goal

Build the evaluation infrastructure so that two models, or two providers, or
two prompt versions can be compared against the same labelled cases and the
difference reported.

## What to read first

1. `docs/reasonix/specs/phase-2-evaluation-infrastructure.md` — the contract.
   Every field name, every metric name, and every behaviour in it is fixed.
2. `docs/reasonix/plans/phase-2-evaluation-architecture.md` — why the new
   package sits beside `evaluation.py` instead of inside it.
3. `docs/reasonix/plans/phase-2-evaluation-infrastructure.md` — the file-by-file
   steps, the public names, and the test list.
4. `src/product_hunter/evaluation.py`, `src/product_hunter/quote.py`,
   `src/product_hunter/normalization.py`, `src/product_hunter/cli.py` — the
   code you reuse and the code you must not break.

## The rules

**Do not break Phase 1.** `src/product_hunter/evaluation.py` is not in the
blast radius. The commands `ph eval`, `ph review-sheet`, `ph report`,
`ph extract`, `ph fetch`, `ph doctor`, `ph sources`, `ph version`, and the
whole `ph db` group keep their current arguments and their current behaviour.
The existing test suite must pass unchanged.

**Do not fabricate a result and do not fabricate a label.** The shipped
dataset `eval/datasets/ds-v1.json` carries real inputs and `null` in every
`expected` block. No label is ever written by you. A metric that has no
label is `null`, the task is `blocked`, and the report says so. Never derive
a precision, a recall, or a severity agreement from the model's own output
and present it as quality.

**Reuse before you write.** `quote.locate_quote`, `quote.canonicalize`,
`normalization.normalize_fields`, `normalization.Domain`, and
`config.repo_root` already do the work. Use them. A second definition of
what a verbatim quote is, or of what a normalized key is, would drift from
the pipeline.

**No new dependency.** `pydantic` and `PyYAML` are installed. Nothing else
is added to `pyproject.toml`.

**The result is deterministic.** No timestamp, no random value, no clock, no
network. The same dataset and the same outputs produce byte-identical result
JSON. A test proves it by running the scoring twice and comparing the bytes.

**Fail closed.** A malformed dataset, a malformed output, an unknown task, a
duplicate case id, an output for a case the dataset does not hold: each one
raises an error that names the file and the problem. Nothing is guessed and
nothing is skipped silently.

**Every model in the package sets `extra="forbid"`,** as everywhere else in
this repository.

## The blast radius

Touch only these paths:

```
src/product_hunter/eval/__init__.py
src/product_hunter/eval/cases.py
src/product_hunter/eval/tasks.py
src/product_hunter/eval/run.py
src/product_hunter/eval/report.py
src/product_hunter/cli.py
tests/test_eval_cases.py
tests/test_eval_tasks.py
tests/test_eval_run.py
tests/test_cli_bench.py
eval/datasets/ds-v1.json
eval/thresholds.yaml
eval/README.md
README.md
```

If the work needs a file outside this list, stop and report that instead of
touching it.

## The order

Follow the plan: `cases.py`, then `tasks.py`, then `run.py`, then
`report.py`, then the CLI group, then the dataset and the threshold file,
then the documentation. Each step is testable before the next one starts.

## The seed dataset

The inputs are real text taken from the pages this repository has already
captured, in `data/raw/` and `data/processed/text/`. Read them, pick short
passages that read naturally, and build about three cases per family. Every
`expected` block stays `null`.

For the grouping families, use the real documents that exist in
`data/processed/text/` and the real fetch records in `data/raw/fetches.jsonl`.
A `duplicate_detection` case lists the real `doc_id` and `text_sha256` pairs.
An `author_independence` case lists the real mentions with their real author
keys. A `clustering` case lists the real pain statements the Phase 1 runs
produced. A `competitor_identification` case describes a real product and
lists real candidate products. A `counter_evidence_classification` case
pairs a real claim with real evidence lines.

A human labels these later. The inputs must be labelable, so keep them short
and unambiguous about what is being asked.

## The text

`eval/README.md` and the new `README.md` section are prose. They follow the
repository's existing voice: plain, specific, no marketing language, no
"in today's", no rule-of-three padding, no binary contrasts. The style
guide is the `README.md` that is already in the repository — read it and
match it.

## The gate

`uv run pytest` must be green. Run it. If it fails, fix the code, never the
test. A test may only change if the spec is wrong, and then the spec changes
too and the change is reported.

## What to report back

- the files you created and the files you modified, with the line counts
- the verbatim tail of the `uv run pytest` output
- the names of the ten task families and the metric each one produces
- anything in the spec you could not build, and why
- anything you left out on purpose
- the one thing a reviewer should look at hardest
