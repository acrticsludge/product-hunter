# Stage 4 dispatch, follow-up 3: review fixes to the skills and the Apify entry

**Node:** `loop-free-implement-1` (same session as follow-ups 1 and 2)
**Radius (only these paths may be written):**

```
.opencode/skills/source-reliability/SKILL.md
.opencode/skills/source-independence/SKILL.md
.opencode/skills/entity-resolution/SKILL.md
.opencode/skills/problem-clustering/SKILL.md
.opencode/skills/frequency-analysis/SKILL.md
opencode.json
```

---

## Why this round exists

The review of this wave raised two findings against the five new skills and one against the Apify entry. Everything else in the wave was clean.

## 1. Each skill must say what it is grounded in, and when it applies

The five skills describe Phase 2 and Phase 3 behaviour. Phase 3 code does not exist yet, and the Phase 1 skills are the opposite: every claim in them is checkable against code in the repository today, for example the exact id formula in `evidence-provenance` or the exact reason code `raw_hash_mismatch`.

Right now one of the five names a repository file, four name none, and none states which phase owns the implementation. A reader in a pre-Phase-3 session could take "the analytics layer computes it" for a fact about code that is not written.

Add exactly one line to each of the five files, directly under the opening paragraph and before the first `##` heading. The line states three things:

1. the specification section the skill implements
2. the repository files the skill governs, where such files exist today
3. the phase that owns the implementation, in a way that says the method applies once that phase lands

The house style holds. The line is a single sentence, or at most two short ones. No filler, no cheerleading, no second person, no em dash, and no banned phrase from the writing rules.

Use these grounds. Verify each against the repository before you write it, and do not cite a file that does not exist:

| Skill | Specification section | Repository files that exist today | Phase that owns the implementation |
|---|---|---|---|
| `source-reliability` | §7, and the source half of §12 | `config/sources.yaml`, `src/product_hunter/sources.py` | 2 |
| `source-independence` | §12 | `src/product_hunter/db/`, the `authors`, `communities` and `propagation_edges` tables in `db/migrations/0001_initial.sql` | 3 |
| `entity-resolution` | §12, and the identity half of §15A | the `author_links` table in `db/migrations/0001_initial.sql` | 3 |
| `problem-clustering` | §14, and the Qdrant half of §15A | `config/sources.yaml` for the source rules, and the Qdrant collection named in `opencode.json` | 3 |
| `frequency-analysis` | §16 | the DuckDB path in `opencode.json` | 3, with the counting in 4 |

Two constraints on the wording:

- A skill that governs a rule the code already enforces today should say so, so a reader knows the rule is live. A skill whose subject is a later phase should say the method applies once that phase lands, so a reader does not believe the implementation exists.
- `db/migrations/0001_initial.sql` has `authors`, `author_links`, `propagation_edges` and `communities` tables, but the `authors` table carries no identity confidence, no hashed identity key, no first and last seen, and no deletion status. Do not claim those fields exist. If a skill needs them, say the phase that adds them.

Change nothing else in the five files. No heading changes, no table changes, no reordering, no rewording of the existing text.

## 2. The Apify entry needs a timeout

The `apify` entry in `opencode.json` has no `timeout`. The two local servers carry `timeout: 120000`. The OpenCode MCP documentation lists `timeout` as an option for both local and remote servers, in milliseconds for fetching tools, with a default of 5000. The Apify server publishes 233 tools with long descriptions, and it has to fetch that list over the internet, so a five second budget is the flakiest line in the file.

Add `"timeout": 120000` to the `apify` entry. Keep `"type": "remote"`, the url, and `"enabled": true` exactly as they are. The operator's decision to enable the server is not being changed.

The final file must be exactly:

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
        "enabled": true,
        "timeout": 120000
      }
    }
  }
}
```

## Constraints

- Do not write any other file. Do not touch `docs/`, `.env.example`, `.gitignore`, `src/`, `db/`, `config/`, or `tests/`.
- A parallel session is working in this repository right now. Ignore its work.
- Do not run the servers, start any container, or install anything.

## What to report back

- the exact final text of `opencode.json`
- confirmation that it parses as JSON
- for each of the five skills, the exact line you added, and confirmation that nothing else in the file changed
- anything you found while verifying the grounds in the table, with the reason, without fixing it and without widening the radius
