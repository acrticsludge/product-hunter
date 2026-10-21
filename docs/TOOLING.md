# Product Hunter tooling

**Date:** 2026-10-21
**Scope:** the OpenCode environment for this repository. MCP servers, agent skills, and the credentials a later phase will need.

The rule for this file: a server is called working only when a real call answered in this repository. Anything else is written down as what it is.

---

## 1. How the environment is wired

MCP servers live in two places.

- The machine's global config, `~/.config/opencode/opencode.jsonc`, holds the servers this repository shares with everything else. That file is not in this repository and this project never changes it.
- This repository's `opencode.json` holds the servers that belong to Product Hunter. OpenCode merges the project config over the global one instead of replacing it, so both sets are loaded.

The merge is verified, not assumed. Both project servers appear in the live tool catalog alongside the global ones, and `opencode mcp list` shows every server with its status.

MCP servers are an interactive surface. The worker in `src/product_hunter/` owns authoritative collection, storage, and analysis, and it does not depend on any of these servers to run. Specification §6 says this, and the worker follows it.

## 2. Servers verified in this repository

Each row was proved by a call that returned real data.

| Server | Kind | What was called | Result |
|---|---|---|---|
| `github` | remote | `get_me` | returned the account `acrticsludge` |
| `playwright` | local | `browser_tabs list` | returned a tab |
| `chrome-devtools` | local | `list_pages` | returned a page |
| `context7` | remote | `resolve-library-id` | returned the library id `/duckdb/duckdb-web` |
| `cloudflare-api` | remote | `get_user` | returned the account record |
| `cloudflare-docs` | remote | `migrate_pages_to_workers_guide` | returned the guide |
| `better-auth` | remote | `search_docs` | returned eight documentation results |
| `design-systems` | local | `get_site_colors` | returned a colour set |
| `sentry` | remote | `search_sentry_tools` | returned the tool catalogue |
| `neon` | local | `list_projects` | returned the org and its projects |
| `Framelink_Figma_MCP` | local | `get_figma_data` | reached the Figma API and returned 404 for a non-existent file, which proves the server and its token work |
| `apify` | remote | `search-actors`, `search-apify-docs` | returned real Actors and documentation, authenticated through OAuth |
| `astro` | remote | `search_astro_docs` | returned the content collections guide |
| `astryx` | remote | `search` | returned the Button component entry |
| `nova-memory` | local | `memory_stats` | returned 13 stored memories, newest 2026-10-20 |
| `supabase` | local | `list_tables` | the server answered and rejected the project reference, because the global config points it at the placeholder `YOUR_PROJECT` |

## 3. Servers this repository added

Two, both for §38A Phase 3, both local processes, neither carrying a credential.

### `qdrant`

The official Qdrant server, `qdrant/mcp-server-qdrant`, pinned to release `0.8.1`.

```text
uvx --python 3.13 mcp-server-qdrant@0.8.1
QDRANT_URL=http://localhost:6333
COLLECTION_NAME=product_hunter_evidence
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
```

The server starts, lists the `qdrant-find` and `qdrant-store` tools, and downloads the local `all-MiniLM-L6-v2` model on first use. §14 asks for local embeddings with `sentence-transformers`, and `all-MiniLM-L6-v2` is the sentence-transformers model this server ships with, so the environment block names the package default explicitly rather than choosing a different one.

A search call returns `All connection attempts failed`, because no Qdrant instance runs yet. Port 6333 was checked and nothing is listening. Phase 3 starts the instance and creates the collection.

### `duckdb`

The community `mcp-server-duckdb` package, pinned to release `1.1.0`.

```text
uvx --python 3.13 --with "mcp<2" mcp-server-duckdb@1.1.0 --db-path db/product_hunter.duckdb
```

The live tool answers `SELECT 1 AS ok` with `[(1,)]` and `SELECT 42 AS answer, version()` with `[(42, 'v1.5.6')]`.

Two version pins are required, and both exist because the unpinned form was tried first and failed.

- `--python 3.13`. The machine's default interpreter is 3.14. `mcp-server-qdrant` pins `fastmcp==2.7.0`, which resolves to `pydantic-core==2.33.2`. That release publishes no `cp314` wheel, so uv falls back to a source build that needs Rust, and the install fails. Python 3.13 has a prebuilt wheel for every dependency. `uv` caches a managed CPython 3.13 for this. The project virtual environment stays on 3.14 and `.python-version` is unchanged.
- `--with "mcp<2"`. `mcp-server-duckdb` declares `mcp>=1.0.0`, so the resolver installs SDK 2.3.0, which removed the `Server.list_resources` attribute the server calls at startup. The server dies before it answers. The package's own requirement allows the 1.x line, and the 1.x SDK works.

The exact package versions are pinned as well. An unpinned `uvx` resolves and executes whatever is on PyPI at launch, inside the agent's process, and this wave already watched version drift break a server. Pinning costs a manual bump when upstream ships a fix.

The DuckDB file is generated, so `.gitignore` carries `*.duckdb`. The server runs read-write, because a read-only DuckDB server refuses to start when the file is missing, and a server that fails to start breaks the OpenCode startup. A fresh clone has no file. Phase 3 provisions the file, owns its lifecycle, and switches the server to read-only.

## 4. Servers that do not answer, and servers this project does not need

| Server | State | Why it is not fixed here |
|---|---|---|
| `postman` | responds with 401, invalid API key | no API collection work in this project |
| `vercel` | responds with 404 for the current user | the project deploys nowhere, and the source of truth is SQLite |
| `postiz` | configured as a remote server and fails to connect, `ConnectionRefused` on `localhost:4007` | social posting, no use in this project |
| `MCP_DOCKER` | configured as a local server, and the Docker daemon is not running, so the gateway never starts | the project needs no container in its core |

The specification does not name any of these four as a requirement, so none was repaired. Repairing an unrelated server would be time this project does not have.

## 5. Servers that answer but that this project does not need

`astro`, `astryx`, `cloudflare-api`, `cloudflare-docs`, `better-auth`, `design-systems`, `Framelink_Figma_MCP`, `neon`, `nova-memory`, `sentry`, `supabase`. They stay configured in the global file. The specification's source of truth is SQLite, the project has no user interface, and no part of the pipeline authenticates against a hosted service.

`apify` is the exception worth naming. The operator installed it by hand, with OAuth, and it answers. §38A places Apify in Phase 7, behind a measured need, and the worker still owns collection. The server is available when a Phase 7 benchmark justifies it, and it is not on any Phase 2 or Phase 3 path today.

One risk comes with it. The Apify server runs Actors on a paid platform against the account that authorised it, and the entry is `enabled: true`, so any agent working in this repository can reach it and spend that account's credits. The token itself is not in the repository; OpenCode keeps it in its own local auth store, so a clone without that authorisation cannot use the server. If the spend exposure is not wanted, two moves close it: set the entry to `"enabled": false` and leave it as a record, or move the entry out of this repository's `opencode.json` and into the machine's global config, where the operator's other machine-level servers already live. The choice belongs to the operator, because it is their account and their call.

The second cost is context. OpenCode's own documentation warns that MCP servers add their tools to every session, and the Apify server alone publishes 233 tools with long descriptions. That is the price of the entry, paid in every session in this repository.

## 6. The capabilities the specification names, and where each one lives

| Specification §6 role | Where it is today |
|---|---|
| Search | OpenCode's native `websearch` tool, plus the worker's own search adapter. No Search MCP is installed. |
| Fetch | OpenCode's native `webfetch` tool, plus the worker's `ph fetch` command. No Fetch MCP is installed. |
| GitHub | the `github` server, verified |
| Playwright | the `playwright` server, verified |
| Qdrant | the `qdrant` server, added by this wave |
| DuckDB | the `duckdb` server, added by this wave |
| Filesystem | OpenCode's native read, write, glob, and grep tools |
| Git | OpenCode's shell and git |
| Memory and Time | the specification states these are not required dependencies. Persistent state lives in SQLite, and timestamps come from the worker clock. |

## 7. Skills

### Already in this repository, from Phase 1, unchanged

`source-discovery`, `pain-point-extraction`, `pain-point-normalization`, `evidence-provenance`, `evidence-validation`. All five are byte-identical to their committed state and none was touched by this wave.

### Added by this wave

Five skills, one file each, under `.opencode/skills/`. They serve §38A Phase 2 and Phase 3, the phases that come next.

| Skill | Specification section | What it decides |
|---|---|---|
| `source-reliability` | §7, §12 | how much weight a source can carry, and when a tier needs a named basis |
| `source-independence` | §12 | how many people and communities stand behind a claim, and why the count is computed |
| `entity-resolution` | §12 | when two accounts are the same person, and when a link is only a suspect edge |
| `problem-clustering` | §14 | when pain points describe one problem, and how a merge or a split is tested |
| `frequency-analysis` | §16 | when a count, a rate, or a trend may be reported, and what travels with it |

Each carries front matter with `name` and `description` only, a description that starts with "Use when", and a closing rule that fails closed. Each names the specification section it implements, the repository files it governs where those files exist today, and the phase that owns the implementation, so a reader cannot mistake a method for code that is already written. They were written in the same style as the five that Phase 1 shipped.

### The registry was searched first

Every name in the specification's §5 skill list, 30 of them, was looked up on `skills.sh` through the `skills` command-line tool, and each candidate's source page was read before a decision.

The registry holds no official or reputable equivalent for this pipeline's methodology. The findings:

- `firecrawl/cli@firecrawl-search` is the one official candidate, 84K installs, from Firecrawl's own repository. It needs a `FIRECRAWL_API_KEY` and a managed search and crawl service, which the specification's tool-selection ladder places beside Apify and Crawl4AI in Phase 7. Deferred with the install command recorded.
- `coreyhaines31/marketingskills@pricing` and `coreyhaines31/marketingskills@competitor-profiling` are already installed globally on this machine. Installing them again would be a duplicate.
- `mattpocock/skills@research` is reputable and passes three security audits. It tells an agent to research a question against primary sources and write notes, which is what the loop's own gather, verify, and review nodes already do. Not needed.
- Everything else with a matching name is a community skill with an install count under 400, no provenance, and a subject that does not match, such as a dotnet editorconfig rule returned for `severity-analysis`.

### Deferred to a later phase

| Skill | Phase | Reason |
|---|---|---|
| `web-research` | 4 | needs a search backend, which is a Phase 4 decision |
| `severity-analysis` | 4 | the severity rubric already lives in extraction and in evidence validation. Aggregation across a cluster is scoring work, which is Phase 4. |
| `willingness-to-pay` | 4 | the global `pricing` skill is a marketing duplicate, and the specification's willingness-to-pay tiers are Phase 4 scoring |
| `competitor-discovery` | 4 | competitor and alternative search is a Phase 4 research contract stage |
| `competitor-analysis` | 4 | the global `competitor-profiling` skill is a marketing duplicate |
| `counter-evidence` | 4 | the falsification stage is a Phase 4 research contract stage |
| `opportunity-scoring` | 4 | scoring and confidence are Phase 4 |
| `uncertainty-estimation` | 4 | it feeds the confidence model, which is Phase 4 |
| `startup-killer` | 4 | §4 names it as an agent, and it belongs with the Phase 4 promotion gate |
| `research-planner`, `research-budgeting`, `research-stopping` | 4 | §6A's research contract is a Phase 4 deliverable, and the budgets belong with it |
| `opportunity-investigator` | 4, 5 | deep investigation belongs with the Phase 4 contract and the Phase 5 interface |
| `opportunity-report` | 4, 6 | the opportunity report is tied to the Obsidian projection in Phase 6 |
| `trend-detection` | 3, 4 | it reads a time series of clusters, which is the DuckDB analytics work in Phase 3 and the trend questions in Phase 4. The registry holds no equivalent, only a trading regime detector with 558 installs. |
| `temporal-analysis` | 3, 4 | the same analytics surface as `frequency-analysis`, for movement over time. No registry equivalent, only a multimodal assistant skill with 172 installs. |
| `validation-experiment` | 4, 6 | §26 is a Phase 4 research gate output and a Phase 6 projection. No registry equivalent, only a monetisation strategy skill. |
| `mvp-designer` | 6 | it belongs with the Phase 6 projections and the §38 validation path. No registry equivalent, only a community method skill with 213 installs. |
| `distribution-research` | 6 | it belongs with the Phase 6 projections and the Obsidian output. No registry equivalent. |
| `pricing-research` | 4, 6 | it feeds the §20 willingness-to-pay tiers, which are Phase 4 scoring, and the pricing work in Phase 6. The global `pricing` skill is a marketing duplicate. |

## 8. Credentials still needed

None of these was requested, and none is written anywhere in this repository.

| Name | Needed by | Effect while missing |
|---|---|---|
| `SUBQ_API_KEY` | the optional SubQ Snap verification gate in §38A | nothing. The pipeline never reads it, and the empty name sits in `.env.example` so the file keeps its names-only rule. |
| `FIRECRAWL_API_KEY` | only if the Phase 4 search decision goes that way | nothing. The skill is not installed. |
| an Apify token | only if a Phase 7 benchmark justifies Apify | nothing. The operator has already authenticated the Apify server through OAuth, and the worker does not use it. |

The machine's global `~/.config/opencode/opencode.jsonc` stores several credentials in plaintext. That file is outside this repository. The Phase 1 plan already recommends moving those values into environment variables, and that work is still open.

## 9. What this wave did not do

It did not start a Qdrant instance, install a Qdrant or DuckDB client into the project, add a source, fetch anything, or touch the research pipeline. It did not change the database, the migrations, or the source registry. It did not push.

The `mcp-debug-emergency.log` file at the repository root is a pre-existing untracked nova-memory trace. It was left alone.
