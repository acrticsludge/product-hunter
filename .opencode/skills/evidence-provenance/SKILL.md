---
name: evidence-provenance
description: Use when tracing a stored claim back to the bytes it came from, or when deciding whether a record may be kept.
---

# Evidence provenance

## The chain

Every accepted claim walks one chain, and each link is stored:

```text
raw artifact -> document text -> evidence span -> pain point
```

The raw artifact is the fetched bytes, stored by SHA-256 under `data/raw/<source_id>/`. The document text is the parsed result, stored by its own SHA-256 under `data/processed/text/<sha256>.txt` and named in `text_path`. The evidence span is the character range `[char_start, char_end)` in that text, with the quote that occupies it. The pain point is the stored claim that cites the span.

A record without that chain is invalid. It is not stored, and it is not counted as evidence.

## Hashes and ids

- `raw_sha256` is the hash of the fetched bytes. The parser recomputes it and refuses a capture whose bytes changed: `raw_hash_mismatch`.
- `text_sha256` is the hash of the document text and the name of its content-addressed file.
- `doc_id` is `doc_` plus `sha256(fetch_id, source tag, item id, parser version)[:16]`.
- `span_id` is `ev_` plus `sha256(doc_id, char_start, char_end, quote)[:16]`.
- `candidate_id` adds the extractor version to the span basis; `pain_id` adds the extractor version to the span id.

Content hashes are SHA-256 over bytes. Ids are truncated to 16 hex characters for readability.

## The versions

The parser version travels with the document, so a document can be traced back to the parser that read the bytes. A stored candidate carries the provider, the model, the extractor version, the prompt version, and the normalization version. A record without the versions it needs cannot be replayed, so they belong to the record and not only to the run manifest.

## Replay

The same bytes and the same versions produce the same ids, the same text hash, and the same file paths. A replay that produces different ids means a version changed, and that change has to be visible in the record before two runs are compared.

## What to record

- the raw path and the raw hash
- the text path and the text hash
- the source id, the fetch id, and the URL
- the character offsets and the matched quote
- the provider, the model, and the version strings the record needs
- the reason code and the detail for every rejection

A rejection is part of the provenance too. It names the versions that produced it, so a run can be explained without rerunning the model.
