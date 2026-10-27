# Phase 1, Step 3 report

**Date:** 2026-10-20
**Scope:** the extraction command, the review sheet, and the evaluation command.

## What was built

- `src/product_hunter/cli.py`: three commands, `ph extract`, `ph review-sheet`,
  and `ph eval`. The four existing commands are unchanged.
- `src/product_hunter/extraction.py`: the parse-error reject path now records
  `response.extractor_version`, so a rejection carries the version the provider
  reported rather than the configured one. The quote-error reject and the call
  record already did this.
- `tests/test_cli_extract.py`: eleven tests over a temporary repository root, a
  fake provider, and no network.
- `README.md`: the three commands, the extraction rules, and the manual
  evaluation flow.

## Two corrections

**The provider is verified before extraction.** `extract_command` calls
`generator.check()` before any document text is sent. When the check raises a
`ProviderError` the command sets `error = "provider check failed: <detail>"`,
drops the generator, writes the manifest with zero calls, and exits 1. A dead
or mismatched provider now costs one failed run instead of one failed call per
chunk.

**A candidate quote must be inside the chunk the model was shown.** The quote
search falls back to the whole document when the quote is not found from the
chunk offset, so a model could quote a span it was never shown. After
`locate_quote` returns, the span has to be contained in
`[chunk.char_start, chunk.char_end)`: `chunk.char_start <= span.char_start`
and `span.char_end <= chunk.char_end`. A span outside it raises a
`QuoteError` with the reason code `quote_outside_chunk` and the detail
"quote is outside the chunk the model was shown", and the existing reject path
turns that into a rejection instead of a stored candidate. The code differs
from `quote_not_found`, which means the quote is not in the text at all.

## Review fixes

Three fixes came out of the review of this step.

**A quote must be contained in the chunk, not merely overlap it.** The first
version of the guard accepted a span that only overlapped the chunk, so a
quote that started in the neighbouring chunk and ran into the one the model
was shown was stored as evidence. The containment rule and the
`quote_outside_chunk` reason code are described above.

**The review sheet keeps the accepted candidates at a small limit.** The
selection gave half the room to the accepted candidates and half to the
others, so a limit of one held an unaccepted candidate and dropped the
accepted one. The accepted candidates now take the room first, up to the
limit, and the rest of the room is filled with the others. When the accepted
candidates alone fill the limit, the overflow is not included.

**A failed provider check leaves a rejection record.** The run used to print
the failure and write nothing, so a dead provider left no trace in the run
directory. The command now appends an `ExtractionReject` with the reason code
`provider_check_failed`, no document, fetch, or source id, the provider, the
model, both versions, and the timestamp to `rejects.jsonl` before the
artifacts are written.

## Strict schema

The model proposes; the code decides. A candidate is stored only when every
field validates against the schema and the quote resolves to a span in the
document text. The schema is strict (`extra="forbid"`, strict types) and the
answer for one chunk is a single JSON object with at most
`max_candidates_per_chunk` candidates. A JSON fence around the answer is
stripped; anything else that is not valid JSON is a parse error, and a parse
error is a rejection with a reason code, never a partial store.

## Pain validity rubric

| Validity | Severity | Reason |
|---|---|---|
| `genuine` | 2 to 5 | recurring manual work, time cost, money cost, error or delay, compliance or risk |
| `weak` | 1 or 2 | the same reasons, plus incidental |
| `not_pain` | 0 | negative opinion, question, feature request, informational, incidental, other |

The schema enforces the table, so a model answer that calls something genuine
with severity 0, or genuine with a reason that carries no cost, fails
validation instead of being stored. The prompt asks for a named actor, a named
workflow, and a concrete consequence, and the severity anchors run from 0 (none)
to 5 (daily hours, revenue loss, or legal risk).

## Quote rule

The model returns a quote, never offsets. The validator searches the exact
document text first, then a canonical form that folds curly quotes, dashes,
and the single-character whitespace variants. The fold is one character in and
one character out, so a match never shifts an offset. There is no NFKC
expansion and no whitespace collapsing. The match is recorded as `exact` or
`canonical`, with the offsets into the original text. A quote shorter than
`min_quote_chars`, longer than `max_quote_chars`, or absent from the text is
rejected with `quote_too_short`, `quote_too_long`, or `quote_not_found`. A
paraphrase never passes.

A quote that is in the document but not inside the chunk the model was shown
is rejected as `quote_outside_chunk`. The span has to be contained in the
chunk that was sent, so a candidate can never be backed by text the model
never read, and never by a quote that starts before the chunk.

## Deterministic ids

| Id | Basis |
|---|---|
| `doc_id` | `sha256(fetch_id, source tag, item id, parser version)` |
| `span_id` | `sha256(doc_id, char_start, char_end, quote)` |
| `candidate_id` | the span basis plus the extractor version |
| `pain_id` | the span id plus the extractor version, for accepted candidates only |

The ids do not depend on the wording of the model, so a replay over the same
bytes produces the same ids. The pain id is derived from the span, not from the
pain statement, so a paraphrase does not fork the record.

## Provenance

Every accepted candidate carries the provider, the model, the extractor and
prompt versions, the raw path, the raw SHA-256, the text SHA-256, the source
id, the fetch id, the URL, and the span offsets. Every rejection carries the
same versions plus a reason code and a detail. Each run writes
`manifest.json` with the run id, the start and completion timestamps, the
provider, the model, both versions, the limits, the counts (documents, chunks,
calls, candidates, accepted, rejected, failures), and the source ids.

## Evaluation harness

`ph review-sheet --run RUN_ID --out PATH` writes a CSV with the model's verdict
on the left and three empty human columns on the right: `human_valid_pain`
(yes, no, or weak), `human_severity` (0 to 5), and `human_frequency`. The
sheet holds the accepted candidates first, up to the limit, then the others in
the room that is left. When the accepted candidates alone fill the limit, the
overflow is not included.

`ph eval --labels PATH [--out PATH] [--quote-failures N]` reads the filled
sheet, refuses a malformed row instead of guessing, and reports quote fidelity,
precision, recall, severity agreement, the false-positive reasons, and the
per-source counts. `--quote-failures` adds the quotes the extractor never
produced, so the failure rate covers the rejected spans too. Recall is relative
to the candidates the model returned: a pain point the model never proposed is
not counted as a miss.

The 100-row manual set for the A1 acceptance gate is produced by this harness.
It has not been produced yet: the only runs so far are one-document smoke runs.
No human has labelled any row, so no precision, recall, or severity number
exists for the real corpus.

## Gate output

`uv run pytest`:

```text
........................................................................ [ 30%]
........................................................................ [ 61%]
........................................................................ [ 92%]
..................                                                       [100%]
234 passed in 1.13s
```

The gate was 229 passed before the two corrections in this step, and 231
before the review fixes. The three tests added with the review fixes are the
ones that fail when a fix is reverted: a quote that only overlaps the chunk,
a review sheet at a limit of one, and the cap on the accepted candidates. The
provider-check test gained the assertions on the rejection record, and the
fully-outside quote test now expects `quote_outside_chunk`.

`uv run ph --help` exits 0 and lists the seven commands.

`uv run ph extract --limit 1 --run-id gate-smoke` exits 0:

```text
{"run_id": "gate-smoke", "documents": 1, "chunks": 1, "calls": 1, "candidates": 0, "accepted": 0, "rejected": 0, "output_dir": "C:\\Anubhav\\Web Dev Projects\\product-hunter\\data\\processed\\gate-smoke"}
```

One document was parsed and one call was made, and the call reached the model:

```text
{"call_id":"call_0971ff88161297c6","doc_id":"doc_c689f82459e25a48","chunk_index":0,"provider":"opencode-go","model":"deepseek-v4.1-flash","extractor_version":"phase1-0.2","prompt_version":"extract-v2","input_chars":3194,"latency_ms":4385,"candidate_count":0,"accepted_count":0,"rejected_count":0,"status":"ok","error":null,"created_at":"2026-10-20T18:40:22.539007+00:00"}
```

The document was a Flask docs-link feature request: issue 6146 of
`pallets/flask`, asking for a link to the Cloudflare Workers Flask guide in
the "Hosting Platforms" section of the docs. The model answered
`{"candidates": []}`, so the run stored no candidate and no rejection, and the
command exited 0 because one call succeeded.

`uv run ph extract --limit 1 --run-id gate-smoke-2` repeats the run with the two
corrections in place and gives the same result: exit 0, one document, one call
with status `ok`, zero candidates. The call took about four seconds.

`uv run ph review-sheet --run gate-smoke --out data/processed/gate-smoke/review.csv`
exits 0 and writes a header-only sheet, because the run stored no candidate:

```text
{"ok": true, "run_id": "gate-smoke", "rows": 0, "path": "C:\\Anubhav\\Web Dev Projects\\product-hunter\\data\\processed\\gate-smoke\\review.csv"}
```

`uv run ph eval --labels data/processed/gate-smoke/review.csv` exits 0 and
reports an empty sheet: total 0, labelled 0, coverage 0.0, precision null,
recall null.

## The shim failure and the fix

The first live run failed closed, and the fault was not in the CLI, the prompt,
or the schema. The OpenCode CLI is installed as an npm shim, `opencode.cmd`,
which forwards its arguments with `%*`. On Windows a `.cmd` file is run by
`cmd.exe`, which re-parses the command line, so the
`<<<UNTRUSTED_SOURCE_START>>>` fence in the prompt was read as a redirection
operator. The failed call was recorded as
`"opencode api failed: << was unexpected at this time."`.

Verified outside the repo with the same argv-list call the provider uses:

```text
prompt "say pong"                                       rc 0  {"data":{"text":"pong"}}
prompt "<<<UNTRUSTED_SOURCE_START>>> ... <<<...>>>"     rc 1  << was unexpected at this time.
```

The same fenced prompt sent to the real binary,
`npm\node_modules\@opencode\cli\bin\opencode.exe`, returns
`{"data":{"text":"pong"}}` with exit code 0.

The fix is now part of this step, in `src/product_hunter/llm/opencode.py`.
`_resolve_cli` prefers the real `opencode.exe`, looking for it on PATH and then
inside the global npm install, and `generate` refuses to send a prompt through
a `.cmd` or `.bat` shim, because `cmd.exe` re-parses the command line. A plain
`api get` may still use the shim; a prompt may not. The live run now reaches
the model, as the `gate-smoke` call above shows.

## Known limits

- The live run returned zero candidates, so candidate storage is proven by the
  fixture and CLI tests rather than by a live corpus run. The one live document
  was a feature request, which the rubric classifies as not a pain point, and
  the model proposed nothing for it.
- Provider and model provenance is config-claimed. The OpenCode route does not
  echo the model that served a request, so the manifest reports the configured
  ids. `extract_command` now calls `generator.check()` before extraction, which
  proves the configured provider and model are present and active in the
  registry, but not that they served a given answer. A failed check is recorded
  in `rejects.jsonl` as `provider_check_failed`, so a run that never reached
  the model is visible in the run directory.
- The fetch log holds the same fetch id more than once when a URL was fetched
  again. The command keeps the first record of each fetch id, so a run over the
  whole log does not pay for the same document twice.
- A malformed line in `fetches.jsonl` stops the run. A corrupt log is a fault,
  not a partial input.
- The run id must match `[A-Za-z0-9._-]+` and must not contain `..`. The
  default is `ph-<UTC timestamp>-<4 hex>`.
- `documents.jsonl` stores the document text as well as the text hash, and the
  same text is also written under `data/processed/text/`.
- The 100-row manual set is not yet labelled, so the A1 gate is open.

## Next step

Run the 100-row acceptance set: capture 40 Hacker News comments, 40 GitHub
issues, and 20 blog sections, extract all of them, and hand-label 100 rows for
the A1 gate. The provider transport is no longer the blocker.

