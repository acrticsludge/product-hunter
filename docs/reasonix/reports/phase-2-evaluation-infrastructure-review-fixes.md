GOAL: close the findings the stage-6 and stage-7 passes raised against the
benchmark harness that landed in `9455deb`, correct the three stale lines in
`README.md`, and land the result under a message that names the change
without pushing.

TIER: free. Nodes, in order: loop-free-gather (longcat-2.5-preview-free, the
tree and the API surface); loop-free-implement-1 (longcat-2.5-preview-free,
the threshold-gate fix and the four smaller fixes); loop-free-verify-code-1
(the session's own model, minimax-m3.1-flash-free, the code gate, twice);
loop-free-harden (step-5-preview, twice); loop-free-review-1 (beam-free, and
longcat-2.5-preview-free in parallel).

RISK: declared=medium floor=medium. No auth, no payment, no secret, no
database schema change, no deploy config. The trust boundary is a set of
files another program can write: the dataset, the model outputs, and the
threshold file.

ADAPTER: Product Hunter [.loop/adapter.json], 8 forbidden rules in force, 16
files declared at stage 0. The radius was re-declared once, when the
documentation tree moved from `docs/resonix/` to `docs/reasonix/` underneath
this run.

STAGES: 11/11 complete, skipped: none.

GATES:
uv run pytest                                                   946 passed, 6 failed
uv run pytest tests/test_eval_cases.py tests/test_eval_run.py \
  tests/test_eval_tasks.py tests/test_cli_bench.py               190 passed
The commit tree, which is HEAD plus this run's six files in a clean
worktree, reads 821 passed and 0 failed.

The 6 failures are not in this commit. They are in
`tests/test_research_contract.py` and `tests/test_research_gate.py`, which
belong to the research-contract wave and are untracked, in-flight files:
`src/product_hunter/research/`, `config/research_contract.yaml`,
`tests/test_research_contract.py` and `tests/test_research_gate.py` are all
`??` in the working tree, so they are in no commit, and the commit tree does
not contain them. They are: a NaN budget limit not treated as a contract
error, a YAML syntax error escaping as a raw `yaml.parser.ParserError` rather
than a `ContractError`, a one-shot claim iterator read as a promotion, a
stage with no end timestamp not skipping its timeout, and two whitespace
cases counting three sources and three queries where one was meant. The
orchestrator confirmed the commit tree is green in isolation; the fix for
those six belongs to the wave that owns the files.

CHANGES:
src/product_hunter/eval/report.py                                  4 +-
src/product_hunter/eval/run.py                                    3 +-
src/product_hunter/cli.py                                        27 +
tests/test_cli_bench.py                                          54 +
tests/test_eval_run.py                                           36 +
README.md                                                      23 +-
docs/reasonix/reports/phase-2-evaluation-infrastructure-review-fixes.md   (this file)
docs/reasonix/handoffs/phase-2-evaluation-infrastructure-review-fixes-handoff.md

`src/product_hunter/cli.py` and `README.md` are shared with other waves and
are partial-staged. `cli.py` carries only the `ph bench` compare change, and
`README.md` only the four corrections below. The other waves' hunks in both
files stay uncommitted.

HARDEN: verdict on the re-run — "the `must` and every `should` are confirmed
fixed, no regression opened".

The first pass found one `must` and eight `should` items. The `must` was
reproduced by the orchestrator before it was fixed: a thresholds file could
gate a run on a number the model under test produced, because
`check_thresholds` treated every limit as a floor and accepted any metric
name some family produces. A labelled clustering case whose correct answer
was one group of three items, answered by a model that emitted three
singleton groups, with `tasks: { clustering: { min_group_count: 3 } }`
reported `passed: true` and exit 0. The module's own comment already said the
rule — "a count never moves the verdict" — and `compare_runs` honoured it
while the threshold gate did not.

FIXED in two layers, because the gate is the authority and the loader is the
early warning. The gate now evaluates only the metrics in `_HIGHER_IS_BETTER`,
a label-derived rate where higher is better, and a run whose limits are all
elsewhere ends as `passed is None`, not as a pass. The loader now refuses a
threshold key naming anything else, with an `EvalError` that names the metric
and the rule. The shipped `eval/thresholds.yaml` was checked and every key
in it is gateable, so the file is unchanged.

Also fixed: a deeply nested dataset or outputs file raised `RecursionError`
and escaped as a traceback instead of the `{"ok": false, "error": ...}`
envelope `eval/README.md` promises; a thresholds path that existed but was not
a readable file was probed with `is_file()` and so fell into the absent
branch, silently disabling every gate while exiting 0; and the report printed
"Every task of the dataset was measured" above a table of `n/a` values when
every case of a task had errored.

REVIEW: verdict — "ship".

Three `should` items and three `nice` items. FIXED: the report claimed a task
was measured when the dataset held no task at all; `Thresholds.tolerance` was
an unvalidated float, and a negative tolerance silently masked every
regression in `compare_runs`; and `eval/README.md` promised that "the JSON
always carries the reason" while the compare payload had no `reason` field,
so the promise was false for that command. Also fixed: the root `README.md`
listed `ph bench labels --cases PATH` without the `--out PATH` the command
also accepts, and `eval/README.md` already listed it.

Three `nice` items were left open, deliberately, because each would change a
measurement or a contract that this wave does not own:
- `score_normalization` compares `domain` with exact equality while the other
  four fields strip whitespace, so a padded domain is scored wrong while a
  padded actor is not.
- The dataset carries public Reddit handles in `author_id`. They are not
  credentials, but a person should decide whether they are acceptable to
  ship.
- The `eval/thresholds.yaml` comment says "every threshold behind quote
  fidelity is blocked", which is wrong, because `defaults.min_verbatim_rate`
  gates quote fidelity and quote fidelity is label-free and never blocked.

Five `should` items from the first harden pass were also left open and are
recorded here so they are not lost:
- bench paths are not contained, and the three writers truncate in place
  rather than writing atomically
- absolute paths are embedded in result files, so a degraded run is not
  byte-identical across two checkouts
- `ThresholdReport` records no provenance for the thresholds that produced
  `passed`
- `spec.outputs` resolves against a different root than every other path
- the `not_comparable` and `improvement` reason strings were not exercised
  against a live command

WAIVERS: two.

The first is a slip, recorded rather than hidden. The model table for this
loop names `step-5-preview-free`, and no such model is published. The nearest
published id is `step-5-preview`, and it is metered, not free: the provider
reports input 1 and output 2.7 per unit. One complete harden pass ran on it
before the price was checked, and three later review attempts on metered
models (`step-5-preview`, `deepseek-v4.1-flash`, `minimax-m3`) each failed
immediately on "Go usage limit exceeded" and produced no work. Only three
ids are genuinely free: `longcat-2.5-preview-free`, `minimax-m3.1-flash-free`
and `beam-free`. Every node after that point ran on a free id, and the
remaining dispatches stopped overriding the model so the configured selector
applies. The cost was one harden pass.

The second is a declined fix: repository-root containment for the bench path
arguments was not added, for the same reason the feature wave declined it.
The paths come from the operator's own command line, and the Phase 1 commands
set the same precedent.

STOPPED BECAUSE: the commit tree is green, the re-harden confirmed the `must`
is closed with no regression, and the review says ship.

NEXT: a person fills the `expected` blocks in `eval/datasets/ds-v1.json`. The
A1 human-labelled acceptance gate stays blocked until they do, and no code
writes a label. The six failures in the research-contract wave's untracked
files belong to that wave. The open `nice` and `should` items above are
unowned; they want a wave that owns the measurement contract.

## The three stale README lines

`README.md` claimed three things the tree no longer supported.

1. "Five skills under `.opencode/skills/`", with five names. There are ten:
   `entity-resolution`, `evidence-provenance`, `evidence-validation`,
   `frequency-analysis`, `pain-point-extraction`,
   `pain-point-normalization`, `problem-clustering`, `source-discovery`,
   `source-independence`, `source-reliability`.
2. "That route is a Phase 1 bridge behind the `TextGenerator` protocol: Phase 2
   swaps in a direct provider adapter, and the extraction logic does not
   change." Phase 2 did not swap in a direct provider adapter. It added a
   model router, and the router does not drive extraction yet:
   `OpenCodeGenerateProvider` still calls the model pinned in
   `config/extraction.yaml`.
3. The command list did not mention `ph db` at all, though the group has been
   there since Phase 2 step 1: `init`, `status`, `import-run`, `recover`,
   `jobs`, `outbox`, `check`.

## What was skipped and not checked

No live `ph bench run` against a real model. No `ph bench` run against a
read-only filesystem, a network path, or a long path on Windows. The real
accuracy of the 30 dataset inputs was not checked, only their shape, their
task distribution, and that they carry no credential. The
`not_comparable` and `improvement` reason strings were reasoned about but not
exercised. No type checker and no linter is configured in this repository, so
none was run. The byte-identical claim was checked at the model level, not
through the CLI with two checkouts at different paths.
