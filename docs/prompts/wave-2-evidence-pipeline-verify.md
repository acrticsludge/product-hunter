# Verify prompt: Wave 2 evidence pipeline

**Agent.** `loop-free-verify-code-1`
**Worktree.** `C:\Anubhav\Web Dev Projects\product-hunter-w2-evidence`
**Branch.** `wave2/evidence-pipeline`

## The gate

The repository adapter at `.loop/adapter.json` declares the code gate as:

    uv run pytest

Run it from the worktree root. Do not change any file. Do not change any
test. Do not change a failing test to make it pass.

## What to report

1. The exact command you ran.
2. The exact final summary line, verbatim, character for character.
3. The exit code.
4. The number of tests collected, passed, failed, skipped, and errored.
5. The output of `uv run pytest tests/test_evidence_pipeline.py -v`, so the
   new test names are on the record.
6. The output of the migration check below.

```
uv run python -c "import sqlite3,sys; sys.path.insert(0,'src'); from product_hunter.db.connection import connect; from product_hunter.db.migrations import migrate; c=connect(':memory:'); print('version', migrate(c)); print('fk', c.execute('PRAGMA foreign_key_check').fetchall()); print('integrity', c.execute('PRAGMA integrity_check').fetchone()[0]); print('tables', sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\"))); c.close()"
```

The expected result is version 3, an empty foreign key check, `ok` from the
integrity check, and 26 tables.

## How to report a failure

Report the exact failure output, including the test names and the assertion
messages, and the exact summary line. Do not summarise a failure as a pass.
