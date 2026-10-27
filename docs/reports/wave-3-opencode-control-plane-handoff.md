# Handoff: wave 3 session 8, the OpenCode control plane

Branch `wave3/opencode-control-plane`, worktree
`.worktrees/wave3-opencode-control-plane`, base commit `00a6ca3` (main).
Nothing on `main` is touched and no other Wave 3 branch is merged.

## Where the code is

| Thing | Where |
| --- | --- |
| The state layer the OpenCode side speaks to | `src/product_hunter/registry.py` (1214 lines) |
| The plugin that carries one request across | `.opencode/plugins/product-hunter.ts` |
| The slash commands | `.opencode/commands/*.md` |
| The run tables | `db/migrations/0001_initial.sql` (`research_runs`, `research_events`, `research_checkpoints`, `jobs`) |
| The research contract | `config/research_contract.yaml`, loaded by `load_research_contract()` in `src/product_hunter/research/contract.py:425` |
| Lease recovery | `recover_expired_leases()` in `src/product_hunter/db/jobs.py:351`. It opens its own `BEGIN IMMEDIATE`, so never call it inside another transaction. |
| The worker's private masking | `src/product_hunter/runtime/worker.py:56` and `:473` |

## What was verified, and how

OpenCode v2.1.2 is the installed version.

- A command under `.opencode/commands/` was found and its `$ARGUMENTS`
  expanded. The proof: `POST /api/session/{id}/command` with
  `{"name":"research-status","text":"RH-20261022-0001"}` produced the user
  prompt "Call the `product_hunter` tool with action `status` and research_id
  `RH-20261022-0001`." The model call that followed hit a provider free-tier
  limit, which is a quota and not a fault in the command.
- The plugin loads and its tool is callable. `product_hunter({action:"list"})`
  returned the run list.
- The V2 tool result shape, from the installed `@opencode/schema@2.1.2`:
  `Result = { output?, content?, metadata? }`, and the documented example
  returns `{ content }`.
- The `question` tool exists in V2, so `/continue` can ask the operator.
- The repository has no `package.json`, so `@opencode/plugin` cannot be
  imported. The plugin must keep using the Node standard library only and
  default-exporting a plain object.

## The defects to fix

1. `retry_run` accepts `FAILED_PERMANENT` (`registry.py:95` `_RETRYABLE`).
   A permanent failure is not retryable. The refusal in `continue_run`
   (`registry.py:645`) even says "retry it instead", so the two contradict.
2. An unknown stored status is treated as an ordinary non-terminal state, so a
   malformed run is eligible to `continue`.
3. `PAUSE_REQUESTED` and `CANCEL_REQUESTED` are written but nothing acts on
   them. The worker never reads the run status.
4. `/research-status` shows the state, the stage, the progress and the update
   time. It does not show the budget, the blockers, the latest checkpoint, the
   last activity or the errors.
5. Nothing in the registry masks a secret. `research_runs.last_error` and the
   job errors go to the model unfiltered.
6. There is no doctor and no recover.

## The decisions the snapshot settled

A SubQ choice snapshot over the state above returned these, with the
probabilities it reported:

- Retry of a permanent failure: refuse and name a new research request
  (0.969, confidence 0.953).
- A stale pause or cancel request: the control plane reports it and one
  explicit recover command resolves it (0.970, confidence 0.954). A read never
  writes.
- The budget in the status: the contract limits with the measured spend
  beside them (0.879, confidence 0.818).

## The shape the work takes

### `src/product_hunter/redaction.py`, new

Holds the labelled-secret pattern, the length bound, `redact(text)` and
`safe_error(exc)`. The worker imports it and drops its private copies, so the
masking rule lives in one place.

### `src/product_hunter/registry.py`

- A frozenset of the known states. `eligible_runs` drops a run whose stored
  status is outside it.
- `FAILED_PERMANENT` leaves the retryable set. The refusal names the failure
  as permanent and points at a new research request. The refusal in
  `continue_run` changes to agree.
- `RunView` gains `budget`, `blockers`, `latest_checkpoint`, `last_activity`
  and `errors`, each with a default so nothing that builds a view breaks. A
  `_detail` helper fills them. The list keeps the cheap view.
- `render_run` keeps the lines the existing tests assert on (`state:`,
  `progress:`, `worker:`) and adds the rest. `render_runs` marks the runs that
  can be continued right now.
- A `doctor` action that only reads.
- A `recover` action that resolves the outstanding requests whose worker has
  no live lease, and requeues the jobs whose lease expired. Every run it
  touches gets a `RESEARCH_RECOVERED` event.

### `.opencode/plugins/product-hunter.ts`

- The result becomes `{ content, metadata }`.
- `doctor` and `recover` join the action list.
- The argument vector keeps its equals form.

### `.opencode/commands/`

`research-doctor.md` and `research-recover.md` are new. The ten that exist
keep their names and their meaning.

### The tests

`tests/test_registry.py` grows the cases the mission names.
`tests/test_plugin_control_plane.py` is new and drives the real plugin file
with node, the way `tests/test_plugin_project_root.py` does.

## The rules that bind the change

- Never `git add -A`. Stage the paths by name.
- Never commit `.env`, `data/`, `runtime/` or `logs/`.
- Never change a test to make it pass. A test that encodes the old retry
  behaviour would have to change, because the contract changed on purpose; that
  one change has to be called out in the report, with the reason.
- The subprocess call keeps an argument vector and never a command string.
- No untrusted text reaches a shell, a tool name or a URL.
- The database stays the authority. OpenCode holds no state of its own.
