# Phase 2 â€” OpenCode control interface (spec)

Status: implementation contract for the OpenCode control surface of Product
Hunter.
Source: `docs/reasonix/specs/Product-Hunter-Full-Spec.md` sections 6A, 38A
(Phase 5), 41, 43.

## 1. Ownership

OpenCode is the interaction surface. The Product Hunter worker owns research
execution, and SQLite owns run state. The interface may request work and
record requests. It must never run a research stage, and it must never treat
chat history as state.

| Concern | Owner |
| --- | --- |
| Command routing, argument parsing, selection UI | OpenCode interface |
| Command semantics, validation, state transitions, formatting | `product_hunter.registry` |
| Run state, jobs, checkpoints, events | SQLite |
| Stage execution, leases, budgets, gates | Worker |

## 2. Mechanisms, verified in this environment

OpenCode 2.1.2 is installed. The following was checked by running the code, not
by reading the docs:

| Mechanism | Result | Use |
| --- | --- | --- |
| Project plugin, `.opencode/plugins/<id>.ts`, default export `{ id, setup }` | Loads. `context.tool.transform(draft => draft.add(...))` registers a tool that appears in the tool catalog. | The bridge to the worker |
| File-based custom tool, `.opencode/tools/<name>.ts` | Not loaded by 2.1.2. | Not used |
| Markdown command, `.opencode/commands/<name>.md` | Discovery glob is `{command,commands}/**/*.md`. The body is a prompt template, frontmatter carries `description`, `agent`, `model`. | The ten slash commands |
| Interactive question prompt | Available as the built-in `question` tool. | Run picker, ambiguous `/continue` |

Consequences:

- A command file cannot render an application picker. Selection happens through
  the `question` tool, driven by the run list the interface returns.
- The bridge is one plugin registering one tool. The tool calls the state layer
  as a subprocess with an argument vector, never a shell string.
- The plugin must not depend on a package that only resolves from the global
  OpenCode config directory. It uses the Python standard library only.

## 3. Commands

| Command | Tool action | Arguments |
| --- | --- | --- |
| `/research` | `create` | objective |
| `/list-research` | `list` | none |
| `/research-status` | `status` | research id |
| `/continue-research` | `continue` | research id |
| `/continue` | `continue` | none, or a chosen research id |
| `/pause-research` | `pause` | research id |
| `/resume-research` | `resume` | research id |
| `/retry-research` | `retry` | research id |
| `/cancel-research` | `cancel` | research id |
| `/research-log` | `log` | research id |

The tool schema takes one `action` from the list above, an optional
`research_id`, an optional `objective`, and an optional `limit`.

## 4. Request schemas

`product_hunter.registry` owns the schemas, expressed as pydantic models.

| Model | Fields | Rules |
| --- | --- | --- |
| `CreateRequest` | `objective`, `chat_id` | objective stripped, 3 to 2000 characters |
| `TargetRequest` | `research_id` | 3 to 128 characters, letters, digits, dot, underscore, hyphen only |
| `LogRequest` | `research_id`, `limit` | limit 1 to 500, default 50 |
| `RunView` | identity, status, stage, progress, timestamps, counts | read model over one run |
| `CommandResult` | `ok`, `action`, `message`, `text`, `run`, `runs`, `needs_selection` | the single response shape |

A research id resolves against `public_id` first, then `run_id`. An id that
matches neither is an error with the code `unknown_research`, and the error
lists the ids that do exist, capped at ten.

## 5. Run states

The spec states eleven run states. The table has no constraint on `status`, so
the interface reads what is there and normalizes it.

| State | Meaning for the interface |
| --- | --- |
| `QUEUED` | Work is waiting for a worker. |
| `RUNNING` | A worker holds a live lease. |
| `PAUSE_REQUESTED` | The interface asked a running worker to pause. |
| `PAUSED` | The run is stopped and resumable. |
| `WAITING_HUMAN` | The run needs an answer. |
| `BLOCKED_TOOL` | The run is blocked on a tool. |
| `FAILED_RETRYABLE` | The run failed in a way a retry may clear. |
| `FAILED_PERMANENT` | The run failed. Only a retry may move it. |
| `COMPLETED` | Terminal. |
| `CANCEL_REQUESTED` | The interface asked a running worker to stop. |
| `CANCELLED` | Terminal. |

Terminal states are `COMPLETED` and `CANCELLED`. A lowercase value from an
earlier phase maps onto the same state, so `completed` reads as `COMPLETED`.
An unknown value is reported as it stands, so a later state does not break the
interface.

## 6. Transitions the interface may request

| Action | Precondition | Write | Job | Event |
| --- | --- | --- | --- | --- |
| `create` | no non-terminal run with the same objective | run row, first checkpoint | enqueue the first job | `RESEARCH_STARTED` |
| `continue` | run is not terminal, not `FAILED_PERMANENT`, and no worker holds a live lease | status `QUEUED`, `resume_count` + 1 | enqueue a continuation job at priority 0 | `RESEARCH_RESUMED` |
| `pause` | run is not terminal | status `PAUSED`, or `PAUSE_REQUESTED` when a worker holds a live lease | none | `RESEARCH_PAUSED` or `RESEARCH_PAUSE_REQUESTED` |
| `resume` | run is `PAUSED` | status `QUEUED`, `resume_count` + 1 | enqueue a continuation job at priority 0 | `RESEARCH_RESUMED` |
| `retry` | run is `FAILED_RETRYABLE`, `FAILED_PERMANENT`, or `BLOCKED_TOOL` | requeue the failed jobs, status `QUEUED` | requeued jobs | `RESEARCH_RETRIED` |
| `cancel` | run is not terminal | status `CANCELLED`, or `CANCEL_REQUESTED` when a worker holds a live lease, queued jobs set to `cancelled` | none | `RESEARCH_CANCELLED` |

Every transition that writes runs inside one transaction. A failure leaves the
run as it was.

A worker holds a live lease when a job of that run has status `leased` and a
lease expiry in the future. An expired lease means the run is not executing.
The interface reports that from the row, and never guesses that a worker is
running.

## 7. Ambiguous `/continue`

`/continue` without an id collects the runs that are eligible: not terminal,
not `FAILED_PERMANENT`, and without a live worker lease.

- Exactly one eligible run: continue it.
- More than one: return the candidates with `needs_selection` set. The
  interface asks the user through the `question` tool, then continues the
  chosen research id. The interface never picks one on the user's behalf.
- None: report that no run is eligible.

## 8. Creating a run

The research id is `RH-YYYYMMDD-NNNN`, where `NNNN` is the next free number
for that day. The internal `run_id` is derived from the public id, so the same
public id always names the same run.

Creating a run with an objective that a non-terminal run already holds returns
that run and creates nothing. The id stays stable and no second run appears.
A completed or cancelled run does not block a new one.

The run row is written with status `QUEUED`, the first checkpoint is saved at
stage `PLAN`, and the first job is enqueued. The worker takes it from there.

## 9. Status formatting

One rendering per command, produced by the state layer so the wording is
tested. The run line carries the public id, the objective, the state, the
stage, the progress percentage, the resume count, and the last activity. A
run with no live worker says so in words, for example `worker: not running`,
rather than showing a state that implies work in flight.

The list orders runs by state: running, paused, failed, waiting, blocked, then
the most recently updated. Each entry carries the public id, the objective, the
state, the stage, the progress, and the last activity, so the picker in the
interface has everything it needs.

## 10. Errors

| Code | Condition |
| --- | --- |
| `unknown_research` | the id matches no run |
| `invalid_request` | the arguments do not satisfy the schema |
| `invalid_transition` | the action does not apply to the current state |
| `database_missing` | the SQLite file does not exist |
| `internal_error` | anything else |

An error response carries `ok: false`, the code, and a message that names the
offending value. The exit code is 1. The interface reports the message; it does
not retry the command on its own.

## 11. Transport

The plugin runs the state layer as a subprocess with an argument vector and no
shell:

``` text
uv run python -m product_hunter.registry <action> [<args>]
```

The state layer prints one JSON object on stdout. The plugin returns the
`text` field to the model, and keeps the structured fields in the tool
metadata. A non-zero exit, a timeout, or unreadable output becomes an error
string, never an exception that reaches the chat.

The same entry point is reachable as `ph research` for a human at a terminal.

## 12. Out of scope

- The scheduler, the worker loop, and stage handlers.
- Scraping and collection.
- The Obsidian projection.
- The `product-hunter` agent and its subagents.
- Enforcement of the research contract and the tool gate.

## 13. Decisions taken

| Decision | Choice | Why |
| --- | --- | --- |
| Pause on a run with no live worker | write `PAUSED` | The run is stopped, so the durable state says so. A `PAUSE_REQUESTED` nobody acknowledges would leave a run that looks alive. |
| Pause on a run with a live worker | write `PAUSE_REQUESTED` | The worker owns the transition, so the interface records the request. |
| `/research` with an objective a live run already holds | return the existing run | A second run with the same objective would split the evidence and the checkpoint history. |
| Module name | `src/product_hunter/registry.py` | The `src/product_hunter/research/` package belongs to the research contract work. Two modules of one name in one directory cannot both import. |
| Transport | `python -m product_hunter.registry` | The bridge stays independent of the `ph` console script, so a change elsewhere in the CLI cannot break the OpenCode path. |

The pause and create decisions were put to the SubQ Snap decision model with
the state of this interface. It chose to write `PAUSED` when no worker holds
the run, and to reuse a live run that already has the objective.

## 14. Acceptance

- The ten command files exist and name the tool action and the argument.
- The plugin registers one tool that reaches the state layer and returns its
  text.
- A run created in one session is visible from another, and its id resolves in
  both.
- A bare `/continue` with two eligible runs returns the candidates and sets
  `needs_selection`.
- A run with no live worker reports that no worker is running.
- Every action has a test: create, list, status, continue, resume, pause,
  retry, cancel, log, and the failure paths.
