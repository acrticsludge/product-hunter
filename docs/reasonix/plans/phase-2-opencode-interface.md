# Phase 2 â€” OpenCode control interface (plan)

Spec: `docs/reasonix/specs/phase-2-opencode-interface.md`.
Plan choice: an OpenCode project plugin plus markdown commands, calling the
Python state layer as a subprocess. The SubQ Snap decision model chose this
plan over an MCP server with a probability of 0.97 and a confidence of 0.95.

## Files

| File | Change |
| --- | --- |
| `src/product_hunter/registry.py` | new. The state layer: schemas, transitions, formatting, CLI entry point |
| `src/product_hunter/cli.py` | one `research` subcommand that delegates to `registry.main` |
| `tests/test_registry.py` | new. Deterministic tests over a temporary SQLite file |
| `.opencode/plugins/product-hunter.ts` | new. Registers the `product_hunter` tool |
| `.opencode/commands/*.md` | new. Ten command templates |
| `README.md` | the new command and the OpenCode surface |

## `src/product_hunter/registry.py`

### Imports

Only what the repo already has: `sqlite3`, `json`, `datetime`, `enum`,
`dataclasses`, `pathlib`, `pydantic`. Reuse `product_hunter.db` for
`connect`, `migrate`, `enqueue_job`, `save_checkpoint`, `list_checkpoints`,
`list_jobs`. Reuse `product_hunter.config` for `database_path` and
`repo_root`. Reuse `product_hunter.fetch.utcnow` for the clock.

### States

```python
class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    WAITING_HUMAN = "WAITING_HUMAN"
    BLOCKED_TOOL = "BLOCKED_TOOL"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_PERMANENT = "FAILED_PERMANENT"
    COMPLETED = "COMPLETED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"

TERMINAL = frozenset({RunState.COMPLETED, RunState.CANCELLED})
```

`_normalize_state(value)` maps a stored value onto a state: uppercase, and
`completed` to `COMPLETED`, `failed` to `FAILED_RETRYABLE`, `cancelled` to
`CANCELLED`, `running` to `RUNNING`, `queued` to `QUEUED`, `paused` to
`PAUSED`. A value with no mapping is returned uppercased, so a later state
does not break the interface.

### Schemas

```python
class CreateRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    chat_id: str | None = None

class TargetRequest(BaseModel):
    research_id: str = Field(min_length=3, max_length=128,
                              pattern=r"^[A-Za-z0-9._-]+$")

class LogRequest(TargetRequest):
    limit: int = Field(default=50, ge=1, le=500)

class RunView(BaseModel):
    public_id: str
    run_id: str
    objective: str
    status: str
    current_stage: str | None
    current_step: str | None
    progress_percent: int
    resume_count: int
    created_at: str
    updated_at: str
    started_at: str | None
    completed_at: str | None
    last_heartbeat: str | None
    last_error: str | None
    chat_id: str | None
    worker_active: bool
    job_count: int
    checkpoint_count: int
    event_count: int
    artifact_count: int

class CommandResult(BaseModel):
    ok: bool
    action: str
    message: str
    text: str = ""
    run: RunView | None = None
    runs: list[RunView] = Field(default_factory=list)
    needs_selection: bool = False
    code: str | None = None
```

### Errors

```python
class RegistryError(Exception):
    code = "internal_error"
class UnknownResearch(RegistryError):
    code = "unknown_research"
class InvalidRequest(RegistryError):
    code = "invalid_request"
class InvalidTransition(RegistryError):
    code = "invalid_transition"
```

Each carries the message. `UnknownResearch` also carries the ids that do
exist, capped at ten.

### Functions

```python
def create_run(conn, request: CreateRequest, *, now=None) -> RunView
def list_runs(conn, *, status=None, limit=50) -> list[RunView]
def get_run(conn, research_id: str) -> RunView
def continue_run(conn, research_id: str) -> RunView
def resume_run(conn, research_id: str) -> RunView
def pause_run(conn, research_id: str) -> RunView
def retry_run(conn, research_id: str) -> RunView
def cancel_run(conn, research_id: str) -> RunView
def run_log(conn, research_id: str, *, limit=50) -> dict[str, object]
def eligible_runs(conn) -> list[RunView]
def execute(conn, action: str, *, research_id=None, objective=None,
            chat_id=None, limit=50) -> CommandResult
```

`execute` is the single dispatcher the CLI and the plugin both reach. It
catches `RegistryError` and returns a failed `CommandResult` with the code. It
never raises to the caller.

### Behaviour

`create_run`
1. Strip the objective. A run whose objective matches, case folded and with
   whitespace collapsed, and that is not terminal, is returned unchanged. The
   message says the existing run was reused.
2. Otherwise assign `public_id` as `RH-YYYYMMDD-NNNN`, where `NNNN` is the
   count of runs with that date plus one, zero padded to four. Assign
   `run_id` as `run_` plus the first sixteen hex characters of the SHA-256 of
   the public id.
3. Insert the run row: status `QUEUED`, priority 0, progress 0, resume count
   0, contract version `phase2-v1`, `chat_id`, timestamps.
4. Save the first checkpoint at stage `PLAN`, step `research_requested`.
5. Insert the event `RESEARCH_STARTED`.
6. Enqueue the first job: stage `DISCOVERY`, idempotency key
   `research_start:<run_id>`, priority 0.
7. All of it inside one transaction.

`continue_run`
- Refuse a terminal run, `FAILED_PERMANENT`, and a run with a live worker
  lease.
- Set the status to `QUEUED`, raise `resume_count` by one, enqueue a
  continuation job with idempotency key `research_continue:<run_id>:<count>`
  at priority 0, insert `RESEARCH_RESUMED`.

`pause_run`
- Refuse a terminal run.
- With a live worker lease: status `PAUSE_REQUESTED`, event
  `RESEARCH_PAUSE_REQUESTED`.
- Without: status `PAUSED`, event `RESEARCH_PAUSED`.

`resume_run`
- Refuse anything that is not `PAUSED`.
- As `continue_run`, with the event `RESEARCH_RESUMED`.

`retry_run`
- Refuse a run that is not `FAILED_RETRYABLE`, `FAILED_PERMANENT`, or
  `BLOCKED_TOOL`.
- Requeue the run's jobs whose status is `failed` or `dead`: status `queued`,
  attempts 0, worker cleared, lease cleared, lease epoch raised by one so a
  stale worker cannot complete the job, scheduled for now.
- Set the status to `QUEUED`, insert `RESEARCH_RETRIED`.

`cancel_run`
- Refuse a terminal run.
- With a live worker lease: status `CANCEL_REQUESTED`, event
  `RESEARCH_CANCEL_REQUESTED`.
- Without: status `CANCELLED`, queued jobs of the run set to `cancelled`, event
  `RESEARCH_CANCELLED`.
- History stays. Nothing is deleted.

`eligible_runs`
- Runs that are not terminal, not `FAILED_PERMANENT`, and hold no live worker
  lease, ordered as the list order.

`list_runs`
- Ordered by state rank: `RUNNING` 0, `PAUSED` and `PAUSE_REQUESTED` 1,
  `FAILED_RETRYABLE` 2, `WAITING_HUMAN` 3, `BLOCKED_TOOL` 4, then everything
  else 5. Inside a rank, most recently updated first.

`run_log`
- The run view, the events newest first, the checkpoints, the jobs, and the
  stage attempts.

### Live lease

```python
def _has_live_lease(conn, run_id, *, now) -> bool:
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE run_id = ? AND status = 'leased' "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at > ? LIMIT 1",
        (run_id, now.isoformat()),
    ).fetchone()
    return row is not None
```

### Formatting

`render_run(run)` returns the run block:

``` text
RH-20261021-0001  WhatsApp inventory reconciliation
  state: QUEUED   stage: PLAN   progress: 0%   resumes: 0
  worker: not running
  updated: 2026-10-21T11:20:00+00:00
```

`render_runs(runs)` returns the list block, one line per run, with the public
id, the objective, the state, the stage, the progress, and the last activity.
`render_log(payload)` returns the log block.

The wording of the worker line comes from the row, not from a guess: a live
lease reads `worker: running`, otherwise `worker: not running`.

### CLI entry

```python
def main(argv: list[str] | None = None) -> int:
```

Parses `create`, `list`, `status`, `continue`, `resume`, `pause`, `retry`,
`cancel`, `log`. Prints one JSON object:

```json
{"ok": true, "action": "status", "message": "...", "text": "...", "run": {...}}
```

Exit code 0 on success, 1 on failure. A missing database file returns
`database_missing` rather than creating one.

The module carries:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

## `src/product_hunter/cli.py`

Add a `research` subcommand that delegates:

```python
research_parser = commands.add_parser("research", help="control research runs")
research_parser.add_argument("action")
research_parser.add_argument("target", nargs="?", default=None)
research_parser.add_argument("--objective", default=None)
research_parser.add_argument("--chat-id", dest="chat_id", default=None)
research_parser.add_argument("--limit", type=int, default=50)
```

and in `main`:

```python
if args.command == "research":
    from product_hunter.registry import main as registry_main
    return registry_main(["research", ...])
```

The import stays inside the branch so the module loads the same way when the
registry is absent.

## `.opencode/plugins/product-hunter.ts`

A v2 plugin. Default export `{ id: "product-hunter", setup }`. Inside
`setup`, register one tool through `context.tool.transform`:

- name `product_hunter`
- description: what it does and that the worker owns execution
- input: JSON Schema with `action` (enum of the ten actions), `research_id`,
  `objective`, `limit`
- output: string
- execute: spawn the state layer, return its `text`

The spawn uses `node:child_process.spawn` with an argument vector and no
shell:

```typescript
const args = ["run", "--project", directory, "python", "-m", "product_hunter.registry", action, ...rest]
```

The plugin uses the standard library only. It reads the project directory from
the tool context, falls back to the working directory, and never builds a
command string. The timeout is 30 seconds. A failure, a timeout, or output
that is not JSON becomes a short error string.

The plugin also passes the session id as `chat_id` for `create`, taken from
the tool context when it is there.

## `.opencode/commands/*.md`

Ten files, one per command, each with a `description` in the frontmatter and
a prompt body that names the tool action, passes the argument, and tells the
agent to report the returned text. No `agent` and no `model` in the
frontmatter, so the user's model choice stands.

`list-research.md` and `continue.md` tell the agent to use the `question` tool
when the response carries `needs_selection`, and to continue with the research
id the user picked. Neither may guess.

## `tests/test_registry.py`

A temporary SQLite file per test, migrated with `migrate`. Self contained: it
does not import from `conftest.py`, because that file belongs to another
change in this tree.

| Test | Checks |
| --- | --- |
| create | a run row, a `PLAN` checkpoint, a `RESEARCH_STARTED` event, one queued job, the public id shape |
| create with a live objective | returns the same run, creates no second row |
| create with a terminal run | creates a new run |
| list order | running first, paused next, then the rest |
| status | the run view and the counts |
| status of an unknown id | `unknown_research` |
| continue | status `QUEUED`, resume count raised, one continuation job, the event |
| continue on a terminal run | refused |
| continue on a run with a live lease | refused |
| bare continue with one eligible run | continues it |
| bare continue with two eligible runs | `needs_selection` set, two candidates |
| bare continue with none | no candidates, no error |
| pause without a worker | status `PAUSED` |
| pause with a live lease | status `PAUSE_REQUESTED` |
| resume from paused | status `QUEUED`, resume count raised |
| resume from a run that is not paused | refused |
| retry | the failed job is queued again, the lease epoch rose, the status is `QUEUED` |
| cancel | status `CANCELLED`, the queued job is `cancelled`, the history stays |
| cancel with a live lease | status `CANCEL_REQUESTED` |
| log | the events, the checkpoints, the jobs |
| id validation | an id with a slash or a space is refused |
| formatting | the run block names the state, the progress, and the worker line |

The live lease in the tests is written by hand into the `jobs` table with a
lease expiry in the future, or in the past for the expired case.

## Order of work

1. `registry.py` with the schemas, the states, and the read helpers.
2. The transitions, each in a transaction.
3. The formatting.
4. The dispatcher and the CLI entry.
5. The tests, then the gate.
6. The plugin, then the command files.
7. The README.

## Checks

- `uv run pytest` is the gate. The suite must be green.
- The plugin is verified live: the tool appears in the tool catalog and returns
  the text of a real call.
