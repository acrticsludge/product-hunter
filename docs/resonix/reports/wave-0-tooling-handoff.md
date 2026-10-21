# Wave 0 Tooling Setup — Handoff

**Run ID:** wave-0-tooling-2026-10-21
**Repository:** `C:\Anubhav\Web Dev Projects\product-hunter`
**Branch:** `main`
**Baseline commit:** `205a4ba` (Phase 2 step 1: authoritative SQLite evidence core)
**Declared radius:** `opencode.json`, `docs/TOOLING.md`, `docs/resonix/specs/wave-0-tooling-spec.md`, `docs/resonix/plans/wave-0-tooling-plan.md`, `docs/resonix/prompts/`, `docs/resonix/reports/wave-0-tooling.md`, `docs/resonix/reports/wave-0-tooling-handoff.md`, the five new files under `.opencode/skills/`, `.env.example`, `.gitignore`

---

## 1. What this wave changed

Tooling only. No application code, no schema, no migration, no pipeline behaviour, and no change to the source registry.

| Path | Change |
|---|---|
| `opencode.json` | new. Three project-scoped MCP servers: `qdrant` and `duckdb`, local `uvx` processes with no credential, and `apify`, the remote server the operator added by hand |
| `.gitignore` | `*.duckdb` added, so the generated analytics file stays out of the repository |
| `.env.example` | the empty name `SUBQ_API_KEY=` and a comment saying it stays empty until the Phase 7 gate |
| `.opencode/skills/source-reliability/SKILL.md` | new |
| `.opencode/skills/source-independence/SKILL.md` | new |
| `.opencode/skills/entity-resolution/SKILL.md` | new |
| `.opencode/skills/problem-clustering/SKILL.md` | new |
| `.opencode/skills/frequency-analysis/SKILL.md` | new |
| `docs/TOOLING.md` | new, the audit record |
| `docs/resonix/specs/wave-0-tooling-spec.md` | new, the definition |
| `docs/resonix/plans/wave-0-tooling-plan.md` | new, the plan and the MCP matrix |
| `docs/resonix/prompts/04-implement.md` | new, the implement dispatch |
| `docs/resonix/prompts/04b-implement-pins.md` | new, the dispatch that restored the pins and the operator's Apify entry |
| `docs/resonix/prompts/04c-implement-review-fixes.md` | new, the dispatch that added the grounding line to each skill and the Apify timeout |
| `docs/resonix/prompts/05-verify.md` | new, the verify dispatch |
| `docs/resonix/prompts/06-harden.md` | new, the first hardening dispatch |
| `docs/resonix/prompts/06b-harden.md` | new, the second hardening dispatch |
| `docs/resonix/prompts/07-review.md` | new, the review dispatch |
| `docs/resonix/reports/wave-0-tooling.md` | new, the run report |

## 2. Repository map for this run

- Entry point: `src/product_hunter/cli.py`, the `ph` command.
- Source registry: `config/sources.yaml`, loaded by `src/product_hunter/sources.py`. `require()` fails closed.
- Fetch path: `src/product_hunter/fetch.py`, with the URL gate in `src/product_hunter/safety.py`. Both are high-risk paths in `.loop/adapter.json`.
- Evidence core: `src/product_hunter/db/`, with migrations under `db/migrations/`.
- Extraction: `src/product_hunter/extraction.py`, with the rubric in `.opencode/skills/pain-point-extraction/SKILL.md`.
- Normalization: `src/product_hunter/normalization.py`, key `domain:slug(action):slug(object)` under `norm-v1`.
- Tests: `tests/`, gate command `uv run pytest`.
- Project skills: `.opencode/skills/`, five from Phase 1 and five from this wave.
- Spec: `docs/resonix/specs/Product-Hunter-Full-Spec.md`. Phase table in §38A.

## 3. MCP state, observed not assumed

Every server below answered a real call in this session unless the row says otherwise.

Working and reused, not reinstalled: `github`, `playwright`, `chrome-devtools`, `context7`, `cloudflare-api`, `cloudflare-docs`, `better-auth`, `design-systems`, `Framelink_Figma_MCP`, `sentry`, `neon`, `supabase`, `astro`, `astryx`, `nova-memory`, and `apify`, which the operator installed by hand with OAuth.

Responding but unusable as configured: `postman` returns 401 for an invalid API key, `vercel` returns 404 for the current user, and `supabase` points at the placeholder project `YOUR_PROJECT`. `postiz` is configured and fails to connect on `localhost:4007`, and `MCP_DOCKER` is configured with the Docker daemon down. None of the five is needed by this spec, so none was repaired.

Installed by this wave:

- `duckdb`, the community `mcp-server-duckdb` package, pinned to `1.1.0`. `uvx --python 3.13 --with "mcp<2" mcp-server-duckdb@1.1.0 --db-path db/product_hunter.duckdb`. The live tool returns `[(1,)]` for `SELECT 1 AS ok`. The `mcp<2` pin is required: the current SDK 2.3.0 removed `Server.list_resources` and the server crashes at startup without it. The `--python 3.13` pin is required because the SDK 1.x line pulls `pydantic-core==2.33.2`, which has no `cp314` wheel and falls back to a Rust source build.
- `qdrant`, the official `qdrant/mcp-server-qdrant` package, pinned to `0.8.1`. `uvx --python 3.13 mcp-server-qdrant@0.8.1`, with `QDRANT_URL`, `COLLECTION_NAME`, and `EMBEDDING_MODEL` in the environment. The server starts, lists `qdrant-find` and `qdrant-store`, and downloads the local `all-MiniLM-L6-v2` ONNX model. A search call returns `All connection attempts failed`, because no Qdrant instance runs yet. Phase 3 starts it.

Both appear in the live tool catalog, which is the proof that OpenCode merged the project config with the global config instead of replacing it. The exact package versions are pinned because an unpinned `uvx` resolves and executes whatever is on PyPI at launch, inside the agent's process, and this wave already watched that drift break a server.

## 4. Skill state

Already present and untouched: `source-discovery`, `pain-point-extraction`, `pain-point-normalization`, `evidence-provenance`, `evidence-validation`, all under `.opencode/skills/`, all byte-identical to `HEAD`.

Created by this wave: `source-reliability`, `source-independence`, `entity-resolution`, `problem-clustering`, `frequency-analysis`. Each carries front matter with `name` and `description` only, a description that starts with "Use when", and a closing rule that fails closed.

The `skills.sh` registry was searched for all 30 names in the specification's §5 list through the `skills` CLI, and each candidate's source page was read before the decision. The registry holds no official or reputable equivalent for this pipeline's methodology. The one official candidate, `firecrawl/cli@firecrawl-search`, needs a `FIRECRAWL_API_KEY` and a managed search and crawl service, which the spec's tool ladder puts beside Apify and Crawl4AI in Phase 7. `coreyhaines31/marketingskills@pricing` and `@competitor-profiling` are already installed globally, so they are duplicates. The rest are community skills with install counts under 400 and no provenance.

Each of the five new skills names the specification section it implements, the repository files it governs where those files exist today, and the phase that owns the implementation, so a reader cannot mistake a method for code that is already written.

## 5. Deferred, with the reason

| Deferred | Phase | Why |
|---|---|---|
| Search MCP | 4 | OpenCode's native `websearch` covers the interactive role, and the worker's own search adapter is authoritative for the pipeline |
| Fetch MCP | 4 | OpenCode's native `webfetch` and the worker's `ph fetch` already cover it |
| Crawl4AI, large-scale Playwright | 7 | §38A gates them behind a measured need |
| Apify | 7 | §38A gates it behind a measured need. The operator installed the server anyway and it answers, and the worker does not use it. |
| SubQ Snap | optional | The §38A verification gate requires a benchmark before it enters the critical path |
| Langfuse, Redis, Temporal | optional | §38 lists them as deliberately not required in the initial core |
| Obsidian | 6 | The projection lands in Phase 6 |
| `firecrawl/cli@firecrawl-search` skill | 4 | Official, but it needs a key and a managed service |
| `mattpocock/skills@research` | none | Reputable, but a near-duplicate of the loop's own gather and review nodes |

Twenty of the specification's §5 skill names are deferred to a later phase, each with its phase and its reason recorded in `docs/TOOLING.md`.

Credentials still needed, none of them requested: `SUBQ_API_KEY` for the optional Phase 7 gate, `FIRECRAWL_API_KEY` if the Phase 4 search decision goes that way, and a Qdrant instance with no credential at all. The Apify server is already authenticated through OAuth, with the token in OpenCode's own auth store and not in this repository.

## 6. Gates observed

- Code gate: `uv run pytest`, exit 0, `641 passed, 1 skipped in 5.32s`.
- `opencode.json` parses as JSON, holds the three servers with the fields the plan names, and both local servers answer a real MCP call.
- No secret value in any wave file. The only credential reference is the empty name `SUBQ_API_KEY=`.
- All five new skills parse, and the five pre-existing ones are byte-identical to `HEAD`.
- `db/product_hunter.duckdb` is git-ignored.
- The hardening pass ran twice and the review returned a ship verdict with two commit-time items, both handled in the commit step.

## 7. Known conditions a later session must know

- A parallel session is writing Phase 2, Phase 3, and Phase 4 code into this repository at the same time. The test count moved from 627 to 641 during verification. The working tree carries that work as modified and untracked files under `src/`, `tests/`, `config/`, `db/migrations/`, and `docs/`.
- That session also moved the documentation tree. It renamed `docs/resonix/` to `docs/reasonix/`, split the reports into `docs/reports/`, and added `decisions/` and `handoffs/` under the new name. The six tracked files under the old name, including `docs/resonix/specs/Product-Hunter-Full-Spec.md`, show as deleted in the working tree, and that deletion is uncommitted. This wave's files were moved back to the old canonical paths so that this commit stands on its own, and this commit stages no deletion and no file belonging to that session. The rename is theirs to commit or to undo.
- The `.gitignore` in the working tree also carries a change that is not this wave's, from `runtime/` to `/runtime/`. That narrows the ignore rule and exposes `src/product_hunter/runtime/` to a future `git add -A`, against the rule in `.loop/adapter.json` that forbids committing `runtime/`. This wave staged only its own `*.duckdb` line and left the other hunk for the session that wrote it. That hunk deserves a second look.
- The DuckDB MCP runs read-write. A read-only server refuses to start when the file is missing, and a server that fails to start breaks the OpenCode startup. Phase 3 switches it to read-only once the worker owns the file.
- The Qdrant MCP needs an instance at `http://localhost:6333` with a collection named `product_hunter_evidence`. Neither exists yet.
- The Apify MCP is enabled in this repository's `opencode.json` and can run Actors on the operator's paid account. The token is in OpenCode's auth store, not in the repository. The review ruled to commit it as the operator wrote it, and `docs/TOOLING.md` records the two ways to close the exposure.
- `uv` caches a managed CPython 3.13 for these two tools. The project virtual environment stays on 3.14, and `.python-version` is unchanged.
- `mcp-debug-emergency.log` at the repository root is a pre-existing untracked nova-memory debug log. It was left alone, and it is not ignored.
- The machine's global `~/.config/opencode/opencode.jsonc` holds several credentials in plaintext. That file is outside this repository and was not touched. The Phase 1 plan already recommends moving those values to environment variables.

## 8. What this wave did not do

It did not start a Qdrant instance, install a Qdrant or DuckDB client into the project, change the source registry, add a source, fetch anything, or touch the research pipeline. It did not implement Phase 2, and it did not push.
