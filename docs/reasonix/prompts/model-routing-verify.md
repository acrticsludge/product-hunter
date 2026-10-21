# Dispatch prompt — Stage 5 VERIFY (code gate)

**Node:** `loop-free-verify-code-1`
**Repo:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Gate:** `uv run pytest`

---

## What to do

1. Run the code gate: `uv run pytest` from the repository root.
2. Report the **verbatim** final summary line. Do not paraphrase it.
3. If it fails, report the failing test ids and the assertion text, verbatim. Do not
   fix anything. You cannot edit.
4. Confirm the two claims that matter most for this change:
   - `uv run pytest tests/test_llm_contract.py` passes with no edits to that file
     (the OpenCode generation bridge must stay compatible).
   - `uv run pytest tests/test_db_migrations.py tests/test_model_catalog.py
     tests/test_model_discovery.py tests/test_model_routing.py` passes.

## Context you need

- The change under test is the dynamic model router: a model catalogue, discovery
  through the OpenCode CLI, a refresher, thirteen role chains with fallback, and the
  persistence of catalogues, the model inventory, role selections and execution
  provenance.
- `db/migrations/0002_model_routing.sql` is new and additive, so the schema version
  moved from 1 to 2. `tests/test_db_migrations.py` was updated for that one fact:
  two version assertions changed from 1 to 2, and four table names were added to
  `EXPECTED_TABLES`. That is the only test change, and it was approved in
  `docs/resonix/plans/model-routing-implementation-plan.md` §1.2.

## Important: this working tree is shared

Other agents are working in the same checkout at the same time. Other files, other
tests and other migrations may appear or change while you run. That is expected.

- Do not edit, revert, stash, clean, or reset anything.
- Do not commit.
- If the gate fails because of a file outside the model-routing change, say so
  explicitly and name the file. Then re-run the gate once and report both results.
- Judge the model-routing work on the files listed in the declared radius:
  `src/product_hunter/llm/{catalog,discovery,routing,opencode,__init__}.py`,
  `src/product_hunter/{config,cli}.py`, `src/product_hunter/db/{model_routing,__init__}.py`,
  `db/migrations/0002_model_routing.sql`, `config/model_routing.yaml`,
  `tests/test_model_{catalog,discovery,routing}.py`, `tests/test_db_migrations.py`.

## Report

The verbatim gate line, the two focused-gate results, and any file outside the
radius that broke the run.
