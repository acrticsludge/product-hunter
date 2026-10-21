# Handoff for wave 2 session 5: source independence and entity resolution

**Run ID:** wave2-independence-20261021
**Branch:** `wave2/independence`
**Worktree:** `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave2-independence`
**Base commit:** `3d7bc68` (main)

## What this session builds

The independence graph and the deterministic evidence-independence analytics.
The system must distinguish raw mentions from independent evidence. The number
of independent people is computed by the analytics layer, never declared by a
model.

## Where the code is

| Area | Path |
|---|---|
| Package | `src/product_hunter/independence/` |
| Store | `src/product_hunter/db/independence.py` |
| Migration | `db/migrations/0003_independence.sql` |
| Policy | `config/independence.yaml` |
| Spec | `docs/reasonix/specs/wave-2-independence.md` |
| Plan | `docs/reasonix/plans/wave-2-independence.md` |
| Prompt | `docs/reasonix/prompts/wave-2-independence-implement.md` |
| Report | `docs/reasonix/reports/wave-2-independence.md` |

## The repository map a later session needs

### Entry points and modules that matter

- `src/product_hunter/db/connection.py`: `connect(path)`. The pragmas, the
  row factory, and the read-only mode. Every writer opens its connection here.
- `src/product_hunter/db/migrations.py`: `migrate(conn)`. Numbered files in
  `db/migrations/`, one transaction each, `PRAGMA user_version` as the only
  version record. The numbers must be contiguous from 1.
- `src/product_hunter/db/store.py`: the Phase 1 import. It writes `sources`,
  `fetches`, `documents`, `communities`, `authors`, `author_links`, `evidence`,
  `pain_points`. The house style for a writer: deterministic `_hash_id` ids,
  a conflict raised when a row changes under the same id.
- `src/product_hunter/documents.py`: `Document`. The `doc_id` is
  `doc_` plus 16 hex of the SHA-256 of
  `fetch_id|source_tag|identity|PARSER_VERSION`. `text_sha256` is the hash of
  the parsed text, so an exact copy of a post has the same `text_sha256`.
- `src/product_hunter/config.py`: `repo_root()`, `database_path()`.
- `src/product_hunter/research/`: the contract, the gate, the predicates, the
  budget. The gate is where `independent_authors` and
  `independent_communities` are already declared as artifact kinds
  (`KIND_AUTHOR`, `KIND_COMMUNITY`); this wave supplies the numbers those
  artifacts will carry.

### The tables this wave touches

Read `db/migrations/0001_initial.sql` for the base schema. The columns this
wave depends on:

- `authors(author_id, source_id, handle, display_name, profile_url)`: unique
  on `(source_id, handle)`. The store already writes a `declared` author link
  for every document.
- `author_links(link_id, author_id, doc_id, confidence, basis, created_at)`:
  an author to a document link. It stays as it is.
- `communities(community_id, source_id, name, url)`: unique on
  `(source_id, name)`. The store derives the name from the source: Hacker News
  for `hn_algolia`, `owner/repo` for `github_issues`, otherwise the source id.
- `documents(doc_id, source_id, community_id, url, title, author,
  published_at, text_sha256, text_path, ...)`.
- `evidence(span_id, doc_id, quote, char_start, char_end, ...)`.
- `propagation_edges(edge_id, from_doc_id, to_doc_id, relation, confidence,
  created_at)`: the table existed with no writer. This wave writes it.

### Commands

- `uv run pytest`: the code gate. The adapter names it as the only gate.
- `uv run python -m product_hunter.cli --help`: the CLI. This wave adds no
  command.
- `uv run python -c "from product_hunter.db import connect, migrate; ..."`:
  the quick way to open a database.

### Tests that cover the touched area

- `tests/test_db_store.py`: the store writer and the conflict rules.
- `tests/test_db_migrations.py`: the migration runner. A new migration must
  keep this suite green.
- `tests/test_research_gate.py`, `tests/test_research_contract.py`: the gate.
  They read `config/research_contract.yaml`, which this wave does not change.

## The rules this session runs under

- The migration directory is a high-risk path. Stages 2, 6, and 7 of the loop
  are not skippable.
- `db/migrations/**` may not be edited outside the radius.
- No dependency may be added to `pyproject.toml`.
- No test may be changed to make it pass.
- The commit uses explicit paths, never `git add -A`.

## Where the numbers come from

| Number | Source of truth |
|---|---|
| `raw_mentions` | the in-scope documents |
| `canonical_content_count` | the components over repost, crosspost, and syndication edges |
| `independent_author_count` | the resolved, confident identity groups |
| `uncertain_author_count` | every other identity group, counted once each |
| `independent_community_count` | the canonical communities with a root observation |
| `propagation_adjusted_evidence` | the canonical groups that are not derived |
| `source_concentration` | the largest share of raw mentions from one source |
| `author_cluster_concentration` | the largest share of raw mentions from one identity group |

`config/independence.yaml` holds every threshold. `min_independent_authors`
and `min_independent_communities` mirror the validation thresholds in
`config/research_contract.yaml`, and a test asserts the two agree.

## Open threads for the next session

1. `research/persistence.py` names a planned `0003_research_gate.sql`. This
   wave took `0003`. The tool-call persistence wave must take `0004` or later
   and must correct that docstring.
2. **The migration number is contested.** Four branches claim `0003`:
   `main` has `0003_research_gate.sql`, `wave2/clustering` has
   `0003_clustering.sql`, `wave2/evidence-pipeline` has
   `0003_evidence_pipeline.sql`, and this wave has `0003_independence.sql`.
   The runner needs the numbers contiguous, so the integrator has to renumber
   the four files into `0003`, `0004`, `0005`, and `0006`, and the
   `PRAGMA user_version` expectations in `tests/test_db_migrations.py` have to
   follow. This branch is cut from `3d7bc68`, where `0003` was free.
3. The DuckDB materialisation of these numbers is not built. The numbers are
   computed in Python over SQLite.
4. The propagation detector is text-only. A paraphrase reads as a separate
   canonical content, so the error is under-counting propagation.
5. Nothing in `src/` writes `documents.live_status`. The collector marks a
   document deleted in a later wave.
6. `record_author_clusters` writes the complete set of identity groups and
   ungroups every author whose cluster is not in that set. A run that
   analysed part of the corpus must not call it.
