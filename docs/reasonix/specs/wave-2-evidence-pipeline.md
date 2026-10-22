# Wave 2 spec: the evidence pipeline

> **Integrated as `0005_evidence_pipeline.sql`.** The migration named below was
> written as `0003_evidence_pipeline.sql` and was renumbered by the Wave 2 final
> integration. See
> [wave-2-final-integration.md](../reports/wave-2-final-integration.md).

**Status.** Approved for implementation. Written 2026-10-21 on branch
`wave2/evidence-pipeline`, against Wave 1 at commit `3d7bc68`.

**Scope.** The evidence layer only. Clustering, opportunity scoring,
competitor investigation, the Obsidian projection, and the Phase 7
integrations are out of scope and are named in the stop boundary at the end.

## 1. The problem

Wave 1 built the chain from bytes to an accepted record, and it works, but
it only works through one door. `Store.import_run()` reads a Phase 1 run
directory of JSONL artifacts and writes it into SQLite. That is a fine way to
land a completed batch, and it is the only way evidence reaches the
authoritative store today.

Four things are missing, and the rest of the system needs all four before a
pain point can be trusted.

**Provenance is asserted, not checked.** `_insert_evidence()` compares the
fields of an incoming candidate against the stored row and refuses a
disagreement, which is the right instinct, but it never re-derives the span
from the document. A candidate whose `char_start` and `char_end` do not point
at its own quote is written as it stands. The quote was validated at
extraction time, and nothing validates it again on the way into the authority.

**A document that changed cannot be processed again.** `import_document()`
raises `StoreConflict` when a document id reappears with a different
`text_sha256`. That is correct for a replay, where a changed artifact means
somebody edited a run, and wrong for a re-capture, where the source page
changed and the new text is the truth. The two cases are indistinguishable
today because nothing records that a document has a version.

**Evidence has no derived facts.** Spec section 11 asks for source
reliability and evidence strength on every piece of evidence, and for the
live or deleted status of the source. The `evidence` table has none of them.
Strength and reliability are derived facts, so they need a rule version
beside them, or a later rule change silently rewrites what the old rows
mean.

**There is no audit trail.** The store answers "what does this evidence row
say" and "what document does it point at". It cannot answer "when was this
span first seen, what happened to it, and which extraction produced it".
A row that was superseded, deleted, or re-observed has no history.

## 2. What Wave 2 builds

One new service, `product_hunter.evidence.EvidencePipeline`, over one new
migration, `db/migrations/0003_evidence_pipeline.sql`.

Four operations carry the wave. The rest of the public surface supports them.

| Operation | What it does |
|---|---|
| `ingest_document` | Put a parsed document into the authoritative store with its version, liveness, and provenance chain. |
| `record_candidate` | Validate one extracted candidate against the stored document text, then persist the evidence span, its observation, and its lineage. |
| `reprocess_document` | Record a new content version of a document and revalidate the spans that hang off it. |
| `audit` | Return the whole chain behind one span: source, fetch, document, span, observations, pain points, lineage. |

The supporting methods are `record_batch`, `revalidate_document`,
`mark_document_status`, `mark_span_status`, `link_pain_point`, `lineage`,
and `independent_evidence_count`. Section 2.1 lists them.

Two derived facts are computed and stored with every span.

- `reliability` describes the source: `reviewed`, `permitted`, `unverified`,
  `blocked`. It reads the `sources` row, so it is a property of the capture,
  not a guess. The pipeline reads the document's `source_id`; the Wave 1
  import path reads the candidate's. The two agree, because `import_run`
  refuses a candidate whose source differs from its fetch's.
- `strength` describes the evidence: `corroborated`, `direct`, `single`,
  `indirect`. A span that names an actor, a workflow, and a cost is
  `direct`. The same normalized key seen in two documents is `corroborated`.
  A span that is not a pain point is `single` or `indirect`.

Both carry a `derivation_version`, so a later rule can be told apart from
this one. Both are computed by `product_hunter.db.evidence`, so the pipeline
and the Wave 1 import path write the same values.

### 2.1 The public surface

`EvidencePipeline(store, *, repo_root=None, now=utcnow)` takes the store's
connection. Every method below is public.

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

Two exception types carry the failures. `EvidenceRejected` means the
candidate was refused, and its `reason_code` names why. `EvidenceStoreError`
means the database could not take the write, so the caller has to tell the
two apart.

## 3. The evidence model

```text
raw bytes  ->  fetch        (fetches row, raw_sha256, raw_path)
           ->  document     (documents row, text_sha256, content_version, live_status)
           ->  evidence span (evidence row, quote, char range, match_mode, evidence_hash)
           ->  observation  (evidence_observations row, one per sighting)
           ->  pain point   (pain_points row, linked by evidence_pain_links)
           ->  lineage      (evidence_lineage row, one per lifecycle event)
```

The chain is the same one Wave 1 built, with three additions. A document
carries a version, so a re-capture is a new version of the same document
rather than a conflict. An evidence span carries an observation history, so
the same span seen twice is two observations of one span. Every span carries
lineage, so its lifecycle is auditable.

### 3.1 The lineage event types

Seven event types carry the audit trail. The detail is what makes two
events of the same kind different rows.

| Event type | Detail | When |
|---|---|---|
| `document_ingested` | `{reason}:v{content_version}` | A document was put in the store. |
| `document_versioned` | `{reason}:v{content_version}` | A document moved to a new content version. |
| `span_recorded` | `v{content_version}` | A span was written. |
| `span_stale` | `v{content_version}` | A span no longer holds its quote. |
| `document_status` | `{previous}->{status}` | A document's status changed. |
| `span_status` | `{previous}->{status}#{n}` | A span's status changed. `n` is the entity's event count. |
| `evidence_rejected` | `{reason_code}:{candidate_id}` | A candidate was refused. |

An event that changes nothing is not written, so a replay of the same
operation leaves the trail exactly as it was.

## 4. Schema

Migration `0003_evidence_pipeline.sql` is additive. It never rewrites
migration 0001 or 0002.

### 4.1 New columns on `documents`

| Column | Type | Meaning |
|---|---|---|
| `content_version` | INTEGER NOT NULL DEFAULT 1 | Which version of this document's text the row holds. |
| `live_status` | TEXT NOT NULL DEFAULT 'live' | `live`, `deleted`, `stale`, `unknown`. |
| `first_seen_at` | TEXT | When the document was first ingested. |
| `last_seen_at` | TEXT | When the document was last seen. |
| `superseded_by_doc_id` | TEXT REFERENCES documents(doc_id) | The document that replaced this one, when the identity changed. |

### 4.2 New columns on `evidence`

| Column | Type | Meaning |
|---|---|---|
| `evidence_hash` | TEXT | SHA-256 of the canonicalised quote. The dedup key. |
| `strength` | TEXT NOT NULL DEFAULT 'unknown' | `corroborated`, `direct`, `single`, `indirect`, `unknown`. |
| `strength_score` | REAL NOT NULL DEFAULT 0.0 | The strength as a number in [0, 1]. |
| `reliability` | TEXT NOT NULL DEFAULT 'unknown' | `reviewed`, `permitted`, `unverified`, `blocked`, `unknown`. |
| `reliability_score` | REAL NOT NULL DEFAULT 0.0 | The reliability as a number in [0, 1]. |
| `derivation_version` | TEXT NOT NULL DEFAULT 'evidence-v1' | Which rule produced the two derived facts. |
| `live_status` | TEXT NOT NULL DEFAULT 'live' | `live`, `deleted`, `stale`, `superseded`, `unknown`. |
| `observation_count` | INTEGER NOT NULL DEFAULT 1 | How many times the span was observed. |
| `canonical_span_id` | TEXT REFERENCES evidence(span_id) | The first span that carried this content. NULL means this span is the canonical one. |
| `first_seen_at` | TEXT | When the span was first observed. |
| `last_seen_at` | TEXT | When the span was last observed. |
| `superseded_at` | TEXT | When the span stopped verifying against its document. |

### 4.3 New columns on `evidence_pain_links`

| Column | Type | Meaning |
|---|---|---|
| `basis` | TEXT NOT NULL DEFAULT 'extraction' | How the link was made. |
| `confidence` | REAL NOT NULL DEFAULT 0.0 | How strong the link is. |
| `created_by` | TEXT | Who made the link. |

### 4.4 New tables

`document_versions` holds one row per observed version of a document:
`version_id`, `doc_id`, `content_version`, `text_sha256`, `raw_sha256`,
`text_path`, `title`, `published_at`, `live_status`, `reason`, `recorded_at`.
It is unique on `(doc_id, content_version)`.

`evidence_observations` holds one row per sighting of a span:
`observation_id`, `span_id`, `doc_id`, `content_version`, `text_sha256`,
`quote`, `char_start`, `char_end`, `match_mode`, `provider`, `model`,
`extractor_version`, `prompt_version`, `live_status`, `observed_at`.

`evidence_lineage` holds one row per lifecycle event: `event_id`, `span_id`,
`doc_id`, `event_type`, `detail`, `actor`, `created_at`. Both `span_id` and
`doc_id` are nullable, because a document-level event has no span and a
rejection has no span either.

### 4.5 Triggers

Three triggers make the traceability rule a property of the database instead
of a convention in application code.

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

A pain point whose span is missing, empty, or has impossible offsets cannot
be inserted. An evidence link that points at a pain point backed by another
span cannot be inserted, and cannot be moved onto one either. All three fail
with `sqlite3.IntegrityError` and the message the trigger carries.

The third trigger exists because SQLite does not fire a BEFORE INSERT
trigger when an upsert resolves to an UPDATE. Without it the rule would hold
on the insert path and not on the update path.

The migration runner ends a statement where SQLite says one is complete, so
a trigger body keeps the semicolon it needs and a semicolon inside a string
literal does not end a statement.

## 5. Validation rules

`record_candidate` refuses a candidate and records why. Each refusal has a
stable reason code, and the codes are the API.

| Reason code | When |
|---|---|
| `missing_provenance` | The candidate has no `text_sha256`, no `raw_path`, no `raw_sha256`, or no `extractor_version`. |
| `document_not_found` | The candidate's `doc_id` is not in the store, or a status was set on a document that is not in the store. |
| `span_not_found` | A status was set on a span that is not in the store. |
| `document_text_unavailable` | The document text could not be read, is over the size cap, or does not match the hash the document row carries. |
| `provenance_mismatch` | The candidate's `text_sha256` or `raw_sha256` does not match the stored document. |
| `invalid_span` | The offsets are outside the text, or `char_end <= char_start`. |
| `invalid_quote` | The quote is not in the document text at all. |
| `quote_too_short` | The quote is shorter than the configured minimum. |
| `quote_too_long` | The quote is longer than the configured maximum. |
| `span_mismatch` | The quote is in the document, but not at the offsets the candidate claims. |
| `span_id_mismatch` | The candidate's `span_id` is not the hash of its own span fields. |
| `evidence_conflict` | The stored row for this `span_id` disagrees with the incoming record, or the database refused the write. |

The order matters. Provenance is checked before the span, and the span's
offsets before the quote, so the reason code names the earliest thing that
went wrong. The quote's presence is checked before the offsets are compared,
because "the model paraphrased" and "the model quoted the wrong passage" are
different problems and a caller has to be able to tell them apart.

A store failure is not a refusal. `EvidenceStoreError` with the reason code
`store_error` means the database could not take the write at all.

## 6. The strength and reliability rules

Both rules are deterministic and versioned. `derivation_version` is
`evidence-v1` for both.

Reliability reads the `sources` row.

| `terms_status` | `review_basis` | `reliability` | Score |
|---|---|---|---|
| `permitted` or `reviewed` | present | `reviewed` | 1.0 |
| `permitted` or `reviewed` | absent | `permitted` | 0.7 |
| `unknown` | any | `unverified` | 0.3 |
| `restricted` or `prohibited` | any | `blocked` | 0.0 |

A source that is not in the store is `unverified`.

Strength reads the candidate and the store.

| Condition | `strength` | Score |
|---|---|---|
| The candidate is accepted and its normalized key is in two or more documents | `corroborated` | 1.0 |
| The candidate is accepted and its validity is `genuine` | `direct` | 0.8 |
| The candidate's validity is `genuine` but it was not accepted | `single` | 0.4 |
| The candidate's validity is `weak` | `single` | 0.4 |
| The candidate's validity is `not_pain` | `indirect` | 0.1 |

When a second document produces a span for a key that already exists, the
earlier spans for that key are recomputed to `corroborated`. Only live spans
are recomputed, and a span is never downgraded.

## 7. Deduplication

`evidence_hash` is the SHA-256 of the canonicalised quote, using the same
canonicalisation `product_hunter.quote` uses for its fallback match. Two
quotes that differ only in curly quotes, dashes, or whitespace share a hash.

When a span is written, the pipeline looks for the earliest live span with
the same hash. If one exists and it is not this span, the new span records
it in `canonical_span_id` and is a duplicate of it. A span with a NULL
`canonical_span_id` is the canonical one.

The count of independent pieces of evidence for a key is the count of
distinct `coalesce(canonical_span_id, span_id)` values. A copy of a viral
complaint posted a hundred times counts once.

A row written before this wave keeps a NULL `evidence_hash` until the
pipeline or the import path sees it again, and the import path backfills the
hash and the derived facts when it does. The backfill does not restore the
canonical link, so a pre-wave row and a pipeline row for the same quote are
not linked to each other until the pre-wave row is rewritten.

## 8. Idempotency and conflicts

Writing the same span twice is a no-op. The second write finds the row by
`span_id`, compares the immutable fields, and returns the existing row.

The immutable fields are `doc_id`, `quote`, `char_start`, `char_end`,
`match_mode`, `model_quote`, and `occurrence`. A disagreement is an
`evidence_conflict`.

The `span_id` is re-derived on every write from `doc_id`, `char_start`,
`char_end`, and `quote`, using the same hash Wave 1 uses. A candidate whose
`span_id` does not match its own fields is refused with `span_id_mismatch`.
The id is therefore checked against the content, not trusted.

A document that is re-ingested with the same text keeps its stored
`live_status`, and only `last_seen_at` moves, unless the capture is a new
one. A new capture is a fresh observation of the source, so the source's own
status wins, and the document and its spans move together. A replay of the
same capture is not an observation, so the stored status stands. The capture
is identified by its `fetch_id`.

## 9. Reprocessing

`reprocess_document` takes a re-parsed document and does three things.

1. It records the new content version. `documents.content_version` moves up
   by one, the previous version stays in `document_versions`, and the
   document row takes the new text hash and path.
2. It revalidates every span of that document against the new text. A span
   whose text no longer holds its quote at its offsets becomes `stale`, with
   a lineage event. A span that still verifies has its `text_sha256`
   refreshed, its `observation_count` moved up, and a new row in
   `evidence_observations`.
3. It leaves the pain points alone. A pain point that was accepted stays
   accepted, and its span stays linked, because the pain point was a real
   observation of a real quote even when the page around it has since
   changed.

Revalidation is a separate operation from re-extraction on purpose. A
re-extraction that fails halfway must not mark the spans it did not reach as
stale, so nothing in the pipeline couples the two.

## 10. Live and deleted sources

`documents.live_status` and `evidence.live_status` carry `live`, `deleted`,
`stale`, `superseded`, or `unknown`.

The GitHub parser sets a document's `live_status` from the issue state, so a
closed issue is `deleted` from the moment it is parsed. The other two
parsers have no such field and leave it `live`. The evidence row of a
document always carries the document's status, so a deleted source's
evidence never counts as live.

`mark_document_status` sets the status of a document and cascades to its
spans. `mark_span_status` sets the status of one span. Both write a lineage
event, and only when the status actually changes.

The lineage event for a status change carries the transition and the
entity's event sequence, so the same transition happening twice is two
rows. An event that changes nothing is not written at all, so a replay
leaves the trail exactly as it was.

## 11. Security

Captured web content is data. It is never an instruction and never an
argument to a tool.

- The pipeline contains no subprocess call, no `eval`, and no network call.
  Source text never becomes a command, a path, or a URL.
- Every identifier the pipeline writes is a hash. No source text reaches a
  file name or a path.
- The quote is re-derived from the stored document text at write time, not
  taken from the model. A model that returns text that is not in the
  document gets a refusal, whatever the text says.
- The document text is read from the content-addressed file whose hash the
  document row carries, so the text that is checked is the text that was
  captured.
- The text file is refused before it is read when it is over
  `MAX_DOCUMENT_BYTES`, so a hostile capture cannot exhaust memory.

The test for this is a document whose text tries to give orders. The
pipeline stores it as text, refuses a quote that is not in it, and does not
execute anything.

## 12. What the pipeline does not do

- It does not cluster, score opportunities, or investigate competitors.
- It does not write to Obsidian or any other projection.
- It does not call a model. Extraction stays in `product_hunter.extraction`.
- It does not re-key or re-normalise anything. The normalized key still
  comes from `product_hunter.normalization`.

It does touch the Wave 1 import path, in three ways, all of them additive
and all of them covered by the existing suite.

- `Store.import_candidate` writes the derived facts, the observation, and
  the document's liveness on the evidence row, so a row written by the Wave 1
  path looks the same as a row written by the pipeline.
- `Store.import_document` writes `live_status`, `first_seen_at`, and
  `last_seen_at`, and updates the status and the last-seen time when the
  document reappears.
- The new trigger refuses a degenerate span, so `import_run` now raises a
  `StoreConflict` where it used to import the run. The trigger's message is
  carried in the conflict's message.

## 13. Migration number

`0003` is the next free number, and the migration runner refuses a gap, so
this wave takes `0003_evidence_pipeline.sql`. `docs/reasonix/ARCHITECTURE.md`
records that `0003` is taken by this wave. The wave that persists tool
calls must take `0004` or renumber. This is the one integration risk
another Wave 2 branch can hit.

## 14. Assumptions

- The mission names the report path `docs/resonix/reports/...`. The
  repository spells the directory `docs/reasonix`, so the report is written
  there.
- "Where supported" for the live or deleted state means the GitHub issue
  state, because it is the only parser whose source carries one.
- A span that is `stale` still counts as evidence of what was said. It is
  excluded from the live counts, not deleted.
- The pipeline derives strength and reliability. It does not ask a model for
  them, because spec section 11 calls them derived facts.
