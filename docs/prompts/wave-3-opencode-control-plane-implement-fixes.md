# Fix round 1: the findings from stage 6 and stage 7

Work only in `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane`
on branch `wave3/opencode-control-plane`. Do not touch main. Do not merge another
branch. Do NOT commit and do NOT run `git add`.

The code gate is green (2174 passed) and the review said **do not ship**. The
findings below were reproduced by the reviewer and by the harden node against
the real code, not guessed. Fix them.

## The files you may change

The same list as before, plus one file:

- `src/product_hunter/registry.py`
- `src/product_hunter/redaction.py`
- `src/product_hunter/runtime/worker.py`
- `src/product_hunter/db/connection.py`   (new to this round)
- `.opencode/plugins/product-hunter.ts`
- `.opencode/commands/*.md`
- `tests/test_registry.py`
- `tests/test_plugin_control_plane.py`
- `docs/reasonix/specs/wave-3-opencode-control-plane.md`
- `docs/reasonix/plans/wave-3-opencode-control-plane.md`

Nothing else. If a fix needs a file outside this list, stop and report it.

## Must: a continuation throws away an outstanding pause request

`continue_run` refuses a run in `CANCEL_REQUESTED` but not one in
`PAUSE_REQUESTED`. The set `_REQUESTED` already holds both. The bare continue
refuses such a run through `eligible_runs`, so the named continue is the odd
one out: it undoes the operator's pause and the run goes on.

Fix: in `continue_run`, refuse when the status is in `_REQUESTED`, with a
message that says the request waits for the worker. Keep the specific refusal
for `CANCEL_REQUESTED` if you like, but the pause request must be refused too.

The same guard belongs in `resume_run`: a run in `CANCEL_REQUESTED` is not a
paused run.

## Must: a pause throws away an outstanding cancel request

`pause_run` refuses only the terminal states, so it overwrites
`CANCEL_REQUESTED` with `PAUSE_REQUESTED` or `PAUSED`. The cancel the operator
asked for is erased. A cancel is the stronger intent: refuse the pause and say
the cancel is still outstanding.

## Must: a named continue hands a malformed run to a worker

`eligible_runs` refuses a run whose stored status is not one the interface
knows, but `continue_run` does not check, so `/continue-research <ID>` on a
run whose `status` is `GARBAGE` queues it and hands the corrupt row to a
worker.

Fix: `continue_run` and `resume_run` refuse a malformed state, with a message
that names the stored state and says the run has to be repaired first.

## Must: a pause reports the run stopped and the run keeps working

`cancel_run` cancels the queued jobs of a run it stops. `pause_run` writes the
state and touches no job, so a run with a queued job is reported as `PAUSED`
and a worker then picks the job up and runs the whole research of a run the
operator was told is paused. The later `/resume` is then refused with "has a
live worker lease and is already running".

Fix: when there is no live lease, the pause must stop the pending work the
same way the cancel does. The `jobs` table has no `paused` status, so the
queued job of a paused run is cancelled, and `resume_run` enqueues a fresh job
when it resumes. `_queue_continuation` already enqueues a new job when there is
no pending one, so the resume keeps working. When there IS a live lease, leave
the job alone: the worker is on it and finishes the step it is on.

## Must: a secret in the run error still reaches the model

`_errors` masks the error when it renders the text, but `RunView.last_error`
is copied raw out of the row by `_view`, and the plugin puts the whole `run`
object into the tool `metadata`. The raw error travels to the model beside the
masked text.

Fix: mask `last_error` in `_view`, so the raw value never enters a view. That
closes the text, the metadata and the JSON at once. The rendered `errors` line
keeps working, because masking twice is harmless.

## Must: a refusal message and an internal error carry the raw text

Two more places hand an unmasked string to the reader:

- `_validated` puts the offending input in the message: `f"{where}: {error['msg']} (got {error.get('input')!r})"`.
- `execute`'s catch-all puts `f"{type(exc).__name__}: {exc}"` in the message.

Fix: pass both through `redaction.redact`. The value the operator typed is
still shown, except where it names a secret.

## Must: the masking misses two shapes

The pattern only catches a label followed by `=` or `:`. Two shapes leak:

- `{"token": "abc"}` — the closing quote of the label breaks the match.
- a bare credential with no label at all, such as `sk-abc123`.

Fix both in `src/product_hunter/redaction.py`:

- allow an optional closing quote after the label, so a JSON form matches.
- add a rule for the credential prefixes that carry no label. Use a pattern
  that matches the common shapes: `sk-`, `sk_live_`, `pk_`, `ghp_`, `gho_`,
  `ghu_`, `ghs_`, `github_pat_`, `xoxb-`, `xoxp-`, `xoxa-`, `AIza`, `AKIA`,
  `ASIA`, `ya29.`, and a JSON web token, which starts `eyJ` and has two dots.
  Keep the match tight enough that ordinary prose does not match: the token
  part needs at least six characters of token alphabet.

Keep the module's shape: `redact(text)` and `safe_error(exc)`, and keep the
worker's behaviour unchanged. Add a test for each shape.

## Should: a retry on a run a worker still holds says it requeued something

`retry_run` has no live-lease check, and it only touches jobs in
`('failed', 'dead')`. A run in `BLOCKED_TOOL` whose worker still holds the
lease is reported as "queued again" while nothing was requeued and the worker
still holds it. `continue_run` refuses the same case. Fix: `retry_run` refuses
a run with a live lease, the way `continue_run` does.

## Should: the doctor and the recover disagree about a state

The doctor normalises the stored status through `_normalize_state`, so a run
stored as `pause_requested` is reported as an outstanding request. The
recover's SQL compares the raw column against
`('PAUSE_REQUESTED', 'CANCEL_REQUESTED')`, so the same run is never found and
the recover reports that it found nothing. The operator is told about a repair
that does not happen.

Fix: make the recover find the same rows the doctor reports. The simplest
correct form is to compare the normalised value in Python, or to upper-case the
column in the SQL.

## Should: one corrupt checkpoint kills every command on that run

`_latest_checkpoint` calls `list_checkpoints`, which parses every checkpoint of
the run and raises `CheckpointError` on a row whose `state_json` is not JSON.
`_detail` calls it on every single-run command, so one bad row makes
`/research-status` and `/research-log` return an internal error. The status
command is what an operator reaches for when a run misbehaves.

Fix: `list_checkpoints` already takes `skip_invalid=True`. Pass it, in
`_latest_checkpoint` and in `run_log`.

## Should: the write path does not retry a busy file, though the spec says it does

The spec promises that a busy file is waited out. The five second busy timeout
is real, the retry is not: `_transaction` does a bare `BEGIN IMMEDIATE` and a
contended write comes back as `internal_error: database is locked`.

The repo already owns the answer. `_with_busy_retry` and `_is_busy` live in
`src/product_hunter/runtime/worker.py` and are private to it. Move both into
`src/product_hunter/db/connection.py`, next to `connect`, export them, and have
the worker import them from there. Then call the retry from the registry's
`_transaction`, so a contended write waits instead of failing.

Keep the worker's own behaviour the same: the same number of attempts and the
same delay.

## Should: the bare continue picks its candidate outside the write

The eligibility read happens before the write transaction opens, so two bare
continues can both pick the same run. Move the selection inside the write.

Make `_transaction` re-entrant so a nested call joins the transaction that is
already open instead of opening a second one: if the connection is already in a
transaction, yield it and do nothing else; otherwise begin, commit and roll
back as it does now.

After that, the bare continue selects and claims inside one transaction, so
the two commands are serialised.

Note what this does and does not buy: the work is still queued once, because
`_queue_continuation` reuses the pending job rather than adding a second one.
A run that is already `QUEUED` stays eligible, so the second command can still
raise the resume count and write a second `RESEARCH_RESUMED` event. That is a
bookkeeping artefact, not duplicated work. Correct the sentence in the spec
that claims two commands cannot both claim the same continuation, so it says
what the code guarantees: the writes are serialised and the work is queued
once.

## Nice: two cheap ones

- `_known_ids` reads every run to show ten. Put the limit in the SQL.
- `_all_views` sorts three times. Use one sort with a tuple key.
- The budget line puts the measured dimension in the middle. Put the measured
  dimension first, so the eye lands on it.

## The spec needs three corrections

`docs/reasonix/specs/wave-3-opencode-control-plane.md` promises things the
code does not keep. Correct the spec where the code is right and the promise
was wrong:

1. It says "two commands cannot both claim the same continuation". Correct it
   as described above.
2. It says the status shows the "current stage" and the "progress". Nothing
   in the repository writes `research_runs.current_stage` or
   `progress_percent` after the create, so both are the values the request
   carried. Say that, and say that the worker writing them is an integration
   requirement.
3. It says "/pause ... stop the run outright when no worker holds it". After
   the fix above that is true, so leave the sentence and make the code match
   it.

## The tests

Add a test for each of these, following the conventions of
`tests/test_registry.py`:

- a named continue on a run in `PAUSE_REQUESTED` is refused, and the run keeps
  the request;
- a named continue on a run in `CANCEL_REQUESTED` is refused;
- a pause on a run in `CANCEL_REQUESTED` is refused, and the cancel survives;
- a named continue on a run with a malformed state is refused;
- a pause on a run with no worker cancels its queued job, and a resume of that
  run enqueues a fresh job;
- the run error in `result.run.last_error` is masked, not only
  `result.text`;
- the refusal message and the internal error message are masked;
- `{"token": "abc"}` and a bare `sk-abc123` are masked;
- a retry on a run with a live lease is refused;
- the recover finds a run stored as `pause_requested`;
- a corrupt checkpoint does not break the status command;
- a write against a locked file waits instead of failing.

Never change an existing test to make it pass. The existing tests all encode
behaviour that stays.

## The rules

- Every refusal still leaves a durable event.
- The database stays the authority.
- No secret reaches the text the model reads, or the metadata beside it.
- Use the existing helpers and conventions. The shortest complete change wins.
- Run `uv run pytest` when you are done and make it green.

Report back: the files you changed with their diff line counts, the verbatim
pytest tail, and anything in this brief that turned out to be wrong.