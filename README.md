# Product Hunter

Product Hunter turns a manually chosen public page into a stored observation: it
captures the raw bytes, extracts a pain point with a language model, and checks
that the pain point quotes the source verbatim. Phase 1 proves that chain at
small scale. Clustering, scoring, and market validity wait for later phases.

## Commands

- `ph --help` lists the subcommands.
- `ph version` prints the package version, currently 0.1.0.
- `ph doctor` checks the configured provider and model, then prints JSON with
  an `ok` field plus the server details.
- `ph sources` lists the configured source registry as JSON.
- `ph fetch <source_id> <url>` fetches one admitted URL and prints its provenance record.
- `ph extract [--limit N] [--source SOURCE_ID] [--run-id ID]` extracts
  pain-point candidates from the stored fetches and writes one run directory.
- `ph review-sheet --run RUN_ID --out PATH [--limit 100]` writes the review
  sheet of one run.
- `ph eval --labels PATH [--out PATH] [--quote-failures N]` scores a filled
  review sheet and prints the report as JSON.
- `ph report --run RUN_ID [--out PATH]` reports one run from its own artifacts.
- `ph db init|status|import-run|recover|jobs|outbox|check` operates the
  authoritative SQLite store. `init` creates and migrates it, `status` shows
  the schema version and the row counts, `import-run` loads one processed
  extraction run, `recover` requeues expired job and projection leases, `jobs`
  and `outbox` list the queue and the projection tasks as JSON, and `check`
  runs the SQLite integrity and foreign-key checks.
- `ph models [--refresh] [--role ROLE]` lists the model catalogue OpenCode
  offers as JSON. `--refresh` re-discovers it first, and `--role` prints the
  resolved chain for one pipeline role instead of the whole catalogue.
- `ph bench run --cases PATH --spec PATH --out PATH [--thresholds PATH]` scores
  one model's answers against a labelled dataset.
- `ph bench compare --baseline PATH --candidate PATH --out PATH` compares two
  results and reports the regressions.
- `ph bench report --run PATH --out PATH [--compare PATH]` writes the
  benchmark report of one run.
- `ph bench labels --cases PATH [--out PATH]` prints how much of a dataset a
  human has labelled.
- `ph research <action> [ID] [--objective TEXT] [--chat-id ID] [--limit N]`
  controls the research runs. The actions are `create`, `list`, `status`,
  `continue`, `resume`, `pause`, `retry`, `cancel`, and `log`. It prints one
  JSON object and exits 1 on a refusal. `PH_DB_PATH` moves the database.

## Fetch safety

`config/sources.yaml` carries the terms status, allowed uses, domains, and rate
limits for each source. The registry fails closed for unknown, disabled, or
unreviewed sources; `blog_http` stays disabled until specific blogs are
reviewed, and Reddit is absent while its terms are unknown. The shared fetch
path admits only allowlisted hosts and public IPs, revalidates every redirect,
blocks HTTPS-to-HTTP downgrades, applies robots where required, enforces
per-source rate limits, retries only bounded transient failures, and stores raw
bodies by SHA-256 under `data/raw/`. Every fetch writes a provenance record;
every blocked or failed fetch writes a rejection record.

## Extraction

Pain point extraction uses the OpenCode V2 one-shot route through the `opencode
api` CLI. The call is stateless, it reads the OpenCode auth store, and this
repo holds no provider key. That route is a Phase 1 bridge behind the
`TextGenerator` protocol. Phase 2 added a model router, and the router now
drives extraction: `ph extract` resolves the `extraction` role against the live
model catalogue, binds the selected model to the provider, and records the
selection, the catalogue version, and the fallback index in the run manifest.
A router that cannot answer falls back to the model pinned in
`config/extraction.yaml`, and the manifest says which one ran.

No model is named in the business logic. `config/model_routing.yaml` names a
chain of patterns per role, the catalogue is discovered from OpenCode at
runtime, and the chain falls through when a call fails.

Under strict extraction the model proposes candidates; the code decides which
ones are real. A candidate is stored only when its quote is an exact span inside
the chunk the model was shown, and that span is traceable to the document,
fetch, and raw artifact. Everything else becomes a rejection with a stable
reason code in `rejects.jsonl`.

Pain validity has three values, and the schema holds the model to them.
Severity runs from 0 to 5: 0 is none, 2 is an occasional real cost, 5 is daily
hours, revenue loss, or legal risk.

- `genuine` needs a named actor, a named workflow, a concrete cost, severity 2
  to 5, and a reason that carries a cost.
- `weak` needs severity 1 or 2.
- `not_pain` needs severity 0.

Each run writes `documents.jsonl`, `candidates.jsonl`, `pain_points.jsonl`,
`calls.jsonl`, `rejects.jsonl`, and `manifest.json` under
`data/processed/<run_id>/`. The manifest holds the provider, the model, the
extractor and prompt versions, the limits, the counts, and the source ids, and
the ids are deterministic: the same span and the same extractor version always
produce the same pain id, so a replay reproduces them.

## Normalization

A genuine pain point carries five controlled fields: `domain`, `actor`, `action`,
`object`, and `consequence`. The domain is one of 15 fixed values: software,
data, operations, finance, sales, marketing, support, hr, legal, security,
research, education, healthcare, ecommerce, other. The other four are short
phrases, not sentences.

`slugify` lowercases a phrase, drops accents, and caps a slug at 48
characters. The key is `domain:slug(action):slug(object)`, and it travels with
`normalization_version` (`norm-v1`), so a changed slug rule changes the version
and an old key is never read as a new one. The key groups exact wording only,
and `pain_id` stays span-based, so a paraphrase never forks a record. Two long
phrases that share their first 48 slug characters collide, so the key is a
grouping, not a unique identifier.

## Manual evaluation

`ph review-sheet` writes a CSV of one run, the model's verdict on the left
and empty human columns on the right. A human fills `human_valid_pain` (yes, no,
weak), a severity, and a frequency. `ph eval` reads the filled sheet and
reports quote fidelity, precision, recall, severity agreement, and the
false-positive reasons. Recall counts only the candidates the model returned, so
a pain point the model never proposed is not a miss. The sheet also carries
`model_domain`, `model_normalized_key`, `human_normalization_ok`, and
`human_normalized_key`, so the report shows a normalization rate and a key
agreement rate. No quality number is claimed without those labels.

## OpenCode control surface

`.opencode/plugins/product-hunter.ts` registers one OpenCode tool named
`product_hunter`. It takes an `action` (`create`, `list`, `status`, `continue`,
`resume`, `pause`, `retry`, `cancel`, `log`), an optional `research_id`, an
optional `objective`, and an optional `limit`. The plugin runs the state layer
as a subprocess with an argument vector, never a shell string, and returns the
text the state layer wrote.

Ten command files under `.opencode/commands/` use that tool:

| Command | Action |
| --- | --- |
| `/research` | `create` |
| `/list-research` | `list` |
| `/research-status` | `status` |
| `/continue-research` | `continue` |
| `/continue` | `continue` |
| `/pause-research` | `pause` |
| `/resume-research` | `resume` |
| `/retry-research` | `retry` |
| `/cancel-research` | `cancel` |
| `/research-log` | `log` |

`/continue` without a research id continues the one eligible run. When more
than one run is eligible it returns the candidates and asks, through the
`question` tool, which one to continue. It never guesses.

The interface owns the request, the durable state, and the wording. The worker
owns the execution. A run with no live worker lease says `worker: not running`,
so the surface never implies that work is in flight when the database does not
say so.

## Benchmark

The manual harness scores one sheet against one model. The benchmark scores a
versioned set of cases, so two models, two providers, or two prompt versions
can be compared over the same ground and the difference reported. The
infrastructure is `src/product_hunter/eval/`, the data is under `eval/`, and
the formats are in `eval/README.md`.

A case pairs an input with a human label. **A quality number exists only where
a human label exists.** A case with no label still runs, but the metrics that
would need one stay `null`, the task is reported as blocked, and the run does
not pass or fail on it. `quote_fidelity` is the one family that needs no
label, because it is checked against the source text.

The seed dataset `eval/datasets/ds-v1.json` holds real inputs taken from the
pages this repository has captured, and `null` in every `expected` block. The
labels are a human job, and no code writes one. `ph bench labels` prints the
count per task, and the A1 gate stays closed until somebody fills them in: the
benchmark can measure quote fidelity today and nothing else.

A result holds no timestamp and no random value, so the same dataset and the
same answers produce byte-identical JSON and a result file can be committed
and diffed. `eval/thresholds.yaml` holds the starting thresholds. They are
starting values, not measured facts, and the report says the numbers behind
most of them are blocked.

## Worker runtime

`src/product_hunter/runtime/` is the supervised worker that consumes the SQLite
job queue. It claims a job, holds a fenced lease while the handler runs,
renews that lease on a background thread, records the outcome, retries a
failure until the attempt ceiling, recovers leases that expired, and stops on a
signal without abandoning the job it is running.

SQLite is the only queue and the only authority. Every write carries the
worker id and the lease epoch, so a worker that lost its lease cannot mark a
job done, and that job is recovered as work instead. The worker writes one
JSON object per line to stderr, so a run can be reconstructed from the event
stream together with the `stage_attempts` rows.

`python -m product_hunter.runtime` starts one worker. It takes the single
instance lock first, so a second scheduler invocation cannot start an
overlapping worker. `PH_WORKER_HANDLERS` names the handler factory as
`module:attribute`, `PH_WORKER_LOCK` names the lock file, and `PH_DB_PATH`
names the database. Without a handler factory the worker starts idle and
claims nothing.

No handler for the real stages is registered yet, so a worker started today
recovers expired leases and reports that it has no handlers.

## Scheduler

Phase 2 adds a scheduler above the SQLite job queue. It decides what work
exists, in what order, and when a failed attempt runs again. It creates jobs;
it never runs them. The worker claims and runs what the scheduler writes.

A schedule is a recurring definition: a name, a stage, an interval, a
priority band, and optionally a source and a research run. The priorities are
the ones the spec names, P0 for a user continuation down to P4 for
maintenance, and the queue serves the larger number first.

Occurrence times come from a fixed UTC grid, so nothing about a schedule's
state is stored anywhere. The occurrence is the identity of the work: the job
carries the key `schedule:<name>:<occurrence>`, and the queue's own
idempotency check turns a repeated occurrence into a no-op. A scheduler that
restarts computes the same occurrences from the clock and creates nothing
twice.

One tick walks the enabled schedules, applies the source gate, and writes the
due occurrences. A schedule that names a source creates no work when that
source is unknown, disabled, or whose terms are not reviewed or permitted. The
tick reports what it did, including the occurrences it dropped and the ones
that conflicted with a stored job, and it runs in one transaction so a tick
that fails leaves the queue as it was.

The cadence the spec asks for, 15 minute collection, hourly processing, 6
hour re-clustering, nightly investigation and a morning brief, is expressed
as data in `default_schedules()`. Nothing starts it yet, because no stage
handler implements those stages and a running scheduler with nothing to
execute would only spin.

## Project skills

Ten skills under `.opencode/skills/`: `entity-resolution`,
`evidence-provenance`, `evidence-validation`, `frequency-analysis`,
`pain-point-extraction`, `pain-point-normalization`, `problem-clustering`,
`source-discovery`, `source-independence`, and `source-reliability`.

## Secrets

`.env` is gitignored and never committed. `.env.example` holds names only.
