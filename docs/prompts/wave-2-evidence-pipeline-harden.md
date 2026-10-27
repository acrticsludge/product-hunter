# Harden prompt: Wave 2 evidence pipeline

**Agent.** `loop-free-harden`
**Worktree.** `C:\Anubhav\Web Dev Projects\product-hunter-w2-evidence`
**Branch.** `wave2/evidence-pipeline`

You report gaps. You do not edit code.

## What to read

- `docs/reasonix/specs/wave-2-evidence-pipeline.md`, especially sections 5,
  6, 8, 10, and 11.
- `db/migrations/0003_evidence_pipeline.sql`
- `src/product_hunter/evidence.py`
- `src/product_hunter/db/evidence.py`
- the diff of `src/product_hunter/db/store.py`, `src/product_hunter/db/migrations.py`,
  `src/product_hunter/documents.py`, and `src/product_hunter/extraction.py`
  against the base commit `3d7bc68`.
- `.loop/adapter.json` for the forbidden rules that bind this repository.

Use `git diff 3d7bc68 -- <path>` for a changed file.

## What to check

1. **Untrusted content.** Captured web content is data. Look for any path
   where source text, a model quote, a title, an author handle, a URL, or a
   document id could become a shell argument, a command, a path component, a
   URL, an SQL fragment, a log format string, or a lookup key that decides
   control flow.
2. **SQL injection.** Every statement must bind its values. The only
   identifier interpolated into SQL text may be a column name the code
   chooses.
3. **Provenance integrity.** Can a row reach the store that does not trace
   back to a real capture? Check the quote against the document text, the
   hashes, the pain point and link triggers, the document's fetch, and the
   document's text file.
4. **The triggers.** Do the rules hold on every write path, including
   `INSERT OR IGNORE` and `ON CONFLICT DO UPDATE`? Try to break them with a
   throwaway script.
5. **Transaction boundaries.** Is anything written outside a transaction that
   should be inside? What state is left after a refusal, and can a retry
   repair it?
6. **Idempotency and replay.** Writing the same span twice, ingesting the
   same document twice, and replaying a batch must not change the row
   counts. Look for a row whose id is not a hash of its own content.
7. **Error leakage.** No raw `sqlite3` exception may reach a caller of the
   public API. Try to make one leak.
8. **Denial of service by the input.** Can a hostile document make the
   pipeline consume unbounded time, memory, or disk?
9. **Secrets.** Nothing in the new code may read, write, log, or embed a
   secret.

## What to report

For each finding: a severity of `Must fix`, `Should fix`, or `Nice to have`,
the file and the line, what the problem is in plain English, the concrete
way it fails, and what you would change.

Also report what you checked and found clean, and list the attack or misuse
paths you attempted, including the ones that did not work.

Do not edit any file. Scratch scripts go in the system temp directory and get
deleted.
