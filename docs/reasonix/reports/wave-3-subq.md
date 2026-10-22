# Wave 3 Session 5: SubQ Snap as an optional decision provider, run report

**RUN_ID:** wave3-session5-subq
**Node:** REPORT (stage 10)
**Date:** 2026-10-22
**Branch:** wave3/subq-integration
**Commit:** `033f3630eb97e25c9ec83a4a7a67ba53edb9cbca`, "wave3: integrate the optional SubQ decision provider"
**Risk:** declared high, floor medium (adapter floor), SubQ Snap stage 0 advised medium (p = 0.958, confidence 0.938), backend lane (p = 0.987), deep depth (p = 0.966)

## 1. Summary

SubQ Snap is integrated as an optional high-volume decision provider. It answers
structured questions about text: a yes/no gate (noul), a label out of a set
(choice) or a number on a scale (score). It is reached through the decision
routing layer, not through the research pipeline, and it is never a hard
dependency: with no credential, with the API down or with the model withdrawn,
the chain falls through to the configured text model.

The provider is a library with no caller, matching the shape of the other wave 2
subsystems. The research pipeline, the thirteen model roles, the model router
and the model routing config are untouched.

The SubQ API decides over one state and has no batch endpoint. A probe showed
that an array state is one decision about a list, not one decision per list
entry. A batch is therefore one request per item, and the capability report says
so in words.

## 2. Commit hash

Code commit: `033f3630eb97e25c9ec83a4a7a67ba53edb9cbca`, "wave3: integrate the
optional SubQ decision provider". 15 files, 4825 insertions, 12 deletions.

## 3. Tests

Gate command: `uv run pytest` (from the worktree root).

Verbatim result: `2245 passed, 1 skipped in 44.89s`, exit 0.

Counts: 2245 total, 108 collected test cases across the two new test files, 2137
pre-existing tests.

| Test file | Behaviours covered |
|---|---|
| `tests/test_llm_decision.py` | The answer shapes and their type validators; the router skipping, falling back and refusing; the text fallback building a prompt, stripping a fence, recording a malformed body, comparing the probability with the threshold, and continuing; the builder making the chain the config names and refusing a chain without a generator; the config refusing a text provider without a role; the provider being a `DecisionProvider`. |
| `tests/test_llm_subq.py` | The provider disabled without a key; the router using the next provider; the key staying out of the repr; the config loader reading the environment; a body that is not the documented shape as an item failure; a refused request recorded against the item; the timeout retried and then raising; the rate limit retried with `Retry-After` honoured; a refused credential raising at once and the health going unavailable; a provider failure making the router fall back to the text provider; every item of a batch getting an outcome; the batch cap; the provenance on every outcome; the budget refusing a call that would break the call, token and cost limits; the tracker refusing the call here; the spend recorded in the tracker; the ledger counting the call and its tokens; the same question about the same text twice costing one call; the filter outcome carrying the probability and the gate; the ranking ordered by the weighted score; the capability report naming what the API cannot do; the capability check costing no tokens; and the live test, marked `network`, skipping itself without a key. |

One live test passed against the real service: `tests/test_llm_subq.py:615` —
`1 passed, 43 deselected in 1.28s`, exit 0. See section 9.

Visual gate: not required. No UI file was touched.

## 4. What changed

| File | Change | Lines |
|---|---|---|
| `src/product_hunter/llm/decision.py` | new — the answer shapes, the `DecisionProvider` protocol, the text fallback, the router | 900 |
| `src/product_hunter/llm/subq.py` | new — the SubQ config, the HTTP client, the provider, the spend ledger | 1126 |
| `src/product_hunter/llm/base.py` | touched — the shared validation error helper | +16 |
| `src/product_hunter/llm/__init__.py` | touched — the decision router builder | +71 |
| `src/product_hunter/config.py` | touched — the decision routing config, the SubQ config loader, the env overrides | +117 |
| `config/decision_routing.yaml` | new — the provider chain | 15 |
| `.env.example` | touched — the SubQ names, no values | +17 |
| `docs/TOOLING.md` | touched — the credential table | +5 |
| `docs/reasonix/ARCHITECTURE.md` | touched — the provider paragraph | +18 |
| `docs/reasonix/spec-compliance.md` | touched — the requirement 10 and SubQ rows | +17 |
| `docs/reasonix/specs/wave-3-subq.md` | new — the spec, with the verified API contract | 431 |
| `docs/reasonix/plans/wave-3-subq.md` | new — the plan | 122 |
| `docs/reasonix/prompts/wave-3-subq-implement.md` | new — the dispatch prompt | 169 |
| `tests/test_llm_decision.py` | new — 51 cases | 762 |
| `tests/test_llm_subq.py` | new — 57 cases, one of them live | 1045 |

The import graph stayed a tree: `import product_hunter.llm` loads neither
`product_hunter.config` nor `product_hunter.research`, so the pipeline never
imports the SubQ module. The budget tracker is reached through a `SpendTracker`
protocol, which is how the cycle is avoided.

## 5. Capabilities

The provider reports what the API can and cannot do:

| Capability | Available | Reason |
|---|---|---|
| `text_generation` | no | the Decisions API answers structured questions and returns no free text |
| `batch_decision` | no | the API decides over one state; an array state is one decision about the list, so a batch costs one request per item |
| `classify` | yes | a choice question returns the option and its probability |
| `filter_items` | yes | a noul question returns the probability of the true branch |
| `rank` | yes | a score question returns the level, the probabilities of the levels and the confidence |

The ranking is one score per item and a sort. SubQ does not offer a listwise
ranker, and the report says so.

## 6. Credential requirements

| Name | Meaning |
|---|---|
| `SUBQ_API_KEY` | the bearer token, read from the environment only |
| `SUBQ_BASE_URL` | the base URL, `https://api.subq.ai/v1` by default, must be https unless localhost |
| `SUBQ_MODEL` | the model id, `subq-snap-preview` by default |

The other settings are `PH_SUBQ_TIMEOUT_SECONDS`, `PH_SUBQ_MAX_RETRIES`,
`PH_SUBQ_BACKOFF_SECONDS`, `PH_SUBQ_MAX_CALLS`, `PH_SUBQ_MAX_INPUT_TOKENS`,
`PH_SUBQ_MAX_USD`, `PH_SUBQ_MAX_BATCH_ITEMS`, `PH_SUBQ_USD_PER_MTOK_INPUT`,
`PH_SUBQ_FAILURE_THRESHOLD`, `PH_SUBQ_COOLDOWN_SECONDS` and
`PH_SUBQ_MAX_CACHE_ENTRIES`. Every one falls back to the default in
`SubQConfig`. `.env.example` names them and holds no value.

The key is held in a `SecretStr`, so it does not appear in `repr`, in
`model_dump()` or in `model_dump_json()`. It never enters a log, an exception
message, a provenance record or a test fixture.

## 7. Fallback behaviour

The decision router reads `config/decision_routing.yaml`, which names the
providers in the order of preference: `subq`, then `text`. The first provider
that answers wins. A provider that is disabled or unavailable is skipped, a
provider that raises hands the work to the next one, and a provider where every
item failed hands the work to the next one too. A batch where some items
failed keeps those failures and is not paid for twice.

With no credential the SubQ provider reports itself disabled and the router
uses the text provider. With the API down, the circuit opens after three
consecutive failures and the router uses the text provider. A refused credential
keeps the provider unavailable until the credential changes.

The text provider is the fallback over the existing `TextGenerator`, so the
fallback costs no new plumbing: the model routing that already exists resolves
the model.

## 8. Known API limitations

- The Decisions API decides over one state. It has no batch endpoint. An array
  state is one decision about a list, not one decision per list entry. A batch
  is therefore one request per item, and the capability report says so in words.
- The API returns typed answers, not free text. `text_generation` is reported
  as unavailable.
- SubQ does not offer a listwise ranker. The ranking is one score per item and
  a sort.
- The API has no idempotency key. The client keeps the answer under the
  request hash and answers a repeated request from the cache without a call and
  without a cost. That is a client-side guarantee, and the report says so.
- The service sends no rate-limit headers in the observed responses. The client
  honours `Retry-After` when it is present and falls back to exponential
  backoff with a cap.
- The key held in the local OpenCode configuration is permitted only for
  `subq-snap-preview` on the Decisions endpoint. `POST /responses` returns 403
  and `subq-preview` is not listed. The adapter only targets the Decisions
  endpoint.
- Inputs and outputs are deleted within 60 days of processing. Content flagged
  for a policy violation may be kept while it is investigated.

## 9. Live smoke test

The live test passed against the real service:

```
tests/test_llm_subq.py .                                                 [100%]
1 passed, 43 deselected in 1.28s
```

Command: `SUBQ_API_KEY` from `C:\Users\anubh\.config\opencode\.env` into the
environment, then `uv run pytest tests/test_llm_subq.py -m network`.

A wider live exercise against all three operations succeeded:

- `check()` returned `{"model": "subq-snap-preview", "models": ["subq-snap-preview"]}`
  at no token cost.
- `classify` on an invoice sentence returned `finance` with probability 0.962
  and confidence 0.925; on a coffee sentence it returned `other` with
  probability 0.999.
- `filter` returned `yes` with probability 0.997 and `passed=true` for the
  invoice sentence, and `no` with probability 0.010 for the coffee sentence.
- `rank` returned 1.014 for the invoice sentence and 0.137 for the coffee
  sentence, correctly ordered.
- Repeating the filter did not make a call: `cache_hits 2`.
- The spend was 6 calls, 661 input tokens, 0 output tokens, an estimated
  $0.000033, health `healthy`.

The live test is not blocked. It is skipped by default when the key is absent,
which is how it runs in CI.

## 10. What the review and the harden pass found

Two adversarial reviews and one harden pass ran over the change, and all three
found real defects.

### The harden pass

It found one open leak path, which was closed: the key leaked through
`model_dump()` and `model_dump_json()` because `repr=False` does not mask
serialisation. It is now a `SecretStr`. The pass also found a hostile
`SUBQ_BASE_URL` receiving the bearer token with redirects followed by default;
that is closed with an https rule and `follow_redirects=False`. The unbounded
answer cache is now a bounded LRU cache. The tracker reporting an overrun after
a completed call no longer fails the call; the overrun is recorded instead.

### The first review

It returned `do not ship`, with four must-fix findings: an httpx failure that
is not a `TransportError` escaping the chain, the call limit not binding
against failed calls, a cached answer being refused at the call limit, and the
provider's own token and dollar limits breaking silently. Seven should-fix
findings followed: the fatal refusal only stopping the provider for the
cooldown, the item text closing the untrusted region, the router swallowing a
run budget refusal, the SubQ provider accepting a label outside the options,
the pipeline importing the SubQ module, the mid-batch failure throwing away
answers already paid for, and `model_role` being inert. Five nice-to-have
findings were closed as well: the usage block is now `extra="ignore"`, the
router refuses a provider that returns too few outcomes, the router exposes
`capabilities()`, a repeated question name is refused, and the score scale is
documented. Two pieces of duplication and dead code were removed: the copied
error helper and the unreachable except clause.

The fix round moved the accounting from "after a successful answer" to
"before every request", through an `authorize` callback on the client. A cached
answer is now free, a failed request still counts, and a request that was never
made does not.

### The second review

It returned `do not ship` again, on two must-fix findings that the fix round
itself had introduced or left behind:

- The call counter was incremented before the token, dollar and tracker
  checks, so a call that was refused before it went out still spent the call
  budget. A provider with a token limit of one could make no call at all and
  was then locked out by a limit it had never reached. The counter now moves
  after every refusal and before the request.
- A base URL that cannot be built escaped the chain as a raw `ValueError`,
  because httpx reports an unusable URL as a plain exception rather than an
  httpx error. A one-character typo in `SUBQ_BASE_URL` stopped the pipeline
  instead of falling through to the text provider. The config now refuses a
  URL with no host and a URL that carries a credential in it, and the client
  catches the exception that httpx raises for the rest.

The two should-fix findings were closed as well: the capability report no
longer describes a provider the chain would skip, and the credential-change
test now changes the credential where it is read from, the way an operator
would. Three of the four nice-to-have findings were closed: the answer
envelope ignores a field the service adds later, an outcome outside the range
of the items that were asked is noted instead of dropped, and the rest were
left as they are.

Both must-fix findings were verified directly, outside the test suite: a
refused call now leaves `spend.calls` at 0 with 0 HTTP requests, and the next
call still works; a URL that cannot be built now hands the work to the text
provider, which answers it.

### The final state

The gate is green, the live test passed, the import graph check proved the
pipeline does not load the SubQ module, and both reviews' findings are closed.
Two blind `except Exception` clauses remain in `subq.py`, at the two places
where the budget tracker is reached through a protocol and its error tree is
therefore unknown. They are deliberate, and each carries a comment saying so.

## 11. What was skipped and what was not checked

- The `models()` call has no retry, while `decide()` does. A transient failure
  of the free model list fails `check()` at once. The provider is not wired into
  the pipeline, so this only affects an operator who calls `check()` directly.
- The two remaining house-style items from the review were left as they are:
  the SubQ score is in level-index space and the text score is 0 to 1. They
  mean different things and the `DecisionOutcome.score` docstring says so.
- No benchmark of SubQ against the cheap model was run. Spec §38A asks for
  that before SubQ is promoted into the default path, and the benchmark is its
  own piece of work. The provider is optional and nothing in the pipeline calls
  it.
- No caller of the provider exists in the tree, so it gates nothing and
  persists nothing. A table for it arrives with the wave that owns the
  migration.
- Concurrency was not tested. The cache is thread-safe because it is an
  `OrderedDict` mutated under a single thread, but the provider is not exercised
  under concurrent access.
- The untrusted-text handling in `extraction.py` and `clustering/adjudication.py`
  has the same marker-escape pattern as the text fallback had. That pattern was
  fixed in the new module only; the other two are out of this change.
- `ruff check` reports two blind `except Exception` clauses in `subq.py`, at the
  two places where the budget tracker is reached through a protocol. They are
  deliberate. The other findings ruff reports on the touched files
  (`config.py` ISC004, `catalog.py` UP017, `opencode.py` UP035 and RUF046)
  exist on `main` as well and were not touched.
- The gate's `code` line read FAIL at the start of the session, before any
  change here. The gate script writes its own file with
  `Set-Content -Encoding utf8NoBOM`, which Windows PowerShell 5.1 does not
  support, and the lint findings it then reported are the three pre-existing
  ISC004 ones in `config.py`. The test suite was green at that point too.

## 12. Closing

```text
GOAL: Wave 3 Session 5: SubQ Snap as an optional decision provider
TIER: free (models used per node in order)
RISK: declared=high floor=medium (no auth, no schema, no payment, no secret, no infra path touched)
ADAPTER: Product Hunter [.loop/adapter.json]
STAGES: 11/11 complete, skipped: none
GATES: code gate: uv run pytest -> 2245 passed, 1 skipped in 44.89s, exit 0
GATES: visual gate: not required, no UI file was touched
GATES: live smoke test: 1 passed, 56 deselected, exit 0, model subq-snap-preview, 6 calls, 661 input tokens, 0 output tokens, $0.000033 estimated, 2 cache hits, health healthy
GATES: SubQ Snap stage 0: one call, 444 input tokens, 0 output tokens, 0 errors, 0 cache hits, model subq-snap-preview, risk medium (p = 0.958), lane backend (p = 0.987), depth deep (p = 0.966)
GATES: SubQ Snap stage 3: one call, 466 input tokens, 0 output tokens, 0 errors, 0 cache hits, model subq-snap-preview, plan B (p = 0.948, confidence 0.896), coupling risk 0.023
CHANGES: 15 files (see section 4)
HARDEN: 1 Must fix (key leak, closed), 2 Should fix (hostile base URL, unbounded cache, closed), 0 Nice to have
REVIEW: do not ship on the first pass and do not ship on the second; ship after two fix rounds, 6 Must fix and 9 Should fix closed
WAIVERS: none
STOPPED BECAUSE: all pass and review ship
NEXT: the collection and investigation waves call `build_decision_router` with a `generator_for_role` that resolves a role through the model routing
```

Note on paths: the task brief spelled the report path as `docs/resonix/...`. That
directory does not exist in this repository. Every other report lives under
`docs/reasonix/`, and the brief's path was a typo. This report is written to
`docs/reasonix/reports/wave-3-subq.md`.
