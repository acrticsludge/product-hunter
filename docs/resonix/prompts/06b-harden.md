# Stage 6 dispatch, follow-up 2: re-harden the Wave 0 tooling changes

**Node:** `loop-free-harden` (same session as the first hardening pass)

The first hardening pass returned four findings. Three are closed, one is a documented handoff, and one new input needs a verdict.

## What changed since the first pass

| Finding | State now |
|---|---|
| F1, `docs/TOOLING.md` missing | closed. The file now exists and records the audit, the verified calls, the deferrals and the reasons. |
| F2, DuckDB read-write against a file that now exists | resolved as a formalised provisioning rule rather than a config change. The plan and `docs/TOOLING.md` now say that the file is created on the first connect, that it is git-ignored, that a read-only server would refuse to start on a fresh clone, that Phase 3 provisions the file and owns its lifecycle, and that Phase 3 switches the server to read-only. The stale sentence claiming the file does not exist is gone. |
| F3, unpinned `uvx` package resolution | closed. `opencode.json` now pins `mcp-server-qdrant@0.8.1` and `mcp-server-duckdb@1.1.0`. Both pinned forms were run over the real MCP stdio protocol: the DuckDB server answers `SELECT 42 AS answer, version()` with `[(42, 'v1.5.6')]`, and the Qdrant server starts and lists its tools. |
| F4, Qdrant configured with no instance running | unchanged, and it fails closed. `problem-clustering` makes the Qdrant check mandatory, so a missing instance skips a cluster rather than producing a wrong number. |
| new, the `apify` entry | the operator hand-edited `opencode.json` to add a remote Apify server, authenticated with OAuth. It now answers: `search-actors` returned real Actor records and `search-apify-docs` returned documentation. The operator's edit also dropped the two package pins, which were restored. The Apify entry is byte-identical to what the operator wrote. |

## The file as it stands

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "qdrant": {
        "type": "local",
        "command": ["uvx", "--python", "3.13", "mcp-server-qdrant@0.8.1"],
        "environment": {
          "QDRANT_URL": "http://localhost:6333",
          "COLLECTION_NAME": "product_hunter_evidence",
          "EMBEDDING_MODEL": "sentence-transformers/all-MiniLM-L6-v2"
        },
        "timeout": 120000
      },
      "duckdb": {
        "type": "local",
        "command": ["uvx", "--python", "3.13", "--with", "mcp<2", "mcp-server-duckdb@1.1.0", "--db-path", "db/product_hunter.duckdb"],
        "timeout": 120000
      },
      "apify": {
        "type": "remote",
        "url": "https://mcp.apify.com/",
        "enabled": true
      }
    }
  }
}
```

## What to check

1. Confirm F1, F2 and F3 are genuinely closed. Read `docs/TOOLING.md` and the plan at `docs/resonix/plans/wave-0-tooling-plan.md` rather than trusting this brief. For F3, confirm the two pinned package versions are the ones on PyPI and that the pinned command forms still resolve, using the `uvx` command-line tool, for example `uvx mcp-server-duckdb@1.1.0 --help` and `uvx --python 3.13 mcp-server-qdrant@0.8.1 --help`.
2. Give a verdict on the new `apify` entry, because it is about to be committed to a repository:
   - The Apify server can run Actors on a paid platform. Can an agent in this repository reach it, and what does that cost? The project needs no Apify in Phase 2 or Phase 3, and the specification gates it in Phase 7.
   - The OAuth token is not in the repository. Confirm where OpenCode stores it and that nothing in the committed file carries a credential.
   - The entry has no `timeout`, unlike the two local servers. Say whether that matters for a remote server.
   - Say plainly whether this entry should be committed, committed with a `tools` or `agent` block that disables it, or left out of the commit. Give the recommendation with the reason. This is a recommendation, not a change.
3. Re-check the five skill files as a prompt surface. Confirm nothing changed since the first pass and that the same categories are still clean.
4. Re-check that `opencode.json` grants no permission, changes no model, disables no safety rule, and sets no `permissions`, `tools`, or `agent` block.
5. State the residual risks that remain open after this pass, ranked, and say which of them block the commit.

## Constraints

- You report, you do not edit and you do not fix.
- A parallel session is still writing application code into this repository under `src/`, `tests/`, `config/`, `db/migrations/`, and `docs/resonix/`. Ignore it. It is not part of this wave.
- Do not stage, commit, stash, clean, or revert anything.
- You may run `uvx` commands and read files. Anything you write goes under `C:\Users\anubh\AppData\Local\Temp\opencode\`, never in the repository.

## What to report back

Plain text: a verdict on each of F1 to F4, the verdict on the Apify entry with the recommendation and the reason, a re-statement of the categories that are clean, the ranked residual risks with the ones that block the commit called out separately, and the checks you did not run.
