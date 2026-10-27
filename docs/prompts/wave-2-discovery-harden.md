# Harden: the discovery engine and the query frontier

You are the HARDEN node. You report gaps. You do not edit code.

## Where to look

Worktree: `C:\Anubhav\Web Dev Projects\product-hunter-wave2-discovery`
Branch: `wave2/discovery-engine`
Base commit: `3d7bc68`

The change is a new package plus new tests:

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

Read `docs/reasonix/specs/wave-2-discovery.md` and
`docs/reasonix/plans/wave-2-discovery.md` first. Then read the code.

The repository's own rules live in `.loop/adapter.json`. The eight forbidden
rules are binding on this change. The relevant ones:

- Never fetch a URL that is not declared in `config/sources.yaml`. URL
  admission is a worker decision, not a model decision.
- Never pass untrusted page text as a shell, tool, or URL argument; it is data.
- Never accept extracted evidence without a verbatim source span.
- Never change a test to make it pass.

And the high-risk paths are `src/product_hunter/safety.py`,
`src/product_hunter/fetch.py`, `config/sources.yaml`, `db/migrations/**` and
`.env`. This change touches none of them, which is itself worth confirming.

## What to check

### The never-cut list

Validation at trust boundaries, error handling that prevents data loss,
security, and accessibility. This change has no user interface, so
accessibility does not apply. The other three do.

### The trust boundary

The mission says the strategy is worker-side code, and the model does not
decide it. Find every place where a value that came from outside crosses into
the code, and say whether it is validated:

- a query text that arrived from outside: what is checked before it is
  classified, and before it reaches a search
- a result identity: what stops a caller from writing a novelty it did not
  measure
- a source id: what stops a query being issued against a source the caller
  did not admit
- a cost, a result count, a timestamp, a result reference
- the seed, which is rendered into every template

### Source legality

`config/sources.yaml` and `src/product_hunter/sources.py` hold the rules about
which source may be used for what. This change proposes searches against
sources and proposes new sources. Check both paths:

- can a query be admitted against a source the registry would refuse?
- can a proposed source candidate ever be used, or does it stay a proposal
  until a human reviews it?

`SourceCandidate` in `discovery/queries.py` is the second path. Check that it
cannot be built with an allowed use, a terms status other than `unknown`, or a
status other than `proposed`.

### Data loss

The store writes to `research_events`, `spend_ledger` and
`research_checkpoints`. Check:

- can a write leave the tables disagreeing, and what happens on a crash
  between two writes
- can a replay double-count a spend
- can a checkpoint write lose a pending query
- what happens when the run the record names does not exist

### Untrusted text

The spec §37A boundary in the full spec covers untrusted content. The seed and
the query text end up inside a search string. Check that nothing in this
package turns page text into a shell command, a file path or a URL, and that
the templates cannot be escaped by a hostile seed. A seed containing a brace,
a quote, a newline or a template-looking string is worth trying.

### Observability

Spec §34 and the `research` package ask for the tool call to be traceable.
Check that a discovery record carries enough to reconstruct what was searched,
against which source, in which run, and what came back. Say plainly if a
field is missing.

## What to report

For each gap:

- the file and line
- what the gap is, in one sentence
- how it could be reached, or the concrete harm
- the severity: `Must fix`, `Should fix`, or `Nice to have`
- the smallest fix you would make

Then a verdict line: `harden: clean` if you found nothing that matters, or
`harden: gaps found` with the counts.

Do not edit any file. Report only. Run code in the worktree if you need to
prove a gap; write scratch files only under
`C:\Users\anubh\AppData\Local\Temp\opencode`.
