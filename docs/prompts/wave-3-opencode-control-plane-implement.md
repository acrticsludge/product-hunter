# Implement: the OpenCode control plane

You are the implementation node of the free-tier loop. Work only in
`C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane`
on branch `wave3/opencode-control-plane`. Do not touch `main`. Do not merge
another Wave 3 branch. Do not commit.

Read first, in this order:

1. `docs/reasonix/reports/wave-3-opencode-control-plane-handoff.md`
2. `docs/reasonix/specs/wave-3-opencode-control-plane.md`
3. `docs/reasonix/plans/wave-3-opencode-control-plane.md`
4. `src/product_hunter/registry.py` in full
5. `.opencode/plugins/product-hunter.ts` in full
6. `tests/test_registry.py` in full

Then write the code. The brief below is exact; follow it.

## Files you may change

Only these:

- `src/product_hunter/redaction.py` (new)
- `src/product_hunter/runtime/worker.py`
- `src/product_hunter/registry.py`
- `.opencode/plugins/product-hunter.ts`
- `.opencode/commands/research.md`
- `.opencode/commands/list-research.md`
- `.opencode/commands/research-status.md`
- `.opencode/commands/continue-research.md`
- `.opencode/commands/continue.md`
- `.opencode/commands/pause-research.md`
- `.opencode/commands/resume-research.md`
- `.opencode/commands/retry-research.md`
- `.opencode/commands/cancel-research.md`
- `.opencode/commands/research-log.md`
- `.opencode/commands/research-doctor.md` (new)
- `.opencode/commands/research-recover.md` (new)
- `tests/test_registry.py`
- `tests/test_plugin_control_plane.py` (new)

Anything else is out of bounds. If you need a file outside this list, stop and
report it instead of editing it.

## The work

### 1. `src/product_hunter/redaction.py`, new

```python
"""Masking of the secrets that reach a reader.

A handler can raise an exception whose message carries a credential, and the
control plane puts error text in front of the operator. The rule lives here so
the worker and the control plane mask the same way.
"""

from __future__ import annotations

import re

# A labelled secret: the label names the setting and the value is what follows
# it. The label stays, so the reader still knows which secret was there.
LABELLED_SECRET = re.compile(
    r"(?i)\b(bearer)\b\s+\S+"
    r"|\b(bearer|basic|api[_-]?key|apikey|access[_-]?key|token|secret|password"
    r"|passwd|authorization|cookie|session[_-]?id|private[_-]?key)\b\s*[=:]\s*"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)

# The longest error text that reaches a reader.
MAX_ERROR_CHARS = 300


def redact(text: str) -> str:
    """The text with every labelled secret masked."""
    return LABELLED_SECRET.sub(_mask, text)


def safe_error(exc: BaseException) -> str:
    """The failure text, masked and cut. The type always stays."""
    message = redact(str(exc))
    if len(message) > MAX_ERROR_CHARS:
        message = message[:MAX_ERROR_CHARS] + "..."
    return f"{type(exc).__name__}: {message}"


def _mask(match: re.Match[str]) -> str:
    """The masked form of one labelled secret."""
    return f"{match.group(1) or match.group(2)}=[redacted]"
```

Then in `src/product_hunter/runtime/worker.py`: delete the private
`_LABELLED_SECRET` and `_MAX_ERROR_CHARS` constants, delete the private
`_redact` function, and make `_safe_error` call `redaction.safe_error`. Keep
`_safe_error` where it is, because other code in that module uses it. Import
the module: `from product_hunter import redaction`. Keep the behaviour of the
worker identical; the only change is that the pattern lives in one place and
covers a few more labels.

### 2. `src/product_hunter/registry.py`

Add `doctor` and `recover` to `ACTIONS`. Keep the tuple ordered the way the
other actions are, and put the two new ones at the end.

**Known states.** Add a module constant:

```python
# The states the interface knows. A stored status outside this set is
# malformed: it is reported as it stands and it is never handed to a worker.
_KNOWN_STATES = frozenset(state.value for state in RunState)
```

In `eligible_runs`, add `and view.status in _KNOWN_STATES` to the filter, so a
malformed run is never eligible.

**Retry.** Change `_RETRYABLE` to hold only `FAILED_RETRYABLE` and
`BLOCKED_TOOL`. In `retry_run`, before the general refusal, add:

```python
        if status == RunState.FAILED_PERMANENT.value:
            raise InvalidTransition(
                f"run {row['public_id']} failed permanently, and a permanent "
                "failure is not retryable. Start a new research request with a "
                "different objective."
            )
```

In `continue_run`, the refusal that currently says "failed permanently; retry
it instead" must be changed so it agrees with the retry rule. It should say
the failure is permanent and that a new research request is the way forward.

**The enriched run.** Add five fields to `RunView`, each with a default so
nothing that constructs a view breaks:

```python
    budget: str = ""
    blockers: list[str] = Field(default_factory=list)
    latest_checkpoint: str = ""
    last_activity: str = ""
    errors: list[str] = Field(default_factory=list)
```

Write a `_detail(conn, view)` helper that returns a copy of the view with
those five fields filled. It needs:

- `budget`: the per-run limits the research contract declares, with the
  measured spend beside them. Load the contract with
  `load_research_contract()` from `product_hunter.research.contract`; it
  raises `ContractError` when the contract is missing or invalid, and in that
  case the budget line says the limits are unknown rather than failing. The
  measured spend is the count of rows in the `tool_calls` table for that run,
  which is the one dimension the database can prove. The recorded budget on
  the run, `research_runs.budget_json`, wins when it is not null, because a
  run that recorded its own budget is telling the truth about itself. Redact
  it before it is rendered.
- `blockers`: why the run is not moving. Compute it from the state and from
  the leases, as a list of short phrases. Cover at least: a live worker lease
  on the run; the worker lease expired while the state still says RUNNING; the
  state waits for a human; the state is blocked on a tool; the state is
  waiting for the worker to carry out a pause; the same for a cancel; the
  failure is retryable; the failure is permanent; the stored state is not one
  the interface knows; the run is queued but no job is waiting for it; and the
  case where no worker holds a lease on any run at all, which is what the
  database can prove about the worker.
- `latest_checkpoint`: the newest checkpoint of the run, as
  `stage/step v<version> <created_at>`. The checkpoints are read with
  `list_checkpoints(conn, run_id=...)` from `product_hunter.db`, the same call
  `run_log` makes.
- `last_activity`: `last_heartbeat` when it is set, otherwise `updated_at`.
- `errors`: the run's `last_error` and the `last_error` of its failed or dead
  jobs, each passed through `redaction.redact`, at most five entries.

The list keeps the cheap view. Only the single-run results get the detail, so
one run costs a handful of extra queries and a listing of five hundred costs
none.

**The run block.** `render_run` must keep the lines the existing tests assert
on, which are `state: <STATE>`, `progress: <N>%` and `worker: running` or
`worker: not running`. Add the other fields as their own lines: `budget:`,
`checkpoint:`, `last activity:`, `blockers:` and `errors:`. A line with
nothing in it is left out, so a healthy run stays short.

`render_runs` must mark the runs that can be continued right now. A run is
continuable when it is not terminal, is not permanently failed, is not waiting
for the worker to carry out a pause or a cancel, holds no live lease, and its
stored state is one the interface knows. Say so in the line, for example by
ending it with `continuable`, and leave that word off the runs that are not.

**The doctor.** A new function `doctor(conn)` that reads and writes nothing and
returns a dictionary with:

- `contract_version`: the version of the research contract, or the word
  `unknown` when the contract cannot be read.
- `runs_by_state`: how many runs sit in each state.
- `live_leases`: how many jobs are leased with a lease that has not expired.
- `outstanding_requests`: the runs in `PAUSE_REQUESTED` or `CANCEL_REQUESTED`
  whose worker holds no live lease. Each entry names the public id and the
  state. These are the requests the worker will never carry out.
- `malformed_states`: the runs whose stored state is not one the interface
  knows, each with its public id and the state as stored.
- `expired_leases`: how many jobs are leased with a lease that has expired.
- `queued_jobs`: how many jobs are waiting.

Render it as a block of lines the way `render_runs` does. Add a `doctor` branch
to `_execute` that returns that block as the text. The doctor takes no run id.

**The recover.** A new function `recover(conn, research_id=None)` that:

- finds the runs in `PAUSE_REQUESTED` or `CANCEL_REQUESTED` whose worker holds
  no live lease, optionally narrowed to one run;
- turns each pause request into `PAUSED` and each cancel request into
  `CANCELLED`, cancelling the queued jobs of a cancelled run with it, the same
  way `cancel_run` does when no worker holds the run;
- writes a `RESEARCH_RECOVERED` event on every run it touches, naming the
  state it was in and the state it is in now;
- requeues the jobs whose lease expired, by calling `recover_expired_leases`
  from `product_hunter.db.jobs` **outside** its own transaction, because that
  function opens a `BEGIN IMMEDIATE` of its own.

The state changes and their events go in one transaction, the way the other
writers do it. The lease recovery runs after that transaction has committed.
Return the list of the runs that were recovered and the number of jobs that
were requeued. Add a `recover` branch to `_execute`. The action takes an
optional run id: with one, only that run is considered; without one, every
stale request in the database is.

**The dispatcher.** `recover` and `doctor` need no `TargetRequest`, because
`doctor` takes no id and `recover` takes an optional one. Handle them before
the `target = _validated(TargetRequest, ...)` line, the way `create`, `list` and
`log` are handled.

### 3. `.opencode/plugins/product-hunter.ts`

Three changes, nothing else moves.

- The action list gains `doctor` and `recover`.
- The tool result becomes the V2 shape. Today `execute` returns
  `{ output, metadata }` and the tool declares `output: { type: "string" }`.
  The documented V2 shape is `{ content, metadata }`, where `content` is the
  string the model reads. Return `{ content: <the text>, metadata: {...} }` and
  drop the `output` field from the tool definition. Keep the `metadata` keys
  as they are.
- The argument vector keeps its equals form. `buildArgs` already puts every
  value in the `--name=value` shape, which is what makes a value that starts
  with a dash a value and not an option. Keep that, and keep the `create`
  special case, where the objective is the only value that is sent.

Do not add a dependency. The repository has no `package.json`, so
`@opencode/plugin` cannot be imported, and the plugin keeps using the Node
standard library only. Do not change the spawn call: it passes an argument
vector and never a command string, and it must stay that way.

### 4. The commands

`research-doctor.md` and `research-recover.md` are new. Follow the shape of the
files that exist: YAML frontmatter with a `description`, then the instruction.

- `research-doctor.md`: call the `product_hunter` tool with action `doctor`
  and no other argument, and report the text it returns.
- `research-recover.md`: call the `product_hunter` tool with action `recover`
  and the research id `$ARGUMENTS`. When the id is empty, recover every stale
  request and say that is what happened. Report the text the tool returns.

The ten command files that exist keep their names and their meaning. You may
tighten a body where it says more than the tool returns. Do not add a shell
block to any of them: a shell block in a command runs outside the agent's tool
permission flow.

### 5. The tests

`tests/test_registry.py` grows. Follow the file's own conventions: the `conn`
fixture, the `_create`, `_set_status` and `_lease` helpers, and the
`execute(conn, ...)` dispatcher. Add cases for:

- the status block naming the budget, the blockers, the latest checkpoint, the
  last activity and the errors;
- a retry of a `FAILED_PERMANENT` run being refused, with the code
  `invalid_transition`, and the message naming a new research request;
- a run whose stored status is not one the interface knows being reported as
  malformed and not being eligible to continue;
- a stale pause request being reported by the doctor and being resolved by the
  recover, with a `RESEARCH_RECOVERED` event;
- a stale cancel request being resolved the same way, and the queued jobs of
  the run being cancelled with it;
- the doctor reporting the contract version and the number of runs;
- a secret in a run's `last_error` being masked in the status text;
- a secret in a job's `last_error` being masked in the status text;
- two commands at once: two connections on the same file, one creating a run
  and one continuing it, both leaving the database consistent. The connection
  carries a five second busy timeout, so a busy file waits instead of failing.

`tests/test_plugin_control_plane.py` is new. Drive the real plugin file with
node, the way `tests/test_plugin_project_root.py` does: write a small
TypeScript driver into a temporary directory, run it with node, and read its
JSON from stdout. The driver imports the plugin file and calls the exported
`buildArgs` and reads the exported `ACTIONS`. Export `buildArgs` and `ACTIONS`
from the plugin if they are not exported yet.

Check:

- the action list holds the nine original actions plus `doctor` and `recover`;
- `buildArgs` returns an array, never a string;
- a value that begins with a dash stays a value, because of the equals form;
- the objective is only sent for the `create` action;
- the plugin file contains no `shell: true` and no `exec(` and no backtick
  shell interpolation;
- the plugin file never passes the objective or the research id through a
  shell.

Skip the whole module when node is missing or when node cannot run a
TypeScript file, the way the existing plugin test does.

## The rules

- Never change an existing test to make it pass, except the one case the
  contract changes on purpose: a test that asserts a `FAILED_PERMANENT` run
  can be retried. The existing retry tests use `FAILED_RETRYABLE` and must keep
  passing untouched.
- Every refusal leaves a durable event, the way `_record_refusal` does.
- The database stays the authority. Nothing is written outside SQLite.
- No secret reaches the text the model reads.
- Use the existing code, the existing helpers and the existing conventions.
  The shortest complete change wins.
- Run `uv run pytest` when you are done and make it green.

When you are done, report: the files you changed with their line counts, the
test result verbatim, and anything you found that the brief did not cover.
