# Wave 3, session 8: the OpenCode control plane

Branch `wave3/opencode-control-plane`, worktree
`.worktrees/wave3-opencode-control-plane`, base `00a6ca3`.

| | |
| --- | --- |
| Implementation commit | `3430f72989f515c79e83acb0c52a0359f366e9b7` |
| Report commit | the branch tip, one commit later |
| Code gate | `uv run pytest` → 2194 passed, exit 0 |
| Review verdict | `ship` |
| OpenCode | v2.1.2 |

The mission asked for the report at `docs/resonix/reports/`. The repository
spells that directory `docs/reasonix/`, and every one of its 150-odd documents
lives there, so the report is at
`docs/reasonix/reports/wave-3-opencode-control-plane.md`.

## What the control plane does now

Twelve slash commands sit on eleven registry actions.

| Command | Action | What it does |
| --- | --- | --- |
| `/research` | `create` | Writes the run, the first checkpoint, the first event and the first job in one transaction, then reports the run id. |
| `/list-research` | `list` | Lists the runs by state and marks the ones that can be continued right now. |
| `/research-status <ID>` | `status` | Names the run id, the state, the current stage, the progress, the budget, the blockers, the latest checkpoint, the last activity and the errors. |
| `/continue-research <ID>` | `continue` | Queues one named run. |
| `/continue` | `continue` | Queues the one eligible run. With more than one it returns the candidates and asks; it never picks one. |
| `/pause-research <ID>` | `pause` | Records a durable pause request, or stops the run outright when no worker holds it. |
| `/resume-research <ID>` | `resume` | Resumes a paused run from its checkpoint, reusing the pending job instead of adding a second one. |
| `/retry-research <ID>` | `retry` | Requeues a retryable failure. Refuses a permanent one and names the next step. |
| `/cancel-research <ID>` | `cancel` | Cancels without touching the history. |
| `/research-log <ID>` | `log` | Shows the events, the checkpoints, the jobs and the attempts. |
| `/research-doctor` | `doctor` | Reads the store and writes nothing. |
| `/research-recover` | `recover` | Resolves the requests a missing worker left behind and requeues the jobs whose lease expired. |

## The OpenCode mechanisms, verified rather than assumed

OpenCode v2.1.2 is the installed version. Each of these was checked against
the running product, and the documentation was read for the ones that have no
runtime evidence.

**Commands live in `.opencode/commands/`, one `.md` file per command, and the
file name is the command name.** Proven at runtime: `POST
/api/session/{id}/command` with `{"name":"research-status","text":"RH-20261022-0001"}`
produced the user prompt "Call the `product_hunter` tool with action `status`
and research_id `RH-20261022-0001`", so the command was found and
`$ARGUMENTS` expanded. The V2 docs are explicit that the legacy singular
`command/` directory also works but that new files belong in `commands/`.

**Plugins live in `.opencode/plugins/` and are loaded automatically.** Proven in
the server log: `loading plugin id="...\.opencode\plugins\product-hunter.ts"`.

**A tool returns `{ content, metadata }` in V2.** The published
`@opencode/schema@2.1.2` types give `Result = { output?, content?, metadata? }`
and the plugin guide's own example returns `{ content }`. The reviewer
confirmed the same against the v2.1.2 spec through Context7, independently. A
tool that declares no `output` schema must supply non-empty model content, and
this one always does: `payload.text || payload.message`.

**Tools register through `ctx.tool.transform((editor) => editor.add({...}))`**
with a JSON Schema for the input.

**The `question` tool exists in V2**, so `/continue` can ask the operator to
pick between candidates instead of guessing.

**The documented plugin entrypoint is `Plugin.define` with
`import { Plugin } from "@opencode/plugin"`.** The repository has no
`package.json`, so that package cannot be resolved from the project. The plugin
therefore keeps its plain default export and the Node standard library only.
It loads and its tool is callable, which is the thing that matters; the
canonical import stays a one-line change for whoever adds a `package.json`.

**Shell blocks in a command run outside the agent's permission flow.** None of
the twelve commands uses one. Every command reaches the registry through the
plugin tool, and the tool spawns one fixed module with an argument vector and
never a command string.

## What the live runs showed

Against a real OpenCode v2.1.2 (a standalone server, the project copied
outside the main checkout so that only one plugin file was in scope):

- `/research-doctor` was discovered, expanded, and the model called the
  `product_hunter` tool with action `doctor`. The tool's `content` came back
  to the model verbatim:

  ```
  research doctor
    contract version: phase4-v1
    runs by state: QUEUED 2
    live leases: 0
    expired leases: 0
    queued jobs: 2
    outstanding requests: none
    malformed states: none
  ```

- `/research-recover` was discovered and expanded the same way. A run left in
  `PAUSE_REQUESTED` by a worker that had died was resolved to `PAUSED`, and the
  status that came back named the budget, the checkpoint, the last activity
  and the blockers.

- The plugin resolved its own project root from the file it ships in, so the
  registry read the database that belongs to that tree rather than the one
  the chat happened to start in.

**One artefact to know about.** With a worktree nested inside the main
checkout, OpenCode loads both `.opencode/plugins/product-hunter.ts` files,
because they carry the same plugin id, and the ancestor's wins the
registration: the live tool then exposes the old nine-action schema. The
server log shows both loads. It is a property of the layout, not of the
change, and it disappears when the project is not nested under another
checkout that carries the same plugin.

## The security posture

**Nothing raw reaches the model.** The masking lives in one place,
`src/product_hunter/redaction.py`, and the worker imports it rather than
keeping its own copy. It covers a labelled secret, the JSON shape
(`{"token": "abc"}`), a bare provider key (`sk-…`, `ghp_…`, `xox…`, `AIza…`,
`AKIA…`) and a JSON web token. It is applied to the run's own error, to the
job errors, to the refusal text and to the internal error text, so the raw
value cannot travel in the tool `metadata` beside the masked text either.

The pattern is deliberately tight enough not to over-mask: "the token count
was 5", "skipping" and "a bearer of bad news" come through unchanged.

**No shell, no command string.** The plugin spawns `uv run --project <dir>
python -m product_hunter.registry <action> --name=value`. The values travel in
the equals form, so a value that begins with a dash is a value and not an
option, and the objective and the run id are data that never reaches a shell.

**No injection.** The objective is length-bounded and stripped, the run id
matches `^[A-Za-z0-9._-]+$`, the limit is clamped, and every statement is
parameterised.

**Nothing is deleted.** There is no `DELETE` anywhere in the change. A cancel
cancels the pending jobs and leaves the run history, the checkpoints and the
events alone.

**Every refusal leaves a trace.** `RESEARCH_REQUEST_REFUSED` records the
action, the code and the reason, in its own transaction, after the refused
transaction has rolled back. The internal-error path records one too.

## What the review and the security pass changed

The first pass over the diff returned `do not ship`. Here is what it found
and what closed it.

| Finding | Fix |
| --- | --- |
| A named continue discarded an outstanding pause request | `continue_run` refuses any state in `_REQUESTED` |
| A pause discarded an outstanding cancel request | `pause_run` refuses `CANCEL_REQUESTED` |
| A named continue handed a malformed run to a worker | `continue_run` and `resume_run` refuse an unknown state |
| A pause reported the run stopped while its job stayed queued | The pause cancels the pending job when no worker holds the run |
| The run error reached the model raw in the tool metadata | `last_error` is masked in `_view` |
| The masking missed the JSON shape and a bare key | Two more rules in the masking module |
| A refusal message and an internal error carried the raw text | Both go through the masking |
| A retry on a run a worker still holds claimed to requeue something | `retry_run` refuses a live lease |
| The doctor and the recover disagreed about a stored state | The recover compares the same normalised value the doctor does |
| One corrupt checkpoint killed every command on that run | `list_checkpoints(..., skip_invalid=True)` |
| The write path did not retry a busy file, though the spec said it did | The retry moved into `db/connection.py` and both callers use it |
| The bare continue picked its candidate outside the write | The selection and the claim share one transaction |
| The masking over-masked "a bearer of bad news" | The bare `bearer` alternative is gone |
| The recover left a paused run with a runnable job | The pause branch cancels the job, and again after the lease recovery |
| The internal-error path left no event | It records a refusal event too |

The second review confirmed all fourteen closed and returned `ship`, with
`Lean: -0 lines possible`.

## The tests

`uv run pytest` → **2194 passed**, exit 0. The baseline before this change was
2140, so the change added 54 cases.

They cover the subjects the mission named: the command parsing and the
argument vector, an unknown run id, an ambiguous `/continue`, the pause and
the resume, the retry of a retryable and of a permanent failure, the cancel,
the missing worker, a malformed state, a stale state, two commands at once,
and the secret masking in the text, in the metadata and in the refusal
message.

`tests/test_plugin_control_plane.py` drives the real plugin file with node, the
way `tests/test_plugin_project_root.py` already did, and checks the action
list, the argument vector, the equals form and the absence of a shell. It
skips when node is missing or cannot run a TypeScript file, because that is a
property of the machine.

## The integration requirements

These are what the worker has to do before the control plane can promise more
than it currently does.

1. **The worker must write the run's progress.** Nothing in the repository
   writes `research_runs.current_stage`, `current_step`, `progress_percent` or
   `last_heartbeat` after the create, so the status shows the values the
   request carried. The spec says so now, and the change to the code is the
   worker's.
2. **The worker must honour the requests it is given.** It never reads the run
   status, so a pause issued against a live worker waits for a worker that
   does not look. The control plane resolves a request only when the worker
   holding it is gone, and reports the rest in the doctor.
3. **The worker must not requeue the work of a run that is paused or
   cancelled.** The control plane cancels the pending job when it stops a run,
   and a worker that requeued it would undo that.
4. **The worker should write the error text already masked.** The control
   plane masks on read, which is the boundary that matters, but masking at the
   source too would keep the raw value out of the database.
5. **A budget ledger is missing.** `research_runs.budget_json` is never
   written, so the budget line falls back to the per-run limits the research
   contract declares, with the tool-call count beside `tool_calls_total`,
   which is the only spend the `tool_calls` table can prove. The other
   dimensions need a ledger.
6. **Two new event types reach the stream.** `RESEARCH_RECOVERED` marks a
   request the control plane resolved, and `RESEARCH_REQUEST_REFUSED` now
   covers the internal-error path as well as the refusals. Anything that reads
   the event stream should know both.

## What was skipped, and what was not checked

- **Not checked:** the plugin was never run inside the live TUI. Every proof
  here is a standalone server or the API.
- **Not checked:** the worker's own stages were never run against a paused or
  a cancelled run, only against the job and lease rows a worker would see.
- **Known limit:** a run that is already `QUEUED` stays eligible for a bare
  continue, so two concurrent bare continues can both raise the resume count
  and write a second event. The work itself is queued once, because the
  pending job is reused rather than a second one added.
- **Known limit:** `list_runs` builds a view for every run and slices
  afterwards, so the limit is cosmetic on the query. Two hundred runs cost
  two hundred and one statements.
- **Known limit:** `_insert_event` counts the run's events to mint the event
  id, so that count grows with the table. Fixing it properly wants an index,
  and an index means a migration, which is outside this session's radius.
- **Skipped on purpose:** the budget line prints all twelve per-run limits.
  Eleven of them carry no spend the database can prove, and the line is the
  widest in the status block. The snapshot asked for the limits with the
  measured spend beside them, so they are all there.
- **Skipped on purpose:** `continue_run` and `resume_run` remain near-copies
  and the three lease queries remain three shapes. Both are readability, not
  correctness, and both are cheap to change later.

## How the decisions were made

A SubQ choice snapshot over the state of the change settled the three places
where more than one answer was defensible. It asked for the retry of a
permanent failure, the resolution of a stale request, and the budget line; it
returned a refusal that points at a new request (0.969, confidence 0.953), an
explicit recover command rather than a write on read (0.970, 0.954), and the
contract limits with the measured spend beside them (0.879, 0.818). One call,
571 input tokens, no errors.
