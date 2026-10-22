# Fix list: Wave 2 Session 5, from the stage 6 and stage 7 passes

Every item below is a finding from the hardening pass or the adversarial
review. Fix them in this order. Do not add a feature while fixing a finding.
Do not change a test to make it pass. Do not add a dependency.

`uv run pytest` must pass when you are done.

---

## Must

### F1: `record_author` refuses every author the collector already created

`src/product_hunter/db/independence.py`, `record_author`.

The Phase 1 store (`src/product_hunter/db/store.py::_ensure_author`) creates the
author row first, with `identity_key` NULL. `record_author` compares the
stored `(identity_key, first_seen_at, last_seen_at, deletion_status)` with the
incoming tuple and raises `IndependenceConflict` on any difference, so a NULL
stored key against a hashed incoming key always mismatches. The identity key
can therefore never be attached to a real account, and the `identity_key`
strong signal can never fire on store-loaded data.

Change the write to an upsert with these rules:

- `identity_key`: a NULL stored value is unset, so fill it. A stored value
  that differs from the incoming one is a conflict and raises
  `IndependenceConflict`. A stored value that equals the incoming one is a
  no-op.
- `first_seen_at`: the earlier of the stored and the incoming value. A NULL
  stored value takes the incoming one.
- `last_seen_at`: the later of the two, by the same rule.
- `deletion_status`: `deleted` is final. A stored `deleted` is never replaced
  by `active`.

Add a test that seeds an author row the way the Phase 1 store does (identity
key NULL), calls `record_author` with a key, and asserts the key is stored
hashed. Add a second test that a different key for the same author raises
`IndependenceConflict`.

### F2: the identity key is hashed on write and compared verbatim

`src/product_hunter/independence/identity.py`, `hash_identity_key` and
`_fired_signals`; `src/product_hunter/db/independence.py`, `record_author`.

`load_authors` returns the stored hash and `_fired_signals` compares
`left.identity_key == right.identity_key`, so a raw key and its stored hash
never match, and a read-modify-write double-hashes the value.

Make the hash self-identifying and idempotent:

```python
_IDENTITY_KEY_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

def hash_identity_key(raw_key: str) -> str:
    """The stored form of an identity key: a tagged hash, never the key.

    The value is tagged so a stored hash is recognisable and hashing it again
    is a no-op. The digest is domain separated, so the stored value is not the
    plain hash of the key and a low-entropy key is not one dictionary lookup
    away from the raw.
    """
    if _IDENTITY_KEY_RE.match(raw_key):
        return raw_key
    digest = hashlib.sha256(
        f"independence-identity-key:v1|{raw_key}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"
```

Then compare through the hash in `_fired_signals`:

```python
if left.identity_key and right.identity_key:
    if hash_identity_key(left.identity_key) == hash_identity_key(right.identity_key):
        fired.add("identity_key")
    else:
        fired.add("identity_key_conflict")
```

Update the hashing test in `tests/test_independence_store.py` to assert
`stored == hash_identity_key("raw-identity-key")` and
`stored != "raw-identity-key"`, instead of comparing against a bare
`hashlib.sha256` digest.

### F3: the pair list is materialised before a single pair is compared

`src/product_hunter/independence/propagation.py::_pairs` and
`src/product_hunter/independence/identity.py::_pairs`.

Both build the full list of unordered pairs. At 10,000 mentions that is 50
million tuples, about 3.7 GB, before any comparison runs.

Turn both into generators. The call sites already iterate once.

```python
def _pairs[T](ordered: Sequence[T]) -> Iterator[tuple[T, T]]:
    """Every unordered pair, in a fixed order."""
    for index in range(len(ordered)):
        for other in range(index + 1, len(ordered)):
            yield ordered[index], ordered[other]
```

### F4: the spec's policy-validation rules are not the rules the code enforces

`docs/reasonix/specs/wave-2-independence.md` §5.1.

The spec says a policy is invalid when "suspect_threshold is not above
confident_threshold" and when "the sum of the weak signal weights reaches
merge_threshold". The shipped `config/independence.yaml` violates both
(suspect 0.5 < confident 0.6; the weak weights sum to 1.65 against a merge
threshold of 0.85), and the code accepts it because it enforces different
rules.

The code is right and the spec is wrong: the suspect threshold scores a pair
and the confident threshold scores a group, so they are not on one scale, and
the weak-signal rule is enforced in code by the score cap, which is stronger
than a sum check.

Amend §5.1 to state the rules the code enforces:

- `merge_threshold` must be above `suspect_threshold`.
- `confident_threshold` must not be above `merge_threshold`.
- `model_signal` may not reach `merge_threshold` on its own.
- no single weak signal may reach `merge_threshold` on its own.
- the effective weak ceiling, `min(sum of the weak weights, suspect_threshold)`,
  must stay below `merge_threshold`.
- `display_name` may not reach `suspect_threshold`.
- a similarity threshold is in `(0, 1]`, a concentration share is in `(0, 1]`,
  a minimum is not negative.

And say, in one line, why the pair threshold and the group threshold are not
ordered against each other.

### F5: `_read_text` can read outside the repository root

`src/product_hunter/db/independence.py`, `_read_text`.

`text_path` comes from the database. A stored absolute path or a `..`
component resolves outside the root and reads an arbitrary file into a
mention, whose text then reaches the analytics and the report.

Resolve the path and require it to sit under the root. A path that escapes
returns the empty string, exactly as a missing file does.

```python
def _read_text(self, text_path: str | None) -> str:
    """The parsed text of a document, or an empty string when it is gone.

    A stored path that leaves the repository root is refused: the path comes
    from the database, and the text it names is untrusted.
    """
    if not text_path:
        return ""
    root = self._root.resolve()
    try:
        path = (root / text_path).resolve()
    except OSError:
        return ""
    if path != root and root not in path.parents:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
```

Add a test that a document whose `text_path` is `../outside.txt` loads with an
empty text.

### F6: the migration has no rollback or recovery note

`db/migrations/0003_independence.sql`.

Spec §38B asks every migration for forward and rollback or recovery notes.
The file has a forward note and no recovery note.

Add a recovery note to the header. The migration is additive, so the recovery
is a restore, not a reverse: name the backup and name the statements that
undo it, so an operator has both.

```
-- Recovery: the migration is additive, so it is undone by restoring the
-- pre-migration backup. To reverse it in place, drop the five new tables and
-- the sixteen added columns; SQLite supports ALTER TABLE ... DROP COLUMN
-- from 3.35, and every added column is nullable or carries a default, so no
-- data is lost by dropping it.
```

---

## Should

### F7: a contradiction is dropped instead of recorded

`src/product_hunter/independence/identity.py`, `_score` and `_relation`.

A negative signal with no positive signal against it clamps the score to
zero, so no edge is produced and the conflict is never counted. The case that
matters is the same platform handle with a different identity key: the
positive signal says one person, the negative says two, and the pair
disappears.

When a negative signal fired **and** at least one positive signal fired,
produce a `same-person-suspect` edge with `conflict=True` and confidence
`conflict_score_cap`, even when the score falls below `suspect_threshold`. A
pair with only negative signals produces no edge: two accounts with different
identity keys are two strangers, not a contradiction.

Add tests for both shapes: handle plus conflicting key, and conflicting keys
alone.

### F8: the per-author confidence column holds the cluster's confidence

`src/product_hunter/independence/identity.py`, `build_author_clusters`, and
`src/product_hunter/db/independence.py`, `record_author_clusters`.

`UPDATE authors SET identity_confidence = ?` writes `cluster.confidence` for
every member, so the per-author value the spec defines in §7.1 is not
recoverable from the database.

Give `AuthorCluster` a `member_confidences: dict[str, float]` field, fill it
with the per-author confidence the cluster builder already computes, and
write that value for each member instead of the cluster's.

### F9: `load_mentions` binds one parameter per document

`src/product_hunter/db/independence.py`, `load_mentions`.

SQLite caps bound parameters. A scope of more than 32,766 documents cannot be
read, and the failure is a driver error rather than a store error.

Chunk the id list, query each chunk, concatenate the rows, and sort the
result by `doc_id`. A chunk of 500 keeps the parameter count well under the
limit.

### F10: a link that names a missing row raises a driver error

`src/product_hunter/db/independence.py`, `record_identity_link` and
`record_community_link`.

`foreign_keys` is on. A community link whose `alias_of` names a community
that is not in the table, or an identity link that names an author that is
not in the table, raises a raw `sqlite3.IntegrityError` and escapes the
store's own error contract.

Check the referenced row first. A missing row raises `IndependenceError`
naming the missing id. Add a test for each.

### F11: a changed relation collides instead of being recorded beside the original

`src/product_hunter/independence/identity.py`, `_identity_link`, and
`db/migrations/0003_independence.sql`.

The entity-resolution skill the wave implements says a later run that
disagrees with a merge does not silently delete it: it records its own method
and confidence beside the original. The link id hashes the pair and the
detection method but not the relation, and the table's unique key is
`(author_id, related_author_id, detection_method)`, so a pair that is a merge
in one run and a suspect in the next raises a conflict instead of keeping
both.

Include the relation in the link id basis, and change the unique key to
`(author_id, related_author_id, detection_method, relation)`. The migration
is not released, so change the file rather than adding a fourth migration.

### F12: the model-signal test asserts over an empty list

`tests/test_independence_identity.py`.

`model_signal` weighs 0.35 and the suspect threshold is 0.5, so the pair
produces no edge and the assertion is vacuous: the test would pass if the
resolver dropped model signals on the floor.

Assert both shapes: a model signal alone produces no merge and no edge, and
a model signal beside a second weak signal produces a suspect edge whose
`signals` include `model_signal`.

### F13: anonymous content groups are counted as suspect identity groups

`src/product_hunter/independence/identity.py` and
`src/product_hunter/independence/analytics.py`.

An anonymous content group holds no account and cannot be a person, so it
does not belong in `suspect_identity_groups`. It stays in
`uncertain_identity_groups` and `uncertain_author_count`.

Give the anonymous node its own status, `anonymous`, and exclude it from the
suspect counter. Update the test that pins the old number.

### F14: a changed cluster membership leaves stale rows behind

`src/product_hunter/db/independence.py`, `record_author_clusters`.

The cluster id is a hash of the member list, so a changed membership is a
different cluster. The old cluster row stays, its member rows stay, and an
author who left the group keeps a `cluster_id` that points at a cluster it is
no longer in.

Within the clusters the call writes: remove the member rows that are not in
the incoming membership, and clear `authors.cluster_id` for an author whose
previous cluster is not among the clusters being written.

### F15: the token set is recomputed for every pair

`src/product_hunter/independence/propagation.py`, `detect_propagation`.

`token_similarity` tokenises both texts on every call, so each mention is
tokenised 2(n-1) times instead of once.

Compute each mention's token set once, before the pair loop, and compare the
sets. Keep `token_set` and `token_similarity` exported and working on their
own; the pair loop uses the precomputed sets.

### F16: the cluster builder scans every link for every cluster

`src/product_hunter/independence/identity.py`, `build_author_clusters`.

The inner-link list is rebuilt for every cluster, which is clusters times
links. Bucket the links by member once, before the cluster loop.

### F17: the token pattern is ASCII only

`src/product_hunter/independence/propagation.py`, `_TOKEN`.

`[^0-9a-z]+` treats every non-Latin script as a separator, so a post in
Cyrillic, Greek, or Devanagari has an empty token set and is never a repost
or a syndication. Use a unicode-aware pattern and keep the two-character
length filter.

### F18: the edge direction compares timestamps as strings

`src/product_hunter/independence/propagation.py`, `_direction`.

`2026-10-21T10:00:00.500Z` sorts before `2026-10-21T10:00:00Z` because `.`
sorts before `Z`, so mixed-precision timestamps order by codepoint rather
than by time.

Parse both timestamps when both parse, compare the datetimes, and fall back
to the document id when either does not parse.

### F19: `normalise_name` folds spaces away

`src/product_hunter/independence/identity.py`, `normalise_name`.

The pattern removes spaces as well as `.` and `@`, so `ab cd` and `a bcd` are
the same account. The display name is a weak signal, so the fold is wider
than it reads.

Remove only `@` and `.`, and collapse a run of whitespace into a single
space. `Jane Doe`, `jane.doe`, and `Jane  Doe` then fold to `jane doe`, and
`ab cd` stays distinct from `a bcd`. Update the spec §7.2 text to match, and
add a test.

### F20: a mention whose author is unknown escapes the author-cluster share

`src/product_hunter/independence/analytics.py`, `_metrics`.

`cluster_of_doc` returns `None` for an author that is not in the author list,
so the mention is dropped from the numerator and kept in the denominator.
Bucket it under a per-author key so it counts against somebody.

### F21: the compared text has no size bound

`src/product_hunter/independence/propagation.py` and
`config/independence.yaml`.

A very long document makes `difflib.SequenceMatcher` run in O(n*m) on every
pair, and the token set of that document is unbounded.

Add `max_text_chars: 20000` under `propagation` in the policy, and truncate
the text that is compared to that length. A document longer than the bound is
compared on its first 20,000 characters. The bound is a policy value, not a
constant.

### F22: the spec does not record two rules the code follows

`docs/reasonix/specs/wave-2-independence.md`.

Add to §8.3 and §11.2:

- the flat `from_span_id` and `to_span_id` columns hold the first evidence
  span of each side; the complete span set is in the provenance JSON.
- the `normalise_name` rule, after F19.

---

## Not to be done

- No new feature, option, or configuration the list above does not ask for.
- No change to `pyproject.toml`.
- No change to a test's assertions except where this list names the test.
- No change to the spec outside §5.1, §7.2, §8.3, and §11.2.
