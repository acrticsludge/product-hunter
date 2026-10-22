# Review prompt: Wave 2 evidence pipeline

**Agent.** `loop-free-review-1`
**Worktree.** `C:\Anubhav\Web Dev Projects\product-hunter-w2-evidence`
**Branch.** `wave2/evidence-pipeline`
**Base commit.** `3d7bc68`

You are the adversarial reviewer. Try to prove the change wrong. You do not
edit code.

## The diff

`git diff 3d7bc68` plus the untracked new files:

- `db/migrations/0003_evidence_pipeline.sql`
- `src/product_hunter/db/evidence.py`
- `src/product_hunter/evidence.py`
- `tests/test_evidence_pipeline.py`
- `docs/reasonix/specs/wave-2-evidence-pipeline.md`
- `docs/reasonix/plans/wave-2-evidence-pipeline.md`
- `docs/reasonix/reports/wave-2-evidence-pipeline.md`

Use `git status --porcelain` to see the untracked files and read them.

## The contract

`docs/reasonix/specs/wave-2-evidence-pipeline.md` is the approved spec and
`docs/reasonix/plans/wave-2-evidence-pipeline.md` is the plan. Read both.

The mission: a production evidence pipeline that turns collected source
documents into durable, traceable evidence, preserving provenance at every
step, so that no downstream pain point can exist without traceable evidence.
The chain is RAW SOURCE -> DOCUMENT -> EVIDENCE SPAN -> CLAIM/PAIN POINT ->
CLUSTER -> OPPORTUNITY. Clustering, opportunity scoring, competitor
investigation, the Obsidian projection, and Phase 7 integrations are out of
scope and must not be implemented.

## The rubric

Use the `ponytail-review` skill: six passes, in order, correct, safe, load,
tested, fast, lean. Read the connected code, not only the diff.

Then answer:

1. Does the code do what the spec says? Go clause by clause through spec
   sections 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, and check the implementation
   against each. Name any clause the code does not honour, and any behaviour
   the code has that the spec does not describe.
2. Is any test proving a behaviour the code does not have, or asserting
   something weaker than the spec requires? Name the test and what it fails
   to catch.
3. Is any test asserting the wrong thing, that is, freezing a bug?
4. Did the wave duplicate something that already existed? The repository
   already had `product_hunter.quote`, `product_hunter.extraction`,
   `product_hunter.documents`, `product_hunter.normalization`, and
   `product_hunter.db.store`.
5. Did the wave change the behaviour of the Wave 1 code it touched in a way
   the existing tests do not cover? The touched files are
   `src/product_hunter/db/store.py`, `src/product_hunter/db/migrations.py`,
   `src/product_hunter/documents.py`, `src/product_hunter/extraction.py`,
   `src/product_hunter/db/__init__.py`, and `tests/test_db_migrations.py`.
6. Is the scope held? Any CLI command, worker handler, scheduler entry,
   projection handler, model call, clustering, opportunity scoring,
   competitor work, or Obsidian work is a scope breach and a `Must fix`.
7. Lean. What can be deleted? Name the lines and say how many. If the code
   is already lean, say so, and do not manufacture a count.
8. Is anything in the new code or the new documentation wrong, overstated,
   or unreadable? The documentation is part of the deliverable.

## Rules

- Do not edit any file. Do not change any test. Scratch scripts go in the
  system temp directory and get deleted.
- Every finding needs a severity, a file and a line, a plain-English
  statement of the problem, and a concrete way it fails. A finding with no
  failure mode belongs under `Nice to have` at most.
- If there is no `Must fix`, say so plainly. Do not inflate a `Should fix`
  to look thorough. A clean review is a valid outcome.
- End with a `Verdict:` line that is exactly one of `ship`,
  `ship with fixes`, `do not ship`.
- End with a `Lean:` line in the form `Lean: -N lines possible`, and N may
  be 0.

## Report back

The six-pass findings ordered by severity, the answers to the eight
questions, the `Verdict:` and `Lean:` lines, and what you did not check.
