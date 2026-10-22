# Review: the discovery engine and the query frontier

You are the REVIEW node. You are adversarial. Your job is to try to prove the
change wrong, not to agree that it works.

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

Everything is new, so `git status` shows untracked files rather than a diff.
Use `git diff --no-index NUL <file>` or simply read the files.

Read the spec and the plan first, because the plan is the contract the
implementation was measured against:

- `docs/reasonix/specs/wave-2-discovery.md`
- `docs/reasonix/plans/wave-2-discovery.md`
- `docs/reasonix/handoffs/wave-2-discovery-handoff.md`

Then read the code it reuses, so you judge it against the real contracts:
`src/product_hunter/research/budget.py`, `src/product_hunter/research/evidence.py`,
`src/product_hunter/research/predicates.py`, `src/product_hunter/db/checkpoints.py`,
`src/product_hunter/db/store.py`, `src/product_hunter/normalization.py`,
`src/product_hunter/sources.py`, and `db/migrations/0001_initial.sql`.

## The rules the change must hold

1. The strategy is worker-side code. No family, template, marker or threshold
   may come from a model, a config file, an environment variable, or a caller.
2. A broken search tool is never saturation. `empty` (ran, found nothing) and
   `failure` (the tool broke) stay distinct. A failure never counts towards
   family coverage, never advances the low-yield counter, and leaves the
   frontier blocked rather than saturated.
3. A zero-result outcome is recorded only when a search genuinely came back
   empty and every required family has been executed.
4. Identical inputs give identical ids. The ids are derived, not generated.
5. The record never carries a novelty the frontier did not compute.
6. Every non-trivial branch, parser and arithmetic rule has a test that would
   fail if the rule broke.
7. Nothing outside the declared radius is touched. No migration, no table
   change, no CLI, no worker handler, no config change, no HTTP client.

## Findings to confirm or refute

I have read the diff and I have suspicions. Confirm each with code and a
reproduction, or refute it with code. Do not take any of them on trust, and do
not add to the list without evidence.

1. **The discovery budget has no non-finite guard.** `research/budget.py`
   refuses a non-finite amount with `NonFiniteAmount`, and says why: "every
   comparison against a non-finite value is false and the dimension would switch
   itself off". `discovery/budget.py` compares `self._cost + cost > max_cost` and
   then adds `cost` to `self._cost`. A NaN cost therefore passes the check and
   poisons the running total, so the cost limit stops working for the rest of the
   run. A negative cost reduces the recorded spend. Is this reachable, and is it
   a real hole?

2. **The result count and the result identities are never compared.**
   `frontier.record_execution` takes `result_count` and `result_ids` separately.
   Nothing checks that the number of identities is consistent with the count. A
   caller that passes `result_count=1` with fifty identities gets a record that
   says one result while the frontier tracked fifty, and the novelty is computed
   over fifty.

3. **`repeated_results` fires on the last execution, not the run.**
   `plan` says the cumulative duplicate rate across the run. The code compares
   `self._last_duplicate_rate`. One unlucky query trips the reason.

4. **`min_queries_per_family` defaults to 1, the plan says 2.** With 1, family
   coverage fires after one query per mandatory family, so the frontier reports
   saturated after six queries and the 40-query budget is never reached.

5. **`budget_exhausted` is invisible when nothing is pending.**
   `frontier._budget_exhausted` returns False when there are no pending queries,
   so a frontier that can no longer admit anything reports `saturated=False` with
   no reason. Can a caller tell "stopped" from "has more to do"?

6. **The two store inserts are not atomic and the replay path skips the spend
   row.** `write_record` inserts a `research_events` row and a `spend_ledger`
   row with no transaction between them, and it returns early when the event
   already exists, without checking that the spend row exists. Is a crash or a
   partial write able to leave the two tables disagreeing?

7. **A missing result reference is quietly replaced.** `to_tool_call_provenance`
   uses `result_ref=self.result_ref or self.record_id`.
   `research/evidence.py` justifies the field this way: "A result_ref must match
   `^[A-Za-z0-9._:/-]+$`, so a hand-written summary cannot be passed off as the
   original tool result." Does substituting the record's own id weaken that?

8. **`template_id` is an index into a list.** `generate_queries` builds
   `template_id = f"{family.value}-{index}"`. Reordering the templates renames a
   template's id and can give two different templates the same id. Does that
   matter for anything that reads it?

## Also worth your own attention

- Can anything reach a state where a family is assigned by anything other than
  the worker? Check `Query.assigned_by`, `Query.assignment_reason` and
  `assign_family`.
- Can a failed or empty search be read as saturation by any path, including
  `zero_result_outcome`?
- Does the checkpoint round trip lose anything? Compare `checkpoint_state` with
  `from_state` field by field, and check that the budget's state survives.
- Can an id be forged? Try building a `Query` or a `DiscoveryRecord` whose id
  does not match its fields, and try passing a hand-written `normalized` or
  `novelty`.
- Does the frontier refuse to run a query it already ran, across a checkpoint
  and resume?
- Are any of the tests vacuous, asserting something that would also hold if the
  rule were removed? Pick the three you think are weakest and try to break the
  code they claim to protect.
- Does anything write to a table the change was not allowed to write to?

## What to report

Use the `ponytail-review` rubric: correct, safe, load, tested, fast, lean.

For each finding give:

- the severity: `Must fix`, `Should fix`, or `Nice to have`
- the file and line
- what breaks, in one sentence
- a reproduction, as code or as an exact command
- the smallest fix you would make

End with:

- a `Verdict:` line: `ship`, `ship with fixes`, or `do not ship`
- a `Lean:` line: how many lines you believe could be removed, with the
  evidence for that number. Say `Lean: 0 lines possible` if you found nothing.

Do not edit any file. Report only. If you need to run something, run it in the
worktree; do not write a file outside `C:\Users\anubh\AppData\Local\Temp\opencode`.

---

# Second pass

Your first pass returned "ship with fixes". Fourteen findings were fixed over
three rounds, and the code has changed since you read it. This is a fresh pass
over the current tree. Assume nothing you concluded still holds.

## What changed since your first pass

- **Fix round 1** closed your twelve findings. The budget counts the queries
  that are admitted and not yet run, so a limit binds at admission and not
  after the fact. A refused query counts for nothing. `budget_exhausted` asks
  the budget about every pending query. The result count must equal the number
  of result identities, and the novelty is computed over the list. The two
  store inserts are in one transaction, and a replay repairs a missing spend
  row. A non-finite or negative cost is refused, reusing `NonFiniteAmount`.
  The tracker can be handed back at restore time. `repeated_results` is
  decided on the cumulative rate the verdict reports. The frontier re-derives
  the family and refuses a mismatch, and it grew an `expand` method with a
  per-family template cursor. The record's docstring was corrected. The
  zero-result outcome is no longer sticky. Eight mutation-surviving rules now
  have golden-value tests.
- **Fix round 2** closed the residual you did not find: `_family_of` now
  verifies that a query naming a template really is a rendering of that
  template, matching the text against the template with the `{subject}` slot
  replaced by `.+?`. It fails closed, and `record_execution` no longer falls
  back to the caller's claimed family.
- **Fix round 3** closed the gap the harden pass found: an execution of a
  query that was never admitted is refused, because that query never went
  through the source gate, the budget check or the duplicate check. The two
  refusal statuses are exempt, because a refusal describes a query that was
  not admitted.

## What to do now

1. **Re-verify your findings.** Run your own reproduction scripts, which are
   still under `C:\Users\anubh\AppData\Local\Temp\opencode\disc-review\`. Some
   of them now stop early, because the code they exercise is now refused
   earlier. That is a change in behaviour, not a missing fix: say which script
   stops where and why, and confirm the underlying rule still holds.
2. **Re-run the mutation testing.** Your harness is `mutate.py` in the same
   directory. The mutations it applies may be stale after three rounds. Report
   which of your original 23 mutations are now killed, which survive, and
   which are no longer applicable. Add mutations for the three new rules: the
   template rendering check, the "never admitted" gate, and the admission
   pending accounting.
3. **Look for what the fix rounds introduced.** Three rounds of edits to a
   frontier that has to keep its counters straight is exactly where an
   off-by-one hides. Check the pending accounting: a query is admitted, sits
   in `_pending`, runs, and leaves `_pending`; is it counted exactly once
   everywhere? Check that a refused query leaves no trace in any counter.
   Check that the expansion cursor and the checkpoint version survive a
   round trip.
4. **Check the new public surface.** `expand`, `queries_per_family`,
   `from_state(budget=..., tracker=...)`, `load_frontier(budget=...)` and
   `DiscoveryBudget.check(pending_total=..., pending_source=...,
   pending_family=...)` are new since your first pass. Are they coherent, and
   do they have the failures a caller would hit?
5. **Say what you did not check.** Be explicit.

## What to report

The same format as the first pass: severity, file and line, what breaks in one
sentence, a reproduction, and the smallest fix. Then the `Verdict:` line and
the `Lean:` line.

Do not edit any file. Report only.

---

# Third pass, on the final tree

Two more fix rounds landed after your second pass. The tree you are reading now
is the tree that will be committed.

**Fix round 4** closed your seven findings and the Lean item:

- F1: a raw objective can no longer reach a derived family. `_family_of` refuses
  a family with `requires_normalized_problem` on the marker path, and `expand`
  skips the derived families, because a derived query needs a
  `NormalizedProblem` and the frontier has none. The saturation coverage check
  still counts every required family, because the caller can add derived
  queries itself.
- F2: the seed is normalised in the constructor and an empty seed is refused;
  the rendering pattern is built with `re.DOTALL`; `SaturationPolicy` refuses a
  `queries_per_family` above the fewest templates of the required families; and
  the frontier has a public `expansion_stop_reason`.
- F3: the `budget=` parameter is gone from `from_state` and `load_frontier`.
  The counters always come from the checkpoint; only the tracker is handed
  back.
- F4: `DiscoveryBudget.record` records the spend and then raises the house
  `BudgetExceeded` when the spend is over `max_cost_per_run`.
- F5: the zero-result outcome is not recorded while the frontier is blocked,
  and not with an empty required-family set.
- F6: the checkpoint version advances on every successful save, explicit
  version or not.
- F7: the four unprotected rules now have tests. The run-scope limit at
  admission, the source weight asserted as a priority value rather than an
  order, the expansion cursor asserted through the refusal count, and the
  checkpoint version numbers.
- F10: the marker's runtime case-fold validation moved into the test suite.
  Sixteen lines out of `src/`.
- F9: the empty-token-set guard was kept and pinned with a threshold-0 test.

**Fix round 5** closed the gap the harden re-pass found: a forged checkpoint
could smuggle a query into `_pending` past the admission gates, because
`from_state` repopulated `_pending` without re-checking. Now `from_state`
re-checks the source gate and the family re-derivation for each restored
pending query, `record_execution` re-checks the source gate as a backstop, and
`DiscoveryBudget.record` raises `BudgetExceeded` when the run, source or
family query count goes over its limit, the way `research/budget.py::record`
does for any applicable limit.

## What to do now

1. **Verify the final tree against every rule in the first half of this
   brief.** Not the rules as they were: the rules as they are now.
2. **Re-run your mutation harness** in
   `C:\Users\anubh\AppData\Local\Temp\opencode\disc-review2\mutate2.py` and the
   newer one the implementer wrote in
   `C:\Users\anubh\AppData\Local\Temp\opencode\disc-fix4-check\`. Report which
   mutations are killed and which survive.
3. **Look for what rounds 4 and 5 introduced.** Five rounds of edits to a
   frontier that has to keep its counters straight is where an off-by-one
   hides. Fuzz it again: random admit, run, refuse, expand, checkpoint and
   restore steps, and assert the invariants after every step. The invariants
   are: the sum of the executed and pending counts never exceeds the run
   limit; the per-source and per-family sums hold; a pending query is always
   admissible and its family always derivable; a restored frontier equals the
   one that was saved; and a blocked frontier never reports saturation.
4. **Re-check the new public surface**: `expansion_stop_reason`,
   `frontier.priority()`, the `SaturationPolicy` validator, the `tracker`
   parameter, and the `BudgetExceeded` that now escapes `record_execution`.
5. **Say what you did not check.**

## What to report

The same format as before: severity, file and line, what breaks in one
sentence, a reproduction, and the smallest fix. Then the `Verdict:` line and
the `Lean:` line.

Do not edit any file. Report only.

---

# Fourth pass, on the final tree

Three more fix rounds landed after your third pass. The tree you are reading
now is the tree that will be committed.

**Round 5** closed the gap the harden re-pass found: a forged checkpoint could
smuggle a query into `_pending` past the admission gates. `from_state` now
re-checks the source gate and the family re-derivation for each restored
pending query, `record_execution` re-checks the source gate as a backstop, and
`DiscoveryBudget.record` raises the house `BudgetExceeded` when the run, source
or family query count is over its limit.

**Round 6** closed your M1, S1, S2 and S3.

- M1: the bookkeeping comes first and the budget comes last. The failure path
  increments the failure counters before counting the search; the success path
  applies the coverage, the seen identities and the counters before counting
  the search. The frontier catches the house `BudgetExceeded` from
  `budget.record` and keeps it, so a paid-for search keeps its record; the
  overrun is exposed as `frontier.budget_overrun` and as a verdict signal.
- S1: `expand` clears `_expansion_stop_reason` at the start.
- S2: the frontier keeps a total failure count, and the zero-result outcome
  needs that count to be zero.
- S3: the plan was amended. N1: the store joins a caller's transaction instead
  of always beginning one. N2: the expansion stop reason test pins the exact
  string.

**Round 7** closed the last harden finding: `from_state` now re-checks the
budget for each restored pending query, with the pending counts growing as the
restore goes on. A cost-scope refusal does not take the query out of the
pending set, because the cost of a search is not known before it runs and a
run that crossed its spend keeps the queries it committed to.

**Round 8** made the `budget_overrun` signal survive a checkpoint:
`DiscoveryBudget.exceeded()` returns the names of the limits the run is over,
in the shape of the house `research.budget.BudgetTracker.exceeded()`, and the
verdict's signal is derived from it. The plan and the spec were amended to
match the code.

## What to do now

1. Verify the final tree against every rule in the first half of this brief.
2. Re-run your mutation harness, and add mutations for the round 5 to 8 rules:
   the restore's source, family and budget re-checks, the bookkeeping-before-
   budget ordering, the absorbed `BudgetExceeded`, the cleared stop reason,
   the total failure count, and the derived overrun signal.
3. Fuzz again, with the round 7 and 8 rules in the invariant set: the
   executed-plus-pending sum never exceeds the run limit; a pending query is
   always admissible, budget-admissible and family-derivable; a restored
   frontier equals the one that was saved; a blocked frontier never reports
   saturation; and a frontier whose spend is over the limit reports the
   overrun signal both before and after a restore.
4. Check the new public surface: `DiscoveryBudget.exceeded()`,
   `frontier.budget_overrun`, the total failure count and its checkpoint
   field, and `store._transaction` joining a caller's transaction.
5. Say what you did not check.

## What to report

The same format: severity, file and line, what breaks in one sentence, a
reproduction, and the smallest fix. Then the `Verdict:` line and the `Lean:`
line.

Do not edit any file. Report only.
