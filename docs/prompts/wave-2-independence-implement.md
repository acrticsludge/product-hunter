# Implement prompt for wave 2 session 5: source independence and entity resolution

**Worktree:** `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave2-independence`
**Branch:** `wave2/independence` (already checked out; never touch `main`)

## Read first

1. `docs/reasonix/specs/wave-2-independence.md`: the approved spec. Every
   rule, name, field, threshold, and test case in it is binding.
2. `docs/reasonix/plans/wave-2-independence.md`: the build order and the
   decisions already taken.
3. `src/product_hunter/db/store.py`: the house style for a SQLite writer
   (deterministic `_hash_id` ids, conflict on a changed row, one transaction).
4. `src/product_hunter/research/contract.py`: the house style for a typed,
   validated config with a fail-closed loader.
5. `src/product_hunter/research/evidence.py`: the house style for pydantic
   records with `extra="forbid"`.
6. `src/product_hunter/config.py`: `repo_root()` for config paths.
7. `db/migrations/0001_initial.sql` and `db/migrations/0002_model_routing.sql`:
   the migration style.
8. `tests/conftest.py` and `tests/test_db_store.py`: the house style for tests.

## What to build

Exactly these files, exactly these paths. Do not add a file that is not on
this list, and do not edit a file that is not on this list.

| Path | Contents |
|---|---|
| `src/product_hunter/independence/__init__.py` | public exports |
| `src/product_hunter/independence/policy.py` | `IndependencePolicy`, the loader, the validator |
| `src/product_hunter/independence/records.py` | every pydantic record from the spec §6 |
| `src/product_hunter/independence/identity.py` | author resolution, community resolution, author clusters |
| `src/product_hunter/independence/propagation.py` | repost / crosspost / quote / syndication detection |
| `src/product_hunter/independence/graph.py` | the independence graph, canonical content groups, chains |
| `src/product_hunter/independence/analytics.py` | `analyze()` and `check_concentration()` |
| `src/product_hunter/db/independence.py` | `IndependenceStore` |
| `db/migrations/0003_independence.sql` | the migration, verbatim from the spec §11 |
| `config/independence.yaml` | the policy, verbatim from the spec §5 |
| `tests/test_independence_identity.py` | the identity cases |
| `tests/test_independence_propagation.py` | the propagation cases |
| `tests/test_independence_analytics.py` | the analytics and concentration cases |
| `tests/test_independence_store.py` | the persistence and migration cases |
| `src/product_hunter/db/__init__.py` | add the new exports, keep `__all__` sorted |

The spec is the contract. Where this prompt and the spec differ, the spec
wins. Where the spec is silent, follow the house style of the files listed
above.

## The rules that matter most

1. **No LLM decides a number.** `analyze()` builds `IndependenceMetrics` from
   the graph and from nothing else. A caller can supply signals; the resolver
   applies the configured weight to them and nothing more. A `model_signal`
   alone can never produce a `same-person-merged` edge. The code enforces this
   with a score cap, and `IndependencePolicy` refuses a configuration in which
   it would not hold.

2. **Never merge on a display name.** A display-name match alone produces no
   edge at all, not even a suspect edge.

3. **A conflicted pair is never a merge.** When a negative signal fires
   alongside a positive one, the score is capped at `conflict_score_cap` and
   the edge carries `conflict=True`.

4. **Determinism.** No clock, no file, no query, no randomness, and no
   dictionary-iteration order inside the analysis package. Every timestamp is
   injected by the caller. Every id is a content hash. Every collection that
   reaches an output is sorted.

5. **Fail closed.** An invalid policy raises, naming the rule it broke. A
   policy that cannot be loaded is not a default policy.

6. **Never change a test to make it pass.** The test matrix in the spec §12 is
   the acceptance criteria. If a test fails, fix the code.

7. **Do not add a dependency.** `pydantic`, `pyyaml`, `httpx`, and the
   standard library only. `pyproject.toml` does not change.

8. **Do not implement** scoring, market sizing, competitor analysis, Obsidian,
   Qdrant, or DuckDB materialisation.

## Style

- `from __future__ import annotations` at the top of every module.
- Pydantic v2. `ConfigDict(extra="forbid")` on every record.
- Docstrings that say what the code guarantees, in the voice the neighbouring
  modules use: short, specific, and never restating the code.
- Comments only where the code cannot show the why. One line.
- Type hints on every public function.
- No `print`. No `logging` calls that fire on the happy path.
- Line length under 100 characters.

## Finish

1. `uv run pytest` passes, with the new tests included.
2. `uv run ruff check src tests` is clean, if ruff is available. It is not a
   dependency of the project, so do not add it; if it is not installed, skip
   this step and say so.
3. Report back:
   - the exact `uv run pytest` summary line,
   - the list of files you created or changed,
   - any place where the spec was ambiguous and what you decided,
   - anything you did not implement and why.

Do not commit. Do not push. Do not change `main`.
