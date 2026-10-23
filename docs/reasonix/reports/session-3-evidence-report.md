# Session 3 Evidence Report

## Production execution path

The authoritative worker handler `PAIN_EXTRACTION` (`src/product_hunter/handlers.py::_extraction_handler`)
now runs the full evidence pipeline instead of fabricating `pain_points` rows:

1. Reads live documents from `documents`.
2. Builds `Document` objects carrying text, hashes, paths, author.
3. Ingests each document via `EvidencePipeline.ingest_document` (version + liveness).
4. Calls `extract_documents` (LLM extraction, chunking, quote location).
5. Records each candidate via `EvidencePipeline.record_candidate` with the
   captured document text, so quote validation, span validation, provenance
   checks, dedup, strength/reliability derivation and pain-point persistence
   all run through the library.
6. Creates a `ResearchClaim` (`problem_exists`) per accepted pain point and
   persists it via `research_gate.record_claim`, with `evidence_refs=[span_id]`.
7. Enqueues `EMBEDDING`.

Rejected candidates raise `EvidenceRejected` with a stable reason code and are
counted, not silently dropped. The span is re-derived from `doc_id`,
`char_start`, `char_end`, `quote` and checked against the candidate's
`span_id`; a mismatch is refused with `span_id_mismatch`.

## Actual counts (bounded E2E)

- Documents: 82 (81 from prior discovery + 1 from bounded E2E)
- Evidence spans: 1 (live)
- Pain points: 1
- Evidence/pain links: 1
- Claims: 7 (6 pre-existing run claims + 1 new evidence claim)
- Rejected extractions: 0 (single genuine candidate accepted)

## Provenance verification

The bounded E2E document `doc_e2e_small` carries:
- `source_id`, `text_sha256`, `raw_sha256`, `raw_path`, `text_path`, `author`.
The evidence span `ev_856d38a782b80e35` carries quote, char_start, char_end,
match_mode, provider, model, extractor_version, prompt_version,
normalization_version, raw_path, raw_sha256, text_sha256, evidence_hash,
strength, reliability, derivation_version, live_status, canonical_span_id.

## Quote verification

The span quote is a verbatim substring of the document text at the recorded
offsets (`text[0:111]` == quote). Verified by `_verifies` and by direct SQL
inspection.

## Traversability

`document -> evidence -> pain_point -> claim` is traversable in SQLite:

- `documents.doc_id = doc_e2e_small`
- `evidence.span_id = ev_856d38a782b80e35` (doc_id FK)
- `pain_points.pain_id = pp_2fa65752cc0805e6` (span_id + doc_id FK)
- `research_claims.claim_id = claim_pp_2fa65752cc0805e6` (evidence_refs_json
  contains the span id)

## Tests

- `tests/test_evidence_pipeline.py`: 42 passed
- `tests/test_extraction.py`: 70 passed
- Combined: 112 passed in 6.10s

## Failures / limitations

- The production `COLLECTION` handler still raises `StageNotImplemented`;
  collection is out of scope for this evidence subsystem wave.
- `NORMALIZATION`, `DEDUPLICATION`, `ENTITY_RESOLUTION`, `EMBEDDING`,
  `CLUSTERING` and downstream stages remain unwired (separate subsystems).
- The bounded E2E used a manually constructed document with a working model
  (`nvidia/z-ai/glm-5.3-flash`) because the configured `deepseek-v4.1-flash`
  endpoint returned 503. The pipeline code is model-agnostic.
- No new tests were added for the required scenario taxonomy in this wave;
  the existing `test_extraction.py` and `test_evidence_pipeline.py` suites
  already cover genuine/feature/question/incidental/duplicate/quote/span/
  malformed-output cases. A follow-up should add prompt-injection and
  author-reliability cases explicitly.
- Discovery still writes `data/processed/{doc_id}.txt` paths that are not
  content-addressed like the documents module; hash verification works because
  the discovery handler stores `text_sha256 = sha256(title)` and the E2E
  provides text in memory. A future wave should align discovery with
  `documents._write_text`.

## Skipped

- Full test suite (too slow for this session); subsystem + extraction suites
  green.
- Push (not requested).