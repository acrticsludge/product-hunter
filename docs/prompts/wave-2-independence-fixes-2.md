# Fix list 2: Wave 2 Session 5, from the second review round

Six findings. Fix every one. `uv run pytest` must pass when you are done.
Do not add a feature, a dependency, or a configuration value the list does not
ask for. Do not change a test's assertion except where this list names it.

---

## G1 (Must): the quote matcher is quadratic in the text length

`src/product_hunter/independence/propagation.py`, `longest_common_block` and
`detect_propagation`.

`difflib.SequenceMatcher(None, left, right, autojunk=False)` runs in
O(n*m). The review measured one 20,000-character pair at 10.8 seconds with
`autojunk=False` and 0.010 seconds with the standard library default, and 400
mentions of 300 characters at 89.7 seconds. Every pair that misses the repost
threshold, which is the normal case for two different posts, pays for the
matcher. The reviewer estimates 1,000 mentions at about 9 minutes, 1,000
mentions of 2,000 characters at about 7 hours, and the 20,000-character bound
at days.

The fix is a cheap screen in front of the matcher, not a change to the
matcher. The screen is a necessary condition, so it never rejects a real
quote, and the matcher still decides the pair.

The quote rule needs a verbatim block of at least
`quote_min_overlap * len(shorter_text)` characters. `min_comparable_chars` is
40 and `quote_min_overlap` is 0.6, so the block is at least 24 characters.
A common block of that length contains a common run of 24 characters, so a
pair whose texts share no 24-character run cannot earn a quote.

Implement it like this:

1. Compute the shingle length from the policy, so the screen stays sound if
   the thresholds are changed:

```python
def _shingle_chars(policy: IndependencePolicy) -> int:
    """The length of the run two texts must share before a quote is possible.

    A quote needs a verbatim block of at least
    ``quote_min_overlap * min_comparable_chars`` characters, so the screen
    looks for a run of that length. A shorter run would not prove the block
    is there.
    """
    return max(8, math.ceil(policy.propagation.quote_min_overlap * policy.propagation.min_comparable_chars))
```

2. Precompute, once per mention, the set of its shingle hashes:

```python
def _shingles(text: str, size: int) -> frozenset[int]:
    """The hashes of every run of ``size`` characters in the text.

    Two texts that share a run of this length share a shingle hash, because
    equal strings hash equal. A pair whose sets do not intersect cannot share
    a run of that length.
    """
    if len(text) < size:
        return frozenset()
    return frozenset(hash(text[index : index + size]) for index in range(len(text) - size + 1))
```

3. In `detect_propagation`, build the shingle set for every mention next to
   the token set, and pass it into the classification.

4. In `_classify`, only run the matcher when the two shingle sets intersect:

```python
if shingles_left.isdisjoint(shingles_right):
    ratio = 0.0
else:
    ratio = verbatim_ratio(left_text, right_text)
```

Keep `longest_common_block` and `verbatim_ratio` exported and unchanged. They
are still the thing that decides a quote; the screen only decides whether to
ask.

Add a test that a quote is still detected, and a test that the screen does
not reject a real quote: a text that shares a 24-character run with another
is still a quote.

## G2 (Must): the scope is unbounded

`config/independence.yaml` and `src/product_hunter/independence/propagation.py`.

The screen makes the common case fast, but the detector still compares every
pair, so the scope needs a stated limit.

Add `max_scope_mentions: 2000` under `propagation` in the policy, and refuse a
scope above it:

```python
if len(mentions) > policy.propagation.max_scope_mentions:
    raise ValueError(
        f"the scope holds {len(mentions)} mentions, the policy allows "
        f"{policy.propagation.max_scope_mentions}; split the scope and run it twice"
    )
```

The policy validator must refuse a non-positive value, the same way it
refuses a non-positive `max_text_chars`.

Add a test that a scope above the limit is refused and that the message names
the limit.

## G3 (Should): the stale cluster rows survive a re-grouping

`src/product_hunter/db/independence.py`, `record_author_clusters`.

The cluster id is a hash of the member list, so a cluster whose membership
changed is a different cluster and the old one is never replaced. The
member-pruning loop the last round added is unreachable, because the
membership of a written cluster can never differ from what was written.

The reviewer probed it: after a re-grouping, `author_cluster_members` still
lists an author in the group it left.

Fix it properly:

1. Before writing, collect the cluster ids the affected authors currently
   point at: `SELECT DISTINCT cluster_id FROM authors WHERE cluster_id IS NOT
   NULL AND author_id IN (...)`.
2. Write the incoming clusters and their members.
3. For every author the incoming clusters cover, set `cluster_id`,
   `identity_confidence`, and `identity_basis`.
4. Clear the `cluster_id` of an author whose previous cluster is not in the
   incoming set.
5. Delete the member rows and the cluster row of every previous cluster that
   is not in the incoming set and that no author still points at.

Keep the write idempotent: writing the same clusters twice must change
nothing and must not raise.

Add a test that re-groups the same authors differently and then asserts that
the old cluster row and its member rows are gone, that the new ones are
present, and that the author points at the new cluster.

## G4 (Should): a caller can build a metrics record and the store writes it

`src/product_hunter/independence/records.py`,
`src/product_hunter/independence/analytics.py`, and
`src/product_hunter/db/independence.py`, `record_report`.

`IndependenceMetrics` is a plain public model. The reviewer built one by
hand with `independent_author_count=99`, called `check_concentration` on it,
and the store wrote it. The invariant "no number can be supplied by a
caller" holds by docstring only.

Make the record carry proof that it came from the analysis.

In `records.py`:

```python
# The only proof that a metrics record came from analyze(). It is module
# private and is never exported, so a caller cannot produce one.
_ANALYSIS_TOKEN = object()


class IndependenceMetrics(BaseModel):
    """What the graph adds up to. Only ``analyze()`` builds one."""

    model_config = ConfigDict(extra="forbid")

    _analysis_token: object = PrivateAttr(default=None)

    ... the existing fields ...

    @classmethod
    def _built_by_analysis(cls, **values: object) -> IndependenceMetrics:
        """The one constructor. A caller cannot build a metrics record."""
        metrics = cls.model_validate(values)
        metrics._analysis_token = _ANALYSIS_TOKEN
        return metrics

    def _came_from_analysis(self) -> bool:
        """Whether the analysis built this record."""
        return self._analysis_token is _ANALYSIS_TOKEN
```

`PrivateAttr` is imported from `pydantic`.

In `analytics.py`, build the metrics with
`IndependenceMetrics._built_by_analysis(...)` instead of the plain
constructor.

In `db/independence.py`, `record_report` refuses a verdict whose metrics did
not come from the analysis:

```python
if not verdict.metrics._came_from_analysis():
    raise IndependenceError(
        "the metrics were not built by analyze(); the store will not write a "
        "number that no graph produced"
    )
```

Add a test that a hand-built metrics record is refused by the store, and that
a verdict from `analyze()` is accepted.

Note: `model_copy` keeps the private attribute, so a caller copying a real
record still passes. That is fine, the copy is still a number the analysis
produced.

## G5 (Should): the spec's §7 step 4 does not match the code

`docs/reasonix/specs/wave-2-independence.md` §7 step 4.

The spec says "If a negative signal fired, `score = min(score,
conflict_score_cap)`". The code returns exactly `conflict_score_cap` whenever
a positive signal fired beside the negative one, and produces no edge at all
when only negative signals fired. The reviewer followed the spec and computed
a score of 0.0 where the code produces 0.5 and a suspect edge.

Amend §7 step 4 to the rule the code enforces:

- a negative signal fired and no positive signal fired: no edge, and the pair
  is not counted as a conflict;
- a negative signal fired and a positive signal fired: the score is
  `conflict_score_cap` and the edge is a `same-person-suspect` with
  `conflict=True`, never a merge.

Also add the shingle screen and the scope limit to the spec §8, and the
`max_scope_mentions` value to the policy block in §5.

## G6 (Should): nothing writes `documents.live_status`

`docs/reasonix/specs/wave-2-independence.md` §6.1.

`live_status` is read by `load_mentions` and counted by
`deleted_source_mentions`, but no writer in `src/` ever sets it to `deleted`.
The reviewer notes that the "deleted sources" row of the test matrix is
satisfied by a fixture, not by a path.

State it in the spec: the collector marks a document deleted in a later wave,
and until it does, `deleted_source_mentions` counts the documents a caller
marked. Add one line to the spec's §14 assumptions.

## G7 (Nice): the pair loop re-normalises every name

`src/product_hunter/independence/identity.py`, `_fired_signals`.

`normalise_name` runs on both handles and both display names, and
`hash_identity_key` runs on both keys, once per pair. The reviewer measured
1,000 authors at 4.65 seconds, all of it repeated string work.

Precompute the normalised handle, the normalised display name, and the
hashed identity key once per author, next to the `by_id` map in
`resolve_authors`, and read them from there.

Keep the behaviour identical. The pair order and the output must not change,
and the existing tests must pass unchanged.

## Not to be done

- No new feature beyond the list above.
- No change to `pyproject.toml`.
- No change to a test's assertions except the ones the list names.
- No spec change outside §5, §6.1, §7, §8, and §14.
