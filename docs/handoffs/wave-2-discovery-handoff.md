# Handoff: Wave 2, Session 2, discovery engine

**Run:** Wave 2 Session 2, discovery engineer
**Branch:** `wave2/discovery-engine`
**Worktree:** `C:\Anubhav\Web Dev Projects\product-hunter-wave2-discovery`
**Base commit:** `3d7bc68`
**Spec:** `docs/reasonix/specs/wave-2-discovery.md`
**Plan:** `docs/reasonix/plans/wave-2-discovery.md`

## The task in one paragraph

Build the discovery engine: the code that decides **what** to search. It keeps
a persistent query frontier, expands it along code-defined query families,
measures the yield of every search, and stops on a deterministic saturation
signal. Collection, which decides how a source is fetched, is a different wave
and is not touched here.

## Repo map

| Path | What it is |
|---|---|
| `src/product_hunter/discovery/` | the new package, six modules plus an init |
| `src/product_hunter/research/` | the contract, the budget, the provenance types, the gate |
| `src/product_hunter/sources.py` | the source registry and its legality rules |
| `src/product_hunter/normalization.py` | `NormalizedProblem`, the input to the derived families |
| `src/product_hunter/db/checkpoints.py` | the checkpoint container |
| `db/migrations/0001_initial.sql` | `research_events`, `spend_ledger`, `research_checkpoints` |
| `config/research_contract.yaml` | the shipped contract, its budget and its stopping block |
| `config/sources.yaml` | the admitted sources |
| `tests/test_db_checkpoints.py` | the pattern for a test that needs a run row |

## What the repository already gives, and what the new code must not rebuild

- `research.budget.BudgetTracker` already tracks per-run, per-stage and
  per-source spend and refuses the call that would break a limit. The
  discovery budget consults it rather than replacing it.
- `research.evidence.ToolCallProvenance`, `ToolCallStatus` and
  `ZeroResultOutcome` are the provenance vocabulary. The discovery record
  converts into a `ToolCallProvenance` so the gate can read it.
- `research.predicates` reads the family off the `query_family` field of a
  tool call. The conversion puts the family there.
- `db.checkpoints.save_checkpoint` and `load_checkpoint` are the checkpoint
  container. `load_checkpoint` with no version returns the newest one.
- `normalization.NormalizedProblem` is the input to the competitor and
  counter-evidence families.
- `sources.SourceRegistry.require` is what enforces the terms rules. The
  frontier never calls it; the caller passes the admissible source ids in.

## Integration points, verified

- `research_events` has no public writer. `registry._insert_event` and
  `store.Store._insert_event` are both private, so `discovery/store.py` owns
  its own insert. The event id convention is `evt_` plus 16 hex characters of
  a SHA-256 basis.
- `spend_ledger` has no writer today. Its columns are `entry_id`, `run_id`,
  `stage`, `tool`, `provider`, `model`, `units`, `unit_type`, `cost_micros`,
  `created_at`. There is no foreign key on `run_id`.
- `save_checkpoint` refuses a run that is not in the store, and
  `research_events.run_id` is a foreign key. A test therefore inserts a
  `research_runs` row first, the way `tests/test_db_checkpoints.py` does.
- The literal stage name `DISCOVERY` is used by `registry.create_run` and
  `registry._queue_continuation` when they enqueue a job. No handler is
  registered for it anywhere. This wave adds no handler, so the stage name
  stays a string.

## Decisions taken at stage 1

The persistence approach was the one genuinely ambiguous shape, so it went to
SubQ Snap. The answer was to reuse `research_events`, `spend_ledger` and
`research_checkpoints` rather than add a migration: choice `A`, probability
0.977, confidence 0.953, and a 0.981 confidence that this is the
lower-integration-risk option. The migration number `0003` stays reserved for
the research-gate wave named in `docs/reasonix/ARCHITECTURE.md`.

SubQ call: 1 call, 793 input tokens, 0 output tokens, 0 errors, 0 cache hits,
model `subq-snap-preview`.

## Assumptions carried into the code

- The collection engine is a separate wave. It hands the frontier the result
  identities after a search, and it decides what "useful" means.
- The set of admissible sources arrives as a list of source ids. The caller
  obtained it from the source registry.
- A discovered source is a proposal. It carries `terms_status` of `unknown`
  and no allowed use, and it stays a proposal until a human reviews it.

## Boundaries this run must not cross

- The blast radius is the twenty files declared at stage 0. Nothing outside it
  is edited.
- No migration, no table change, no change to `config/sources.yaml`.
- No CLI command, no worker handler, no job dispatch.
- No HTTP client. The frontier never calls a search endpoint.
- `main` is not touched, and no other wave-2 branch is merged.

## The rule that shapes everything

The strategy lives in worker-side code. A model may propose a query; it may
not pick the family, pick the next expansion, decide the wording of a variant,
or decide when to stop. Those are code decisions with names and reasons, and
they reproduce.
