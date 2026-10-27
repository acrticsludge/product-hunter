# Phase 2 — OpenCode control interface (report)

Goal: give OpenCode a control surface over the Product Hunter research
registry, without taking the worker's job away from it.

Spec: `docs/reasonix/specs/phase-2-opencode-interface.md`
Plan: `docs/reasonix/plans/phase-2-opencode-interface.md`
Handoff: `docs/reasonix/handoffs/phase-2-opencode-interface.md`

## What was built

| Path | Change | Size |
| --- | --- | --- |
| `src/product_hunter/registry.py` | new. The state layer: request schemas, run states, the transitions the interface may request, the status formatting, the dispatcher, the command line entry point | 1117 lines |
| `src/product_hunter/cli.py` | one additive `research` subcommand that delegates to the registry | +20 lines |
| `tests/test_registry.py` | new. 34 deterministic tests over a temporary SQLite file | 551 lines |
| `.opencode/plugins/product-hunter.ts` | new. An OpenCode v2 plugin that registers one tool named `product_hunter` | 254 lines |
| `.opencode/commands/*.md` | new. Ten command templates | 10 files |
| `README.md` | the "OpenCode control surface" section and the `ph research` bullet | +37 lines |

No schema change. `research_runs`, `research_events`, `research_checkpoints`
and `jobs` already carried every column this work needs, so no migration was
written and the high-risk path `db/migrations/**` was not touched.

## The mechanism, verified rather than assumed

OpenCode 2.1.2 is installed here. The docs and the installed binary disagree in
one place that matters, so the choice was made from a running probe:

| Mechanism | Result |
| --- | --- |
| Project plugin, `.opencode/plugins/<file>.ts`, default export `{ id, setup }` | Loads. `context.tool.transform(draft => draft.add(...))` registers a tool that appears in the catalog. The server log records `loading plugin id="...product-hunter.ts"`. |
| File-based custom tool, `.opencode/tools/<file>.ts` | Not loaded by 2.1.2. The docs still describe this path. |
| Markdown command, `.opencode/commands/<file>.md` | The discovery glob is `{command,commands}/**/*.md`. The body is a prompt template with `$ARGUMENTS`. |
| Interactive question prompt | The built-in `question` tool. This is what the run picker and an ambiguous `/continue` use. |

The server was inspected with the documented `opencode api` CLI only, never
with raw HTTP, and the plugin was proven by watching the tool appear in the
live tool catalog.

The consequence that shaped the design: a command file cannot render an
application picker. So the ten command templates are prompt templates that call
one tool, and the tool returns the run list for the model to offer through the
`question` tool.

## The behaviour the task asked for

| Requirement | Where it lives | How it was proven |
| --- | --- | --- |
| Command schemas | `CreateRequest`, `TargetRequest`, `LogRequest`, `RunView`, `CommandResult` | 34 tests |
| Argument validation | pydantic bounds, plus a `research_id` pattern of letters, digits, dot, underscore, hyphen | a test refuses an id with a slash or a space |
| Research id handling | resolves against `public_id` first, then `run_id`; the public id is `RH-YYYYMMDD-NNNN` | create, then status, then list |
| Status formatting | `render_run`, `render_runs`, `render_log` | the text is asserted in the tests |
| Error handling | `unknown_research`, `invalid_request`, `invalid_transition`, `database_missing`, `internal_error`; exit code 1 on a refusal | the refusal paths are tested |
| Ambiguous `/continue` | the eligible runs are returned with `needs_selection` | two eligible runs returned both candidates and picked neither |
| Pause and resume requests | `PAUSED` with no worker, `PAUSE_REQUESTED` with a live lease, `resume` only from `PAUSED` | tested with a hand-written lease |
| Continuation requests | a continuation job, `resume_count` raised, `RESEARCH_RESUMED` | tested |
| Works with no worker running | the lease is read from the row, never assumed | the smoke run reads `worker: not running` |
| Deterministic tests | 34 tests, no network, no clock of its own | the gate |

Two decisions were put to the SubQ Snap decision model, because both are
materially different readings of the spec. It chose to write `PAUSED` when no
worker holds the run, and to reuse a live run that already has the objective.
It also chose the plugin plan over an MCP server, with a probability of 0.97.

## The gates

| Gate | Result |
| --- | --- |
| Code, `uv run pytest` | 717 passed on the first run, 834 passed after the review fixes, exit 0 |
| Harden, three rounds | Round 1 raised four findings, round 2 two more, round 3 `HARDEN: pass` with no new finding |
| Review, two rounds | Round 1: `changes requested`, 3 Must, 4 Should, 6 Nice. Round 2: `ship`, all thirteen closed |

### The review findings, and what happened to them

| Finding | Fix |
| --- | --- |
| A bare continue with no eligible run told the user the database was empty | The text names the real condition: `no research run is eligible to continue`. The interface does not state something false about durable state. |
| A continue enqueued a second job for a run that already had pending work, so the stage ran twice | A run's pending work is at most one job. The pending job is re-queued: lease epoch raised, worker and lease cleared, scheduled for now. |
| A run whose cancel was requested could be picked up by a bare continue, and the cancel was undone | `CANCEL_REQUESTED` and `PAUSE_REQUESTED` are out of the eligible list, a named continue on one is refused, and the status text says the worker finishes the cancel. |
| A resume refused a run the interface itself paused | `resume_run` accepts `PAUSED` and `PAUSE_REQUESTED`. |
| An unknown action was reported as an invalid research id | The action is checked first and the message names the offending action. |
| The list path never validated the limit, so a limit of 0 reported no runs | The limit is validated with the same bounds as the log action. |
| The retry path was only tested against states nothing produces | A test now drives the reachable `dead` job status. |
| Dead code, a doubled create scan, an unbounded log query, and dead guidance in a template | Removed, folded, bounded, and rewritten. |

Two Nice findings were not acted on, and the reviewer accepted both reasons: the
plugin keeps its own copy of the action list, because it is the tool's own
contract boundary and a shared build costs more than the drift, and the list
path runs about five queries per run, because the alternative is a
denormalised counter column for a local table bounded at 500 rows.

Two Nice findings were raised in the second round and are left open: a run
that somehow already holds two pending jobs keeps one queued after a continue,
and a re-queued job keeps the payload of the job it replaces rather than the
payload of a fresh continuation job. Neither state is reachable from the
shipped code.

## The tree is shared

Another agent has been working in this repository at the same time, and its
work is visible in the same working tree. Three things came out of that:

1. The module is `src/product_hunter/registry.py` and not `research.py`. The
   other agent owns the `src/product_hunter/research/` package, and a package
   and a module of one name in one directory cannot both import.
2. The documents moved with that agent's rename of the documentation tree
   to `docs/reasonix`, and the radius was re-declared to match.
3. `cli.py` and `README.md` carry that agent's uncommitted benchmark work.
   Committing either file whole would claim that work and would leave the
   commit pointing at `src/product_hunter/eval/`, which is not in the commit.
   The index therefore holds `HEAD` plus this run's hunks only. Their working
   tree version stays untouched and uncommitted.

The gate was red for a while because of that agent's in-flight model routing
and scheduler tests. This run's files were green throughout, and the suite
returned to green before the commit.

## The three open items, closed

**The native session check.** The tool answers a live call from inside a
running OpenCode session. A model called it twice, `{"action":"status",
"research_id":"RH-20261021-0003"}` and `{"action":"list"}`, and both calls
returned the state layer's own text: the run block for `RH-20261021-0003` with
`worker: not running`, and the list of three runs. The tool appears in the
session's tool catalog with its full input schema, and the server logs the
plugin load.

To make that possible the plugin no longer sets `options: { codemode: false }`,
so the control surface is reachable from the Code Mode runtime as well as from
the model natively. The earlier `opencode run` attempts were unreliable: the
client attaches to the most recent session in the working directory, so one
attempt streamed another session's transcript and a second produced no output
at all before it was stopped. Calling the tool from the session that is
already open removes that ambiguity.

An earlier transcript from a live session in this repository also shows the
model making the same two calls and printing the same two outputs, so the
model-driven path was already working before the option was removed. A session
started under an explicit id, `ses_phverify_20261021`, was then asked for the
status of one run and made the same call through the code runtime, with the
same result.

**The two smoke-test runs.** `RH-20261021-0001` and `RH-20261021-0002` were
cancelled through the interface itself, so their history stays and the
cancellation is recorded as an event. `RH-20261021-0003` is not this run's
and was left alone.

**The plugin's commit provenance.** The plugin file is committed in `8e5172c`,
a commit of the parallel agent, because that agent added the project-root
resolution to the file while this run was writing it. The committed tree is
whole regardless: a clean export of `HEAD` carries the plugin, the ten
command templates, the registry and its tests, and the registry runs from that
export against the real database. The handoff now names the file and the
commit, so the dependency survives any later history surgery.

**One more defect the re-review surfaced, and its fix.** The model only ever
reads the tool's `output` text. The metadata never reaches it. The bare
continue used to hand the model a bare list of eligible runs with no reason to
stop and ask, and the command template keyed off a metadata field the model
cannot see. So a bare `/continue` with two eligible runs could have let the
model pick one, which the spec forbids. The registry now puts the instruction
in the text, ahead of the candidate list: "More than one run is eligible; ask
the user which one to continue. Never pick one on the user's behalf." The
metadata, the message, the flag, the list and the response shape are unchanged,
and the template no longer refers to metadata at all.

The rule is still enforced by the model reading that text. Nothing below the
text layer can stop a model that ignores it.

The same class of defect was in the create action, and the re-review found it.
The sentence that says a live run already holds the objective lived only in
the message, and the plugin drops the message whenever a text is set. So a
second create for the same objective reused the run while the model saw only
the run block, and could have reported a new run for a run that already
existed. The create text now opens with what happened, "created run RH-..." or
"run RH-... already holds this objective; reused it", and the run block
follows. The command template keys off that text.

## Not done, and not checked

- The scheduler, the worker loop, the stage handlers, the research contract,
  the tool gate, and the Obsidian projection. The interface only requests
  work and reads durable state.
- The `product-hunter` agent and its subagents.
- The plugin has no committed test of its own. The repo has no JavaScript test
  runner, and adding one was out of scope. The plugin's behaviour was proven
  with a harness outside the repository, and by live calls from a running
  session.
- Two interface processes acting on one run at the same time was not tested
  under contention. SQLite's busy timeout covers the lock window.
- Per-run evidence and opportunity counts are not shown. The Phase 2 schema
  does not link evidence to a run, and Phase 3 owns that link.
