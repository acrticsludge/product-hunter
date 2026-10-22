# Wave 3, session 8: the OpenCode control plane

## The shape of the thing

OpenCode is the control surface. The Product Hunter worker and its SQLite
database are the authority. Everything the operator asks for becomes a durable
row in `research_runs`, `research_events` and `jobs`; the worker picks the work
up from there and nobody has to hold state in a chat transcript.

The boundary runs one way. If OpenCode disappears, the worker keeps running
because the worker never asked OpenCode for anything. If the worker disappears,
OpenCode can still read the run history, name the state it is in, and repair the
requests that were left outstanding.

## What was already there, and what was wrong with it

Phase 2 built the spine: `src/product_hunter/registry.py` owns the run state,
`.opencode/plugins/product-hunter.ts` carries one request across as a
subprocess, and ten markdown commands under `.opencode/commands/` call the
plugin's one tool. That spine is sound. Six things were missing or wrong.

`retry_run` accepted `FAILED_PERMANENT` and requeued it. A permanent failure is
by definition not retryable, and the refusal in `continue_run` even told the
operator to "retry it instead". The two paths contradicted each other.

An unknown stored status was treated as an ordinary non-terminal state. A run
whose `status` column held `GARBAGE` was eligible to `continue`, so a corrupt
row looked like healthy work waiting for a worker.

`PAUSE_REQUESTED` and `CANCEL_REQUESTED` were written to the database, and
nothing ever acted on them. The worker does not read the run status, so a pause
issued against a live worker stays `PAUSE_REQUESTED` until somebody resolves
it by hand. A run whose worker died mid-pause waits forever.

`/research-status` showed the state, the stage, the progress and the update
time. It did not show the budget, the blockers, the latest checkpoint, the last
activity or the errors, which is most of what an operator asks a status command
for.

Nothing in the registry redacted a secret. The worker has a private
`_safe_error` for exception text; the control plane had no equivalent, and the
run's `last_error` and the job errors were on their way to the model unfiltered.

There was no doctor and no recover. When the worker disappeared, the operator
had a list command and a guess.

## The contract

Nine commands, then two more.

| Command | Action | What it must do |
| --- | --- | --- |
| `/research` | `create` | Record a durable request. Report the run id. Say so when an existing live run already holds the objective. |
| `/list-research` | `list` | Show the runs, and mark which of them can be continued right now. |
| `/research-status <ID>` | `status` | Run id, state, current stage, progress, budget, blockers, latest checkpoint, last activity, errors. The stage and the progress are the values the request carried. |
| `/continue-research <ID>` | `continue` | Queue one named run. |
| `/continue` | `continue` | Queue the one eligible run. With more than one, ask; never guess. |
| `/pause-research <ID>` | `pause` | Record a durable pause request, or stop the run outright when no worker holds it. |
| `/resume-research <ID>` | `resume` | Resume a paused run from its checkpoint, requeueing the pending job rather than adding a second one. |
| `/retry-research <ID>` | `retry` | Requeue a retryable failure. Refuse a permanent one and name the next step. |
| `/cancel-research <ID>` | `cancel` | Cancel without corrupting the history. The worker finishes the cancel when it is holding the run. |
| `/research-log <ID>` | `log` | Provenance and history, with no secret in it. |
| `/research-doctor` | `doctor` | Read-only health report: workers, leases, outstanding requests, malformed states. |
| `/research-recover` | `recover` | Resolve the outstanding requests whose worker is gone, and requeue the jobs whose lease expired. |

### States and who owns the transition

`QUEUED`, `RUNNING`, `PAUSE_REQUESTED`, `PAUSED`, `WAITING_HUMAN`,
`BLOCKED_TOOL`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`, `COMPLETED`,
`CANCEL_REQUESTED`, `CANCELLED`.

`COMPLETED` and `CANCELLED` are terminal. A request state, `PAUSE_REQUESTED` or
`CANCEL_REQUESTED`, means the worker still has work to do: the interface
records the request and says the worker finishes it. A state the interface does
not know is malformed. A malformed run is reported as malformed and is never
eligible to continue.

### The rules that decide the hard cases

**Retry.** `FAILED_RETRYABLE` and `BLOCKED_TOOL` are retryable. `FAILED_PERMANENT`
is not, and the refusal says so and points at starting a new research request,
because a permanent failure needs a different objective rather than the same
one again.

**Stale requests.** A run in `PAUSE_REQUESTED` or `CANCEL_REQUESTED` whose
worker has no live lease is stale: the worker that would have carried it out is
gone. The doctor reports it. The recover command resolves it, and only the
recover command does, so that a read never writes.

**Budget.** `research_runs.budget_json` is never written by anything, so the
run itself records no budget. The status command therefore reports the
per-run limits the research contract declares, with the spend the database can
prove beside them: the tool calls counted for that run.

**Stage and progress.** Nothing in the repository writes
`research_runs.current_stage` or `research_runs.progress_percent` after the
create, so the status command shows the values the request carried: the stage
of the request and a progress of zero. The worker writing them is an
integration requirement, like the worker reading the run status.

**Concurrency.** Every write runs inside `BEGIN IMMEDIATE`, so the writes of
two commands are serialised: the run a bare continue picks is selected and
claimed in the same transaction. The work is still queued once, because a
pending job is reused rather than a second one added. The connection carries
a five second busy timeout, and the write path retries a busy file rather
than failing.

**Secrets.** Nothing that reaches the model is unredacted. The error text of a
run, of a job and of a stage attempt is masked before it is rendered, and the
masking lives in one place that the worker and the control plane share.

## The OpenCode side

The installed OpenCode is v2.1.2. These mechanisms were checked against the
running product and its V2 documentation, not assumed.

- Commands live in `.opencode/commands/`, one `.md` file per command, and the
  file name is the command name. `$ARGUMENTS` expands to the whole argument
  string. The body of the file is the prompt that gets submitted.
- Plugins live in `.opencode/plugins/`. A plugin registers tools through
  `ctx.tool.transform`, and a tool's `execute` returns `{ content, metadata }`.
- The `question` tool exists, so `/continue` can ask the operator to pick
  between candidates.
- The repository has no `package.json`, so `@opencode/plugin` cannot be
  imported. The plugin therefore uses the Node standard library only and
  default-exports a plain object. It loads, and its tool is callable.

The plugin never runs a research stage. It spawns one fixed Python module with
an argument vector, never a command string, and it never puts untrusted text
into a shell. The objective and the run id travel as `--name=value` arguments,
so a value that begins with a dash is still a value.

## What the tests must cover

Command parsing. An unknown run id. A bare `/continue` with more than one
eligible run. Pause and resume. Retry of a retryable and of a permanent
failure. Cancel. A missing worker. A malformed state. A stale request. Two
commands at once. A secret in an error string. The plugin's argument vector.

## Out of scope

The worker keeps its own design. The control plane does not take over
persistence, and it does not teach the worker to read the run status; that
remains an integration requirement, listed in the run report.
