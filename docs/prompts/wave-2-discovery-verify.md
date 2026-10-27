# Verify: the discovery engine and the query frontier

## What to verify

The code gate of this repository is `uv run pytest`. Run it from the worktree
`C:\Anubhav\Web Dev Projects\product-hunter-wave2-discovery` on the branch
`wave2/discovery-engine`.

Report the gate result verbatim.

## What the change is

A new package `src/product_hunter/discovery/` plus five test files:

```
src/product_hunter/discovery/__init__.py
src/product_hunter/discovery/families.py
src/product_hunter/discovery/queries.py
src/product_hunter/discovery/records.py
src/product_hunter/discovery/budget.py
src/product_hunter/discovery/frontier.py
src/product_hunter/discovery/store.py
tests/test_discovery_families.py
tests/test_discovery_queries.py
tests/test_discovery_frontier.py
tests/test_discovery_budget.py
tests/test_discovery_store.py
```

Nothing outside that list is touched: no migration, no table change, no CLI
command, no worker handler, no change to any config file, and no change to any
module that already existed.

## The nine behaviours the change must prove

Every one of these needs a named test that would fail if the rule broke:

1. duplicate queries are refused
2. a query's family is assigned by the worker, never by the caller
3. the budget refuses the query that would break a limit
4. saturation is detected, and it names the reason
5. a failed search is not saturation
6. a zero-result search is a real outcome, not a failure
7. a zero-result outcome needs every required family to have run
8. a checkpoint round-trips and a restored frontier resumes
9. the identifiers are deterministic

## What to report

- the verbatim `uv run pytest` output, including the pass, fail and skip counts
- the pass count, and how many of the passing tests are the new ones
- the names of the tests that cover each of the nine behaviours above
- anything the gate reported that is not a pass, with the exact text

Do not edit any file. You are verifying, not fixing. If a test fails, report
the failure and stop.
