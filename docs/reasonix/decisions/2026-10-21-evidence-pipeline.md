# Decision note — the evidence pipeline

**Date:** 2026-10-21
**Status:** accepted, implemented.
**Spec:** `docs/reasonix/specs/wave-2-evidence-pipeline.md`
**Plan:** `docs/reasonix/plans/wave-2-evidence-pipeline.md`
**Report:** `docs/reasonix/reports/wave-2-evidence-pipeline.md`

Six conventions came out of the evidence pipeline that later phases will
reuse. They are recorded here because each one is easy to break by
accident, and because two of them exist only to stop a specific mistake.

---

## 1. The traceability rule is a trigger, not a convention

A pain point cannot be inserted without a verified evidence span, and an
evidence link cannot point at a pain point backed by another span. Three
triggers in `db/migrations/0003_evidence_pipeline.sql` enforce it: one on
`pain_points`, and two on `evidence_pain_links`, one for the insert path and
one for the update path.

The update trigger exists because SQLite does not fire a BEFORE INSERT
trigger when an upsert resolves to an UPDATE. Without it the rule held on
one path and not the other, which is the kind of gap nobody notices until
somebody writes to the other path.

**Do not** move this rule into application code. A rule that only holds when
the code that writes the row runs is not a rule, it is a convention, and a
convention is not what stops a bad row.

---

## 2. Strength and reliability are derived, never reported

A model may classify a candidate. It may not say how strong the evidence is
or how reliable the source is. Spec §11 calls these derived facts, and the
code derives them:

| Fact | Rule |
|---|---|
| `reliability` | Reads the `sources` row: `reviewed` 1.0, `permitted` 0.7, `unverified` 0.3, `blocked` 0.0. |
| `strength` | Reads the candidate and the store: `corroborated` 1.0, `direct` 0.8, `single` 0.4, `indirect` 0.1. |

Both carry `derivation_version`, so a later rule can be told apart from this
one. A row written under the old rule keeps its old meaning.

**Do not** add a model-provided strength or reliability field. The moment a
model can set them, they stop being measurements of the capture and become
assertions by the thing being measured.

---

## 3. The dedup key is the content, and the count is of canonical spans

`evidence_hash` is the SHA-256 of the canonicalised quote, using the same
canonicalisation `product_hunter.quote` uses for its fallback match. A span
whose hash is already carried by a live span records that span in
`canonical_span_id`.

The count of independent pieces of evidence for a key is
`count(DISTINCT coalesce(canonical_span_id, span_id))`, of live spans only.
A copy of a viral complaint posted a hundred times counts once.

**Do not** count rows. A hundred rows for one complaint is one piece of
evidence, and the count is what the clustering wave will read. Spec §12
asks the system to distinguish 147 mentions from 83 independent people, and
a row count cannot answer that.

---

## 4. The span id is checked against the span, not trusted

`span_id` is `ev_` plus 16 hex characters of the SHA-256 of
`doc_id|char_start|char_end|quote`. It is re-derived on every write, and a
candidate whose id does not match its own fields is refused with
`span_id_mismatch`.

The derivation lives in `product_hunter.extraction.derive_span_id` and the
pipeline imports it, so the extractor and the pipeline cannot drift apart.

**Do not** hand out a span id that was not derived from the span. An id that
does not match its content is a label, and a label cannot be checked.

---

## 5. The quote is re-derived from the stored text, never taken from the model

At write time the pipeline reads the document's text from the
content-addressed file the row points at, checks the file's hash against the
row, and compares `text[char_start:char_end]` with the quote. A model that
returns text the document does not contain gets a refusal, whatever the text
says.

The reason codes separate the failures, because a caller has to act on them
differently: `invalid_quote` is a paraphrase, `quote_too_short` and
`quote_too_long` are length policy, `span_mismatch` is a quote that is in
the document but at the wrong offsets.

**Do not** accept a quote because the model said it. The extraction prompt
already asks for a verbatim quote and the pipeline already checks, and both
are needed: the prompt reduces the refusals, the check is what makes them
safe.

---

## 6. The migration runner ends a statement where SQLite says one is complete

`product_hunter.db.migrations._split_statements` used to split on `;`. That
cuts a SQLite trigger body in half, so migration 0003 could not be applied
at all. It now accumulates characters and ends a statement where
`sqlite3.complete_statement()` says one is complete.

The side effect is that a semicolon inside a string literal is now safe,
which the old runner could not do.

**Do not** go back to splitting on `;`. The first migration with a trigger
in it will fail to apply, and the failure looks like a broken SQL file
rather than a broken runner.
