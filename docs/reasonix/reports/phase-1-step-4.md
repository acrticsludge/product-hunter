# Phase 1, Step 4 report

## What was built

Step 4 completes the Phase 1 normalization contract and makes run metrics
computable from the artifacts instead of from hand-written counts.

Files added or changed:

- `src/product_hunter/normalization.py`: domain vocabulary, slug rules, the
  versioned normalized key, and `normalization_invalid` rejection.
- `src/product_hunter/extraction.py`: normalized fields on the model candidate
  and on the stored candidate, schema enforcement, prompt block, and the
  fail-closed normalization reject path.
- `src/product_hunter/evaluation.py`: normalization columns and metrics in the
  review sheet and report.
- `src/product_hunter/reporting.py`: a run report computed from JSONL
  artifacts.
- `src/product_hunter/cli.py`: `ph report --run RUN_ID [--out PATH]`.
- `config/extraction.yaml`, `.env.example`, `src/product_hunter/config.py`:
  `extractor_version: phase1-0.3`, `prompt_version: extract-v3`,
  `normalization_version: norm-v1`.
- `tests/test_normalization.py`, `tests/test_reporting.py`, and updates to the
  extraction, evaluation, CLI, smoke, and LLM contract tests.
- `README.md` and the five project skills under `.opencode/skills/`.

## Normalized contract

A genuine candidate carries five controlled fields:

| Field | Cap | Content |
|---|---|---|
| `domain` | fixed vocabulary | the area of work |
| `actor` | 120 | the person or team doing the work |
| `action` | 120 | a short verb phrase |
| `object` | 120 | a short noun phrase |
| `consequence` | 200 | the concrete cost |

The domain vocabulary is fixed at 15 values: `software`, `data`, `operations`,
`finance`, `sales`, `marketing`, `support`, `hr`, `legal`, `security`,
`research`, `education`, `healthcare`, `ecommerce`, `other`.

A genuine candidate missing any field fails schema validation. A weak candidate
may leave the fields out. A not_pain candidate sets them to null.

`slugify` applies NFKC, lowercases, drops accents, replaces every run of
non-ASCII letters or digits with one dash, trims dashes, and caps the slug at
48 characters. A field whose slug is empty is rejected. The key is
`domain:slug(action):slug(object)`, capped at 200 characters, and it carries
`normalization_version: norm-v1`.

The 48-character cap is a collision limit. Two different long phrases whose
slugs share their first 48 characters produce the same key. The key is exact
grouping, not a unique identifier, and a phrase long enough to be cut has to
keep its meaning in the part that survives.

`pain_id` stays span-based. Normalization adds a grouping key; it does not
change the identity of a record. A model paraphrase therefore does not fork a
pain point, and a replay of the same span produces the same id.

The key is for exact grouping only. Two sources that describe one problem in
different words produce different keys, and semantic clustering is a later
phase. Model wording drift is a measured limit, not something the key hides.

## Prompt and schema

The extraction prompt is `extract-v3` at extractor version `phase1-0.3`. It
asks for the five normalized fields, lists the domain vocabulary, and requires
null for not_pain. The untrusted source fence and the pain-validity rubric are
unchanged.

A full 8,000-character chunk builds an 11,652-character prompt. With the input
cap at 12,000, that leaves 348 characters of headroom. The next prompt edit has
to account for that, or the chunk cap has to change deliberately.

A candidate that passes the quote check but cannot build a normalized key is
rejected with `normalization_invalid`. It is not stored without a key.

The stored record re-derives its own key. An accepted candidate whose
`normalized_key` does not match the key its own `domain`, `actor`, `action`,
`object`, and `consequence` make is refused, so a tampered key cannot be
stored or read back. An accepted candidate that is missing one of those five
fields is refused too, because it has no key to re-derive. The record stores
the `normalization_version` stripped, the way the key was built with it.

## Evaluation

The review sheet adds `model_domain`, `model_normalized_key`,
`human_normalization_ok`, and `human_normalized_key`. Older sheets without the
new columns read as blank rather than failing.

`ph eval` adds:

- `normalization_n`, `normalization_ok`, and `normalization_rate`
- `normalized_key_n`, `normalized_key_exact`, and `normalized_key_agreement`

The normalization rate counts only rows the model called genuine and the human
confirmed as pain. The key agreement counts the rows where the model and the
human both supplied a key; a row the model called weak or not_pain has no
model key, so it is left out of the count rather than counted as a
disagreement. Both are `None` when their denominator is zero, and the Markdown
says "not labelled" instead of showing a fake number.

Recall remains relative to the candidates the model returned. A pain point the
model never proposed is not counted as a miss, so this is not a corpus recall.

## Run report

`ph report --run RUN_ID [--out PATH]` reads the manifest, documents,
candidates, rejects, and calls for one run. Counts, quote modes, rejection
histograms, source totals, duplicate text hashes, and normalization keys are
computed from the artifacts. A missing manifest or a malformed JSONL line
fails closed with the file and line.

The command was exercised on the step 3 `final-gate` run:

```json
{"run_id": "final-gate", "provider": "opencode-go", "model": "deepseek-v4.1-flash", "extractor_version": "phase1-0.2", "prompt_version": "extract-v2", "normalization_version": "", "documents": 1, "chunks": 1, "calls": 1, "candidates": 0, "accepted": 0, "rejected": 0, "failures": 0, "quote_exact": 0, "quote_canonical": 0, "rejection_counts": {}, "source_counts": {"github_issues": {"documents": 1, "candidates": 0, "accepted": 0}}, "duplicate_text_hashes": 0, "normalization_keys": 0}
```

That run predates the normalization fields, so `normalization_version` is
empty. A run whose stored candidates predate `normalization_version` fails
when those candidates are read, because the field is required on a candidate
record. A run with no candidates reports an empty normalization version
instead of failing.

## Project skills

Five project skills now live under `.opencode/skills/`:

- `source-discovery`
- `pain-point-extraction`
- `pain-point-normalization`
- `evidence-provenance`
- `evidence-validation`

Each is a short rule file for the decisions that phase must keep consistent.
They are local to this repository.

## Gate output

`uv run pytest`:

```text
........................................................................ [ 23%]
........................................................................ [ 46%]
........................................................................ [ 69%]
........................................................................ [ 92%]
.........................                                                [100%]
313 passed in 1.61s
```

`uv run ph --help` lists `report` with the other commands. `uv run ph report
--run final-gate` exits 0 and prints the JSON above. `--out` writes the
Markdown report.

The normalization core alone was 278 passed before the evaluation and report
work landed. The final count is 313.

## Known limits

- The 100-row manual acceptance set is not labelled. The A1 gate is still open,
  and no precision, recall, severity, or normalization rate exists for the real
  corpus.
- No live document containing genuine pain has been extracted. The live runs
  used a feature request and an opinionated comment, both of which correctly
  produced no candidates. Candidate storage is proven by fixtures and CLI
  tests, not by a live corpus.
- The normalized key is exact-grouping only and depends on the model's wording.
  The 15 domain values are fixed until a deliberate vocabulary change and a new
  normalization version.
- A run whose stored candidates predate `normalization_version` fails when
  those candidates are read, rather than showing a partial normalization count.
  A run with no candidates reports an empty normalization version.
- The prompt has 348 characters of headroom at a full 8,000-character chunk.
- The report's source table covers sources seen in documents and candidates. A
  source that produced only rejections appears in the rejection histogram but
  not in the source table.
- `mcp-debug-emergency.log` remains untracked and is not part of the commit.

## Next step

The human-labelled acceptance run is the open Phase 1 gate. It needs a captured
corpus with genuine pain, 100 filled rows, and `ph eval` evidence. Phase 2
starts with the SQLite evidence core: schema, migrations, idempotent jobs,
checkpoints, leases and fencing, the transactional outbox, and recovery tests.
