# Wave 2 plan: the evidence pipeline

**Spec.** [wave-2-evidence-pipeline.md](../specs/wave-2-evidence-pipeline.md)
**Branch.** `wave2/evidence-pipeline`
**Base.** `3d7bc68`

## 1. Files

The table below is the final state, after the review rounds moved some of
it. The plan itself was written before the build, so it is the plan and not
the record; the report is the record.

| File | Change | Lines |
|---|---|---|
| `db/migrations/0003_evidence_pipeline.sql` | new | 127 |
| `src/product_hunter/db/evidence.py` | new | 501 |
| `src/product_hunter/evidence.py` | new | 788 |
| `src/product_hunter/db/store.py` | edit | 382 added |
| `src/product_hunter/db/migrations.py` | edit | 18 |
| `src/product_hunter/db/__init__.py` | edit | 56 added |
| `src/product_hunter/documents.py` | edit | 6 added |
| `src/product_hunter/extraction.py` | edit | 11 |
| `tests/test_evidence_pipeline.py` | new | 958 |
| `tests/conftest.py` | edit | 60 |
| `tests/test_db_migrations.py` | edit | 13 |
| `docs/reasonix/specs/wave-2-evidence-pipeline.md` | new | 371 |
| `docs/reasonix/plans/wave-2-evidence-pipeline.md` | new | this file |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-*.md` | new | 4 files, 248 lines |
| `docs/reasonix/reports/wave-2-evidence-pipeline.md` | new | 385 |
| `docs/reasonix/ARCHITECTURE.md` | edit | 88 |

Three files the plan did not name are in the wave, and the plan was
corrected rather than the code bent.

- `src/product_hunter/db/migrations.py`. The old runner split SQL on `;`,
  which cuts a SQLite trigger body in half. Migration 0003 cannot be applied
  without the change. The runner now ends a statement where SQLite says one
  is complete.
- `tests/test_db_migrations.py`. The schema version moved from 2 to 3 and
  three table names were added. No assertion was removed.
- `src/product_hunter/extraction.py`. The span id derivation moved here, so
  the pipeline and the extractor cannot drift apart. The change is additive
  and the ids are byte-for-byte the same.

`product_hunter/quote.py` is read, not changed. The pipeline reuses
`locate_quote`, `canonicalize`, `SpanMatch`, and the `ExtractedCandidate`
model as they stand.

## 2. Build order

Each step is a working state. The gate runs at the end, not between steps.

**Step 1, the migration.** Write `0003_evidence_pipeline.sql` with the
columns, the three tables, the indexes, and the triggers from spec
section 4. Check that `migrate()` reaches version 3 and that
`PRAGMA foreign_key_check` is clean on an empty database.

**Step 2, the SQL layer.** Write `src/product_hunter/db/evidence.py` with
functions that take a `sqlite3.Connection` and return plain rows or
`sqlite3.Row` objects, in the style of `db/jobs.py` and
`db/checkpoints.py`:

```text
write_evidence_span(conn, span, *, now)      -> SpanWrite
record_observation(conn, span, *, now)       -> bool
append_lineage(conn, event, *, now)          -> bool
count_lineage_events(conn, *, span_id, doc_id) -> int
link_pain_point(conn, pain_id, span_id, ...) -> bool
set_document_status(conn, doc_id, status)    -> int
set_span_status(conn, span_id, status)       -> int
find_canonical_span(conn, evidence_hash)     -> str | None
recompute_strength(conn, normalized_key)     -> int
document_versions(conn, doc_id)              -> list[Row]
lineage_events(conn, *, span_id, doc_id)     -> list[Row]
load_span(conn, span_id)                     -> Row | None
load_document(conn, doc_id)                  -> Row | None
write_document_version(conn, version, *, now) -> bool
spans_for_document(conn, doc_id)             -> list[Row]
evidence_hash(quote)                         -> str
derive_reliability(conn, source_id)          -> tuple[str, float]
derive_strength(pain_validity, accepted)     -> tuple[str, float]
```

`write_evidence_span` holds the conflict rule and the idempotency rule from
spec section 8. It is the pipeline's authoritative writer of the evidence
row. `Store._insert_evidence` is the other writer, and it calls the same
derivation functions, so the two agree on the derived facts.

**Step 3, the store additions.** In `db/store.py`, split the pain-point half
of `import_candidate` into a public `insert_pain_point(candidate) -> bool`,
so the pipeline can write the evidence itself and still reuse the Wave 1
pain-point write. `import_candidate` calls both halves, so its behaviour does
not change and its test keeps passing.

Add `import_document_version(document, *, reason) -> DocumentVersionImport`
for the pipeline. It reuses `_ensure_community`, `_ensure_author`, and
`_ensure_author_link`. It inserts the row when the document is new, touches
`last_seen_at` when the text is unchanged, and moves to a new
`content_version` when the text changed, writing a `document_versions` row
for the version it leaves behind. `import_document` keeps its strict
Wave 1 semantics and its `StoreConflict`.

**Step 4, the document liveness.** In `documents.py`, add `live_status` to
`Document` with the default `live`, carry it on `_Parsed`, and set it from
the GitHub issue `state`: `closed` becomes `deleted`, everything else stays
`live`. The other two parsers leave it `live`.

**Step 5, the service.** Write `src/product_hunter/evidence.py`: the models,
the two rule functions, and `EvidencePipeline`.

```text
EvidencePipeline(store, *, repo_root=None, now=utcnow)
  .ingest_document(document, *, fetch=None)      -> DocumentIngest
  .record_candidate(candidate, *, document_text=None) -> EvidenceWrite
  .record_batch(candidates, *, document_text=None)    -> BatchOutcome
  .reprocess_document(document, *, fetch=None)    -> DocumentIngest
  .revalidate_document(doc_id, *, document_text=None) -> RevalidationOutcome
  .mark_document_status(doc_id, status, *, detail="")
  .mark_span_status(span_id, status, *, detail="")
  .link_pain_point(pain_id, span_id, *, basis, confidence, created_by)
  .audit(span_id)                                 -> EvidenceAudit
  .lineage(*, span_id=None, doc_id=None)          -> list[LineageEvent]
  .independent_evidence_count(normalized_key)     -> int
```

`record_candidate` runs the checks in the order of spec section 5 and raises
`EvidenceRejected` with the reason code. `record_batch` catches the refusal
per candidate so one bad candidate does not stop the rest, and returns the
counts.

**Step 6, the tests.** Write `tests/test_evidence_pipeline.py` with one test
per scenario in section 3.

**Step 7, the docs.** Update `ARCHITECTURE.md` and write the report.

## 3. Test scenarios

The mission names ten. The list below is those ten plus the two the schema
rules need.

| # | Test | What it proves |
|---|---|---|
| 1 | `test_duplicate_evidence_is_stored_once` | The same span written twice is one row, and the second span with the same content points at the first through `canonical_span_id`. |
| 2 | `test_changed_source_content_creates_a_new_version` | A re-captured document moves to version 2, the old version stays in `document_versions`, and the span that no longer verifies goes `stale`. |
| 3 | `test_invalid_spans_are_refused` | Offsets outside the text, and `char_end <= char_start`, are refused with `invalid_span`. |
| 4 | `test_invalid_quotes_are_refused` | A quote that is not in the document is refused with `span_mismatch` or `invalid_quote`. |
| 5 | `test_missing_provenance_is_refused` | A candidate without a text hash, a raw path, or an extractor version is refused with `missing_provenance`. |
| 6 | `test_replaying_the_same_pipeline_is_idempotent` | Running the whole pipeline twice leaves the row counts unchanged. |
| 7 | `test_conflicting_evidence_is_refused` | A candidate whose `span_id` does not match its own fields is refused with `span_id_mismatch`, and a stored row that disagrees is an `evidence_conflict`. |
| 8 | `test_a_deleted_document_marks_its_evidence` | Marking a document deleted cascades to its spans and excludes it from the live count. |
| 9 | `test_malformed_extraction_is_rejected_without_writing_evidence` | An extractor reject becomes a lineage event and no evidence row. |
| 10 | `test_a_partial_pipeline_failure_keeps_the_good_evidence` | A batch with one bad candidate writes the good ones and refuses the bad one. |
| 11 | `test_the_pipeline_recovers_after_a_failure` | A retry after a refused candidate writes the rest and leaves the store consistent. |
| 12 | `test_a_pain_point_cannot_exist_without_evidence` | The trigger refuses a pain point with no verified span, and the link trigger refuses a mismatched link. |
| 13 | `test_source_text_is_never_executed` | A document whose text tries to give orders is stored as data, and the pipeline runs no subprocess. |

The tests use the existing fixtures in `tests/fixtures/`, and the shared
builders in `tests/conftest.py`.

## 4. The gate

`uv run pytest` has to pass, both the new file and the whole suite. The
existing suite is the regression net: `test_db_store.py` covers the Wave 1
import path that this wave must not change.

## 5. Risk

| Risk | Handling |
|---|---|
| The two new triggers break the Wave 1 import path | The import writes evidence before the pain point and the link, which is the order the triggers require. `test_db_store.py` is the check. |
| `0003` collides with the research-gate wave | Recorded in the spec and in the report. The other wave takes `0004` or renumbers. |
| The strength and reliability rules are wrong | They are versioned, so a later wave can change them without rewriting history. |
| A quote is verified against a text that is not the captured text | The document row carries the hash of the text file, and the pipeline reads that file. |
| The pipeline grows a second write path beside `Store` | The pipeline reuses `Store.insert_pain_point` and `Store.import_document_version`. It does not re-implement the pain-point write. |
