# Dispatch: stage 4 IMPLEMENT, wave 2 clustering

**Node:** `loop-free-implement-1`
**Date:** 2026-10-21
**Branch:** `wave2/clustering`
**Spec:** [../specs/wave-2-clustering.md](../specs/wave-2-clustering.md)
**Plan:** [../plans/wave-2-clustering.md](../plans/wave-2-clustering.md)
**Handoff:** [../handoffs/wave-2-clustering-handoff.md](../handoffs/wave-2-clustering-handoff.md)

## Brief

Build the problem clustering engine described in the spec. The spec is the contract. Every numbered rule in it, every field of every table, every row of the test matrix, and the do-not list at the end of the plan are binding.

Work in the repository root, on the branch `wave2/clustering`, which is already checked out. Do not create a branch, do not commit, do not push.

## Order of work

Follow the plan's six steps. Each step leaves the suite green, so run `uv run pytest` after each step and not only at the end.

1. The data: `problems.py`, `similarity.py`, `clusters.py`, the config loader, `config/clustering.yaml`.
2. The migration and the store: `db/migrations/0003_clustering.sql`, `src/product_hunter/db/clustering.py`.
3. The adjudicator: `adjudication.py`.
4. The engine: `engine.py`.
5. The behaviour matrix: `tests/test_clustering_engine.py`.
6. The wiring: `__init__.py` and the helper that turns a result into `ArtifactRecord` values.

## What the code must do

The engine decides which pain points describe the same underlying problem. It never decides whether a problem is a good opportunity.

- Membership is decided on meaning. Two pain points that share every word but sit in different domains do not merge. Two pain points that say the same thing in different words stay together.
- No count of mentions, authors, communities or people appears anywhere in the package.
- History is never rewritten. A merge, a split or a re-cluster leaves the old rows alone and writes a lineage edge to the successor.
- Vectors from two different embedding models are never compared. A different space raises `MixedVectorSpaceError`; the store refuses to apply a result from another space.
- Every model call is recorded with provider, model, model version, prompt version and config version.
- No model is named in the code and no threshold is written in the code. The model and the thresholds come from `config/clustering.yaml`.
- The engine is a pure function of its inputs. It reads no clock and no database. The store owns every write.

## The conventions to follow

Read `docs/reasonix/handoffs/wave-2-clustering-handoff.md` for the map of the code that already exists, and follow the conventions it lists. In short: pydantic records with `extra="forbid"`, a `reason_code` on every exception, error details that carry ids and counts and never raw untrusted text, deterministic ids of a prefix plus 16 hex characters of a SHA-256, ISO-8601 timestamps, and a `shortcut:` comment for every limit that is known and deferred.

Look at `src/product_hunter/db/model_routing.py` and `src/product_hunter/db/store.py` before writing the store. Look at `src/product_hunter/extraction.py` before writing the prompt and the strict parsing. Look at `src/product_hunter/config.py` before writing the config loader.

## The tests

Three files: `tests/test_clustering_core.py`, `tests/test_clustering_engine.py`, `tests/test_clustering_store.py`. Every row of the spec's test matrix gets a test.

The engine tests use two doubles defined in the test files, never in the package: a scripted embedder that answers a vector per canonical text, and a scripted adjudicator that answers a decision per pair of pain ids. No test touches the network, a model or an embedding package.

## The radius

Only the files listed in the handoff's section 6 may be created or edited. `src/product_hunter/config.py` is the only existing source file that may be edited, and only to add the clustering config models and the loader.

Do not change `config/model_routing.yaml`, `config/research_contract.yaml` or `config/sources.yaml`. Do not change the extraction, fetch, safety, quote, documents, registry or research code. Do not add a dependency. Do not add a CLI command or a worker handler in this wave.

## What to report back

- The list of files created and edited, with line counts.
- The full `uv run pytest` result, verbatim, with the exit code.
- The result of the clustering package's own test run, verbatim.
- The public names the spec's section on public names asks for, as they actually landed.
- Every place where the spec and the code disagree, and which one you followed.
- Every limit you deferred, and whether it carries a `shortcut:` comment.
- What you did not check.
