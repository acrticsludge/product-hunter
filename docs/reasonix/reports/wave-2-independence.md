# Wave 2 Session 5: source independence and entity resolution

**Branch:** `wave2/independence`
**Base commit:** `3d7bc68` (main)
**Implementation commit:** `a9974b8fc09b0927d29a3a3e73d7517d14448d6a`
**Spec:** [`../specs/wave-2-independence.md`](../specs/wave-2-independence.md)
**Plan:** [`../plans/wave-2-independence.md`](../plans/wave-2-independence.md)

---

## 1. What this session built

The independence graph and the deterministic evidence-independence analytics
from spec §12. The system distinguishes raw mentions from independent
evidence, and the number of independent people is computed by the analytics
layer, never declared by a model.

The shape:

```text
mentions, authors, communities
        |
        +--> resolve_authors()      -> same-person-merged / same-person-suspect
        +--> resolve_communities()  -> same-community-merged / same-community-suspect
        +--> detect_propagation()   -> repost / quote / crosspost / syndication
        |
        v
   build_graph()   content groups, author clusters, propagation chains
        |
        v
   analyze()       the seven numbers, plus the uncertainty counters
        |
        v
   check_concentration()  a verdict against config/independence.yaml
```

### The fifteen deliverables

| # | Deliverable | Where |
|---|---|---|
| 1 | Author (entity) resolution | `independence/identity.py::resolve_authors` |
| 2 | Community resolution | `independence/identity.py::resolve_communities` |
| 3 | Author identity confidence | `independence/identity.py::author_confidence` |
| 4 | Author links | `author_links` (existing) and `author_identity_links` (new) |
| 5 | Suspected same-person relationships | `same-person-suspect` links, and the suspect identity groups |
| 6 | Cross-post detection | `independence/propagation.py`, `same_content_cross_source` |
| 7 | Repost detection | `independence/propagation.py`, `same_content_same_source` |
| 8 | Quote and propagation detection | `independence/propagation.py`, `verbatim_quote_overlap` |
| 9 | Propagation edges | `propagation_edges`, extended by migration `0003` |
| 10 | Independence graph | `independence/graph.py::build_graph` |
| 11 | Independent-author counting | `independence/analytics.py` |
| 12 | Independent-community counting | `independence/analytics.py` |
| 13 | Source concentration | `independence/analytics.py` |
| 14 | Propagation-adjusted evidence count | `independence/analytics.py` |
| 15 | Uncertainty accounting | `independence/analytics.py`, the six counters |

### Every relationship keeps its evidence

`AuthorIdentityLink`, `CommunityLink`, and `PropagationEdge` all carry the
detection method, the confidence, the provenance, the timestamp, and the
source and evidence ids. The provenance names the platforms, the documents,
and the evidence spans the relationship rests on.

## 2. The numbers, and what they are not

| Number | Meaning |
|---|---|
| `raw_mentions` | the in-scope documents. One mention is one observed post, not one extracted quote |
| `canonical_content_count` | the components over the repost, crosspost, and syndication edges |
| `independent_author_count` | the resolved, confident identity groups |
| `uncertain_author_count` | every other identity group, counted once each, including the authorless ones |
| `independent_community_count` | the canonical communities that contribute a root observation |
| `propagation_adjusted_evidence` | the canonical content groups that are not derived |
| `source_concentration` | the largest share of the mentions held by one source |
| `author_cluster_concentration` | the largest share of the mentions held by one identity group |

The concentration shares all use the raw mention count as their base, so the
four of them are comparable and a report can put them in one table. The
community and propagation-chain concentrations are computed the same way and
are reported alongside them.

### The model never supplies a number

`IndependenceMetrics` is built only by `analyze()`, from the graph and from
nothing else. A model may supply an `IdentitySignal`; the resolver applies the
configured weight to it and decides the relation itself. A model signal alone
can never merge two accounts, and the guarantee is in code rather than in the
configuration: a pair with no strong signal is capped at the suspect
threshold, so the merge threshold is unreachable from weak signals whatever
the weights say. The policy validator refuses a configuration that would let a
weak signal or a model signal reach the merge threshold on its own.

## 3. The concentration check

The limits are configuration, not code. `config/independence.yaml`:

```yaml
concentration:
  max_single_source_share: 0.8
  max_single_community_share: 0.6
  max_single_author_cluster_share: 0.4
  max_single_propagation_chain_share: 0.3
  min_independent_authors: 10
  min_independent_communities: 3
```

`check_concentration()` returns a verdict with one violation per broken rule,
each carrying the observed value and the limit that was broken. The two
minima mirror the validation thresholds in `config/research_contract.yaml`, and
a test asserts the two files agree so neither drifts silently.

## 4. Schema

`db/migrations/0003_independence.sql` is additive. It adds five tables
(`author_identity_links`, `community_links`, `author_clusters`,
`author_cluster_members`, `independence_reports`) and extends three
(`authors` with eight columns, `documents` with two, `propagation_edges` with
six). Migrations `0001` and `0002` are untouched.

The identity key column holds a tagged hash, never a raw key. The hash is
domain separated (`independence-identity-key:v1|`) and self-identifying
(`sha256:<64 hex>`), so hashing a stored value again is a no-op and the
stored value is not one dictionary lookup away from a low-entropy raw key.

`research/persistence.py` names a planned `0003_research_gate.sql`. This wave
took `0003` because the runner demands contiguous numbers and `0003` was the
next free one. The wave that persists tool calls must take `0004` or later
and must correct that docstring.

## 5. Tests

```
uv run pytest
1061 passed in 16.47s
```

The independence suite is 81 of those: 30 identity, 22 propagation, 12
analytics, 17 store and migration.

| Case | Test |
|---|---|
| same author | `test_one_account_with_many_mentions_is_one_independent_author` |
| different authors | `test_two_accounts_are_two_independent_authors` |
| uncertain identity | `test_a_display_name_match_alone_creates_no_edge` |
| reposts | `test_five_copies_of_one_post_collapse_to_one_observation` |
| quote chains | `test_a_quote_chain_has_one_root_and_two_derived_observations` |
| cross-posts | `test_the_same_content_on_two_platforms_is_one_canonical_content` |
| syndication | `test_a_near_duplicate_is_a_syndication` |
| every relation | `test_every_relation_is_detected` |
| independent communities | `test_three_communities_are_three_independent_communities` |
| single-community concentration | `test_every_mention_in_one_community_breaks_the_community_rule` |
| single-source concentration | `test_one_source_holding_every_mention_breaks_the_source_rule` |
| author-cluster concentration | `test_one_author_holding_every_mention_breaks_the_cluster_rule` |
| propagation | `test_a_chain_of_copies_breaks_the_chain_rule` |
| missing author identity | `test_a_mention_with_no_author_is_uncertain_and_never_independent` |
| deleted sources | `test_a_deleted_mention_stays_in_the_counts_and_is_reported_as_deleted` |
| conflicting identity signals | `test_a_contradicted_pair_is_a_suspect_and_never_a_merge`, `test_a_conflicting_key_beside_a_shared_handle_is_recorded_as_a_conflict`, `test_conflicting_keys_alone_are_two_strangers_not_a_contradiction` |
| model signal | `test_a_model_signal_alone_never_merges`, `test_a_model_signal_beside_a_second_weak_signal_is_a_suspect`, `test_a_caller_may_not_supply_a_strong_signal` |
| weak signals | `test_weak_signals_cannot_merge_even_when_their_weights_add_up` |
| policy validation | `test_the_shipped_policy_loads_and_is_valid`, `test_an_invalid_policy_is_refused_and_names_the_rule`, `test_a_policy_file_that_is_not_yaml_is_refused` |
| configurable limits | `test_the_limits_come_from_the_configuration` |
| contract agreement | `test_the_policy_minima_mirror_the_research_contract` |
| no model-declared number | `test_the_numbers_are_a_function_of_the_graph` |
| determinism | `test_the_same_graph_always_gives_the_same_numbers`, `test_the_detection_is_the_same_every_time_it_runs` |
| propagation does not move the author count | `test_the_propagation_does_not_change_the_author_count` |
| relationship evidence | `test_every_edge_carries_its_method_confidence_provenance_and_timestamp` |
| persistence | `test_the_relationships_and_the_report_survive_a_round_trip`, `test_a_changed_record_under_the_same_id_is_refused` |
| identity key hashing | `test_the_identity_key_is_hashed_before_it_is_written`, `test_a_stored_hash_and_the_raw_key_compare_equal` |
| path boundary | `test_a_text_path_that_leaves_the_root_reads_nothing` |
| migration | `test_the_migration_applies_to_a_fresh_database`, `test_the_migration_applies_to_a_version_two_database` |

`tests/test_db_migrations.py` was edited: four version constants moved from 2
to 3, because the runner now applies three files. No assertion was weakened,
skipped, or deleted. The same file still asserts `== 2` at line 95, where it
uses its own temporary migrations directory.

## 6. What the passes found

The loop ran the code gate, a hardening pass, and two adversarial review
rounds. The first review returned 22 findings: 6 Must, 10 Should, 6 Nice. All
22 were fixed, and a second hardening pass confirmed 18 of the 19 code fixes in
place and clean, with one Nice finding left: the store compared
`first_seen_at` and `last_seen_at` as strings, so a millisecond timestamp and
a plain one ranked by codepoint. That was fixed by comparing the instants, and
a test now covers it.

The second review returned 7 findings: 1 Must, 4 Should, 2 Nice. All 7 were
fixed.

Three findings changed the design rather than a line of code:

- **The spec contradicted itself.** §5.1 said a policy is invalid when the
  suspect threshold is above the confident threshold, and when the weak signal
  weights sum to at least the merge threshold. The shipped policy violates
  both rules and the code accepted it, because the code enforces different and
  better rules. The spec was amended to state the rules the code enforces.
  The pair threshold and the group threshold score different things and are
  not on one scale; the weak-signal guarantee is enforced by a score cap in
  the resolver, which is stronger than a sum check.
- **The identity key was hashed on write and compared verbatim.** A raw key
  and its stored hash never compared equal, so the one merge path that does
  not depend on a normalised handle could never fire on store-loaded data, and
  a read-modify-write double-hashed the value. The hash is now tagged and
  idempotent, and the comparison goes through the hash.
- **The quote matcher was quadratic in the text length.** The review measured
  one 20,000-character pair at 10.8 seconds, and 400 mentions of 300
  characters at 89.7 seconds. The matcher now sits behind a shingle screen: a
  quote needs a verbatim block of at least 24 characters, so a pair whose
  texts share no 24-character run cannot earn one. The screen is a necessary
  condition, so it never rejects a real quote, and the matcher still decides
  the pair. 400 distinct mentions of 320 characters went from about 72
  seconds to 0.22 seconds. A scope limit of 2,000 mentions is now policy, and
  the detector refuses a scope above it.
- **A caller could write a number no graph produced.** `IndependenceMetrics`
  was a plain public model, so a hand-built one with
  `independent_author_count=99` reached the report table. The metrics record
  now carries a module-private token that only `analyze()` holds, and the
  store refuses a verdict that does not carry it.

### What was verified by hand, after the last fix round

Three things the last round could have got wrong were probed directly rather
than trusted to the new tests.

- The shingle screen is sound. For seven policy combinations, the screen
  length is never longer than the shortest block a quote can earn, so the
  screen can never reject a real quote. The proof is that equal strings hash
  equal, so two texts that share a run of the screen length share a shingle
  hash.
- The metrics token closes the hole. The token is not reachable from the
  package namespace, a hand-built metrics record does not carry it, and the
  store refuses to write one. The probe wrote zero reports.
- The cluster write leaves a consistent state. After a re-grouping, the
  cluster rows, the member rows, and the per-author `cluster_id` all name the
  same group, the group the author left is gone, and writing the same set
  again changes nothing.

### What the lean pass left, and why

The review named eight lines it would delete: the `_pairs` generator is
duplicated in `identity.py` and `propagation.py`, and the group hash is
recomputed in `graph.py`. The duplication is deliberate for now. The house
already carries five identical copies of the `_hash_id` helper, one per
module, and the review said consolidating those is not this wave's job.
Removing the two `_pairs` copies needs a new shared module and five import
edits to a package that has just been reviewed and tested, for six lines. The
debt is recorded rather than paid at ship time.

The `shortcut:` ledger for this wave is empty: the new code declares no
deferred shortcut. The four markers in the repository are pre-existing and
none of them is in this wave's radius.

## 7. Assumptions

- **A mention is a document, not an extracted quote.** The spec's worked shape
  is `147 mentions -> 91 canonical observations`. A count that counted
  extracted quotes would collapse for a reason that has nothing to do with
  independence. A SubQ Snap call was asked to choose between the two: the
  document reading won at 0.65 against 0.35, on a confidence of 0.31. The
  margin is thin and the choice is a judgement call, not a settled fact.
- **The identity key comes from the collector, already opaque.** The package
  hashes it again before storage, so a raw key never lands in the database, a
  log, an exception message, or a report.
- **Cross-linking between accounts and signature reuse are caller-supplied
  signals.** The package does not scrape profiles to find them. A caller may
  not supply a strong signal; the resolver refuses one.
- **The propagation detector is text-only.** It uses the token-set similarity
  and the longest verbatim block, with no Qdrant. A paraphrase reads as a
  separate canonical content, so the error is under-counting propagation, not
  over-counting independence.
- **The `normalise_name` fold removes `@` and `.` and keeps a word
  boundary.** `Jane Doe`, `jane.doe`, and `Jane  Doe` fold to `jane doe`;
  `ab cd` and `a bcd` stay apart.
- **The compared text is bounded.** `propagation.max_text_chars` is 20,000. A
  longer document is compared on its head. A scope above
  `propagation.max_scope_mentions`, which is 2,000, is refused with a message
  naming the limit.
- **A suspect group is one possible person, not two.** The identity groups are
  the connected components over the merge and suspect edges together, so a
  suspected pair is counted once, in the uncertain column.
- **A deletion is a status, not a removal.** A deleted mention stays in every
  count, and `deleted_source_mentions` says how much of the count rests on
  content that can no longer be re-checked.

## 8. Unresolved identity limitations

These are the limits of what the wave can decide, stated so a later reader
does not read more into a number than it holds.

1. **Pseudonymous handles are the ceiling.** The only strong signals are a
   shared identity key and the same handle on the same platform. A collector
   that cannot supply an identity key leaves the resolver with weak signals
   alone, and weak signals can never merge. Two accounts of the same person on
   two platforms therefore stay two uncertain groups, not one person.
2. **The number of real people is a range, never a point.** The report
   carries the independent count and the uncertain count side by side. The
   honest reading is `independent` to `independent + uncertain` distinct
   people, and the uncertain column is never folded into the independent one.
3. **A paraphrase is invisible to the detector.** Without embeddings, a
   reworded copy is a separate canonical content. The chain concentration
   check therefore under-counts a paraphrase chain.
4. **A cross-platform alias is only as good as the collector.** Community
   resolution joins two platforms on an explicit alias or a matching name.
   A community that answers to two names on one platform stays two
   communities, and the community count is an upper bound.
5. **The resolution is quadratic in the accounts.** Every pair of accounts is
   compared, and the pair list is a generator now, so the memory is bounded,
   but the time is not. 2,000 accounts took 8.7 seconds to resolve in the
   measured run; 10,000 would take minutes. A scope that large needs a
   blocking prefilter, which this wave did not build.
6. **The propagation detector is quadratic in the mentions.** 1,000 mentions
   took about 70 seconds to compare before the shingle screen, and the screen
   brought 400 mentions of 320 characters down to 0.22 seconds. The pair
   count is still quadratic, so the scope limit of 2,000 mentions is what
   keeps the cost bounded, and a corpus larger than that needs a scope split
   or an inverted index.
7. **The cluster write is whole-set.** `record_author_clusters` writes the
   complete set of identity groups and ungroups every author whose cluster is
   not in that set. A run that analysed part of the corpus must not call it,
   or it clears the groups of the authors it did not analyse. The docstring
   says so; the store does not enforce it.
8. **The DuckDB surface is not built.** The numbers are computed in Python
   over SQLite. Spec §16 makes DuckDB the analytical layer, and that
   materialisation is a later wave.
9. **The research gate is not wired to these numbers yet.** The gate already
   declares the `author` and `community` artifact kinds, and the
   `validation` stage already asks for `independent_person_count` and
   `community_count`. Nothing in the pipeline produces those artifacts from
   this package yet.
10. **Nothing writes `documents.live_status`.** The deletion counter reads
    it, and no writer in `src/` sets it to `deleted` yet. The collector does
    that in a later wave, so until it does, `deleted_source_mentions` counts
    the documents a caller marked by hand.
11. **The identity key hash is unsalted.** A domain separator is applied, so
    the stored value differs from the plain hash of the key. A low-entropy key
    is still guessable by anyone who knows the domain prefix. An HMAC with a
    secret from the environment would close it, and that needs a secret store
    this wave did not introduce.

## 9. Not built, on purpose

Scoring, market sizing, competitor analysis, Obsidian projection, Qdrant
embedding, DuckDB materialisation, and a CLI command. The package is a
library, like the rest of `src/product_hunter/research/`.

## 10. Where the next session starts

- `docs/reasonix/handoffs/wave-2-independence-handoff.md`: the repo map.
- `docs/reasonix/prompts/wave-2-independence-fixes.md` and
  `docs/reasonix/prompts/wave-2-independence-fixes-2.md`: the 29 findings and
  what each fix was, for a reviewer who wants to check the fixes.
- `docs/reasonix/ARCHITECTURE.md`: updated for the new tables, the new
  subsystem, and the migration number.
