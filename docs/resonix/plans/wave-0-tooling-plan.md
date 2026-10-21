# Wave 0 Tooling Setup — Plan

**Date:** 2026-10-21
**Definition:** `docs/resonix/specs/wave-0-tooling-spec.md`
**Source of truth:** `docs/resonix/specs/Product-Hunter-Full-Spec.md`

---

## 1. MCP matrix

Every row was probed with a real tool call in this session. "Verified" means the call returned data, not that a process started.

| Spec role (§6) | Server | Phase | Observed | Decision |
|---|---|---|---|---|
| GitHub MCP | `github` | 4, 6 | `get_me` returned the account `acrticsludge` | keep, reuse |
| Playwright MCP | `playwright` | 4, 7 at scale | `browser_tabs list` returned a tab | keep, reuse |
| documentation/context | `context7` | 4 | `resolve-library-id` returned `/duckdb/duckdb-web` | keep, reuse |
| browser inspection | `chrome-devtools` | 4 | `list_pages` returned a page | keep, reuse |
| Qdrant MCP | `qdrant` | 3 | absent | **install** |
| DuckDB MCP | `duckdb` | 3 | absent | **install** |
| Search MCP | none | 4 | OpenCode ships a native `websearch`; no MCP present | defer to Phase 4 |
| Fetch MCP | none | 4 | OpenCode ships a native `webfetch`; the worker ships `ph fetch` | defer, duplicate path |
| Apify MCP | `apify` | 7 | operator installed it by hand with OAuth after this plan was written, and it answers | installed by the operator, out of phase, unused by the worker |
| Filesystem | none | — | OpenCode ships read, write, glob, grep | not needed |
| Git | none | — | OpenCode ships shell and git | not needed |
| Memory / Time | none | — | §6 states these are not required dependencies | never install |

MCPs that respond but that this spec does not need: `cloudflare-api`, `cloudflare-docs`, `better-auth`, `design-systems`, `Framelink_Figma_MCP`, `sentry`, `neon`, `supabase`, `astro`, `astryx`, `nova-memory`. They stay configured and are recorded as available but not required.

MCPs that do not answer: `postman` returns 401 for an invalid API key, `vercel` returns 404 for the current user. `postiz` and `MCP_DOCKER` are absent from the tool catalog. None of the four is needed by this spec, so none is repaired here.

## 2. The servers in `opencode.json`

Two local `uvx` servers added by this wave, both carry no credential, and both start without a network service running. One remote server added by the operator afterwards.

The configuration lives in `opencode.json` at the repository root, and that file is the source of truth. The decisions behind it are the ones below.

- `qdrant` runs `uvx --python 3.13 mcp-server-qdrant@0.8.1` and reads three environment values: `QDRANT_URL` `http://localhost:6333`, `COLLECTION_NAME` `product_hunter_evidence`, and `EMBEDDING_MODEL` `sentence-transformers/all-MiniLM-L6-v2`.
- `duckdb` runs `uvx --python 3.13 --with "mcp<2" mcp-server-duckdb@1.1.0 --db-path db/product_hunter.duckdb`.
- `apify` is the remote server the operator added by hand, at `https://mcp.apify.com/`.
- All three carry `timeout: 120000`.

The file goes at the repository root as `opencode.json`. OpenCode merges the project config over the global one instead of replacing it, so the eighteen global servers keep working. The `mcp.servers` key shape matches the global config that is already proven to load on this machine.

The `apify` entry is the operator's, added by hand after this plan was written, and authenticated with OAuth. It answers. §38A places Apify in Phase 7, behind a measured need, and the worker owns collection either way, so the server sits on no Phase 2 or Phase 3 path.

`timeout` is 120000 rather than the 5000 default, because the first `uvx` run downloads the package and would otherwise fail the tool fetch. The Apify server publishes 233 tools that it has to fetch over the internet, so it needs the same headroom.

The version pins are there because both servers were installed and run over the real MCP protocol before this plan was accepted, and both failed without them.

- `--python 3.13` on both. The machine's default interpreter is 3.14, and `mcp-server-qdrant` pins `fastmcp==2.7.0`, which resolves to `pydantic-core==2.33.2`. That release publishes no `cp314` wheel, so uv falls back to a source build that needs Rust, and the install fails. Python 3.13 has a prebuilt wheel for every dependency. `uv` downloads and caches a managed CPython 3.13 on first use, and the project virtual environment stays on 3.14.
- `--with "mcp<2"` on the DuckDB server. `mcp-server-duckdb` declares `mcp>=1.0.0`, so the resolver installs the current SDK, 2.3.0, which removed the `Server.list_resources` attribute that the server calls at startup. The server dies before it can answer. The package's own requirement allows the 1.x line, and the 1.x SDK works.
- `@0.8.1` and `@1.1.0` on the two packages. An unpinned `uvx` resolves and executes whatever is on PyPI at launch, inside the agent's process. The hardening pass showed the drift is real, and this wave already watched it break a server. Pinning costs a manual bump when upstream ships a fix.

The DuckDB server runs read-write. A read-only DuckDB server refuses to start when the file is missing, the file is generated and git-ignored, and a fresh clone has no file, so a read-only server would fail to start and break the OpenCode startup. The file is created on the first connect and is covered by `*.duckdb` in `.gitignore`. Phase 3 provisions the file, owns its lifecycle, and switches the server to read-only.

The DuckDB path is relative to the workspace root, which is where OpenCode launches the server. The file is generated, so `.gitignore` gains `*.duckdb`. That is the one addition to the declared radius, and it is recorded in stage 1.

The Qdrant instance is not started by this wave. The server loads without one and only fails on a call that needs it. Phase 3 starts the instance and records the collection contract.

## 3. Skill plan

The registry at `skills.sh` was searched for every name in the specification's §5 list, 30 of them. Its API needs a Vercel OIDC token, so the search ran through the `skills` CLI, and each candidate's source was read on the skills.sh page before a decision.

| Priority skill | skills.sh result | Decision |
|---|---|---|
| source-discovery | matches `mattpocock/skills@research` | keep the project skill |
| pain-point-extraction | matches `first-fluke/oh-my-agent@oma-market`, 41 installs | keep the project skill |
| pain-point-normalization | no match | keep the project skill |
| evidence-validation | no match | keep the project skill |
| evidence-provenance | matches `k-dense-ai/scientific-agent-skills@scientific-writing` | keep the project skill |
| problem-clustering | no skill found | create |
| source-independence | `filipdopita-tech/mythos-skill@mythos`, 2 installs | create |
| entity-resolution | matches `mattpocock/skills@resolving-merge-conflicts` | create |
| frequency-analysis | matches `linkfox-ai/linkfox-skills@linkfox-product-title-analyze` | create |
| source-reliability | matches `mattpocock/skills@research` | create |
| severity-analysis | matches `wshaddix/dotnet-skills@dotnet-editorconfig` | defer |
| willingness-to-pay | `coreyhaines31/marketingskills@pricing`, 76.6K installs | defer, duplicate of the installed `pricing` skill |
| competitor-discovery | matches `linkfox-ai/linkfox-skills@linkfox-amazon-search` | defer |
| competitor-analysis | `coreyhaines31/marketingskills@competitor-profiling`, 91.8K installs | defer, duplicate of the installed `competitor-profiling` skill |
| counter-evidence | `b143kc47/deep-research-skill@deep-research`, 32 installs | defer |
| opportunity-scoring | `sylphxai/skills@opportunity-scoring-review`, 49 installs | defer |
| uncertainty-estimation | no match | defer |
| startup-killer | no match | defer |
| research-planner | `nkz55/research-planner`, 4 installs | defer |
| research-budgeting | matches `mattpocock/skills@research` | defer |
| research-stopping | matches `mattpocock/skills@research` | defer |
| opportunity-investigator | matches `mattpocock/skills@improve-codebase-architecture` | defer |
| opportunity-report | `froessell/app-store-opportunity-research`, 368 installs | defer |
| web-research | `firecrawl/cli@firecrawl-search`, 84K installs, official | defer, needs a key and a managed service |
| trend-detection | matches `agiprolabs/claude-trading-skills@regime-detection`, 558 installs | defer |
| temporal-analysis | matches `bmad-labs/skills@ai-multimodal`, 172 installs | defer |
| validation-experiment | matches `phuryn/pm-skills@monetization-strategy`, 3.1K installs | defer |
| mvp-designer | matches `easychen/opc-methodology@opc-mvp-designer`, 213 installs | defer |
| distribution-research | matches `mattpocock/skills@research` | defer |
| pricing-research | matches `mattpocock/skills@research` | defer |

The one official registry skill that fits is `firecrawl/cli@firecrawl-search`. It is Firecrawl's own skill for Firecrawl's search and scrape API. It needs a `FIRECRAWL_API_KEY`, and a managed search and crawl service sits at the bottom of the spec's tool-selection ladder, next to Apify and Crawl4AI. It is recorded as the named candidate for Phase 4 with its install command, and it is not installed now.

`mattpocock/skills@research` is reputable and security-audited, but it is a general instruction to send a background agent after primary sources and write notes. The loop's own gather, verify, and review nodes already do that, so it would be a near-duplicate rather than a new capability.

The five skills created in this wave are written for this repository, in the same style as the five that Phase 1 shipped. They encode the spec sections they implement, the deterministic id rules, the fail-closed posture, and the reason codes. A registry skill cannot be specific to those.

| New skill | Spec section | Phase it serves |
|---|---|---|
| `source-reliability` | §7, §12 | 2, 3 |
| `source-independence` | §12 | 3 |
| `entity-resolution` | §12, §15A | 3 |
| `problem-clustering` | §14, §15A | 3 |
| `frequency-analysis` | §16 | 3, 4 |

## 4. Files

| Path | Change |
|---|---|
| `opencode.json` | new, three MCP servers, two of them added by this wave |
| `.gitignore` | add `*.duckdb` |
| `.env.example` | add the empty `SUBQ_API_KEY=` name |
| `.opencode/skills/source-reliability/SKILL.md` | new |
| `.opencode/skills/source-independence/SKILL.md` | new |
| `.opencode/skills/entity-resolution/SKILL.md` | new |
| `.opencode/skills/problem-clustering/SKILL.md` | new |
| `.opencode/skills/frequency-analysis/SKILL.md` | new |
| `docs/TOOLING.md` | new, the audit record |
| `docs/resonix/specs/wave-0-tooling-spec.md` | new |
| `docs/resonix/plans/wave-0-tooling-plan.md` | new |
| `docs/resonix/prompts/` | new, one file per dispatch |
| `docs/resonix/reports/wave-0-tooling.md` | new, the run report |
| `docs/resonix/reports/wave-0-tooling-handoff.md` | new |

## 5. Verification

1. `uvx mcp-server-duckdb` starts, answers `tools/list`, and runs a query.
2. `uvx mcp-server-qdrant` starts and answers `tools/list`. A search call is expected to fail with a refused connection, because no Qdrant instance runs yet, and that is reported as exactly that.
3. `opencode mcp list` runs in the project and shows the global servers plus the two new ones, which proves the project config merges instead of replacing.
4. `uv run pytest` passes, which proves the application is untouched.
5. Every new skill file parses, and its name and description front matter is well formed.
6. `git diff` shows no secret value.

## 6. Commit

One commit, `chore: prepare OpenCode tooling for Product Hunter`, with the explicit paths from section 4. No push. The pre-existing untracked `mcp-debug-emergency.log` stays untracked.
