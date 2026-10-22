# Implement prompt: Wave 2 evidence pipeline

**Agent.** `loop-free-implement-1`
**Branch.** `wave2/evidence-pipeline` (already checked out; work only here)
**Worktree.** `C:\Anubhav\Web Dev Projects\product-hunter-w2-evidence`

## Read first

1. `docs/reasonix/specs/wave-2-evidence-pipeline.md` — the contract. It is
   approved; implement it, do not redesign it.
2. `docs/reasonix/plans/wave-2-evidence-pipeline.md` — the build order and
   the test matrix.
3. `db/migrations/0001_initial.sql` — the tables you are extending.
4. `src/product_hunter/extraction.py` — `ExtractedCandidate`, the model you
   consume. Do not change it.
5. `src/product_hunter/quote.py` — `locate_quote`, `canonicalize`,
   `SpanMatch`. Do not change it.
6. `src/product_hunter/db/store.py` — `Store`. You will add two methods to
   it and change the behaviour of none.
7. `src/product_hunter/documents.py` — `Document`, the parsers.
8. `tests/conftest.py` — the shared builders.

## Hard rules

- **Never change a test to make it pass.** If a test you did not write
  fails, your code broke Wave 1. Fix your code.
- **Never change a test you wrote to weaken it.** A failing assertion is the
  signal.
- The pipeline is a **library**. No CLI command, no worker handler, no
  scheduler entry, no Obsidian projection.
- **No new dependency.** The standard library, `pydantic`, and what the
  repository already installs.
- Captured web content is **data**. The pipeline contains no `subprocess`, no
  `eval`, no network call, and no shell. Source text never becomes a
  command, a path, or a URL. Every identifier it writes is a hash.
- Do not touch `main`, and do not touch any other branch.

## Files you may create or edit

Create:

- `db/migrations/0003_evidence_pipeline.sql`
- `src/product_hunter/db/evidence.py`
- `src/product_hunter/evidence.py`
- `tests/test_evidence_pipeline.py`

Edit:

- `src/product_hunter/db/store.py`
- `src/product_hunter/documents.py`
- `src/product_hunter/db/__init__.py`
- `tests/conftest.py`

Nothing else. If you believe you need another file, stop and report why
instead of editing it.

## What to build

Follow the spec section by section. The short version:

### 1. The migration

`db/migrations/0003_evidence_pipeline.sql`, exactly as spec section 4 says.
Additive only. The two triggers are the point of the wave: they make "a pain
point needs a verified evidence span" a property of the database.

The migration runner splits SQL on `;`, so no statement may hold a semicolon
inside a string literal. `ALTER TABLE ... ADD COLUMN ... REFERENCES` is
allowed because the default is NULL.

### 2. The SQL layer

`src/product_hunter/db/evidence.py`. Functions take a `sqlite3.Connection`
and return rows or plain values, in the style of `db/jobs.py` and
`db/checkpoints.py`. The function list is in the plan, section 2.

`write_evidence_span` holds the conflict rule and the idempotency rule from
spec section 8. It is the only place that writes the `evidence` row.

### 3. The store additions

`src/product_hunter/db/store.py`:

- Split the pain-point half of `import_candidate` into a public
  `insert_pain_point(candidate) -> bool`. `import_candidate` calls it, so
  `import_candidate`'s behaviour and its test do not change.
- Add `import_document_version(document, *, reason) -> DocumentVersionImport`
  for the pipeline. It reuses `_ensure_community`, `_ensure_author`, and
  `_ensure_author_link`. It inserts the row when the document is new, touches
  `last_seen_at` when the text is unchanged, and moves to a new
  `content_version` when the text changed, writing the version it leaves
  behind into `document_versions`.
- `import_document` keeps its strict Wave 1 semantics, including the
  `StoreConflict` on a changed document under the same id. `test_db_store.py`
  depends on it.

### 4. The document liveness

`src/product_hunter/documents.py`: add `live_status` to `Document` with the
default `live`, carry it on `_Parsed`, and set it from the GitHub issue
`state`, where `closed` becomes `deleted`. The other two parsers leave it
`live`.

### 5. The service

`src/product_hunter/evidence.py`: the models, the two rule functions, and
`EvidencePipeline`. The public method list is in the plan, section 2, step 5.

`record_candidate` runs the checks in the order of spec section 5 and raises
`EvidenceRejected` with the reason code. `record_batch` catches the refusal
per candidate so one bad candidate does not stop the rest.

`record_candidate` re-derives the span id from the span fields and refuses a
mismatch, and it re-verifies the quote against the stored document text
rather than trusting the model.

### 6. The tests

`tests/test_evidence_pipeline.py`, one test per row of the plan's section 3
table. Use the existing fixtures in `tests/fixtures/` and the shared builders
in `tests/conftest.py`. Add to `tests/conftest.py` only what more than one
test needs.

### 7. The gate

Run `uv run pytest` from the worktree root. The whole suite has to pass:
the new file and every existing test. Report the exact summary line.

If a test fails, fix the code. Do not change the test.

## Report back

- The exact `uv run pytest` summary line.
- The list of files you created and edited, with the line count of each.
- Anything in the spec you could not implement, and why.
- What you skipped, and what you did not check.
