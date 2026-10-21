---
name: pain-point-extraction
description: Use when extracting candidates from a document, or when deciding whether a candidate is a real pain point.
---

# Pain point extraction

The model proposes; the code decides. A candidate is stored only when the schema accepts it and its quote resolves to a span inside the chunk the model was shown. Everything else becomes a rejection with a stable reason code.

## The three verdicts

| Verdict | Severity | Reason must be one of |
|---|---|---|
| `genuine` | 2 to 5 | `recurring_manual_work`, `time_cost`, `money_cost`, `error_or_delay`, `compliance_or_risk` |
| `weak` | 1 or 2 | the same five, plus `incidental` |
| `not_pain` | 0 | `negative_opinion`, `question`, `feature_request`, `informational`, `incidental`, `other` |

The schema enforces the table, so a `genuine` candidate with severity 0, or one whose reason carries no cost, fails validation instead of being stored.

## The actor, workflow, consequence test

A genuine candidate names three things: the actor who does the work, the workflow or task they do, and the concrete consequence they pay (time lost, money lost, a repeated error, a delay, manual work, or a risk). Missing any one of the three means it is not genuine, however strongly the text complains.

## What never counts

- generic negativity or insults with no workflow and no consequence
- a question with no stated problem
- a feature request or a wish with no current cost
- informational or descriptive text
- a one-off incidental annoyance
- jokes, meta commentary, and political remarks

Do not infer a pain from a negative tone, and do not turn a frustrated feature request into a pain.

## The quote

The model returns a quote, never offsets. The quote must be an exact substring of the text between the untrusted markers, at least 20 characters and at most 2000. No paraphrase, no ellipsis, no retyping, no reordering. One quote per candidate, and that quote must carry the actor, the workflow, and the consequence.

The validator searches the exact text first, then a canonical form that folds curly quotes, dashes, and the whitespace variants. The fold is one character in and one character out, so offsets never shift. A paraphrase never passes.

The span must be contained in the chunk the model was shown: `chunk.char_start <= span.char_start` and `span.char_end <= chunk.char_end`. A quote that exists elsewhere in the document is rejected as `quote_outside_chunk`.

## Severity anchors

0 none. 1 minor or incidental. 2 occasional real cost. 3 recurring manual work with a meaningful time cost. 4 several hours a week, an expensive workaround, or a repeated operational failure. 5 daily hours, revenue loss, compliance or legal risk, or safety risk.

## Fail closed

No candidate is stored on the model's word. The rejection codes are `quote_not_found`, `quote_too_short`, `quote_too_long`, `quote_outside_chunk`, `schema_invalid`, `invalid_json`, `prompt_too_large`, `provider_error`, `provider_check_failed`, `budget_exceeded`, and `normalization_invalid`. A parse error rejects the whole answer, never a partial store. Every rejection lands in `rejects.jsonl` with the versions that produced it.
