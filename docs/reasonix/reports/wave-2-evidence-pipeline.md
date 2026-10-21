# Wave 2 report: the evidence pipeline

**Branch.** `wave2/evidence-pipeline`
**Base.** `3d7bc68`
**Date.** 2026-10-21
**Gate.** `uv run pytest` -> `1020 passed in 19.14s`, exit 0

## 1. What this wave built

The evidence layer. It turns a parsed document and an extracted candidate
into a durable, traceable evidence span, and it keeps the trace.

The Wave 1 chain already reached an accepted record. It did it through one
door, `Store.import_run()`, which reads a finished run directory of JSONL
artifacts. Four things were missing, and each one is a hole a downstream
pain point could fall through.

Provenance was asserted rather than checked. A candidate's offsets were
never re-derived from the document, so a candidate whose `char_start` and
`char_end` did not point at its own quote was written as it stood.

A document that changed could not be processed again. A re-capture and a
tampered artifact looked the same to the store, and the store refused both.

Evidence carried no derived facts. Spec §11 asks for source reliability and
evidence strength on every piece of evidence, and for the live or deleted
status of the source. None of the three existed.

Nothing was auditable. The store could say what an evidence row contained.
It could not say when the span was first seen, what happened to it, or which
extraction produced it.

The wave closes all four. `product_hunter.evidence.EvidencePipeline` is a new
library over one new migration, and the migration turns the traceability rule
into a property of the database.

## 2. Schema changes

`db/migrations/0003_evidence_pipeline.sql`, 127 lines, additive only.
Migration 0001 and 0002 are untouched.

### 2.1 New columns on `documents`

| Column | Type | Meaning |
|---|---|---|
| `content_version` | INTEGER NOT NULL DEFAULT 1 | Which version of the text the row holds. |
| `live_status` | TEXT NOT NULL DEFAULT 'live' | `live`, `deleted`, `stale`, `unknown`. |
| `first_seen_at` | TEXT | When the document was first ingested. |
| `last_seen_at` | TEXT | When the document was last seen. |
| `superseded_by_doc_id` | TEXT REFERENCES documents(doc_id) | The document that replaced this one. Reserved, no writer today. |

### 2.2 New columns on `evidence`

| Column | Type | Meaning |
|---|---|---|
| `evidence_hash` | TEXT | SHA-256 of the canonicalised quote. The dedup key. |
| `strength` | TEXT NOT NULL DEFAULT 'unknown' | `corroborated`, `direct`, `single`, `indirect`. |
| `strength_score` | REAL NOT NULL DEFAULT 0.0 | The strength as a number. |
| `reliability` | TEXT NOT NULL DEFAULT 'unknown' | `reviewed`, `permitted`, `unverified`, `blocked`. |
| `reliability_score` | REAL NOT NULL DEFAULT 0.0 | The reliability as a number. |
| `derivation_version` | TEXT NOT NULL DEFAULT 'evidence-v1' | Which rule produced the two derived facts. |
| `live_status` | TEXT NOT NULL DEFAULT 'live' | `live`, `deleted`, `stale`, `superseded`, `unknown`. |
| `observation_count` | INTEGER NOT NULL DEFAULT 1 | How many times the span was observed. |
| `canonical_span_id` | TEXT REFERENCES evidence(span_id) | The first span that carried this content. NULL means this span is canonical. |
| `first_seen_at` | TEXT | When the span was first observed. |
| `last_seen_at` | TEXT | When the span was last observed. |
| `superseded_at` | TEXT | When the span stopped verifying. |

### 2.3 New columns on `evidence_pain_links`

`basis` (TEXT NOT NULL DEFAULT 'extraction'), `confidence` (REAL NOT NULL
DEFAULT 0.0), and `created_by` (TEXT).

### 2.4 New tables

`document_versions` holds one row per observed version of a document, unique
on `(doc_id, content_version)`. A version stays when the document moves on,
so the history of a document is complete.

`evidence_observations` holds one row per sighting of a span. The sighting is
identified by the span, the document, the content version, and the text hash,
so the same span seen again in the same version is the same sighting.

`evidence_lineage` holds one row per lifecycle event. `span_id` and `doc_id`
are both nullable, because a document event has no span and a rejection has
neither.

### 2.5 The triggers

Three triggers, and they are the point of the wave.

```sql
CREATE TRIGGER trg_pain_points_require_evidence
BEFORE INSERT ON pain_points
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM evidence e
    WHERE e.span_id = NEW.span_id
      AND e.doc_id = NEW.doc_id
      AND e.char_start >= 0
      AND e.char_end > e.char_start
      AND e.quote <> ''
)
BEGIN
    SELECT RAISE(ABORT, 'a pain point needs a verified evidence span');
END;
```

```sql
CREATE TRIGGER trg_evidence_pain_links_require_matching_span
BEFORE INSERT ON evidence_pain_links
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM pain_points p
    JOIN evidence e ON e.span_id = p.span_id AND e.doc_id = p.doc_id
    WHERE p.pain_id = NEW.pain_id AND e.span_id = NEW.span_id
)
BEGIN
    SELECT RAISE(ABORT, 'an evidence link needs a pain point backed by that span');
END;
```

```sql
CREATE TRIGGER trg_evidence_pain_links_require_matching_span_update
BEFORE UPDATE ON evidence_pain_links
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM pain_points p
    JOIN evidence e ON e.span_id = p.span_id AND e.doc_id = p.doc_id
    WHERE p.pain_id = NEW.pain_id AND e.span_id = NEW.span_id
)
BEGIN
    SELECT RAISE(ABORT, 'an evidence link needs a pain point backed by that span');
END;
```

The third exists because SQLite does not fire a BEFORE INSERT trigger when an
upsert resolves to an UPDATE. Without it the rule would hold on the insert
path and not on the update path, which the adversarial review found.

## 3. The APIs

### 3.1 `product_hunter.evidence.EvidencePipeline`

`EvidencePipeline(store, *, repo_root=None, now=utcnow)`.

| Method | Returns |
|---|---|
| `ingest_document(document, *, fetch=None)` | `DocumentIngest` |
| `reprocess_document(document, *, fetch=None)` | `DocumentIngest` |
| `record_candidate(candidate, *, document_text=None)` | `EvidenceWrite` |
| `record_batch(candidates, *, document_text=None)` | `BatchOutcome` |
| `revalidate_document(doc_id, *, document_text=None)` | `RevalidationOutcome` |
| `mark_document_status(doc_id, status, *, detail="")` | the number of spans that changed |
| `mark_span_status(span_id, status, *, detail="")` | the number of rows that changed |
| `link_pain_point(pain_id, span_id, *, basis, confidence, created_by)` | whether the link was new |
| `audit(span_id)` | `EvidenceAudit` |
| `lineage(*, span_id=None, doc_id=None)` | a list of `LineageEvent` |
| `independent_evidence_count(normalized_key)` | an integer |

`record_candidate` checks, in this order, and stops at the first failure:
provenance, the document, the document text, the hash match, the span
offsets, the quote, and the span id. A refusal raises `EvidenceRejected`
with a stable `reason_code`. A store that cannot take the write raises
`EvidenceStoreError` with the reason code `store_error`, so a caller can
tell a bad candidate from a broken database.

The reason codes are the API. They are `missing_provenance`,
`document_not_found`, `span_not_found`, `document_text_unavailable`,
`provenance_mismatch`, `invalid_span`, `invalid_quote`, `quote_too_short`,
`quote_too_long`, `span_mismatch`, `span_id_mismatch`, and
`evidence_conflict`.

### 3.2 `product_hunter.db.evidence`

Conn-level functions, in the style of `db/jobs.py` and
`db/checkpoints.py`. `write_evidence_span`, `record_observation`,
`append_lineage`, `count_lineage_events`, `link_pain_point`,
`set_document_status`, `set_span_status`, `find_canonical_span`,
`recompute_strength`, `document_versions`, `lineage_events`, `load_span`,
`load_document`, `write_document_version`, `spans_for_document`, plus the
three derivations `evidence_hash`, `derive_reliability`, and
`derive_strength`.

### 3.3 The store additions

`Store.insert_pain_point(candidate, *, basis, confidence, created_by)` is
the pain-point half of `import_candidate`, split out so the pipeline can
write the evidence itself and still reuse the Wave 1 write.
`Store.import_document_version(document, *, reason)` is the version-aware
document write. `Store.import_candidate` and `Store.import_document` keep
their behaviour for every path the existing tests cover.

### 3.4 What was deliberately not built

No clustering, no opportunity scoring, no competitor investigation, no
Obsidian projection, no Phase 7 integration, no CLI command, no worker
handler, no scheduler entry, and no model call. `EvidencePipeline` is a
library and nothing in the repository calls it yet.

## 4. The provenance model

### 4.1 The chain

```text
raw bytes  ->  fetch         (fetches row, raw_sha256, raw_path)
           ->  document      (documents row, text_sha256, content_version, live_status)
           ->  evidence span (evidence row, quote, char range, match_mode, evidence_hash)
           ->  observation   (evidence_observations row, one per sighting)
           ->  pain point    (pain_points row, linked by evidence_pain_links)
           ->  lineage       (evidence_lineage row, one per lifecycle event)
```

### 4.2 What is checked, and where

| Check | Where |
|---|---|
| The document's raw hash matches its fetch's raw hash | `EvidencePipeline._ingest`, and `import_run` |
| The document's text hash matches the file the row points at | `_read_text` |
| The text at the offsets is the quote | `_check_quote` |
| The quote is in the document at all | `_check_quote` |
| The offsets are inside the text | `_check_span` |
| The span id is the hash of the span's own fields | `_check_span_id` |
| The candidate's hashes match the stored document | `_check_provenance_match` |
| The pain point has a verified span | a database trigger |
| The evidence link is backed by the pain point's own span | a database trigger, on insert and on update |

### 4.3 The derived facts

`reliability` reads the `sources` row, so it is a property of the capture
rather than a guess.

| `terms_status` | `review_basis` | `reliability` | Score |
|---|---|---|---|
| `permitted` or `reviewed` | present | `reviewed` | 1.0 |
| `permitted` or `reviewed` | absent | `permitted` | 0.7 |
| `unknown` | any | `unverified` | 0.3 |
| `restricted` or `prohibited` | any | `blocked` | 0.0 |

A source that is not in the store is `unverified`.

`strength` reads the candidate and the store.

| Condition | `strength` | Score |
|---|---|---|
| Accepted, and the normalized key is in two or more documents | `corroborated` | 1.0 |
| Accepted, and the validity is `genuine` | `direct` | 0.8 |
| The validity is `genuine` but it was not accepted | `single` | 0.4 |
| The validity is `weak` | `single` | 0.4 |
| The validity is `not_pain` | `indirect` | 0.1 |

Both carry `derivation_version`, so a later rule can be told apart from this
one. Both are computed by `product_hunter.db.evidence`, so the pipeline and
the Wave 1 import path write the same values.

### 4.4 Deduplication

`evidence_hash` is the SHA-256 of the canonicalised quote, using the same
canonicalisation `product_hunter.quote` uses for its fallback match. Two
quotes that differ only in curly quotes, dashes, or whitespace share a hash.

A span whose hash is already carried by a live span records that span in
`canonical_span_id`. The count of independent pieces of evidence for a key
is the count of distinct `coalesce(canonical_span_id, span_id)` values, so a
copy of a viral complaint posted a hundred times counts once.

### 4.5 The lineage

Seven event types. The detail carries the state, so two occurrences of the
same transition are two rows, and an event that changes nothing is not
written at all, so a replay leaves the trail exactly as it was.

| Event type | Detail |
|---|---|
| `document_ingested` | `{reason}:v{content_version}` |
| `document_versioned` | `{reason}:v{content_version}` |
| `span_recorded` | `v{content_version}` |
| `span_stale` | `v{content_version}` |
| `document_status` | `{previous}->{status}` |
| `span_status` | `{previous}->{status}#{n}` |
| `evidence_rejected` | `{reason_code}:{candidate_id}` |

## 5. Security

Captured web content is data. It never becomes an instruction and never
becomes an argument to a tool.

- The pipeline holds no subprocess call, no `eval`, and no network call. The
  test `test_source_text_is_never_executed` scans both modules for those
  names and runs the pipeline against a document whose text tries to give
  orders.
- Every identifier the pipeline writes is a hash. No source text reaches a
  file name or a path.
- The quote is re-derived from the stored document text at write time, not
  taken from the model. A model that returns text the document does not
  contain gets a refusal, whatever the text says.
- The document text is read from the content-addressed file whose hash the
  document row carries, and the hash is checked on every read, so the text
  that is verified is the text that was captured.
- The text file is refused before it is read when it is over
  `MAX_DOCUMENT_BYTES`, 16 MB, so a hostile capture cannot exhaust memory.
- Every statement binds its values. The only identifier interpolated into
  SQL text is a column name the code itself chooses.
- The public API does not leak a raw SQLite exception. Every public write
  path converts a `sqlite3.Error` into an `EvidenceStoreError`.

## 6. Tests

`tests/test_evidence_pipeline.py`, 40 tests, 1142 lines. The suite is
`1020 passed`, which is the 980 of the base commit plus 40 new tests.

The ten the mission names:

| Scenario | Test |
|---|---|
| Duplicate evidence | `test_duplicate_evidence_is_stored_once` |
| Changed source content | `test_changed_source_content_creates_a_new_version` |
| Invalid spans | `test_invalid_spans_are_refused` |
| Invalid quotes | `test_invalid_quotes_are_refused` |
| Missing provenance | `test_missing_provenance_is_refused` |
| Idempotent replay | `test_replaying_the_same_pipeline_is_idempotent` |
| Conflicting evidence | `test_conflicting_evidence_is_refused` |
| Deleted documents | `test_a_deleted_document_marks_its_evidence` |
| Malformed extraction | `test_malformed_extraction_is_rejected_without_writing_evidence` |
| Partial pipeline failure | `test_a_partial_pipeline_failure_keeps_the_good_evidence` |
| Recovery and retry | `test_the_pipeline_recovers_after_a_failure` |

The other 29 cover the rest of the contract: the pain-point trigger, the
link trigger on both write paths, a failed pain-point write leaving no
evidence row, the read-only store, the text size cap, the audit chain, the
reliability table, the corroborated strength, the revalidation branch that
still verifies, the lineage keeping every transition, the Wave 1 import
path, a document that disagrees with its fetch, a closed GitHub issue, and
the security scan.

Five assertions changed during the wave, and each one got more precise. The
quote check was reordered so that "the model paraphrased" and "the model
quoted the wrong passage" are different reason codes, and the five
assertions that froze the old, muddled meaning now name the exact code.

## 7. Files changed

New:

| File | Lines |
|---|---|
| `db/migrations/0003_evidence_pipeline.sql` | 137 |
| `src/product_hunter/db/evidence.py` | 589 |
| `src/product_hunter/evidence.py` | 872 |
| `tests/test_evidence_pipeline.py` | 1142 |
| `docs/reasonix/specs/wave-2-evidence-pipeline.md` | 463 |
| `docs/reasonix/plans/wave-2-evidence-pipeline.md` | 174 |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-implement.md` | 136 |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-verify.md` | 36 |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-harden.md` | 63 |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-review.md` | 87 |
| `docs/reasonix/prompts/wave-2-evidence-pipeline-report.md` | 68 |
| `docs/reasonix/decisions/2026-10-21-evidence-pipeline.md` | 155 |
| `docs/reasonix/reports/wave-2-evidence-pipeline.md` | this file |

Edited:

| File | Change |
|---|---|
| `src/product_hunter/db/store.py` | 382 added. `insert_pain_point`, `import_document_version`, the derived facts on the Wave 1 evidence write, the observation on that write, and the liveness rules. |
| `src/product_hunter/db/__init__.py` | 56 added. The new public names. |
| `src/product_hunter/db/migrations.py` | 18. The statement splitter now ends a statement where SQLite says one is complete. |
| `src/product_hunter/documents.py` | 6 added. `live_status` on `Document`, and the GitHub issue state. |
| `src/product_hunter/extraction.py` | 11. `derive_span_id` moved here, so the extractor and the pipeline cannot drift. The ids are unchanged. |
| `tests/conftest.py` | 60. One candidate helper instead of two. |
| `tests/test_db_migrations.py` | 13. The schema version moved from 2 to 3 and three table names were added. No assertion was removed. |
| `docs/reasonix/ARCHITECTURE.md` | 88. The table count, the migration runner, the evidence pipeline section, and the migration number hazard. |

## 8. Limitations

**The pipeline has no caller.** It is a library. Nothing in the repository
invokes it, by design, because the mission's stop boundary excluded a CLI
command, a worker handler, and a projection. The next wave that wires it
into a run is the integration point.

**The `corroborated` strength never downgrades.** A span that was
corroborated by two documents keeps the label when one of them is deleted.
The live count drops, because `independent_evidence_count` filters on
`live_status`, but the stored label does not move.

**A canonical span that is deleted leaves its duplicates pointing at it.**
`find_canonical_span` only considers live spans, so a new copy of the same
content after the canonical one is deleted becomes a new canonical span. The
independent count then sees two. This is a corner of the dedup rule, and it
is not covered by a test.

**`superseded_by_doc_id` and the `superseded` status have no writer.** The
schema carries them because the spec asked for them. A re-capture keeps the
same `doc_id`, so a document is never superseded by a different document id
in the current design, and a span is marked `stale` rather than
`superseded`.

**The migration runner's splitter is quadratic in the file size.** It calls
`complete_statement` on the accumulated buffer at every semicolon. A 318
line migration is not a problem, and a very large one would be.

**The backfill of a pre-wave row does not restore the canonical link.** A row
written before this wave gets its hash and its derived facts when the import
path sees it again, but its `canonical_span_id` stays NULL, so it and a
pipeline row for the same quote are not linked to each other. No database
file exists in the tree, so nothing needs the upgrade today.

**The liveness of a document comes from the capture, not from the source.**
A document's status is the status of the source at the time of the last
capture. A capture that says the content is gone sets the document to
`deleted`; a replay of the same capture leaves the stored status alone.

## 9. Integration requirements

**The migration number, and a collision that is already visible.** This wave
took `0003_evidence_pipeline.sql`, because the runner refuses a gap and
`0003` was the next free number.

The research-gate wave has an uncommitted
`db/migrations/0003_research_gate.sql` in the main working tree, checked on
2026-10-21. The merged tree cannot hold two migration 0003 files, and the
runner raises `MigrationError: duplicate migration number 3` when it tries.

It cannot be this wave that renumbers. The runner refuses a gap, so a branch
carrying 0001, 0002 and 0004 cannot migrate at all, and this branch has to
stand on its own. The research-gate wave must take `0004`.

The two waves also changed the same lines of `tests/test_db_migrations.py`,
the `migrate(conn) == 2` assertions, so that file will conflict textually.
Whoever renumbers to `0004` also moves those assertions to `== 4` and adds
the other wave's table names to `EXPECTED_TABLES`.

`docs/reasonix/ARCHITECTURE.md` records this.

**The migration runner changed.** It used to split SQL on `;`, which cuts a
SQLite trigger body in half, so migration 0003 could not be applied at all.
It now ends a statement where `sqlite3.complete_statement()` says one is
complete. A semicolon inside a string literal is now safe, which the old
runner could not do. Anything that depends on the old split-on-semicolon
behaviour should be checked.

**`Store.import_run()` can now fail where it used to succeed.** The new
trigger refuses a degenerate span, so a run whose candidate has
`char_end == char_start` raises a `StoreConflict` instead of importing. The
trigger message is carried in the conflict message. A caller that catches
`StoreError` still catches it.

**`Store.import_document()` and `import_candidate()` write more columns.**
They now write `live_status`, `first_seen_at`, `last_seen_at`, the derived
facts, and an observation row. The column set changed, not the semantics:
an existing document with different content is still a `StoreConflict`, and
a replay still writes no new row.

**The `evidence` table has two writers.** `write_evidence_span` in
`db/evidence.py` is the pipeline's authoritative writer and holds the
conflict and idempotency rules. `Store._insert_evidence` is the Wave 1
writer. Both call the same three derivations, so the two agree on the
derived facts, the liveness, and the observation.

**A future wave that clusters needs the independent count.**
`independent_evidence_count(normalized_key)` and `canonical_span_id` are
the hook. The count is of canonical spans, not of rows, so a copy of the
same complaint does not inflate it.

## 10. Assumptions

- The mission names the report path `docs/resonix/reports/...`. The
  repository spells the directory `docs/reasonix`, so the report is written
  there. The same applies to the spec and the plan.
- "Where supported" for the live or deleted state means the GitHub issue
  state, because it is the only parser whose source carries one. The Hacker
  News and blog parsers leave the status `live`.
- A span that is `stale` still counts as evidence of what was said. It is
  excluded from the live counts, not deleted.
- The pipeline derives the strength and the reliability. It does not ask a
  model for them, because spec §11 calls them derived facts.
- A document is identified by its `doc_id`, and the `doc_id` is derived from
  the fetch, the source, the identity, and the parser version. A re-capture
  therefore keeps the same `doc_id` and moves to a new content version.

## 11. Decisions recorded

Six conventions came out of this wave that a later phase will reuse. They
are in `docs/reasonix/decisions/2026-10-21-evidence-pipeline.md`, because
each one is easy to break by accident.

1. The traceability rule is a trigger, not a convention.
2. Strength and reliability are derived, never reported.
3. The dedup key is the content, and the count is of canonical spans.
4. The span id is checked against the span, not trusted.
5. The quote is re-derived from the stored text, never taken from the model.
6. The migration runner ends a statement where SQLite says one is complete.

## 12. Not checked

- The pipeline was not run against a real Phase 1 run directory, so the
  mixed path, a Wave 1 row beside a pipeline row, is argued from code and
  from the tests rather than from production data.
- The migration was not applied under a SQLite older than 3.50.
- The `Obsidian` projection and the research wave's migration number were
  not checked against a live branch. The research wave's migration was
  checked in the main working tree, and it collides; see section 9.
- The pre-wave backfill was not tested against a real version 2 database,
  because none exists in the tree.
- The other 980 tests were not read one by one. They were run, and they pass.
