# Plan: the OpenCode control plane

## Order of work

The work runs in the order the dependency allows. The masking helper comes
first because two later pieces need it, the state layer comes next because the
plugin and the commands only speak to it, and the OpenCode side comes last
because it is thin.

### 1. One place that masks a secret

`src/product_hunter/redaction.py` is new. It holds the labelled-secret pattern
and the length bound that `runtime/worker.py` keeps privately today, plus a
`redact(text)` and a `safe_error(exc)` for callers that hold text rather than an
exception. The worker imports it and drops its private copies. The control
plane uses it on every error string it renders.

The pattern is the worker's own, widened a little: `authorization`, `cookie`,
`session` and `access key` join the list, and a quoted value is masked whole
rather than up to the first space.

### 2. The state layer

`src/product_hunter/registry.py` carries the changes.

**Known states.** A frozenset of the `RunState` values. A stored status outside
it is malformed. `eligible_runs` drops malformed runs, so a corrupt row is
never handed to a worker as work.

**Retry.** `FAILED_PERMANENT` leaves the retryable set. The refusal names the
failure as permanent and points at a new research request, because the same
objective again will fail the same way. The refusal in `continue_run` is
changed to agree, since it used to say "retry it instead".

**The enriched run.** `RunView` gains five fields with defaults, so nothing
that builds a view breaks: `budget`, `blockers`, `latest_checkpoint`,
`last_activity` and `errors`. A `_detail` helper fills them from the run row,
the contract and the tool call count. The list stays on the cheap view, so a
listing of five hundred runs does not run five hundred extra queries.

**The run block.** `render_run` keeps the lines the tests already assert on
(`state:`, `progress:`, `worker:`) and adds the budget, the checkpoint, the
last activity, the blockers and the errors. `render_runs` marks which runs can
be continued right now.

**The doctor.** A new action that reads and writes nothing. It reports the
contract version, how many runs sit in each state, whether any worker holds a
lease, the outstanding requests whose worker is gone, the malformed states and
the jobs whose lease expired.

**The recover.** A new action that resolves the outstanding requests whose
worker has no live lease: a pause request becomes a pause, a cancel request
becomes a cancellation, and the queued jobs of a cancelled run are cancelled
with it. It also requeues the jobs whose lease expired, through the same
`recover_expired_leases` the `ph db recover` command uses, outside any
transaction of its own. Every run it touches gets a `RESEARCH_RECOVERED`
event.

### 3. The OpenCode side

`.opencode/plugins/product-hunter.ts` changes in three ways. The tool result
becomes `{ content, metadata }`, which is the shape the installed V2 schema
documents; the action list gains `doctor` and `recover`; and the argument
vector keeps its equals form so a value that starts with a dash stays a value.
Nothing else moves. The plugin still uses the Node standard library only, still
spawns one fixed module with an argument vector, and still never runs a stage.

Two new command files appear: `.opencode/commands/research-doctor.md` and
`.opencode/commands/research-recover.md`. The ten that exist keep their names
and their meaning; their bodies are tightened where they said more than the
tool returns.

### 4. The tests

`tests/test_registry.py` grows the cases the mission names: the budget, the
blockers, the checkpoint, the last activity and the errors in the status; a
retry of a permanent failure; a malformed state; a stale request; the doctor;
the recover; a secret in an error string; two commands at once.

`tests/test_plugin_control_plane.py` is new. It drives the real plugin file
with node, the way `tests/test_plugin_project_root.py` already does, and
checks the action list, the argument vector, the equals form and the absence
of a shell.

## What the change deliberately does not do

It does not teach the worker to read the run status, so a pause issued against
a live worker still waits for the worker. That is an integration requirement
and the report says so. It does not write a budget ledger. It does not move
persistence out of SQLite, and it does not make OpenCode the authority for
anything.
