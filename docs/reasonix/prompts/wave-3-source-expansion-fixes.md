# Wave 3, Session 3: the fix round

Six fixes on top of the implementation. Read
`docs/reasonix/specs/wave-3-source-expansion.md` and
`docs/reasonix/prompts/wave-3-source-expansion-implement.md` first, then the
files you are changing.

Do not add a feature. Do not widen the surface. Every fix below is a defect
the orchestrator found while reading the implementation.

## F1. The candidate's decision moves out of the status field

`discovery/queries.py` changed `SourceCandidate.status` from
`Literal["proposed"]` to `Literal["proposed", "approved", "rejected"]`, and
`tests/test_discovery_queries.py` was edited so the wave-2 assertion would
still hold. A wave-2 test file should not have been edited for this.

Put the decision in its own field and leave the status alone:

```python
status: Literal["proposed"] = "proposed"
decision: Literal["proposed", "approved", "rejected"] = "proposed"
```

The status says what the object is: a proposal, always. The decision says
what a reviewer concluded about it. Neither carries permission: the terms
status stays `unknown` and the allowed use stays empty.

Then:

- `git checkout -- tests/test_discovery_queries.py`. That file returns to
  its wave-2 content, byte for byte, and the wave-2 assertion that a
  candidate refuses to be built with `status="approved"` passes again.
- `source_expansion.py` writes the decision into the `source_candidates.status`
  column, reads it back as `decision=`, and sets it in
  `approve_candidate` and `reject_candidate`.
- `cli.py` reports the decision under the key `decision`.
- The docstring of `SourceCandidate` says the status is the proposal's own
  state and never changes, and that the decision is the reviewer's verdict.

The wave-2 test file is the only test file you may restore. Every other test
file keeps the changes it already has, except where a fix below says
otherwise.

## F2. A candidate that fails a floor is kept out, not ranked lower

The spec says the floors "keep a candidate out regardless of its score". The
implementation sorts ineligible candidates last and returns them when the
limit is large enough. That is not keeping them out.

`select_candidates` must return only the eligible candidates:

```python
eligible = [score for score in scored if score.eligible]
ordered = sorted(eligible, key=lambda score: (-score.total, -score.volume))
return ordered[:limit]
```

`select_communities` must skip a community whose score is not eligible, the
same way. A community nobody should collect from is not taken.

Add a test to `tests/test_source_expansion.py`: an ineligible candidate is
not returned even when the limit is larger than the number of eligible ones,
and an ineligible community is not taken.

## F3. The clock is UTC everywhere

`source_expansion.py` and `discovery/queries.py` use `utcnow()`. The new
code uses `date.today()`, which is the local date. A review that expires
midnight must not depend on the machine's timezone.

- `sources.py` cannot import `product_hunter.fetch`, because `fetch.py`
  imports `sources.py`. Add a small local helper:

```python
def _utc_today() -> date:
    """Today in UTC, the clock the rest of the repository reads."""
    return datetime.now(timezone.utc).date()
```

and use it as the default for `today` in `SourceRegistry.admission` and
`require`.

- `source_policy.py` imports `utcnow` from `product_hunter.fetch` and uses
  `utcnow().date()` for the same default.

## F4. The health status reads the effective status

`source_policy.health_status` reads `source.status`. A source whose review
expired still says `active`, so a stale source with a clean history reports
`healthy`. It must read `source.effective_status(today)`, and it takes a
`today` parameter that defaults to the UTC date.

`derive_reliability` already uses `effective_status`. Give it the same
`today` parameter and the same UTC default, so one clock serves both.

Add a test: a source whose review has expired is quarantined, not healthy.

## F5. A retired source is not also suspended

`retire_source` sets the retired fields and leaves the suspension fields
behind, so the entry says it was suspended on a date it was never suspended
on. Clear `suspended_at` and `suspend_reason` when a source is retired.

## F6. The command line helper is annotated

`cli.py` has `def _candidate_payload(candidate) -> dict[str, object]:`. Give
the parameter its type, `SourceCandidate`, and import it.

## After the fixes

`uv run pytest` must be green, and the summary line must show more tests
than the 2184 it showed before, because F2 and F4 each add one.

Do not commit. Do not write the report file.
