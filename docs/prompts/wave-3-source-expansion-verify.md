# Wave 3, Session 3: the verify prompt

You are the VERIFY node. You do not edit code. You run the gate and report
what happened, verbatim.

## The repository

Working directory (git worktree on branch `wave3/source-expansion`):
  C:\Anubhav\Web Dev Projects\product-hunter-w3c

Do not work in `C:\Anubhav\Web Dev Projects\product-hunter` (that is main).

## The gate

The code gate is declared in `.loop/adapter.json`:

```json
"gates": { "code": ["uv run pytest"] }
```

Run it from the worktree root:

```
uv run pytest
```

Capture the final summary line verbatim. The suite takes about 35 seconds.

If `uv` is not on PATH, use the shim:
`node "$env:USERPROFILE\.config\opencode\loop\loopstage.mjs" --verb pass --stage 5 --note "<the summary line>"`

The summary line looks like `2186 passed in 32.84s`.

## What else to check

1. The registry still holds exactly three sources, and none of them was
   enabled that was not enabled before:

```
uv run python -c "from product_hunter.sources import load_registry; r=load_registry(); print([(s.source_id, s.status.value, s.enabled, s.terms_status.value) for s in r.list()])"
```

Expect: `hn_algolia` active, `github_issues` active, `blog_http` pending_review
and disabled.

2. The shipped config files parse and the community registry agrees with the
   source registry:

```
uv run python -c "from product_hunter.sources import load_registry; from product_hunter.communities import load_communities; r=load_registry(); c=load_communities(); c.validate_against(r); print([x.community_id for x in c.list()])"
```

Expect: `ask_hn`, `github_issues`.

3. The new CLI command does not crash on an empty database:

```
uv run python -m product_hunter.cli source-candidates list --path "$env:TEMP\ph-verify-empty.db"
```

It may report that the database does not exist; that is a correct answer, not
a crash. What you are looking for is a traceback.

4. `git status --short` shows only the files this wave declares.

5. The migration applies to a fresh database and the schema version is 8:

```
uv run python -c "from product_hunter.db import Store; s=Store.open(':memory:'); import sqlite3; print(sqlite3.connect(':memory:').execute('select 1').fetchone()); print(s.conn.execute('PRAGMA user_version').fetchone()[0]); print(sorted(r[0] for r in s.conn.execute(\"select name from sqlite_master where type='table'\"))); s.close()"
```

Expect the version to be 8 and `source_candidates` and `source_reviews` in the
table list.

## What you must not do

- Do not edit any file.
- Do not fix anything you find. Report it.
- Do not weaken, skip or delete a test.

## The report

Give the orchestrator:

- the exact summary line of `uv run pytest`, and its exit code
- the output of each of the five checks above
- every failure, with the file, the line and the reason
- a final line that is exactly `GATE: PASS` or `GATE: FAIL`

A check that could not be run is not a pass. Say `GATE: FAIL` and name the
gap.
