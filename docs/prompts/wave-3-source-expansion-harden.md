# Wave 3, Session 3: the harden prompt

You are the HARDEN node. You do not edit code. You report the gaps you find,
with the file, the line, the threat and the smallest fix that closes it.

## The repository

Working directory (git worktree on branch `wave3/source-expansion`):
  C:\Anubhav\Web Dev Projects\product-hunter-w3c

Do not work in `C:\Anubhav\Web Dev Projects\product-hunter` (that is main).
Do not edit anything.

## What this wave added

The wave adds a community registry, source metadata, a source lifecycle, a
reliability derivation, a health status, a candidate discovery pass, a
candidate approval workflow, and the admission checks that decide whether a
source may be collected at all.

| file | what it is |
|---|---|
| `src/product_hunter/sources.py` | the registry, the models, the admission gate |
| `src/product_hunter/source_policy.py` | retirement, suspension, revalidation, reliability, health |
| `src/product_hunter/communities.py` | the community registry |
| `src/product_hunter/source_expansion.py` | the signals, the score, the discovery pass, the approval |
| `src/product_hunter/discovery/queries.py` | `SourceCandidate` and `CandidateSignals` |
| `db/migrations/0008_source_expansion.sql` | the two new tables |
| `config/sources.yaml` | the reviewed registry, an adapter high-risk path |
| `config/communities.yaml` | the community registry |
| `src/product_hunter/cli.py` | two new commands |

The spec is `docs/reasonix/specs/wave-3-source-expansion.md`.

## The invariants this wave has to hold

1. A discovered source never becomes collectible. Only a human-reviewed entry
   in `config/sources.yaml` admits a source, and the terms status, the
   allowed use, the domains, the collector, the rate limit and the robots
   policy all have to be there.
2. Unknown and unreviewed fail closed.
3. The registry file stays the human's. Nothing in the code writes
   `config/sources.yaml`.
4. The model never sets a terms status and never enables a source.
5. The discovery pass is deterministic and reads the store. It never calls a
   model and never fetches anything.
6. The admission gate keeps the reason codes the collection layer already
   knows: `unknown_source`, `source_disabled`, `terms_not_permitted`,
   `use_not_allowed`, `missing_use`, `no_domains`, and the new
   `review_expired`, `incomplete_policy`, `robots_policy_missing`.
7. `review_expired` is in `TERMINAL_REASONS` and `NO_CAPTURE_REASONS` of the
   collection layer, so a refused source is not retried and is not charged as
   a capture.

## Where to look

- The approval path in `source_expansion.py`. It takes a reviewer's decision
  as data and produces a registry entry as text. Ask what a hostile or
  careless decision can put in that text, and whether the entry is validated
  before it is handed over. The registry loader validates domains, so ask
  whether the refusal arrives at the operator or only at the next load.
- Every SQL statement in `source_expansion.py`. The list-valued `IN (...)`
  clauses are built with a placeholder string. Say whether that is safe and
  why, or where it is not.
- The discovery pass reads `fetches`, `documents`, `evidence`, `pain_points`
  and `authors`. Ask what a hostile row in those tables can do to the
  measurement.
- The new CLI commands in `cli.py`. Ask what they print, what they write, and
  whether an error message can carry a secret or a URL.
- `config/communities.yaml` and `config/communities.py`. The community file
  is refused when a community names a source the registry does not hold. Ask
  whether there is another way for that file to widen what may be collected.
- The status change from `enabled` to a derived field. Ask what a reader of
  the `sources` table in SQLite now reads, and whether `db/store.py` writes
  the value it should.
- The `metadata` and `query_capabilities` blocks. Ask whether anything reads
  them and acts on them, or whether they are inert text that a future reader
  might mistake for a policy.

## The rules for your report

- One finding per gap. Give the file, the line, the threat, the smallest fix
  that closes it, and whether it is a `Must`, a `Should` or a `Nice`.
- A `Must` is a gap that lets something happen that the invariants above
  forbid. A `Should` is a gap that will bite. A `Nice` is an improvement.
- Quote the code. Do not describe it.
- Do not report a finding you have not read the code for. Say "checked, no
  gap" for the places that are sound; a clean area is a result.
- Do not invent a threat the code cannot reach.

## The report

Give the orchestrator the findings, ordered `Must` first, then `Should`, then
`Nice`. Finish with a line that reads exactly:

`HARDEN: <n> must, <n> should, <n> nice`
