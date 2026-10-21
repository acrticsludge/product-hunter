---
name: evidence-validation
description: Use when checking a run before its numbers are believed, or when deciding whether a candidate may be counted.
---

# Evidence validation

## The checklist, fail closed

A candidate is counted only when every check passes:

1. The answer parses as one JSON object holding at most the configured number of candidates.
2. The candidate validates against the strict schema, and its verdict, severity, and reason agree with the rubric.
3. The quote is at least the minimum length and at most the maximum length.
4. The quote resolves to a span in the document text, exactly or by the canonical fold.
5. The span is contained in the chunk the model was shown.
6. A genuine candidate carries the five normalized fields, and they build a key.

Any failed check rejects the candidate. There is no repair step and no partial store.

## Rejection codes

`quote_not_found`, `quote_too_short`, `quote_too_long`, `quote_outside_chunk`, `schema_invalid`, `invalid_json`, `prompt_too_large`, `provider_error`, `provider_check_failed`, `budget_exceeded`, `normalization_invalid`, plus the capture codes `raw_missing`, `raw_hash_mismatch`, `missing_raw_provenance`, `empty_document`, `parse_error`, and `unknown_source`. A broken artifact is reported with its file and line, so the run that wrote it can be found.

## The containment rule

`chunk.char_start <= span.char_start` and `span.char_end <= chunk.char_end`. Overlap is not containment. A quote that starts in the neighbouring chunk and runs into the shown one is evidence the model never saw, so it is rejected as `quote_outside_chunk`.

## The 100-row review sheet

`ph review-sheet --run RUN_ID --out PATH [--limit 100]` writes the model's verdict on the left and empty human columns on the right: `human_valid_pain` (yes, no, weak), `human_severity` (0 to 5), `human_frequency`, `human_normalization_ok`, and `human_normalized_key`. The accepted candidates take the room first, and the rest of the limit goes to the others.

`ph eval --labels PATH` scores the filled sheet and refuses a malformed row instead of guessing.

## The metrics

Quote fidelity (exact, canonical, failed, and the ok rate), precision, recall, severity exact and within one point, the normalization rate, the key agreement, the false-positive reasons, and the per-source counts. A candidate the model called genuine and the human did not confirm is a false positive.

## The recall limit

Recall is relative to the candidates the model returned. A pain point the model never proposed cannot be counted as a miss, so the number is not a corpus recall. Coverage is the share of sheet rows that carry a human verdict, and an unlabelled row counts for nothing.

## No number without labels

No precision, recall, severity, or normalization rate is claimed until a human has labelled the rows behind it. A run report counts artifacts and quotes; it does not measure model quality.
