# Final audit 10: the control plane and the security boundary

**Probe:** Final Audit Probe #10. **Date:** 2026-10-22. **Mode:** read-only. No source code was changed.

**Path note.** The brief asked for `docs/resonix/reports/final-audit-10-control-security.md`.
`resonix` is the spelling this tree was renamed away from. Commit `e699551` ("docs: fold the
documentation tree into one reasonix folder") merged the old `resonix` tree into `reasonix`, and
`7a32a04` ("docs: drop the misspelled docs root from the documentation tree") removed the last
references. Every one of the nine trees inspected here carries `docs/reasonix`. Probe #4 landed its
sibling report at `docs/reasonix/reports/final-audit-04-storage.md` for the same reason, and so does
this one. The docs root that exists is `docs/reasonix/`.

---

## 1. The trees audited

`git worktree list --porcelain` gave all eight. Two live outside the project folder, one under a
local opencode worktree root, one under the system temp directory. `main` is `00a6ca3`.

| Branch | Worktree path | Head |
|---|---|---|
| `wave3/opencode-control-plane` | `<repo>/.worktrees/wave3-opencode-control-plane` | `5e45c63` |
| `wave3/autonomous-operation` | `C:/Anubhav/Web Dev Projects/product-hunter-w3a` | `1efc328` |
| `wave3/source-expansion` | `C:/Anubhav/Web Dev Projects/product-hunter-w3c` | `a9540ee` |
| `wave3/duckdb-analytics` | `<repo>-wt/wave3-analytics` | `3474e58` |
| `wave3/observability-evaluation` | `<repo>-wt/wave3-observability` | `317e7f2` |
| `wave3/scraping-ladder` | `<repo>-wt/wave3-scraping-ladder` | `5f66d46` |
| `wave3/subq-integration` | `~/.local/share/opencode/worktree/44767c/wave3/subq-integration` | `172a74f` |
| `wave3/qdrant-semantic` | `%LOCALAPPDATA%/Temp/opencode/wt-wave3-qdrant` | `357dd5e` |

All eight worktrees are clean at their commit. `main` carries one untracked file, the sibling report
from Probe #4.

**What was run.** The code gate on the control-plane branch: `uv run pytest` in
`.worktrees/wave3-opencode-control-plane`, exit code 0, no failures. The branch's own report claims
"2194 passed"; the suite is green, the exact count is not reproduced here because the summary line is
not emitted by this pytest configuration.

**How the work was split.** Four read-only deep-dives ran in parallel against the four areas the
brief names: worker authority, fetch and SSRF, LLM tool arguments, and projection writes with git.
The findings below were then re-checked by hand against the source, and the eight binding rules in
`.loop/adapter.json` were swept across all nine trees directly.

**Three findings were corrected before this report was published, and every correction is recorded
in place.** A hardening pass and an adversarial review pass ran over the whole finding list.

The first draft held that the old plugin's `{ output }` result shape left the tool's text
unreachable on OpenCode v2.1.2, which would have made all ten commands dead on `main` and on seven
branches. The review raised this as HIGH on the same grounds. The v2.1.2 runtime source settles it
against both: a tool that declares an output schema has its `output` validated and turned into the
model-visible content. The finding is now F5 at Low.

The second correction is a branch attribution. The marker-stripping helper in `llm/decision.py`
lives on the `wave3/subq-integration` branch, not in `main`, so the fix for F7 has to be written
rather than copied from a tree that is already merged.

The third correction is a severity change the review forced, and it is the one that matters most for
the reader. The first draft called the retry of a permanent failure a High defect. The Phase 2
specification made it the specified behaviour, and the Wave 3 specification reversed it. F1 is now a
requirement conflict at Medium, not a bug. The review also refuted the premise of the first draft's
F16: nothing writes `RUNNING` to `research_runs.status`, so the window that finding described does
not exist, and it is now a Low about event noise. The review's own HIGH on CP-2 was not accepted,
because the runtime source it lacked is the authority on the question.

One finding the review found and this report had missed is now F17: the run objective is echoed back
to the model as unmarked text.

---

## 2. Control-plane verdict

**The design is right, and the branch that implements it well is the wrong branch to merge first.**

Three things hold up under inspection, on every tree:

- **OpenCode is the control surface, and it holds no state.** The plugin
  (`.opencode/plugins/product-hunter.ts`) registers one tool, spawns one fixed Python module with an
  argument vector, and hands the text back. It never runs a research stage. The registry
  (`src/product_hunter/registry.py`) owns the durable run state. The worker owns execution. The
  boundary runs one way.
- **The worker is the authority.** `create_run` enqueues a `DISCOVERY` job
  (`registry.py:840-848` on the control-plane branch). The worker claims jobs from the `jobs` table
  and resolves handlers by stage (`runtime/worker.py:154-173`). Nothing in the plugin or the registry
  executes a stage. One caveat, and it is the sharpest one in this report: no handler for the
  `DISCOVERY` stage ships in the repository (F4b).
- **Cross-chat continuity is SQLite.** `database_path()` resolves `PH_DB_PATH` or
  `db/product_hunter.db` under the repository root (`config.py:243-251`). The registry opens that
  file (`registry.py:1726`); the worker opens the same one (`runtime/__main__.py:65`). The session
  id travels as `--chat-id` and is stored as a label. A second chat reaches a first chat's run
  through the database, never through a transcript.

`/continue` does not guess. This holds in both the old and the new registry. When more than one run
is eligible, the dispatcher returns the candidates and sets `needs_selection`
(`registry.py:1634` on the control-plane branch, `registry.py:1084` in the old one), and
`.opencode/commands/continue.md` hands the choice to the operator through the `question` tool. The
`question` tool is a real built-in in OpenCode v2.1.2, so the flow is sound on a client that can
render it. A headless client that cannot will still receive the candidate list as text and can ask
in plain chat, so the path degrades rather than breaks.

**Where the verdict turns.** The corrected control plane exists on exactly one branch.
`wave3/opencode-control-plane` carries a rewritten `registry.py` (git blob
`9addfe4799cd5ca6e21d92d5776a1b2d108b8303`, 63 KB). The other seven Wave 3 branches and `main` all
carry the same older file (blob `464deba04cabc5bcb1d1abfdee12fcbe1f552309`, 41 KB).
The table below is the whole difference. The first five rows are the launch blockers, covering F1,
F1b, F3 and F2. The last three are improvements that are worth taking but do not block. Merging the
seven non-control-plane branches first carries the older file forward.

| | `main` and 7 branches | `wave3/opencode-control-plane` |
|---|---|---|
| `retry_run` accepts `FAILED_PERMANENT` | yes, per the Phase 2 contract (`registry.py:95-100`) | no, per the Wave 3 contract (`registry.py:106-111`) |
| `retry_run` refuses a live worker lease | no | yes (`registry.py:1084-1087`) |
| an unknown stored status is refused | no | yes (`registry.py:936-941`) |
| an unknown status is ineligible for a bare continue | no (`registry.py:345-360`) | yes (`registry.py:552-568`) |
| secrets masked before the error is shown or stored | no (zero redaction) | yes (`registry.py:327-329`) |
| `doctor` and `recover` actions | absent | present |
| busy-file retry on the write path | absent | present (`db/connection.py:87-99`) |
| tool result shape | legacy `output`, which v2.1.2 still accepts | documented `content` |

The `continue_run` refusal in the old file tells the operator to "retry it instead"
(`registry.py:645-647`), and `retry_run` in the same file accepts that state. The Phase 2
specification intends exactly that pairing, and the Wave 3 specification reverses it. The point for
a launch is not that one of the two is wrong, but that the tree currently holds both.

---

## 3. The ten commands, one by one

The brief names ten commands. The control-plane branch ships twelve; `/research-doctor` and
`/research-recover` are the two extra.

| Command | Action | Verdict | Notes |
|---|---|---|---|
| `/research` | `create` | PASS for the control plane | Writes the run, the first checkpoint, the first event and the first job in one transaction. Reuses a live run that already holds the objective, and says so. Nothing shipped can execute the stage it enqueues (F4b). |
| `/list-research` | `list` | PASS | Ordered by state then by recency, and marks the runs that can be continued right now. |
| `/research-status <ID>` | `status` | PASS on the control-plane branch | Budget, blockers, latest checkpoint, last activity and errors. On the other eight trees the run error is stored raw and reaches the client raw (F2). |
| `/continue-research <ID>` | `continue` | PASS on the control-plane branch | Refuses terminal, permanent, outstanding-request, unknown-state and live-lease. On the other eight trees an unknown stored status is handed to a worker (F3). |
| `/continue` | `continue` | PASS | Never guesses, in either registry. Selection and claim share one transaction. |
| `/pause-research <ID>` | `pause` | PARTIAL | The request is recorded durably. No worker reads it (F4). |
| `/resume-research <ID>` | `resume` | PASS | Only a paused run resumes; the pending job is reused, not duplicated. |
| `/retry-research <ID>` | `retry` | Behaviour differs by branch | On the Phase 2 contract a permanent failure is retried, because retry is the only transition that moves it (`phase-2-opencode-interface.md:90-91`, `106-109`). The Wave 3 contract refuses it (F1). The old registry also lacks the live-lease guard (F1b). Correct on the control-plane branch. |
| `/cancel-research <ID>` | `cancel` | PARTIAL | The request is recorded durably. No worker reads it (F4). |
| `/research-log <ID>` | `log` | PASS on the control-plane branch | Events, checkpoints, jobs and attempts, all bounded by one limit, all scoped to the run. The old registry never prints the job error at all, so it hides the problem rather than leaking it (F2). |
| `/research-doctor` | `doctor` | PASS | Read-only. Reports contract version, states, live and expired leases, outstanding requests, malformed states. Control-plane branch only. |
| `/research-recover` | `recover` | PASS | Resolves the requests whose worker is gone, and requeues the jobs whose lease expired. Control-plane branch only. |

**The OpenCode mechanisms are used correctly on the control-plane branch, and not on the others.**
The installed product is `opencode v2.1.2`. Verified against the V2 documentation and the published
`@opencode/schema@2.1.2` types:

- Commands live in `.opencode/commands/`, one `.md` per command, the file name is the command name,
  `$ARGUMENTS` expands to the whole argument string, the body is the prompt. Correct in every tree.
- Plugins live in `.opencode/plugins/` and are loaded automatically. Correct in every tree.
- A tool registers through `ctx.tool.transform((editor) => editor.add({...}))` with a JSON Schema for
  the input. Correct in every tree.
- **A tool returns `{ content, metadata }` in V2.** `ToolStateCompleted` in the published v2.1.2
  schema is `{ status, input, content: NonEmptyArray<{type:"text",text}|{type:"file",...}>, metadata? }`.
  There is no `output` field on a tool state; the only `output: string` in that schema belongs to the
  shell tool. The control-plane branch returns `{ content, metadata }` and drops the
  `output: { type: "string" }` declaration, which is the documented shape. The other eight trees
  still use the legacy `output` shape, and the v2.1.2 server still accepts it: the legacy value is
  validated against the declared schema and turned into the model-visible text. Both work. See F5
  for the source and the one trap the legacy path sits next to.
- The `question` tool exists in V2, so `/continue` can ask the operator to pick.
- The plugin default-exports a plain object instead of `Plugin.define`. This is fine: the published
  `define` is the identity function, so the two are the same value at runtime. The repository has no
  `package.json`, so `@opencode/plugin` cannot be imported from the project. Worth a one-line change
  when a `package.json` arrives, and worth nothing today.

**OpenCode's permission system is not a safety authority for this tool, and must not be treated as
one.** The plugin calls node's `child_process.spawn` directly, so OpenCode's `shell` rules do not
gate it: the user's global `opencode.jsonc` denies `shell` with `git push*`, `git reset --hard*`,
`drop table*` and the rest, and none of that reaches the registry. A plugin tool that asserts no
permission inherits the default `{action:"*",resource:"*",effect:"allow"}`.

The gap is real and it is worth writing down, because the alternative reading ("the shell deny list
protects this") is wrong. It is also not exploitable as it stands. The spawned command is fixed
(`uv run --project <dir> python -m product_hunter.registry <action>`), the shell is never used, and
every value the model can influence is validated by a pydantic schema before it acts
(`CreateRequest`, `TargetRequest` with `^[A-Za-z0-9._-]+$`, a clamped limit, and parameterised
SQL). The registry's own validation is the only gate, and for this command it is sufficient. The
correct fix, when one is wanted, is `ctx.permission.assert` inside the tool rather than a wider
permission rule.

**The plugin resolves its own repository root.** `findProjectRoot(PLUGIN_DIR)` is tried before the
session's directory, so the tool always reads the database that belongs to the tree the plugin ships
in. That is the right default. It has one consequence, covered in F6.

---

## 4. Security verdict

**The fetch and evidence paths are in good shape. The gaps are at the edges: a prompt-injection
escape in two of the three prompt builders, a proxy bypass of the SSRF gate, and a set of known
shortcuts around the byte ceiling and DNS rebinding that the code itself already records.**

What is right, verified across all nine trees:

- **No shell anywhere.** Zero hits for `shell=True`, `os.system`, `os.popen`, `subprocess.call`,
  `subprocess.run` or `subprocess.Popen` in any `src/` tree. The only `subprocess` use is the
  OpenCode CLI, always an argument vector with `shell=False` and a timeout
  (`llm/opencode.py:121-152`, `llm/discovery.py:87-113`).
- **No `eval`, `exec`, `pickle.loads`, `marshal` or unsafe `yaml.load`** in any `src/` tree. Only
  `yaml.safe_load`.
- **No git.** Zero git invocations in any `src/` tree. No `commit`, no `push`, no `clone`, no
  `reset`, no `clean`. Nothing in the system can push.
- **No external account actions, and one external service call.** No HTTP POST to a third party
  that acts on an account, no SMTP, no OAuth, no webhook, and nothing that posts research output
  anywhere. The single exception is on the `wave3/subq-integration` branch, which calls a decision
  service at `https://api.subq.ai/v1/systemone` (`llm/subq.py:343`). See F21 for what that call
  carries.
- **No secrets in the repository.** No provider key, bearer token or password in any `src/`,
  `config/` or `db/` file. The only `deepseek` hits are model names and routing patterns. `.env`,
  `data/`, `runtime/` and `logs/` are gitignored and untracked in every tree.
- **No browser cookies and no operator credentials reach a fetched host.** The Playwright context
  in the scraping-ladder branch sets `accept_downloads=False`, `permissions=[]`,
  `service_workers="block"` and no `storage_state`.
- **SSRF admission is real.** `safety.admit_url` (`safety.py:73-106`) checks the scheme against the
  source, refuses URL userinfo, refuses IP-literal hosts, requires the host to be in the source
  allowlist, and allows only ports 80 and 443. `is_public_ip` (`safety.py:31-50`) refuses loopback,
  private, link-local, multicast, reserved, unspecified and CGNAT addresses, and unwraps
  IPv4-mapped IPv6. Every redirect hop is re-admitted and re-resolved
  (`fetch.py:345-347`), the hop count is capped (`fetch.py:340`), and an HTTPS to HTTP downgrade is
  refused (`safety.py:113-114`).
- **The Obsidian projection is contained.** `VaultLayout.resolve` resolves the target and refuses
  anything outside the vault root (`projection/vault.py:169-179`). Note components are restricted to
  `^[A-Za-z0-9._-]{1,120}$` and otherwise slugified and hashed (`vault.py:73`, `197-206`).
- **Untrusted content is never executed.** Raw bytes are stored content-addressed under
  `data/raw/<source_id>/<sha256>.<ext>` before any parsing, the filename is a hash, and the source id
  is validated. No raw content reaches a shell, a path, a SQL fragment or a tool argument.
- **The extraction contract is the one place the live pipeline refuses a model claim, and it works.**
  Strict pydantic schemas with `extra="forbid"`, a quote-verbatim rule that requires the cited span to
  be a real substring of the chunk the model was shown, containment of that span inside the chunk,
  and fail-closed rejection codes.
- **No prompt tells the model to obey captured text.** A grep of `.opencode/skills/` and `eval/`
  finds nothing that treats captured content as an instruction. The `pain-point-extraction` skill
  says the code decides and demands verbatim quotes, and the `source-discovery` skill states that
  "URL admission is a worker decision" and that "a URL that came from a model is a proposal and
  meets the same gate as any other". The prompt guidance and the code agree.

What is not right is in section 6. On the security boundary the two that matter most are F7, where
captured text can close the region the prompt declares as data, and F8, where a proxy environment
variable routes the fetch around the private-IP check.

---

## 5. The trust boundary, link by link

The brief asks for the chain to be verified. Here is what each link actually does.

```
Internet
  -> raw capture            content-addressed, never executed
  -> quarantine / sanitize  HTML to text, hostile URLs blocked, control characters dropped
  -> structured evidence    strict schemas, fail-closed rejection codes, quote-verbatim
  -> LLM analysis          untrusted text inside a delimited DATA region
  -> validated output       the quote must resolve to a span in the shown chunk
  -> worker state          SQLite, fenced leases, durable checkpoints
```

**Internet to raw capture: PASS.** `Fetcher._fetch` (`fetch.py:307-410`) admits the URL, honours
robots where the source requires it, follows at most `source.max_redirects` hops, each hop
re-admitted, and writes the bytes to `RawStore.put` (`raw_store.py:40-69`). The path is a SHA-256.
Nothing is parsed before it is stored.

**Raw capture to quarantine: PASS.** `documents.py` parses with BeautifulSoup and extracts text,
decomposing `script, style, noscript, nav, footer` first (`documents.py:29`, `234-258`). The
projection's `sanitize.neutralize` (`projection/sanitize.py:52-73`) escapes `&`, `<`, `>`, backticks,
wikilinks, Templater braces, Obsidian comments, and breaks markdown links, and `_block_hostile_urls`
(`76-90`) refuses `javascript:`, `vbscript:`, `data:` and `file:` URLs.

**Structured evidence: PASS.** The extraction contract refuses a candidate whose quote is not a
verbatim span of the shown chunk (`quote.py:84`), refuses a span that is not contained in that chunk,
and records a rejection code for every failure. This is the strongest link in the chain.

**LLM analysis: PARTIAL, and this is F7.** The prompt wraps the captured text in
`<<<UNTRUSTED_SOURCE_START>>>` / `<<<UNTRUSTED_SOURCE_END>>>` and instructs the model to treat it as
data (`extraction.py:353-357`, `clustering/adjudication.py:74-82`). That instruction is real. But in
those two builders the captured text is substituted without stripping the marker strings, so a page
that contains the literal end marker can close the region early and the rest of the page reads as
instructions. The third builder already gets this right: `llm/decision.py:665-668` removes the
markers before substitution and uses the stripped value at line 618. The fix exists in the repository
and is not applied in two of three places.

**Validated output to worker state: PASS, with one gap in the middle.** A candidate that fails any
check is rejected and never stored, and a stored record re-derives its own normalized key and refuses
a record whose key does not match. The gap: the research contract's *gate* is a library with no
caller. `require_promotable`, `evaluate_run_gate`, `evaluate_promotion_gate` and `evaluate_stage_gate`
are defined in `db/research_gate.py` and re-exported from `db/__init__.py`, and nothing outside that
module calls them. `docs/reasonix/RESEARCH_CONTRACT.md` says so plainly: "There is no CLI command, no
worker handler, and no caller, so the code is complete and reachable as a library and gates nothing
yet." The budget, the promotion thresholds and the hard requirements are therefore not enforcing
anything on a live run. Only the extraction contract bites today. This is F9.

**The rule the whole chain rests on** is stated in the repository's own contract document and holds
in the code: "Agent instructions alone are not enforcement. A rule that lives only in a prompt is a
request, not a contract." The extraction contract is a contract. The research gate is, for now, a
specification with a library attached.

---

## 6. Vulnerabilities and gaps

### F1 (Medium): the retry rule changed in Wave 3, and only one branch has the new one

**Files:** `src/product_hunter/registry.py:95-100` (`_RETRYABLE` includes
`RunState.FAILED_PERMANENT.value`) and `registry.py:751-777` (`retry_run`) in `main` and in seven of
the eight Wave 3 branches. The new rule is at `registry.py:106-111` and `1059-1106` on
`wave3/opencode-control-plane`.

**This is a requirement conflict, not a bug in the old code.** The Phase 2 interface specification
makes retry the way out of a permanent failure on purpose:
`docs/reasonix/specs/phase-2-opencode-interface.md:90-91` says `FAILED_PERMANENT` means "The run
failed. Only a retry may move it", and the transition table at line 106-109 gives `retry` the
condition "run is `FAILED_RETRYABLE`, `FAILED_PERMANENT`, or `BLOCKED_TOOL`", while `continue` is
refused for a permanent failure. The old `continue_run` refusal points the operator at "retry it
instead" (`registry.py:645-647`) because that is what the specification says to do.

The Wave 3 specification reverses the decision
(`docs/reasonix/specs/wave-3-opencode-control-plane.md:22-24`): "A permanent failure is by definition
not retryable, and the refusal in `continue_run` even told the operator to 'retry it instead'. The
two paths contradicted each other."

Both positions are defensible. The one thing that is not defensible is having two of them in the
tree at once. The Wave 3 specification is the later one and is the branch that implements it, so
merging the other seven branches first ships the older contract and leaves the newer specification
unimplemented.

### F1b (Low): `/retry-research` does not refuse a run a worker still holds

**Files:** `src/product_hunter/registry.py:751-777` (old) against `registry.py:1084-1087` (fixed).

The old `retry_run` updates the `jobs` rows straight to `queued` and resets `worker_id`, with no
check that a worker holds a live lease on the run. The retry reports that it queued something while
the worker still owns the job, and the worker's next `complete_job` is fenced out by the bumped
`lease_epoch`, so the stage is lost rather than completed.

The live-lease case is narrower than it first looks. `fail_job` releases the lease when it records
the failure (`db/jobs.py:308-316` sets `worker_id = NULL, lease_expires_at = NULL` and moves the job
to `queued` or `dead`), so a run that has failed does not normally hold a live lease on the job
that failed. The gap is a consistency one: the Wave 3 branch added the guard, the others did not,
and the guard is cheap.

### F2 (Medium): run and job errors are stored unmasked and handed to the client raw

**Files:** `src/product_hunter/registry.py:280` (`last_error=row["last_error"]`, no masking) and
`.opencode/plugins/product-hunter.ts:239-247` (the tool result's `metadata` carries `payload.run`),
in `main` and in seven of the eight Wave 3 branches. Fixed on `wave3/opencode-control-plane` at
`registry.py:327-329` and `545-549`, with the masking in `src/product_hunter/redaction.py`.

The old registry has zero occurrences of "redact". A handler that raises with a credential in the
message writes that message into `research_runs.last_error` or `jobs.last_error`, and it stays there
in the clear. The durable store is the wrong place to keep a secret, and other readers of the same
rows exist: the DuckDB analytical layer, the Obsidian projection, and any dump of the database file.

What reaches the model is narrower than it first looks, and it is worth being precise about it. The
old `render_run` (`registry.py:839-851`) and `render_log` (`registry.py:868-899`) never print the
error, so the model-visible text carries no error at all. The raw value travels in the tool result's
`metadata`, and OpenCode keeps the tool `metadata` beside the tool `content` on the tool state
(`packages/core/src/session/message-updater.ts:353-357`), while the model request carries only the
result's content items (`packages/core/src/session/model-request.ts:166-175`). On that evidence the
secret reaches the TUI and the session transcript, and I could not confirm that it reaches the
model.

The new registry puts the error in front of the model deliberately, and masks it first. The gap in
the old one is the write path, not the read path, and the branch's own integration requirement
number 4 says the same thing: "masking at the source too would keep the raw value out of the
database."

### F3 (Medium): an unknown stored status is handed to a worker

**Files:** `src/product_hunter/registry.py:345-360` (`eligible_runs`, old) and `631-669`
(`continue_run`, old) against `552-568` and `906-955` (fixed).

The old `eligible_runs` filters on terminal, permanent, requested and worker-active, but not on
"is this a state the interface knows". A row whose `status` column holds `GARBAGE` is eligible for a
bare `/continue`, so a corrupt row looks like healthy work waiting for a worker. The fixed version
refuses it and says the run has to be repaired first.

### F4 (High): the worker never honours a pause or a cancel

**Files:** `src/product_hunter/registry.py:1008-1056` (`pause_run`) and `1109-1144` (`cancel_run`).
Confirmed by grep across all nine trees: `PAUSE_REQUESTED` and `CANCEL_REQUESTED` appear in
`registry.py` and nowhere else. No worker, scheduler or CLI module reads `research_runs.status`.

When a worker holds the run, `pause_run` records `PAUSE_REQUESTED` and `cancel_run` records
`CANCEL_REQUESTED`, and leaves the leased job alone. No code carries the request out. The status text
tells the operator "the worker pauses the run when it sees the request"
(`registry.py:1245-1252`), and the worker never looks. The request sits until the lease expires and
someone runs `/research-recover` by hand.

This is disclosed. The control-plane branch's own spec names it: "The worker does not read the run
status, so a pause issued against a live worker stays `PAUSE_REQUESTED` until somebody resolves it by
hand." It is listed there as an integration requirement for the worker. It is still the largest gap
between what the commands promise and what the system does, and no Wave 3 branch closes it.

### F4b (High): nothing in the repository can execute the stage the control plane enqueues

**Files:** `src/product_hunter/registry.py:842` and `889` (`enqueue_job(stage="DISCOVERY", ...)`)
on the control-plane branch, `src/product_hunter/runtime/__main__.py:41` and `104-113`
(`_load_handlers`), `src/product_hunter/runtime/worker.py:135-136` (`if not self.handlers:
return None`).

The worker takes its handler mapping from the environment: `PH_WORKER_HANDLERS` names a
`module:attribute` factory, and `_load_handlers` imports it. No such module ships in the
repository. There is no `PH_WORKER_HANDLERS` reference anywhere in `src/`, no handler factory, and
no handler bound to the `DISCOVERY` stage that `create_run` and `_queue_continuation` enqueue.

The consequence is that a worker started with the shipped code has an empty handler map, claims
nothing, and the run that `/research` created sits in the queue forever with no error anywhere. The
control plane is complete and correct. The execution side it drives has no implementation of the
stage it asks for.

A worker that has handlers for other stages behaves differently, and worse in a quieter way. It
claims the `DISCOVERY` job, finds no handler for the stage, and records the failure
(`runtime/worker.py:154-163`), so the job burns its attempts and ends in the dead-letter table. The
operator sees a failed run instead of a run that never started.

This is the same class of gap as F4, and it is the one that matters most for a launch: F4 says the
worker does not read the requests it is given, and F4b says there is no worker behaviour to read
them with. Both close when a `DISCOVERY` handler module ships and the worker is started with
`PH_WORKER_HANDLERS` pointing at it.

Probe #9 reached the same place from the autonomy side and called it F-03 ("No handler exists for
any stage the cadence names") and F-09 ("Jobs for stages with no handler accumulate without bound"),
in `docs/reasonix/reports/final-audit-09-autonomous-runtime.md`. Two independent probes, two
different trees, the same gap.

### F5 (Low): the tool result shape on eight of nine trees is the legacy one, and it still works

**Files:** `.opencode/plugins/product-hunter.ts:212`, `217`, `232`, `238` in `main` and in the seven
non-control-plane branches. Changed on `wave3/opencode-control-plane` at `product-hunter.ts:239-249`.

**This started as a HIGH finding and the OpenCode v2.1.2 source refuted it.** The published
`@opencode/schema@2.1.2` models a finished tool call as
`ToolStateCompleted = { status, input, content: NonEmptyArray<...>, metadata? }`, with no `output`
field on a tool state, and the V2 plugin guide shows `execute` returning `{ content }`. The eight
branches still return `{ output: "..." }` and declare `output: { type: "string" }` on the tool, which
reads like a dead result on a V2 server.

It is not dead. `packages/core/src/tool/runtime.ts` at tag `v2.1.2` has two paths. When a tool
declares an output schema, the server takes the `output` value, validates it against that schema,
and synthesises the model-visible content from it:

```
46:    if (tool.output === undefined) {
47:      if ("output" in result) return yield* Effect.die("Tool result declared output without an output schema")
50:        content: normalizeContent(result.content),
54:    if (!("output" in result)) return yield* new Tool.Error({ message: "Tool did not return its declared output" })
55:    const output = yield* encodeOutput(tool.output, result.output)
58:      content: normalizeContent(result.content, output),
```

and `normalizeContent` (`runtime.ts:261-264`) turns an absent content into
`[{ type: "text", text: stringify(output) }]`. So the old plugin's `{ output: "..." }` reaches the
model as text, and the new plugin's `{ content, metadata }` reaches it as text. Both work.

Two things are still worth saying. The old shape is a legacy path the V2 plugin guide does not
document, and the new one is the documented one, so the control-plane branch's change is worth
keeping on its own merits. And the old plugin sits next to a trap: a tool that declares no output
schema and returns an `output` key dies the effect rather than degrading (`runtime.ts:47`). The old
plugin always declares the schema, so it never hits that, but a future edit that drops the
declaration and keeps the return would turn every command into a defect.

The control-plane branch's own live test against a real v2.1.2 server confirms the `content` shape
works and shows the tool text coming back verbatim.

### F6 (Medium): the same plugin id in every tree, and one tree is nested inside another

**Files:** `.opencode/plugins/product-hunter.ts` in every tree, all with `id: "product-hunter"` at
line 177 (old) and 179 (new).

OpenCode discovers `.opencode` in ancestor directories of the session's location. The control-plane
worktree sits at `<repo>/.worktrees/wave3-opencode-control-plane`, inside the main checkout, so both
plugin files load when a session runs in the worktree. The branch's own report records the server log
showing both loads, and says the ancestor's wins the registration, which leaves the live tool
exposing the old nine-action schema. The worktree's own `/research-doctor` and `/research-recover`
then call an action the loaded tool does not have, and the model gets "Unknown action".

The report calls this a property of the layout. It is, and it disappears when the project is not
nested under another checkout carrying the same plugin. It is still a launch hazard for the workflow
the branches are built for.

### F7 (Medium-High): captured text can close the untrusted-data region

**Files:** `src/product_hunter/extraction.py:355-357` (the markers) and `428-429` (the substitution),
and `src/product_hunter/clustering/adjudication.py:76-82` (the markers) and `103-105` (the
substitution).

A page whose body contains the literal `<<<UNTRUSTED_SOURCE_END>>>` closes the region the prompt
declares as data. Everything after it is presented to the model as prompt body, in the same channel
as the instructions. The adjudication builder reaches the same place by a different route: it uses
`_PROMPT.format(left=..., right=...)`, and `.format` raises on a literal brace in the substituted
text, so braces fail closed. The markers contain no braces, so the injection still lands.

`llm/decision.py` already does this correctly, on the `wave3/subq-integration` branch:
`_without_markers` (`decision.py:665-668`) removes both markers, and line 618 substitutes the
stripped value. The helper carries a comment about why it is repeated rather than shared. That
branch is unmerged, so the fix is not in `main` and not in the other six branches. It has to be
written for the extraction and adjudication prompts, and it has to be shared rather than copied
three times.

**Impact.** The worst outcome is instruction hijack inside the model, not code execution: nothing
downstream turns model prose into a command, a path or a tool call. But the hijacked model can
still produce a candidate that passes the quote-verbatim check, because a poisoned page can make the
model propose its own injected text as a "pain point" and quote it correctly. The quote check
protects provenance, not the interpretation.

The review put this at Medium rather than Medium-High, on the grounds that the damage is data
integrity and not code execution. That reasoning is right about the kind of damage and I kept the
higher severity anyway, because the product's entire output is extracted pain points and a page
that can make the model propose attacker-chosen text as a genuine pain point corrupts exactly what
the operator paid for. If the operator only wants the injection to be noticed rather than
prevented, Medium is the right number.

**The path is live today.** `documents.chunk_documents` (`documents.py:129-165`) slices the parsed
text into chunks with no marker sanitisation; `_parse_blog` (`documents.py:234-258`) sets the text
from `soup.get_text(...)` over the raw bytes; the HN Algolia and GitHub Issues parsers
(`documents.py:171-197`, `200-231`) copy attacker-controlled comment and body text straight into
`Document.text`; and `_build` (`documents.py:271-289`) copies the parsed text in untouched. The
`blog_http` source is disabled in `config/sources.yaml`, but `hn_algolia` and `github_issues` are
enabled and their text fields are attacker-written, so the raw body reaches the prompt.

### F8 (Medium-High): a proxy environment variable routes around the SSRF gate

**Files:** `src/product_hunter/cli.py:395` (`httpx.Client(follow_redirects=False)`), used by
`Fetcher._send` (`fetch.py:422-427`).

httpx defaults to `trust_env=True`. No `trust_env` appears anywhere in any `src/` tree, and no
`HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` is read or cleared by the code. With a proxy set, httpx
sends the hostname to the proxy and the proxy resolves it independently, so the local DNS answer
that `safety.admit_url` checked is not the answer the request uses. The allowlist still holds by
hostname, but the "resolves to a public IP" guarantee does not, and a hostname that resolves
publicly locally and privately at the proxy is exactly the case the private-IP check exists to stop.
`cli.py:395` is the only production construction of the client; `fetch.py:274` and
`collection/orchestrator.py:343` inject it.

`NO_PROXY` is not part of this. It excludes a host from proxying, and the domain and IP allowlist
still runs, so it does not widen the boundary. The vector is `HTTP_PROXY`, `HTTPS_PROXY` and
`ALL_PROXY`.

### F9 (Medium): the research contract gate has no caller

**Files:** `src/product_hunter/db/research_gate.py:717-838` (`evaluate_stage_gate`,
`evaluate_run_gate`, `evaluate_promotion_gate`, `require_promotable`), re-exported at
`src/product_hunter/db/__init__.py:76-85` and `159-188`. No call site exists in any tree.

The budget model, the twelve per-run budget dimensions, the hard requirements, the evidence
thresholds and the promotion policy are all implemented and fail-closed, and nothing calls them.
`docs/reasonix/RESEARCH_CONTRACT.md` is honest about this and says the pipeline that would make it
bite is the next wave's work. The security consequence is specific: a run can exceed its budget, skip
a required tool, and be promoted, because the only enforcement on the live path is the extraction
contract.

### F10 (Medium): the byte ceiling is checked after the body is buffered

**Files:** `src/product_hunter/fetch.py:370-377`; the same shape in the scraping-ladder branch at
`src/product_hunter/collection/adapters/base.py:483-487`.

`response.content` is read in full, then compared with `source.max_bytes`. The cap therefore limits
what is stored, not what is transferred, and a large response is materialised in memory before it is
refused. The code says so itself, in a `shortcut:` comment at `fetch.py:322-325`. The same applies to
the browser rungs, which additionally may receive a compressed body and decompress it before the cap.

### F11 (Medium): a DNS rebind inside the admission window is not caught

**Files:** `src/product_hunter/fetch.py:326-330` (the `shortcut:` comment), `safety.py:52-62`
(`resolve_public_ips`).

The host is resolved and every address is checked before the request, and every redirect hop is
resolved and checked again, but the client is handed the hostname and resolves it again at connect
time. The window between the check and the connect is not closed. The comment says so.

### F12 (Medium): the Apify rung delegates fetching to a remote actor

**Files:** `src/product_hunter/collection/adapters/adapters.py:318-341` (`ApifyDriver.take`) and
`adapters.py:79-90` (`ApifyAdapter`) in the scraping-ladder branch.

The seed URL is admitted locally, then sent to a remote actor that performs its own requests inside
another trust boundary: `run_input = {"startUrls": [{"url": request.url}], "maxItems": 1}`
(`adapters.py:325-328`), handed to `client.actor(self._actor_id).call(...)` (`adapters.py:332`). Those
requests are not re-admitted, not counted against the run budget, and not covered by the local
redirect rules. The local Playwright and crawl4ai rungs are gated per request and per redirect
(`adapters.py:144-159`, `235-248`); the Apify rung is not, because the fetcher is not in this
process.

### F13 (Medium): the model weights are downloaded with no integrity check and no pinned revision

**Files:** `src/product_hunter/semantic/providers.py:583-596` (`snapshot_download`),
`providers.py:323-339` (`_check_snapshot`), `providers.py:114-126` (`_checked_model_id`),
`config/semantic.yaml` in the qdrant-semantic branch.

The download comes from HuggingFace, over the library's default TLS. `_check_snapshot` checks that
the required files exist and are non-empty, and nothing else: there is no checksum. The `revision`
in the config is empty, so the download follows whatever the host serves at the time. The model id
is validated as `org/name` against a regex (`providers.py:114-126`), so it is not influenced by
untrusted input, but the bytes that arrive are trusted on the strength of the hostname.

### F14 (Low): Windows reserved device names are not rejected in note paths

**Files:** `src/product_hunter/projection/vault.py:73` (`_SAFE_COMPONENT`),
`vault.py:197-206` (`_component`), `vault.py:208-212` (`_folder_name`), `vault.py:169-179`
(`resolve`).

`CON`, `NUL`, `PRN`, `AUX`, `COM1` to `COM9` and `LPT1` to `LPT9` match the component filter, so
`_component` returns them unchanged, and `_folder_name` rejects only `/`, `\` and the two dot names.
`resolve` checks containment under the vault root and does not reject a reserved device name, so a
source, market or day component named `CON` resolves to a path Windows cannot create as a regular
file.

The opportunity note names themselves are not exposed: `_note_name` (`vault.py:182-189`) appends an
eight-character hash to the slug, so a note is never a bare reserved name. The gap is in the
folder and component names. The containment check still holds, so this is a denial of service on
one path, not a traversal.

### F15 (Low): reference-style markdown links survive the Obsidian sanitizer

**Files:** `src/product_hunter/projection/sanitize.py:52-73` (`neutralize`).

`neutralize` breaks `](` so an inline link or image cannot form (`sanitize.py:72`), and
`_escape_line_starts` escapes only `#`, `|`, `---`, `= ` and `~~~` at the start of a line
(`sanitize.py:45`, `93-107`). It does not touch `][`, and a line that begins `[ref]:` is not in the
marker list, so a link definition survives. `https` is an allowed scheme, so the definition's URL
is left alone. A crafted quote can therefore leave a live reference definition behind, and a reader
who opens the note has their client fetch a URL the sanitizer would have refused inline. The same
gap applies to `!`, which is not escaped, so `![alt][ref]` survives whole.

### F16 (Low): a bare `/continue` on a queued run inflates the resume count

**Files:** `src/product_hunter/registry.py:906-955` (`continue_run`) and `869-903`
(`_queue_continuation`) on the control-plane branch.

**This started as a Medium and the review refuted its premise.** The first draft claimed that a run
whose stored status is `RUNNING` could be requeued between stage jobs, and that the stage would run
twice. Nothing writes `RUNNING` to `research_runs.status`. The only writes are `'QUEUED'`
(`registry.py:881`, `1095`), a parameter for the pause, cancel and recover transitions
(`registry.py:1033`, `1123`, `1191`), and `'completed'` on import (`db/store.py:998`). `RUNNING`
exists as an enum value, a state alias and a list rank, and nothing else.

So a run is `QUEUED` for its whole life, and a bare `/continue` on a queued run raises
`resume_count` and writes a second `RESEARCH_RESUMED` event. The work itself is not duplicated: a
pending job is reused rather than a second one added (`registry.py:869-903`), and `enqueue_job` keys
on `research_continue:{run_id}:{count}` so the same continuation does not produce a second row. The
effect is a noisy event stream and a resume count that does not mean what it says.

### F17 (Low): the run objective is echoed back to the model as unmarked text

**Files:** `src/product_hunter/registry.py:1258` (`render_run`: `f"{run.public_id}  {run.objective}"`)
and `registry.py:1308` (`render_runs`), on every tree. `registry.py:765-777` (`_reusable_row`) and
`779-849` (`create_run`) on the control-plane branch.

The objective is whatever the operator typed at `/research`, and it comes back verbatim in the
model-visible text of `/list-research`, `/research-status` and `/research-log`. Nothing marks it as
data. This is a second injection channel, separate from F7, and the review found it.

The sharp case is persistence. A run created with a hostile objective keeps it, and every operator
who later lists the runs receives that objective as instruction-bearing text. An operator who pastes
a fragment of a web page as the objective has pasted it into every future listing.

Two related properties make this a design trade-off rather than a defect. The objective is the
operator's own words, and `create_run` reuses a live run that already holds the objective
(`_reusable_row`), so a second chat asking for the same objective inherits the first chat's run.
And `list_runs` returns every run with no `chat_id` filter, so `chat_id` is a label for the event
stream and never an authority on what a chat may see or continue. That is the right shape for a
single-tenant control plane, and it should be written down rather than left implicit.

The fix is small: render the objective inside a marked data region, or leave it out of the
instruction-facing text and keep it in the tool's `metadata`.

One more consequence sits in the same place. When `create_run` reuses a live run, the reused run
keeps the `chat_id` and the `agent` of the first chat, so the event stream names the wrong
requester for a request made from a different session. The run itself is correct, and the message
the operator gets says the run was reused, so the effect is an inaccurate audit trail rather than a
wrong outcome.

### F18 (Info): the plugin's timeout may orphan the Python child on Windows

**Files:** `.opencode/plugins/product-hunter.ts:119-125` (`child.kill()`), `103-106` (the spawn).

The plugin kills the `uv` process when the registry exceeds 30 seconds. On Windows, `uv run` spawns
a Python child, and killing the parent does not necessarily take the child with it. The child is
bounded by the registry's own work, so the effect is a leaked process rather than an unbounded one.

### F19 (Info): the OpenCode CLI is resolved by searching the machine, not by a fixed path

**Files:** `src/product_hunter/llm/opencode.py:75-118` (`_nested_npm_exe`, `_cli_candidates`,
`_resolve_cli`).

The resolver prefers a real `opencode.exe`, refuses a PowerShell shim outright, and refuses a
`.cmd` or `.bat` shim for anything that carries a prompt, because cmd.exe re-parses the command
line. That is careful work. It does mean the binary that receives the extraction prompt is whichever
`opencode.exe` the machine presents first.

### F20 (Low): no content-type allowlist on the fetch path

**Files:** `src/product_hunter/fetch.py:353` (`content_type = _content_type(response)`, captured but
never gated), `src/product_hunter/raw_store.py:29-31` (any type is stored, falling back to `.bin`).

A response of any content type is accepted and written to the raw store. `blog_http` is disabled in
`config/sources.yaml`, and the enabled sources are API and feed shaped, so the exposure is limited
today. The gate belongs next to the byte ceiling, not after it.

### F21 (Medium): untrusted text crosses to a third-party service on the SubQ branch

**Files:** `src/product_hunter/llm/subq.py:325-348` (the request body and the POST),
`src/product_hunter/llm/subq.py:264` (`httpx.Client(follow_redirects=False)`),
`src/product_hunter/llm/subq.py:119-145` (the base URL check), on the `wave3/subq-integration`
branch.

The body is `{"model": ..., "state": ..., "questions": {name: payload}}`, and the question payloads
carry the text items the decision is about. The text items are the same untrusted captured text the
rest of the pipeline treats as data, and this branch sends them to `api.subq.ai`. That is the one
place in the system where untrusted content leaves the machine for a third party, and it is a
governance decision rather than a bug: the operator has to accept that the captured text of a
research run is disclosed to an external service, and the repository does not record that anywhere
in the contract document.

The credential handling on this path is otherwise careful. The key is a `SecretStr` that never
enters a log line, an exception message or a provenance record. The base URL must be `https` unless
it points at `localhost`, redirects are not followed, and the answer is validated against a strict
schema with `extra="forbid"` before it is used, and the answer can only be a label, a probability,
a score and a confidence, never a tool name, a stage or a command.

The one gap is the same one as F8. The client is built without `trust_env=False`, so `HTTPS_PROXY`
routes the request, and the request carries the API key in its headers. `HTTPS_PROXY` alone cannot
read a TLS tunnel, but a machine with a proxy set is a machine whose egress is not the operator's
own, and the base URL check permits a plain `http://` host when the configuration points at
`localhost`.

---

## 7. The adapter's eight forbidden rules

`.loop/adapter.json` lists eight rules that bind this repository. Each was checked against all nine
trees.

| # | Rule | Verdict | Evidence |
|---|---|---|---|
| 1 | No provider secret in the repo | PASS | No `sk-`, `ghp_`, `xox`, `AKIA`, `AIza` or `Bearer <token>` literal in any `src/`, `config/` or `db/` file. The `deepseek` hits are model names and routing patterns. |
| 2 | No raw HTTP to the OpenCode server | PASS | The code shells out to the documented CLI: `llm/discovery.py:41-43` names `opencode api get /api/model` and `/api/info`; `llm/opencode.py:187-192`, `251-258` build the argv. No HTTP call to the OpenCode server exists. |
| 3 | No `shell=True`, no command string | PASS | Zero hits for `shell=True`, `os.system`, `os.popen`, `subprocess.call`, `subprocess.run`, `subprocess.Popen` in any `src/` tree. The two `subprocess` call sites pass an argv list with `shell=False` (`llm/opencode.py:134-141`). The plugin passes an argv vector with `shell: false` (`product-hunter.ts:103-106`). |
| 4 | Fetch only URLs declared in `config/sources.yaml` | PASS with a caveat | `safety.admit_url` requires `host in source.domains` (`safety.py:85`) and the source is resolved from the registry (`sources.require`, `fetch.py:310-312`). The caveat is F8, the proxy path, which can reach a different address for an allowlisted host. |
| 5 | Never pass untrusted page text as a shell, tool or URL argument | PASS | No `subprocess` call site takes a scraped value. Model output is validated into strict pydantic models and none of its fields is a path, a URL or a command. F7 is about the prompt, not about an argument, and does not breach this rule. |
| 6 | Never accept evidence without a verbatim source span | PASS | `quote.py:84` (`locate_quote`), the containment rule, and the fail-closed rejection codes. The research gate that would enforce the rest has no caller (F9). |
| 7 | Never change a test to make it pass | PASS in the audited diff | The control-plane branch adds 54 cases and changes no existing assertion. The full suite is green. Not assessable across the whole history. |
| 8 | Never commit `.env`, `data/`, `runtime/` or `logs/` | PASS | `git ls-files` returns nothing under those paths in any of the nine trees. `.gitignore` covers all four. |

---

## 8. Mandatory fixes before launch

1. **Merge `wave3/opencode-control-plane` first, or rebase every other branch onto it.** F1, F1b,
   F2, F3 and the busy-retry fix all live on that one branch. Merging the other seven first carries
   the old control plane into `main`, and with it the old retry contract (F1), the missing
   live-lease guard (F1b), the corrupt-row handling (F3) and the unmasked errors (F2). This is the
   single highest-value action in this report and it is a sequencing decision, not a code change.
2. **Ship a `DISCOVERY` handler and start the worker with it.** F4b. `/research` records a request
   and enqueues a `DISCOVERY` job, and no module in the repository can execute that stage. The
   worker takes its handler map from `PH_WORKER_HANDLERS` as a `module:attribute` factory
   (`runtime/__main__.py:104-113`), and the environment variable is set nowhere in the tree. Until
   that module exists, every run an operator creates sits in the queue with no error.
3. **Give the worker a way to see a pause or a cancel.** F4. Either the worker polls
   `research_runs.status` between stages and acts on `PAUSE_REQUESTED` and `CANCEL_REQUESTED`, or the
   commands stop promising it. The current text ("the worker pauses the run when it sees the
   request") describes a worker that does not exist. A worker that checks the run status once per
   stage and before claiming the next job closes it.
4. **Strip the untrusted-region markers from captured text in both prompt builders.** F7. The helper
   exists on the `wave3/subq-integration` branch at `llm/decision.py:665-668`. Apply it at
   `extraction.py:428` and `clustering/adjudication.py:103-105`, and share one copy rather than
   three.
5. **Set `trust_env=False` on the fetch client, or clear the proxy variables before the fetch.**
   F8. One line at `cli.py:395` and at the client the scraping-ladder branch builds. Without it the
   private-IP guarantee in `safety.py` does not hold on a machine with a proxy set. The SubQ client
   wants the same line (`llm/subq.py:264`), because its request carries an API key.
6. **Call the gate, or mark the budget as advisory.** F9. Either a worker handler records tool calls
   and artifacts and a promotion path calls `require_promotable`, or the contract document stops
   implying that the budget and the promotion thresholds are enforced. As it stands the run can
   overspend its budget and be promoted with no refusal anywhere.

---

## 9. Recommended fixes, not mandatory

- **Give the plugin a per-tree id** (F6), or stop nesting a worktree inside the checkout that owns
  the same plugin. The id is one line; the collision is one session's worth of confusion.
- **Stream the fetch body with a cap** (F10) and **pin the resolved address in the transport**
  (F11). Both are recorded as `shortcut:` comments in the code already; closing them is a change to
  the shared fetch path, so it wants its own session and its own tests.
- **Pin the model revision and check a checksum** on the HuggingFace download (F13).
- **Admit the Apify actor's requests, or do not use the Apify rung** (F12). As it stands the rung
  that looks most capable is the one with the weakest boundary.
- **Refuse the Windows reserved device names** in `projection/vault.py` (F14) and **neutralise
  reference-style links** in `projection/sanitize.py` (F15). Both are small and both are in a
  component that writes to a human's vault.
- **Refuse a `RUNNING` run in `continue_run`** unless the lease is demonstrably absent for longer
  than the lease (F16). Nothing writes `RUNNING` today, so this is hardening a state the code
  declares but never reaches.
- **Mark the objective as data when it is rendered back** (F17), and decide whether `chat_id` is a
  label or an authority, on the record.
- **Record the requester on a reused run** (F17) so the event stream names the chat that actually
  asked.
- **Record the SubQ disclosure in the contract document** (F21). If the system sends captured text
  to an external decision service, the contract document says so, and the operator can see it
  before the first run rather than after one.

---

## 10. What was skipped, and what was not checked

- **Not checked:** no OpenCode server was started for this audit. The plugin was not invoked, and
  the ten commands were not exercised end to end. The V2 API claims come from the published
  documentation, the published `@opencode/schema@2.1.2` types, the OpenCode v2.1.2 source at tag
  `v2.1.2` (`packages/core/src/tool/runtime.ts`,
  `packages/core/src/session/model-request.ts`,
  `packages/core/src/session/message-updater.ts`) and the control-plane branch's own recorded live
  test. F5 was settled from the source rather than from a live server, and the source is decisive
  on it.
- **Not checked:** the plugin was never run inside the TUI. Every plugin claim is from the source
  and the documentation.
- **Not checked:** the worker was never run against a paused, a cancelled or a recovered run. The
  lease and epoch fencing was read, not observed.
- **Not checked:** no handler module ships, so the worker was never run with a real handler
  (`runtime/worker.py:135-136`). The claim in F4b is that no `DISCOVERY` handler exists in the
  repository, which is a fact about the tree; whether one exists outside the repository, in the
  deployment, is not something this audit can see.
- **Not checked:** the DuckDB, Qdrant and observability branches were read for their security
  surface only. Their analytical correctness is out of scope here and was covered by other probes.
- **Not checked:** `httpx`'s runtime behaviour with a proxy set was not exercised. The finding is
  from the default and from the absence of `trust_env` in the code.
- **Not checked:** whether the Playwright and crawl4ai rungs honour proxy environment variables.
- **Not checked:** the Apify actor's own behaviour after it receives the seed URL.
- **Not checked:** the huggingface_hub TLS configuration beyond the library default.
- **Not checked:** the safety-relevant test coverage was not assessed, only the presence of tests.
- **Skipped on purpose:** the audit did not propose patches. The brief asked for findings, and every
  fix above names the file it belongs in.
