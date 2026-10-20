# Phase 1 Implementation Plan

**Status:** awaiting approval. No product code written, no dependencies installed.
**Date:** 2026-10-20
**Source of truth:** `docs/resonix/specs/Product-Hunter-Full-Spec.md` (3,240 lines)
**Phase under plan:** §38A Phase 1, "Hand-prove extraction and normalization"
**Lane:** backend / research infrastructure. No UI, so no `DESIGN.md` is required.

---

## 1. What Phase 1 has to prove

Phase 1 proves the evidence chain at small scale. The deliverable is a small,
auditable pipeline that turns a manually chosen public page into a stored
observation, an extracted pain point, and a verbatim quote that a human can check.

From §38A, in scope:

- a small manually selected source set
- raw capture and quarantine
- pain-point extraction
- quote-verbatim validation
- normalization and deterministic IDs
- basic provenance

Acceptance, from §38A:

| # | Criterion | How this plan proves it |
|---|---|---|
| A1 | at least 100 manually inspected evidence spans processed | review sheet with 100 rows and verdicts in `docs/resonix/reports/phase-1-acceptance.md` |
| A2 | quote-verbatim check passes for >=99% of accepted evidence | report splits exact, canonical, rejected, and repaired counts |
| A3 | invalid or missing source spans fail closed | rejection tests plus `rejects.jsonl`; no accepted record without a span |
| A4 | duplicate document/content hashes are deterministic | replay test produces identical IDs from identical bytes |
| A5 | every accepted pain point traces to a source span | invariant test and a report query over the output |

Nothing in Phase 1 claims clustering, independence, scoring, or market validity. Those
start in Phases 3 and 4.

---

## 2. Environment audit

Verified on this machine, not assumed.

| Item | Observed | Consequence |
|---|---|---|
| Repo | `main` at `55ab511`, clean tree, one file: the spec | greenfield scaffold |
| Remote | `github.com/acrticsludge/product-hunter.git` | push stays approval-gated |
| Python | 3.14.7 (`python`, `py`) | usable; wheel support verified below |
| uv | 0.12.9 | use `uv venv` / `uv add`; no pip bootstrap |
| Node | 24.19.0 | not needed for the worker |
| npm / bun / opencode shims | `.ps1` blocked by PowerShell execution policy | call `.cmd` shims or `cmd /c`; does not affect Python |
| Git | 2.55.0 | fine |
| Docker | 29.8.2 installed, daemon not running | not needed until Phase 3 |
| sqlite3 CLI | not on PATH | irrelevant; Python `sqlite3` is stdlib and Phase 2 uses it |
| Obsidian | not found | Phase 6 |
| Project `.env` | absent | create `.env.example`, never commit `.env` |
| `.gitignore` | absent | add before the first capture |
| CI / gates | none | no code gate exists yet |
| `.loop/adapter.json` | absent | create in the scaffold commit, once a real gate exists |

### OpenCode surface

- Project layout per current docs: `.opencode/agents/` and
  `.opencode/skills/<name>/SKILL.md`. Both are plural, confirmed at
  `https://docs.opencode.ai/docs/agents/` and `https://docs.opencode.ai/docs/skills/`.
- Skills installed globally: 192 under `~/.agents/skills`, 2 under
  `~/.config/opencode/skill` (the loop skills). No research-methodology skill exists yet.
- MCP servers configured: GitHub, Playwright, chrome-devtools, context7, and others.
  None are required on the Phase 1 worker path.
- Authenticated model providers: `deepseek`, `openrouter`, `nvidia`, `opencode`,
  `inferx`, `opencode-go`, `tokenharbor`. No project-scoped key exists.

### Security finding outside the task

The global `~/.config/opencode/opencode.jsonc` stores several credentials in plaintext
(a GitHub token, a Figma key, a Postiz bearer token, a Neon key). Values are not
reproduced here and must not be copied into this repo. Rotating and moving them to
environment variables is recommended, but it is not part of Phase 1.

---

## 3. Exact Phase 1 dependency set

### Runtime

- Python 3.14.7, standard build (not free-threaded), via `uv venv --python 3.14`.
- Windows 10/11, PowerShell. No WSL requirement.
- Git for version control.

Python 3.14 is safe for the whole spec, including later phases. Binary wheels for
`cp314-win_amd64` exist for `pydantic-core` 2.41.5, `lxml` 6.1.3, `numpy` 2.4.1,
`scipy` 1.17.0, `scikit-learn` 1.9.1, `torch` 2.14.1, `tokenizers` 0.23.3, and
`duckdb` 1.5.6. The remaining packages are pure Python. If a Phase 3 dependency
misbehaves, `uv python pin 3.13` is a one-line fallback with no code change.

### Python packages, Phase 1 only

| Package | Verified version | Official fact checked | Why |
|---|---|---|---|
| `pydantic` | 2.14.1 | requires Python >=3.10 | schema-constrained extraction records |
| `httpx` | 0.28.1 | requires Python >=3.8 | fetch, redirects, retry transport |
| `beautifulsoup4` | 4.15.0 | requires Python >=3.7 | HTML to text |
| `lxml` | 6.1.3 | cp314 Windows wheel published | parser backend |
| `PyYAML` | 6.0.3 | cp314 Windows wheel published | source registry |
| `python-dotenv` | 1.2.4 | pure-Python wheel | one `.env` loader |
| `pytest` | 9.1.1 | requires Python >=3.10 | tests |

Install, after approval:

```powershell
uv venv --python 3.14
uv add pydantic httpx beautifulsoup4 lxml pyyaml python-dotenv
uv add --dev pytest
```

Deliberately not added: `tenacity` (a 25-line retry helper is enough and testable),
`respx` (`httpx.MockTransport` is built in), `typer`/`rich` (`argparse` is stdlib),
`pydantic-settings` (plain env reads are enough), `structlog` (JSON logs via `json`),
`polars`, `orjson`, `pytest-cov`.

### External services, Phase 1

| Service | Need | Status |
|---|---|---|
| Hacker News via Algolia search API | discovery of complaint comments | live check returned 200, 38,391 hits, fields include `comment_text`, `author`, `created_at` |
| GitHub REST API | issue and discussion evidence | live check: 60 req/hr unauthenticated, 10 search req/min; 5,000 req/hr with a token |
| Public blog / RSS pages | direct HTTP evidence | manual URL list, robots checked |
| OpenCode v2 server API | pain-point extraction with no separate provider key | verified on v2.1.0: `opencode api post /api/experimental/generate` returned `{"data":{"text":"pong"}}` with `DEEPSEEK_API_KEY` unset |
| Direct OpenAI-compatible API (optional) | fallback, or Phase 2+ if a key is supplied | DeepSeek verified: base `https://api.deepseek.com`, `POST /chat/completions`, `response_format={"type":"json_object"}` |

A GitHub token is optional but strongly preferred. Without one, Phase 1 can still
reach 100 spans from HN plus blogs, so the run is not blocked.

The extraction default does not need a separate DeepSeek key. It uses the existing
OpenCode auth through the documented server API. See section 7 for the exact
mechanism and its limits.

### Not installed in Phase 1

Qdrant, Qdrant MCP, DuckDB, DuckDB MCP, sentence-transformers, torch,
scikit-learn, Apify, Crawl4AI, Playwright-at-scale, SubQ Snap, Langfuse,
Redis, BullMQ, Temporal, Docker services, Obsidian plugins. Each belongs to
Phase 3, 6, or 7. SubQ specifically requires the §38A benchmark gate before it can
enter an autonomous path, so it is excluded even though a key exists on this machine.

---

## 4. Phase 1 source set and legality registry

Three source types, chosen because their public access terms are clear and their
content is static enough to avoid a browser.

| `source_id` | Type | Collector | Terms status | Why this one |
|---|---|---|---|---|
| `hn_algolia` | community | official-ish search API | reviewed, permitted for discovery, retrieval, analysis, storage | public comments, no auth, no stated rate limit; we self-cap |
| `github_issues` | code host | REST API | reviewed, permitted for public repository data | real workflow complaints with stable URLs and authors |
| `blog_http` | publication | direct HTTP | reviewed per domain, robots checked | independent voices outside aggregators |

Blocked on purpose:

- **Reddit**: `terms_status: unknown`, `allowed_use: [none]`, `enabled: false`.
  API access and storage terms need a real review before a collector is allowed to
  touch it. This is exactly the §7 legality gate.
- App stores, review sites, and forums with unclear scraping terms: deferred.

The registry lives in `config/sources.yaml` and carries the §7 fields:
`source_id`, `source_type`, `terms_status`, `allowed_use`, `reviewed_at`,
`review_basis`, `robots_or_api_notes`, `collector`, `rate_limit_policy`, plus
`domains` for the fetch allowlist and `enabled`.

The fetcher refuses any source whose status is not `reviewed`/`permitted`, whose
`allowed_use` does not include the requested operation, or whose host is outside
the source's declared domains.

---

## 5. Pipeline and file layout

Phase 1 writes artifacts only. SQLite starts in Phase 2, as §38A says.
That keeps this phase small and avoids designing a schema before its invariants are
known.

```text
product-hunter/
  pyproject.toml
  .python-version
  .env.example
  .gitignore
  README.md
  .loop/adapter.json
  .opencode/skills/<five phase-1 skills>/SKILL.md
  config/
    sources.yaml
    extraction.yaml
  src/product_hunter/
    __init__.py
    config.py          env + paths + limits
    models.py          Pydantic records and rejection codes
    ids.py             hashing, canonicalization, stable IDs
    safety.py          URL admission, allowlist, robots, IP checks
    fetch.py           httpx client, manual redirects, retry, ETag
    extract.py         source adapters: HN JSON, GitHub JSON, blog HTML
    llm.py             extractor adapters: OpenCode generate, run fallback, optional direct API
    pipeline.py        capture -> extract -> validate -> write
    cli.py             argparse entry point
  tests/
    fixtures/
    test_ids.py
    test_safety.py
    test_quote_verbatim.py
    test_fail_closed.py
    test_schema.py
    test_fetch_retry.py
    test_pipeline_determinism.py
    test_injection.py
  data/                gitignored: raw + processed artifacts
  runtime/             gitignored: run manifests, events, rejects
  docs/resonix/
    plans/phase-1-implementation-plan.md
    reports/phase-1-acceptance.md
```

Deviation from §39: worker config lives in root `config/`, not `.opencode/config/`.
`.opencode/` is the OpenCode surface; the worker must not depend on it, per §6A.

### Run flow

```text
source registry
  -> capture: GET with allowlist + robots + retry + ETag
  -> raw store: data/raw/<source_id>/<sha256>.json|html   (immutable)
  -> text extraction: BeautifulSoup/lxml -> data/processed/<run_id>/text/<doc_id>.txt
  -> LLM extraction: one call per document, JSON schema
  -> validation: quote must exist in the document text; offsets recorded
  -> normalization: controlled fields + versioned normalized_key
  -> artifacts: documents.jsonl, evidence.jsonl, pain_points.jsonl, rejects.jsonl
  -> manifest.json + events.jsonl + usage counters
  -> report: acceptance metrics
```

`runtime/runs/<run_id>/` holds `manifest.json` and `events.jsonl`. A `run_id` is a
UTC timestamp plus a short random suffix, for example `ph-20261020T101500Z-a1b2`.
Re-running the same capture with the same raw bytes produces the same document IDs.

---

## 6. Data contracts

### Identifiers

| ID | Derivation | Stability |
|---|---|---|
| `doc_id` | `doc_` + `sha256(raw_bytes)[:16]` | same bytes, same ID, always |
| `text_sha256` | `sha256(extracted_text)` | changes if the parser changes; parser version is recorded |
| `span_id` | `ev_` + `sha256(doc_id + ":" + char_start + ":" + char_end + ":" + quote)[:16]` | same span, same ID |
| `pain_id` | `pp_` + `sha256(span_id + "|" + extractor_version)[:16]` | stable across replay; not derived from free LLM text |
| `normalized_key` | `domain:action:object` slugs, from constrained fields | versioned by `normalization_version`; used for exact grouping only |

Content hashes are SHA-256 over bytes. Text is decoded as UTF-8 with a declared
fallback, then NFC-normalized before hashing. IDs truncate to 16 hex characters for
readability; the full hash stays in the record.

`pain_id` derives from the span and extractor version, not from the model's wording.
That is what makes replay deterministic even when the model paraphrases differently.

### Records

`Document`: `doc_id`, `source_id`, `url`, `final_url`, `fetched_at`, `status`,
`content_type`, `raw_sha256`, `raw_path`, `text_sha256`, `text_path`, `etag`,
`last_modified`, `parser_version`.

`EvidenceSpan`: `span_id`, `doc_id`, `quote`, `char_start`, `char_end`,
`match_mode` (`exact` or `canonical`), `extraction_model`, `prompt_version`,
`extractor_version`.

`PainPoint`: `pain_id`, `span_id`, `doc_id`, `domain`, `actor`, `action`, `object`,
`consequence`, `severity_hint`, `normalized_key`, `normalization_version`, `quote`,
`verbatim_ok`.

`Rejection`: `doc_id` or `candidate_id`, `stage`, `code`, `detail`, `created_at`.
Codes are an enum, not prose: `missing_span`, `quote_not_found`, `schema_invalid`,
`source_blocked`, `unsafe_url`, `fetch_failed`, `empty_model_output`,
`non_text_content`.

Every record carries `run_id`, `extractor_version`, and model metadata where a model
was involved. That satisfies the §6B requirement to retain provider, model, and
version.

### Quote validation

1. The model returns a quote, not offsets.
2. The validator searches the exact document text first.
3. If exact fails, it retries against a conservative canonical form: NFKC, collapsed
   whitespace, curly quotes to straight, en/em dashes to hyphen, NBSP to space.
   Offsets are mapped back to the original text.
4. Any match that needs more than that is rejected. Paraphrase never passes.
5. `match_mode` records which path was used, so the report can separate exact from
   canonical and the 99% claim stays honest.
6. If the quote occurs more than once, the first occurrence is used unless the model
   supplied a disambiguating prefix. A wrong occurrence is still a real span in the
   document, so the provenance link stays valid.

Fail-closed rules: missing document, missing quote, quote not found, schema failure,
blocked source, unsafe URL, non-text body, or empty model output all reject. Rejects
are written, counted, and never silently dropped.

---

## 7. Model extraction contract

One narrow interface:

```python
class Extractor(Protocol):
    def extract(self, text: str, meta: DocumentMeta) -> list[RawPainPoint]: ...
```

Default implementation for Phase 1: `OpenCodeGenerateExtractor`. It calls the
OpenCode V2 server through the documented `opencode api` CLI:

```powershell
opencode api post /api/experimental/generate --data '{"prompt":"...","model":{"providerID":"opencode-go","id":"deepseek-v4.1-flash"}}'
```

The response is `{"data":{"text":"..."}}`. The call is stateless, creates no session,
and uses the OpenCode auth store, so no `DEEPSEEK_API_KEY` is needed. This was
verified live on OpenCode v2.1.0 with `DEEPSEEK_API_KEY` unset. The model registry
reports `opencode-go/deepseek-v4.1-flash` as `status=active`, `enabled=true`, with a
1,000,000-token context.

The Python worker invokes the CLI as a subprocess with an argv list, not a shell
string. Direct HTTP to the local server returns 401. `opencode api` performs the
documented discovery and authentication handshake, and there is no documented Python
client, so the CLI is the supported bridge.

Startup capability check, before any extraction:

- `opencode api get /api/info` returns the server version.
- `opencode api get /api/model` must list `opencode-go/deepseek-v4.1-flash` with
  `status=active` and `enabled=true`.
- If either check fails, the run stops with a clear error. It does not silently
  switch models.

Limits of this route, verified against the published OpenAPI schema:

- It lives under the **Experimental one-shot generation** tag. It is published and
  documented, not an undocumented hack, but it carries no stability guarantee.
- The request accepts only `prompt` and `model`. There is no `response_format`,
  `temperature`, `max_tokens`, or usage metadata. JSON shape is enforced by the
  prompt and by Pydantic validation, not by the API.
- `--data` is a command-line string. The Windows process command line is bounded, so
  send a bounded excerpt of candidate sentences with context, not a whole page.
- The response contains only text. Record the requested provider, model, and
  extractor version in every record, because the server does not echo them.

Fallback for long documents: `OpenCodeRunExtractor` uses the stable CLI:

```powershell
opencode run --model opencode-go/deepseek-v4.1-flash --agent plan --format json --file <tmp> "<instruction>"
```

`plan` denies all tools, so attached content is message data. The JSONL stream
carries the answer in `part.text`. This creates a session per call, so it is a
fallback, not the default.

Optional direct adapter: `OpenAICompatibleExtractor` using `httpx` against
`PH_LLM_BASE_URL`, with `PH_LLM_API_KEY`. It keeps the DeepSeek JSON mode and
temperature controls. It implements the same `Extractor` protocol, so switching is
one class.

Call rules for all adapters:

- One retry on empty content, malformed JSON, or timeout, with the same idempotency
  key. Count retries separately.
- Hard caps from `config/extraction.yaml`: max documents, max calls, max input
  characters, max output tokens. Phase 1 caps are 150 documents and 300 calls.
- Every call logs the adapter, requested model, prompt version, input hash, output
  hash, latency, and status to `events.jsonl`. Token counts are recorded when the
  adapter exposes them.
- The extraction call has no tools. Untrusted text cannot trigger an action.

Test seam: a `FakeExtractor` returns fixture outputs, so the pipeline tests never hit
the network or OpenCode.

---

## 8. Security boundary

Phase 1 introduces a network fetcher, so §37A applies now, not later.

- **Host allowlist.** The worker fetches only hosts declared in `sources.yaml`. A
  model cannot propose a URL, and no URL from page content is followed. This removes
  most SSRF risk without a DNS-pinning transport.
- **URL admission.** `http`/`https` only, no credentials in the URL, standard ports
  only, max 5 redirects, each redirect re-checked against the allowlist. Resolved IPs
  must be public; loopback, private, link-local, multicast, reserved, and metadata
  addresses are rejected. Residual DNS-rebinding TOCTOU is documented as a
  `shortcut:` marker and hardened in Phase 4 or 6.
- **Robots.** `urllib.robotparser` (stdlib) is checked for direct blog fetches before
  the request. API sources use their API terms instead.
- **Untrusted content.** Raw bytes stay separate. The prompt fences source text as
  data. Output is schema-validated. Injection markers such as
  "IGNORE PREVIOUS INSTRUCTIONS" are stored as evidence, never executed. A test
  fixture proves this.
- **Secrets.** `.env` is gitignored; `.env.example` contains names only. No key in a
  URL, log, artifact, or commit message. Registry YAML loads with `yaml.safe_load`.
- **Writes.** Only `data/`, `runtime/`, and the report path. No shell execution from
  parsed content. No external side effects.
- **Obsidian safety** is Phase 6; Phase 1 does not write a vault.

---

## 9. Tests and acceptance evidence

Unit and fixture tests, no network by default:

| Test | Proves |
|---|---|
| `test_ids.py` | same bytes and same span produce the same IDs; replay is stable |
| `test_safety.py` | scheme, host, private-IP, redirect, and allowlist rejection |
| `test_quote_verbatim.py` | exact match passes; whitespace and smart-quote variants pass as canonical; paraphrase fails |
| `test_fail_closed.py` | missing doc, missing quote, schema gap, and empty output all reject with a code |
| `test_schema.py` | Pydantic rejects missing or wrong-typed fields |
| `test_fetch_retry.py` | 429 and 5xx retry with backoff; 404 does not retry; `httpx.MockTransport` only |
| `test_pipeline_determinism.py` | two runs over the same fixtures produce byte-identical JSONL IDs |
| `test_injection.py` | prompt-injection text stays data and cannot alter control flow |

One opt-in integration smoke test, marked `network`, performs a single live fetch per
source. It is not part of the default gate.

Acceptance run, after the code gate is green:

1. Capture 40 HN comments, 40 GitHub issues, and 20 blog sections. Target 100 to 120
   candidate documents.
2. Extract all of them with the configured model.
3. Validate and write artifacts.
4. Produce `docs/resonix/reports/phase-1-acceptance.md` with: totals, exact vs
   canonical match counts, rejection histogram, repair rate, duplicate-hash replay
   result, and a 100-row manual inspection table.
5. Manual inspection is the real A1 gate; a generated pass rate alone does not
   satisfy it.

Report metrics are computed from the artifacts by `ph report`, not typed by hand.

---

## 10. Work breakdown

Ordered, each step ends with a check.

1. **Scaffold and gate.** `pyproject.toml`, `.python-version`, `.gitignore`,
   `.env.example`, package skeleton, pytest config, `.loop/adapter.json` declaring
   the real gate (`uv run pytest`). Gate: `uv run pytest` collects and passes zero
   tests; `uv run ph --help` works.
2. **Registry, safety, fetch.** Source registry, URL admission, robots, retry, ETag,
   content-addressed raw store. Gate: safety and retry tests pass; three live smoke
   fetches return 200 and stable hashes.
3. **Extraction and schemas.** Pydantic models, prompts, OpenCode generate adapter
   with startup capability check, `run --file` fallback, fake extractor. Gate: five
   fixtures produce valid records; malformed output rejects; the live adapter
   returns text for one short prompt.
4. **Validation, normalization, IDs.** Quote matching, canonical fallback, rejection
   codes, stable IDs. Gate: verbatim and determinism tests pass.
5. **Pipeline and CLI.** End-to-end run over fixtures, artifacts, events, manifest.
   Gate: two runs are byte-identical on IDs; report command runs.
6. **Acceptance run.** Live capture, extraction, 100 manual inspections, report.
   Gate: A1 to A5 all evidenced.
7. **Docs, skills, commit.** Five project skills, README, phase report, adapter
   check, review, explicit-path commit. Gate: review returns ship; no secrets in the
   diff.

Rough size: about 700 lines of source, 600 lines of tests and fixtures, 300 lines of
config and docs. If it grows past that, the scope has drifted.

---

## 11. Required OpenCode skills

Five project skills, authored in Phase 1, because they encode decisions this phase
must make. Each is a short methodology file with concrete rules.

| Skill | Contents |
|---|---|
| `source-discovery` | registry fields, legality gate, manual source selection, query families |
| `pain-point-extraction` | what counts as pain, quote selection rules, schema, rejection |
| `pain-point-normalization` | controlled fields, domain vocabulary, no paraphrase drift |
| `evidence-provenance` | ID and hash rules, artifact layout, replay |
| `evidence-validation` | fail-closed checklist, rejection codes, acceptance metrics |

Reused installed skills: `ponytail` for code, `test-driven-development` for tests,
`security-and-hardening` for the fetch boundary,
`observability-and-instrumentation` for structured events,
`anti-ai-slop-writing` and `stop-slop` for the README and report prose,
`documentation-and-adrs` for the phase report.

The other 25 skills in §5 are not authored now. They encode clustering, independence,
scoring, competitor, counter-evidence, and experiment methodology that Phases 3 to 6
will use. Writing them today would produce empty prompts with no test behind them.

---

## 12. MCPs

Phase 1 needs no new MCP server.

| Role | Phase 1 use | Decision |
|---|---|---|
| Search | manual source set, no search needed | do not install |
| Fetch | `httpx` in the worker | do not install |
| GitHub | optional interactive spot-checks; worker uses REST | already installed, not on the critical path |
| Playwright | no JS-heavy sources in Phase 1 | already installed, not used |
| Qdrant | Phase 3 | do not install |
| DuckDB | Phase 3 | do not install |
| Apify | Phase 7 | do not install |
| Filesystem / Git | built-in tools | no action |

This is the direct application of "do not install optional Phase 7 integrations".

---

## 13. Explicitly deferred

- **Phase 2:** SQLite schema, jobs, leases, fencing, outbox, migrations.
- **Phase 3:** embeddings, Qdrant, clustering, lineage, entity resolution, DuckDB.
- **Phase 4:** Research Contract, Tool Gate, budgets, competitor and counter-evidence
  gates, scoring.
- **Phase 5:** `product-hunter` agent, commands, custom tool bridge, `/list-research`,
  cross-chat resume.
- **Phase 6:** wider sources, Obsidian projection, daily briefs, model routing,
  scheduler.
- **Phase 7:** Apify, Crawl4AI, Playwright at scale, SubQ Snap, Langfuse, cloud
  worker, Redis/BullMQ/Temporal, two-way Obsidian.
- **Phase 8:** retention, backups, restore canaries, drift detection, disk watchdogs.

No frontend work in any phase of this plan.

---

## 14. Risks and controls

| Risk | Control |
|---|---|
| Model paraphrases instead of quoting | verbatim validation, conservative canonical form, repair retry, fail closed |
| Repair rate hides a bad prompt | report repair and rejection rates separately; stop if repair exceeds 5% |
| GitHub unauthenticated limit (60/hr) | use a token, or lean on HN and blogs; cap requests |
| HTML extraction misses JS-rendered text | Phase 1 sources are API or static; no browser |
| Python 3.14 edge case in a later phase | wheels verified; `uv python pin 3.13` fallback |
| DNS rebinding on a direct fetch | host allowlist plus IP checks; residual TOCTOU marked `shortcut:` |
| Global OpenCode config holds plaintext secrets | do not copy; project `.env` only; recommend rotation |
| Generate route is experimental and may change | adapter protocol, startup capability check, stable `run` fallback, direct API swap |
| Generate route has no JSON mode or temperature control | prompt-level schema, Pydantic validation, one repair retry; direct adapter when controls matter |
| CLI body length on Windows | send bounded excerpts; use `run --file` for long documents |
| OpenCode service unavailable | worker retries and records the failure; authoritative artifacts are not corrupted |
| `run` fallback creates a session per call | use the stateless generate route by default; `run` only for long documents |
| Phase 1 drifts into Phase 2 schema work | artifacts only; SQLite is an explicit non-goal |

---

## 15. Approval decisions

1. **Extraction transport.** Verified options:
   - **A. OpenCode generate route.** `opencode api post /api/experimental/generate`
     with `opencode-go/deepseek-v4.1-flash`. No separate key, stateless, no session.
     The route is experimental and exposes only prompt and model, so JSON shape and
     temperature are prompt-level.
   - **B. OpenCode `run --file`.** Stable CLI, no separate key, `plan` agent with all
     tools denied, handles long documents, creates a session per call.
   - **C. Direct provider key.** Stable, full JSON mode and temperature control,
     worker-owned, requires a DeepSeek or OpenRouter key.

   Recommended for Phase 1: **A**, with **C** as the Phase 2 target if the worker
   must stay independent of OpenCode. Choose A, B, or C.
2. **Source set.** HN Algolia, public GitHub issues, and selected public blogs, with
   Reddit blocked pending a terms review. Confirm or change.
3. **Storage.** Artifacts only in Phase 1, with SQLite starting in Phase 2. Confirm.
4. **Python.** Use the installed 3.14.7, with 3.13 as the documented fallback.
   Confirm.
5. **Manual inspection.** Do you want to perform the 100-row review yourself, or
   should the run pre-label every row and you spot-check a sample? Recommended:
   pre-label all, and you inspect all 100 for the A1 gate.

No install, no code, and no commit happens until these are answered.
