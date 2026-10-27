# Final Audit 07: Analytical Intelligence Layer

**Probe:** Final Audit #7
**Date:** 2026-10-22
**Branch / HEAD:** `main` @ `00a6ca3` ("fix: close the second review round")
**Adapter:** Product Hunter [repo] (`.loop/adapter.json`, 8 forbidden rules, floor risk medium)
**Mode:** READ-ONLY. No source, test, config, migration, or schema file was modified.
**Code gate:** `uv run pytest -q` → **PASS**, 0 failures (run after the audit, tree unchanged).

**Path note.** The task asked for `docs/resonix/reports/final-audit-07-intelligence.md`.
The repository's docs root is `docs/reasonix/`, and probes 01, 02, 03, 04, 05, 08, 09
and 10 had already written their reports to `docs/reasonix/reports/`. Writing to a
second parallel tree would have split the audit set, so this report follows the
repo convention and the sibling probes. Nothing else was touched.

---

## 0. Scope and method

### 0.1 What was audited

The analytical intelligence layer, in the order the task named it:

```text
Pain Points -> Clusters -> Authors/Entities -> Propagation
            -> Independent Evidence -> Validation -> Potential
            -> Confidence -> Opportunity Lifecycle
```

| Area | Files |
|---|---|
| Clustering | `src/product_hunter/clustering/{engine,clusters,problems,similarity,adjudication}.py`, `src/product_hunter/db/clustering.py` (1422 L), `db/migrations/0006_clustering.sql`, `config/clustering.yaml` |
| Independence | `src/product_hunter/independence/{identity,propagation,policy,graph,analytics,records}.py`, `src/product_hunter/db/independence.py` (675 L), `db/migrations/0007_independence.sql`, `config/independence.yaml` |
| Scoring | `src/product_hunter/opportunity/{scoring,signals,policy,snapshot}.py`, `config/scoring_policy.yaml` |
| Lifecycle | `src/product_hunter/opportunity/lifecycle.py`, `src/product_hunter/research/investigation/opportunity.py` |
| Validation (producers of the scoring inputs) | `src/product_hunter/extraction.py`, `src/product_hunter/evidence.py`, `src/product_hunter/research/investigation/*`, `src/product_hunter/research/gate.py`, `db/migrations/0005_evidence_pipeline.sql`, `config/research_contract.yaml` |

Authoritative requirement source: `docs/reasonix/specs/Product-Hunter-Full-Spec.md`
§11 (Evidence Model, line 925), §12 (Independent Evidence, line 975), §20 (Opportunity
Scoring, line 1408), §21 (Counter-Evidence, line 1512), §23 (Opportunity Lifecycle,
line 1608), §24 (Opportunity History, line 1661).

### 0.2 Method

Every claim below was checked by reading the code, not by trusting a docstring. The
high-risk findings were re-verified independently by the orchestrator with direct
`grep`/`read` against the working tree; those are marked **[V]** below. Behavioural
claims that required running code were confirmed with read-only probes against
in-memory SQLite and the shipped YAML configs; no file was written.

Three findings were additionally measured first-hand against the shipped policy
and reported with their probe output rather than with a description: the
counter-evidence credit (M-05), the niche-override band collapse and point value
(S-09), and the four concentration scenarios (M-04, section 6.4). All three probes
ran with `PYTHONDONTWRITEBYTECODE=1` and `python -B` against a temporary script
outside the repository; no repository file was created, modified, or deleted.

### 0.3 Worktree reconnaissance (all 8 Wave 3 branches located)

The Wave 3 worktrees are isolated at paths that differ from the original instructions.
All eight were located via `git worktree list --porcelain`:

| # | Path | Branch | HEAD |
|---|---|---|---|
| 1 | `C:\Anubhav\Web Dev Projects\product-hunter-w3a` | `wave3/autonomous-operation` | `1efc328` |
| 2 | `C:\Anubhav\Web Dev Projects\product-hunter-w3c` | `wave3/source-expansion` | `a9540ee` |
| 3 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-analytics` | `wave3/duckdb-analytics` | `3474e58` |
| 4 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-observability` | `wave3/observability-evaluation` | `317e7f2` |
| 5 | `C:\Anubhav\Web Dev Projects\product-hunter-wt\wave3-scraping-ladder` | `wave3/scraping-ladder` | `5f66d46` |
| 6 | `C:\Users\anubh\.local\share\opencode\worktree\44767c\wave3\subq-integration` | `wave3/subq-integration` | `172a74f` |
| 7 | `C:\Users\anubh\AppData\Local\Temp\opencode\wt-wave3-qdrant` | `wave3/qdrant-semantic` | `357dd5e` |
| 8 | `C:\Anubhav\Web Dev Projects\product-hunter\.worktrees\wave3-opencode-control-plane` | `wave3/opencode-control-plane` | `5e45c63` |

**No Wave 3 branch touches the intelligence layer.** `git diff --name-only main...<branch>`
for all eight returns zero files under `clustering/`, `independence/`, `opportunity/`,
`db/clustering.py`, or `db/independence.py`. The only match anywhere is the spec
document `docs/reasonix/specs/wave-2-clustering.md` on the qdrant branch. The layer
audited here is therefore the complete and only version of it: the Wave 2 code
integrated at `edd557e` and closed out at `00a6ca3`.

---

## 1. Headline

The layer is carefully built and mostly wrong in a specific way: **it is a library
with no pipe attached, and the parts that are wired are wired to the wrong
implementation.**

Three findings dominate everything else:

1. **Nothing in the product constructs the scoring inputs.** Outside `tests/`, the
   only occurrences of `PotentialInputs(`, `ConfidenceInputs(`, `EvidenceItem(`,
   `score_opportunity(`, `build_snapshot(` and `apply_transition(` are the class and
   function definitions themselves. The entire potential/confidence/lifecycle layer
   is exercised by tests only. **[V]**
2. **The `independence/` package is not the independence the score reads.** The
   scorer calls `opportunity/signals.py::compute_independence`, a second and weaker
   implementation with no identity clustering, no canonical communities, no hard
   concentration caps, and no link between its inputs and the propagation graph.
   Fixing the `independence/` package would not change a single number the product
   emits. **[V]**
3. **The independent-author, independent-community, canonical and propagation
   fields that feed the confidence score are all caller-asserted.** A producer that
   writes `is_canonical=True, propagation_confidence=0.0, identity_confidence=1.0`
   for every collected document, including fifty reposts of one post and fifty
   accounts of one person, produces fifty independent authors and fifty independent
   communities. Full-Spec §11 says "Do not count repeated copies of the same content
   as independent evidence" and §12 says "Independence is a computed property, not an
   LLM-provided number." Neither holds on the path that feeds the score.

Scoring arithmetic itself is sound where it exists. The potential/confidence
separation is real, the factors are deterministic, the WTP tier is capped by its
signals, the market mapping is logarithmic and clamped, and the counter-evidence
floor cannot be lowered by a caller. The failures are in what the factors are
*fed* and in what the state machine is *not*.

---

## 2. Verdict by claim

### 2.1 Clustering

| # | Claim | Verdict | Note |
|---|---|---|---|
| 1 | Versioned clusters | **PASS** | `clusters.version`, insert-only `cluster_versions` snapshots, queryable via `ClusterStore.cluster_versions()` (`db/clustering.py:844`). |
| 2 | Merge/split history | **PARTIAL** | Explicit API records edges append-only. The automatic path writes none. **M-09** |
| 3 | Membership validity | **PASS with a gap** | One-open-membership rule enforced by a partial unique index *and* a Python guard *and* write ordering. A cluster absent from a new result keeps stale open memberships. **S-02** |
| 4 | Deterministic IDs | **PASS for cluster IDs** | `sha256` of a sorted basis (`clusters.py:194-217`); no uuid, random, or clock in the ID path. Membership *row* IDs embed `valid_from`. **N-05** |
| 5 | Evidence preservation | **PASS** | The clustering layer never writes `evidence`, `evidence_pain_links` or `pain_points`. Both predecessor and successor resolve to the same `span_id` and `quote` after a merge. |

### 2.2 Independence

| # | Claim | Verdict | Note |
|---|---|---|---|
| 1 | Author identity confidence | **PASS in `independence/`** | Deterministic signal weighting, no model import anywhere in the package. The scorer's copy is a bare caller-asserted float. **M-02, M-03** |
| 2 | Same-person links | **PARTIAL** | Explicit, transitive, auditable edges, and a real guard stops the model asserting a `MERGED` edge. No runaway-merge guard; cross-platform humans are never merged. **S-05** |
| 3 | Repost/quote/crosspost propagation | **PARTIAL** | Detected well. Discounted only for `QUOTE`-derived content groups; reposts land in the same content group as the original and are not discounted. **S-04** |
| 4 | Independent author counts | **FAIL on the wired path** | Counts raw `author_id` strings, not identity clusters. **M-02, M-03** |
| 5 | Community diversity | **FAIL on the wired path** | Raw `community_id` strings, no canonicalisation, no propagation discount. **M-02, M-03** |
| 6 | Concentration limits | **FAIL on the wired path** | Four hard caps exist in `config/independence.yaml` but are enforced only inside the package the score does not read. **M-04** |
| 7 | Uncertain identities | **PASS** | Filtered before counting, never folded into the independent count, cannot claim high confidence. |
| 8 | Deterministic calculations | **PASS** | No model, no randomness, no clock, stable sorts, `math.fsum` for the float sums. |
| 9 | No LLM-declared independence | **PASS in `independence/` / FAIL on the wired path** | No `llm` import in the package. The scorer's inputs are plain caller fields with no join to any of it. **M-03** |

### 2.3 Scoring

| # | Claim | Verdict | Note |
|---|---|---|---|
| 1 | Potential and Confidence separate | **PASS structurally** | No field of one is read by the other; verified by test. But the separation is by *type*, not by *evidence*: opaque `evidence_ids` cross the boundary unchecked. **M-10, S-07** |
| 2 | Severity | **PASS arithmetic / FAIL provenance** | Deterministic `max` over 0..5 ints. The values are unchecked model declarations. **M-11** |
| 3 | Independent population / frequency | **PASS** | Population uses independent counts, not raw counts. Modal frequency tie-break is documented and order-independent. |
| 4 | WTP | **PARTIAL** | The tier is capped by the strongest signal at score time and fails closed. The *signals* are assertions with evidence IDs that are never joined to the evidence set. **M-10** |
| 5 | Reachable market | **PASS** | Log-scaled, clamped 0..1, bottom-up formula implemented literally. No plausibility bound on the four assumptions. **S-13** |
| 6 | Competitive gap | **PASS** | No search ⇒ neutral factor, recorded as an override. Zero found ⇒ capped below the tailwind. The cap (0.7) is still above neutral (0.55). **S-14** |
| 7 | Buildability | **PASS** | `feasibility − 0.1 × blockers`, floored at 0. The inputs carry no evidence references. **S-15** |
| 8 | Evidence quality | **PASS** | Reliability-weighted mean, copies discounted by `1 − propagation_confidence`, `math.fsum` sums. |
| 9 | Confidence (six factors) | **PASS** | All six in 0..1, deterministic, each with a provenance row. Two of the six carry no record IDs. **N-04** |
| 10 | Temporal persistence | **PASS** | Window, future-date refusal, staleness cutoff all present and tested. |
| 11 | Counter-evidence | **FAIL on one branch** | The floor cannot be lowered. But `contradictions_checked == 0` earns *full* credit. **M-05** |
| 12 | Extraction agreement | **PARTIAL** | The extractor-count discount is right. There is no consensus computation anywhere in the repo, and the premise cannot arise. **S-06, S-08** |
| 13 | Niche override | **PASS with two caveats** | Trigger and bound are correct, and it touches only the market factor. It collapses the plausible range to a point estimate and lifts the market factor. **S-09** |
| 14 | Plausible market ranges | **PASS mechanically** | `low <= base <= high` enforced, empty band refused, three runs. The band is entirely caller-asserted and has no ratio plausibility check. **S-16** |
| 15 | Trend a separate signal | **PASS** | Not a field of either input model, read by neither scorer, reported beside the two numbers. |

### 2.4 Lifecycle

| # | Claim | Verdict | Note |
|---|---|---|---|
| 1 | All 11 states defined | **PASS** | `lifecycle.py:19-35` matches Full-Spec §23 exactly. |
| 2 | Explicit transition table, enforced | **PASS** | `ALLOWED_TRANSITIONS` (`:41-90`), illegal edges raise `TransitionRefused`. |
| 3 | Resurrection gated | **PARTIAL** | Requires `new_evidence_count > 0` and a reason, and re-enters `VERIFYING`. The count is a caller-asserted integer that is never compared against the evidence. **S-11** |
| 4 | `DEAD` / `STALE` terminality | **MIXED** | `DEAD` and `ALREADY_SOLVED` are hard terminals. `VALIDATED` is not: it leaks to `STALE`, then to `RESURRECTED`, and can be re-validated without a human being asked again. **S-12** |
| 5 | Enforced at the DB boundary | **FAIL** | No opportunity table, no state column, no CHECK, no trigger. **M-06** |
| 6 | Transition history | **FAIL** | No event model, no actor, no per-transition timestamp. Full-Spec §24 is unimplemented. **M-06** |
| 7 | Score thresholds, `HUMAN_REVIEW` mandatory | **PARTIAL** | The *state* is mandatory for `VALIDATED` and the promotion gate reads the engine's own `ConfidenceScore`. The *human* is a non-empty string. **M-07** |
| 8 | Terminal states really terminal | **PARTIAL** | Two of four are. **S-12** |
| 9 | Every state change recorded | **FAIL** | No history exists, so none is. **M-06** |
| 10 | Single state machine | **FAIL** | Two `OpportunityState` enums with different tables, both publicly exported. **M-08** |

---

## 3. Findings: Must

### M-01: The scoring and lifecycle layer has no producer and no consumer

**Severity:** Must
**Where:** repo-wide

Outside `tests/`, the only matches for `PotentialInputs(`, `ConfidenceInputs(`,
`EvidenceItem(`, `ExtractionAgreement(`, `score_opportunity(`, `build_snapshot(`,
`apply_transition(` and `evaluate_transition(` are the class and function
definitions. **[V]**

```text
src/product_hunter/opportunity/signals.py:53    class EvidenceItem(BaseModel):
src/product_hunter/opportunity/signals.py:349   class PotentialInputs(BaseModel):
src/product_hunter/opportunity/signals.py:365   class ConfidenceInputs(BaseModel):
src/product_hunter/opportunity/scoring.py:466   def score_potential(...)
src/product_hunter/opportunity/scoring.py:681   def score_confidence(...)
src/product_hunter/opportunity/scoring.py:818   def score_opportunity(...)
src/product_hunter/opportunity/lifecycle.py:351  def apply_transition(...)
```

**Consequence.** The potential, confidence and lifecycle numbers this repository
produces today are fixture numbers. Every other finding in sections 2.3 and 2.4
describes a design gap in code that no product path currently executes, which
changes their priority but not their substance: the gaps are in the contract, and
the contract is what the producer will be written against.

---

### M-02: The scorer does not read the `independence/` package

**Severity:** Must
**Where:** `opportunity/scoring.py:698`, `opportunity/signals.py:430-481`, `db/independence.py:48`, `db/__init__.py:15`

```python
# opportunity/scoring.py:698
independence = compute_independence(inputs.evidence, policy)
```

`compute_independence` lives in `opportunity/signals.py`. `IndependenceStore` and
`independence.analyze()` are referenced only by `db/independence.py` and the
`db/__init__.py` re-export. Nothing calls them. **[V]**

| | `independence/` package | `opportunity/signals.py` (the one in use) |
|---|---|---|
| unit of counting | identity cluster (connected components) | raw `author_id` string |
| cross-platform same human | 2 | 2 (no clustering at all) |
| community identity | canonical community id | raw `item.community_id` |
| propagation discount | quote-derived groups, canonical-content roots | `is_canonical` boolean, caller-asserted |
| concentration | 4 hard caps with a violations list | Herfindahl penalty only |
| `min_independent_communities` | enforced | never enforced |
| who may set the fields | the graph, by construction | the caller, by assertion |

The `independence/` package's own guarantee that "only `analyze()` builds a metrics
record" (`records.py:180-189`, enforced at `db/independence.py:524-528` by a
module-private token) does not extend to the score path.

---

### M-03: False independence: the four fields that create independence are caller-asserted

**Severity:** Must
**Where:** `opportunity/signals.py:53-94`

```python
# opportunity/signals.py:72-74
identity_confidence: float = Field(ge=0, le=1)
is_canonical: bool = True
propagation_confidence: float = Field(default=0.0, ge=0, le=1)
```

`community_id` (`:68`) is the fourth. The only guard is an internal consistency
check (`:82-94`): a canonical item carries `propagation_confidence == 0.0`, a copy
carries `> 0.0`. Nothing ties any of the four to the propagation graph, to
`author_confidence()`, to a canonical community, or to the `authors` table's
`identity_confidence` column (which exists, at
`db/migrations/0007_independence.sql:75`, and is never read into an `EvidenceItem`).

**Exploit.** A producer that emits, for every collected document:

```text
is_canonical = True, propagation_confidence = 0.0, identity_confidence = 1.0
```

yields `independent_author_count = N`, `independent_community_count = N`,
`effective_population = N.0`, no concentration cap, and no notion of a satisfied
check. Fifty reposts of one post by one person, or fifty sockpuppet accounts, are
fifty independent people.

**Against the spec.** §11: "Do not count repeated copies of the same content as
independent evidence." §12: "Independence is a **computed property**, not an
LLM-provided number." §12 also requires that the system "discount suspected
propagation and uncertain identity rather than treating every visible account as a
new independent person." On the scored path, every visible account is a new
independent person unless the caller says otherwise.

---

### M-04: Concentration bypass: the caps exist, are stored, and are never checked

**Severity:** Must
**Where:** `config/independence.yaml:30-35`, `independence/analytics.py:45`, `db/independence.py`, `opportunity/scoring.py:671-678`

The shipped independence policy declares four hard caps and two minimums:

```yaml
concentration:
  max_single_source_share: 0.8
  max_single_community_share: 0.6
  max_single_author_cluster_share: 0.4
  max_single_propagation_chain_share: 0.3
  min_independent_authors: 10
  min_independent_communities: 3
```

These are enforced in `independence/analytics.py`, which produces a `satisfied`
verdict and a violations list. That verdict is written to the `independence_reports`
table. Grepping `src/` for `independence_reports` finds it in the migration and in
`db/independence.py` only: **no gate, no pipeline stage, no CLI reads the table.**

The score path uses a different and much weaker mechanism, a Herfindahl penalty:

```python
# opportunity/scoring.py:671-678
def _concentration_penalty(concentration: float, policy: ScoringPolicy) -> float:
    neutral = policy.concentration.neutral_index      # 0.34
    maximum = policy.concentration.max_penalty        # 0.5
    if concentration <= neutral:
        return 1.0
    penalty = maximum * (concentration - neutral) / (1.0 - neutral)
    return max(1.0 - maximum, 1.0 - penalty)
```

Two independent problems. First, the penalty is the only concentration control on
the scored path, so the four share caps never bind. Second, the index it consumes
is **item-weighted, not author-weighted**: `community_counts[item.community_id] += 1`
counts mentions (`signals.py:454-459`), so a community with 100 mentions from 3
authors outweighs a community with 5 mentions from 5 authors. It therefore
disagrees with the community-diversity factor it is meant to bound.

The extreme case is partly rescued by accident: a single community holding
everything gives an index of 1.0 and a 0.5 penalty, which caps the confidence score
at 50 and therefore blocks the 55-point promotion gate. A *moderately* dominant
source, at a share of 0.5 across five sources (index 0.3125, below the neutral
0.34), draws no penalty at all.

**Measured, first-hand** (12 canonical items, 12 distinct authors, policy defaults;
promotion gate is 55 confidence points):

```text
A 12 authors / 12 comms / 12 sources            indep_auth=12 indep_comms=12 conc=0.0833 penalty=1.00 CONF=85.92
B 12 authors / 1  community / 12 sources         indep_auth=12 indep_comms= 1 conc=1.0000 penalty=0.50 CONF=37.96
C 12 authors / 4  comms / 1  source              indep_auth=12 indep_comms= 4 conc=1.0000 penalty=0.50 CONF=42.96
D 12 authors / 4  comms / 5 sources (one at 50%) indep_auth=12 indep_comms= 4 conc=0.3194 penalty=1.00 CONF=85.42
```

B and C are caught, by the penalty. D is not: one source carrying half of all the
evidence lands at index 0.3194, a hair under the neutral 0.34, and draws no
penalty at all, leaving the confidence at 85.42.

**The case that is not caught at all is A.** Twelve accounts that are really one
person's twelve accounts are, on the scored path, twelve independent authors in
twelve communities with no concentration whatsoever. `independence.yaml` has
`max_single_author_cluster_share: 0.4` for exactly this, and the scored path has
no identity clustering to apply it to, so the sockpuppet ring is the *best*
scoring shape available rather than the worst.

---

### M-05: `contradictions_checked == 0` earns full counter-evidence credit

**Severity:** Must
**Where:** `opportunity/scoring.py:626-649`

```python
if summary.contradictions_checked <= 0:
    contradiction_rate = 0.0
else:
    contradiction_rate = (summary.contradictions_found / summary.contradictions_checked)
return coverage * (1.0 - contradiction_rate), required
```

The function's own docstring states the rule it is supposed to enforce: "A check
that found nothing contradicting cannot lower the score, and a check that never ran
contributes nothing to it." The implementation does the opposite of the second
clause. A caller asserting `search_ran=True, families_covered=3,
families_required=3, contradictions_checked=0, contradictions_found=0` gets
coverage `1.0 × (1 − 0.0) = 1.0`, the maximum, which is 15% of the confidence
weight (`config/scoring_policy.yaml`). A check that checked nothing contributes
everything.

`summary.search_ran` is never read by the scorer at all. The only use of the flag
is the `CounterEvidenceSummary` invariant at `signals.py:333-346`, which refuses
`families_covered > 0` when `search_ran` is false. The flag therefore gates the
input model's construction but not the score.

**Measured, first-hand** (12 canonical items, 12 authors, policy defaults):

```text
checked=0  (nothing checked)   counter_evidence factor=1.0000  contribution=15.00  CONF=42.8750
checked=10 (none contradicted)  counter_evidence factor=1.0000  contribution=15.00  CONF=42.8750
search never ran               counter_evidence factor=0.0000  contribution= 0.00  CONF=35.3750
required=0 (floor lowered)     counter_evidence factor=0.3333  contribution= 5.00  CONF=37.8750
```

The first two rows are identical. A check that examined nothing scores the same
as a check that examined ten documents, and both take the full 15 points. The
fourth row confirms the floor itself cannot be lowered: the policy's 3-family
floor applies even when the caller asks for 0.

---

### M-06: The state machine has no persistence boundary and no history

**Severity:** Must
**Where:** `db/migrations/**` (40 `CREATE TABLE` statements), `opportunity/lifecycle.py:351-361`

There is no opportunity table, no state column, no CHECK constraint, no trigger, and
no score or snapshot table. The 40 tables cover sources, fetches, communities,
documents, authors, `author_links`, evidence, pain points, propagation edges,
`research_*`, jobs, stage attempts, projection tasks, spend ledger,
`human_decisions`, model routing, the research gate, collection, the evidence
pipeline, clustering and independence. **[V]** None holds an opportunity, a state, a
score, or a snapshot.

```python
# opportunity/lifecycle.py:351-361
def apply_transition(current, target, context, policy) -> OpportunityState:
    decision = evaluate_transition(current, target, context, policy)
    ...
    return target          # nothing is written
```

`TransitionDecision` (`:142-152`) is returned in memory and discarded.
`TransitionContext` (`:103-139`) has no actor field and no per-transition
timestamp, only `as_of`, the evaluation reference time. So even if a record were
written, "who moved it, when" would not be available.

**Against the spec.** §24 requires "Track score and confidence over time" and
"Also record why the score changed." Both are unimplemented. `ScoreSnapshot` exists
as a content-addressed record in `opportunity/snapshot.py` with no table and no
writer.

---

### M-07: The human gate is a non-empty string

**Severity:** Must
**Where:** `opportunity/lifecycle.py:310-319`, `db/migrations/0001_initial.sql:307-316`

```python
# opportunity/lifecycle.py:310-319
if (current is OpportunityState.HUMAN_REVIEW
        and target is OpportunityState.VALIDATED):
    if not context.human_decision:
        return ("human_decision_missing", "no human decision was recorded")
    return None
```

`human_decision` is `str | None` (`:126`). The docstring is explicit that this is
deliberate: "any non-empty value means the decision was recorded." A `human_decisions`
table exists with `decision`, `rationale` and `decided_by` columns, and only the
projection reads it. The state machine never does.

**Exploit.** `CANDIDATE → VERIFYING → PROMISING → HUMAN_REVIEW → VALIDATED` is
walkable end to end by machine code that asserts `counter_evidence_searched=True`
(a bare bool) and `human_decision="approved"`. The `HUMAN_REVIEW` *state* is
mandatory; the human is not. The `PROMISING → HUMAN_REVIEW` edge (`:299-308`)
requires only that same bool, with no record behind it, and no score floor applies
at that edge or at `VALIDATED`, so the 55-point gate on `VERIFYING → PROMISING` is
the only numeric floor in the machine.

---

### M-08: Two `OpportunityState` enums with different transition tables

**Severity:** Must
**Where:** `opportunity/lifecycle.py:19-90`, `research/investigation/opportunity.py:12-27, 62-63`

```python
# research/investigation/opportunity.py:12-27
class OpportunityState(str, Enum):
    candidate = "CANDIDATE"
    ...

# opportunity/lifecycle.py:19-35
class OpportunityState(str, Enum):
    CANDIDATE = "CANDIDATE"
    ...
```

Same member names, same string values, different member names, different tables.
Neither imports the other. The investigation table allows
`HUMAN_REVIEW → VERIFYING`; the lifecycle table does not. The investigation table
has no `CANDIDATE → REJECTED`; the lifecycle table has it. The pipeline uses the
first, the scorer and lifecycle use the second, and both are publicly exported
from their packages. Because the enums are `str` subclasses with equal values,
accidental cross-use is silent.

---

### M-09: Automatic re-clustering writes no lineage edge

**Severity:** Must
**Where:** `db/clustering.py:255-347` (`apply_result`), vs `:1094` and `:1233`

`_write_edge` is called from exactly two places: `merge_clusters`
(`db/clustering.py:1094`) and `split_cluster` (`:1233`). **[V]** `apply_result`
(`:255-347`), the only write path a pipeline would call, never calls it.

`apply_result` matches proposals to stored clusters by member overlap
(`_match_clusters`, `:425-456`) and hands the historical ID to whichever proposal
overlaps most. When a re-cluster splits a stored cluster, one half keeps the
historical ID and the other half is minted a fresh provisional ID. Neither gets a
`split_from` edge, neither gets `status='split'`, and the losing half is
untraceable to the winner except by inferring it from `cluster_versions.member_ids_json`.

**Against the repo's own decision.** `docs/reasonix/decisions/2026-10-21-cluster-identity-and-lineage.md:35-49`
states "A merge and a split each create a new id, and the predecessor keeps its
history. **Do not** reuse a predecessor's id for a successor." The store's own
module docstring (`db/clustering.py:8-9`) says the opposite: "a proposal takes over
the stored active cluster with the largest member overlap, so a cluster keeps its id
across a re-clustering even when its anchor changes." Both behaviours are live. The
automatic path silently reuses the ID, in contradiction of the decision note.

**Consequence.** Anything that reconstructs "what happened to cluster X" from
`cluster_lineage` alone gets a wrong answer. It must read `cluster_versions` **and**
`clusters.status` **and** the membership intervals together.

---

### M-10: WTP evidence IDs are never joined to the evidence set

**Severity:** Must
**Where:** `opportunity/signals.py:143-168`, `opportunity/scoring.py:209-222`

The scorer does enforce the tier cap, and it does so in the scorer rather than the
model, so it survives a duck-typed input:

```python
# opportunity/scoring.py:209-222
for signal in wtp.signals:
    cap = policy.wtp_factors.signal_caps[signal.kind]
    strongest = max(strongest, wtp_tier_rank(cap))
declared = wtp_tier_rank(wtp.tier)
if declared > strongest:
    raise ScoringError("wtp_tier_exceeds_signals", ...)
```

But the *signal* is the assertion, and it is asserted in two unchecked dimensions.

```python
# opportunity/signals.py:152-154
detail: str = ""
evidence_ids: list[str] = Field(min_length=1)
```

`evidence_ids` only has to be non-empty. Nothing joins it to
`ConfidenceInputs.evidence`, and `detail` may be the empty string, so a WTP signal
can carry no explanation at all. `WtpSignal(kind=paid_customer,
evidence_ids=["ev_does_not_exist"])` scores W4, a WTP factor of 1.0, and about 25
potential points.

**This also breaches the potential/confidence separation.** The separation is
enforced by *type*: `PotentialInputs` and `ConfidenceInputs` share no field name and
no nested type, so a scorer cannot read what it was not handed. But the ID space is
shared. An evidence-derived *quantity* (a count of supporting signals, or a count
of distinct IDs) can travel into the potential side through the free-text ID
channel, which the type separation does not and cannot block.

There is a second, disconnected WTP rule in the investigation layer
(`research/investigation/areas.py:60`, `:81-85`) with **no cap at all**: an executor
can declare `W4` on a complaint-grade finding, and the investigation reports W4.

---

### M-11: Severity and frequency are unchecked model declarations carrying 35% of the potential

**Severity:** Must
**Where:** `extraction.py:151-152`, `:161-184`, `opportunity/scoring.py:479-482`, `:234-241`

```python
# extraction.py:151-152
severity: int = Field(ge=0, le=5)
frequency: FrequencyField
```

Both come straight out of the model's JSON. The only cross-check is the model's
consistency with its own label:

```python
# extraction.py:161-184
if self.pain_validity is PainValidity.genuine:
    if self.severity < 2:
        raise ValueError("a genuine pain point needs severity 2 or higher")
```

Nothing compares either value to the quote it is attached to. A chunk containing
"once a year we spend ten minutes reformatting a PDF" extracted as
`severity=5, frequency=daily` is accepted, stored, and scored. That single
observation sets the severity factor to 1.0 and the frequency factor to 1.0, which
is 35% of the potential weight (`config/scoring_policy.yaml`: severity 0.20,
frequency 0.15).

The quote is verbatim. The severity is not. §11's separation of "immutable
observation" from "derived fact" holds for the span but not for the two numbers
that dominate the potential score.

---

### M-12: The startup-killer's "survived" is the executor's silence

**Severity:** Must
**Where:** `research/investigation/pipeline.py:848-867`, `research/investigation/startup_killer.py:112-135`

§21 requires that "Every promising opportunity must have a deliberate attempt to
kill it." The attempt is implemented as "a search returned ≥ 2 domains of documents
and the executor attached no `CounterEvidenceItem`". No document is ever checked
for contradicting the dimension. An executor that never emits a
`CounterEvidenceItem` returns `survived` for all ten dimensions on any run, and the
investigation proceeds to `supported` and on to `PROMISING` / `HUMAN_REVIEW`.

---

## 4. Findings: Should

**S-01: A dissolved cluster can be re-activated under the same ID with no lineage
record.** `provisional_cluster_id` hashes `origin|<anchor_pain_id>`
(`clustering/clusters.py:194-217`), so a cluster created for anchor `pp-A` has the
ID a later proposal for the same anchor will compute. `_match_clusters`
(`db/clustering.py:425-456`) only considers clusters with `status = 'active'`
(`:1386-1391`), so a dissolved cluster cannot be re-matched by overlap; but the
`setdefault` at `:455` leaves the proposal on its own provisional ID, and
`_update_cluster` (`:609-638`) writes `status = cluster.status.value` **from the
proposal**, resetting it to `active`. The version snapshot records the transition,
so history is not destroyed, but `cluster_lineage` says nothing about it.

**S-02: A cluster absent from a new result keeps stale open memberships.** The
`affected` loop at `db/clustering.py:310-334` only touches clusters affected by a
*held* pain point. A cluster that simply does not appear in the next result is
never revisited: it stays `active`, keeps its open membership, and gets no version
bump and no snapshot. Because `cluster_members` rows are append-only and
`pain_id` carries a foreign key to `pain_points`
(`db/migrations/0006_clustering.sql:81`) with `PRAGMA foreign_keys = ON`, those
rows permanently block deletion of the pain point, closed rows included. The
`shortcut:` marker at `db/clustering.py:1266-1268` describes a wave that "prunes
the pain points a cluster points at"; that pruning cannot happen.

**S-03: `assignment_confidence` is a constant, not a measurement.** Merged and
split memberships are written with confidence 1.0. Any wave that averages this
column over merged clusters is silently wrong.

**S-04: `independent_community_count` can exceed `propagation_adjusted_count`.**
Only a `QUOTE` target marks a content group as derived
(`independence/graph.py:112-132`); a repost, crosspost or syndication copy lands in
the *same* content group as the original (`independence/propagation.py:36`), so
every repost's document is a root observation and its community counts as
independent (`independence/analytics.py:117-121`). The internal invariant
`community_count <= propagation_adjusted_evidence` is violated and is never checked
anywhere.

**S-05: One human with one account per platform counts as N independent people.**
Merges require a strong signal: `identity_key` or `same_platform_handle`
(`independence/policy.py:21`, `independence/identity.py:485-501`). Two accounts of
one person on two platforms share neither, so no edge fires. A bare handle scores
`standalone_handle_confidence = 0.7` (`config/independence.yaml:4`), which clears
`confident_threshold = 0.6`, so both accounts are `RESOLVED` and both count. The
honest reading of the current output is *independent accounts*, not *independent
people*. Note the residual risk: if the collector supplies `identity_key` per human
rather than per platform account, this closes; nothing in the layer distinguishes
the two.

**S-06: There is no extraction-consensus mechanism.** `grep consensus` over the
repository returns two cosmetic hits. `ExtractionAgreement` (`signals.py:307-320`)
is three integers. There is no majority vote, no best-of, no LLM adjudication, no
tie rule and no disagreement record. The premise behind the discount, "ten
extractions from one model are not agreement across models", cannot even arise:
extraction makes one model call per chunk (`extraction.py:565-567`) and the routing
layer is a fallback chain, not re-extraction.

**S-07: `extractors` is an unverifiable count.** `ExtractionAgreement.extractors`
is an integer with no extractor identities, so "agreement across models" is a
convention rather than a structure. A caller asserting `extractors=3` for three runs
of the same model takes full credit.

**S-08: The shipped WTP signal caps contradict Full-Spec §20.** The spec puts
stated budget at W4 and explicit intent to pay at W3; the policy caps
`stated_budget: W3` and `purchase_intent: W2` (`config/scoring_policy.yaml:70-75`).
The spec's W2, "costly workaround / meaningful time or money already spent", has
no `WtpSignalKind` at all.

**S-09: The niche override collapses the plausible range.** The same override
applies to the low, base and high runs (`opportunity/scoring.py:492-499`). When all
three raw market factors sit below the floor, `low == score == high`, and the
plausible range becomes a point estimate with no marker on the score object. The
wave spec acknowledges the collapse, but `ScoreSnapshot` stores the degenerate band
as if it were real, and a reader cannot tell "no uncertainty" from "uncertainty
deliberately removed".

**Measured, first-hand** (market of $500/yr, severity 4, W3, one source):

```text
WITH override    : score=74.2500  low=74.2500  high=74.2500
NO override      : score=65.9996  low=65.0080  high=66.5091   (severity 3)
niche_override   : factor_before=0.3375  factor_after=0.55
```

The band collapses to a single point exactly as described. The override's own
market lift is 0.3375 → 0.55, worth `0.2125 x 0.20 x 100 = 4.25` potential points
here, and up to 11.0 points at the theoretical maximum (factor 0 → 0.55 at weight
0.20). The total score difference in the table above is 8.25, which is 4.25 from
the market lift plus 4.00 from the severity change between the two rows.

**S-10: `propagation_adjusted_count` is computed and never read.**
`opportunity/signals.py:464-468` and `:479` compute it; no factor in
`opportunity/scoring.py` consumes it. §20 lists "propagation-adjusted evidence" as a
confidence consideration. In the wired path the only propagation discount is the
reliability mix, which is item-weighted and not a count.

**S-11: The resurrection gate trusts an asserted integer.**
`new_evidence_count` (`opportunity/lifecycle.py:129`) is compared against nothing.
Unlike the staleness edge, where the engine's own window overrides the asserted
date (`:162-176`), the resurrection gate never consults the evidence. A caller
asserting `new_evidence_count=1` with no new evidence is accepted into
`RESURRECTED`.

**S-12: `VALIDATED` is not terminal.** `opportunity/lifecycle.py:70-72` gives it
`{STALE, DEAD}`, and `STALE → RESURRECTED → VERIFYING` re-enters the machine, so a
validated opportunity can be re-validated without a human ever being asked again.
`DEAD` and `ALREADY_SOLVED` are hard terminals (`:88-89`).

**S-13: The market factor saturates with no plausibility bound.**
`reachable_population` is unbounded (`signals.py:180-183`), and
`reachable_population * incidence * adoption * price` can reach `inf`, which then
reaches `NicheOverride.market_annual_usd`. The factor clamps to 1.0 regardless.

**S-14: A zero-competitor search still reads as a mild tailwind.**
`competition_absent_factor_cap: 0.7` sits above the neutral value of 0.55, so a
search that ran and found nothing scores +0.15 on the competition factor, about
+1.5 potential points, relative to a market nobody examined. §20's "not proof of
a gap" holds; "not a tailwind" does not.

**S-15: `MarketEstimate` and `BuildabilityAssessment` carry no evidence
references.** §20 requires that the system "cite the evidence supporting each
material assumption". A `MarketEstimate` with `reachable_population=1e9` is
indistinguishable from a researched one.

**S-16: The market band has no ratio plausibility check.** `MarketEstimate`
refuses `not low <= base <= high` and an empty band, but nothing stops a caller
presenting a 1.001x band as a tight estimate.

**S-17: The coverage floors are caller-lowable.**
`CoverageRequirements` (`research/investigation/coverage.py:38-50`) is a field on
`InvestigationRequest` (`pipeline.py:248`) that `_check_invariants` does not
validate, so a caller may pass `min_queries=1, min_documents=1, min_domains=1` and
collapse the "nothing exists" conclusion to one document from one domain.
`assess_coverage` and `assess_counter_evidence` are public and take
`required_families` from the caller, so `[]` requires nothing.

**S-18: The investigation stage can never pass the shipped contract, and reports
`succeeded` regardless.** `config/research_contract.yaml:114-129` requires the
tools `search`, `qdrant` and `duckdb`; only `search` is ever called. The required
outputs include `opportunity_score` and `confidence`, which are never emitted. And
`pipeline.py:1486` hard-codes `status=StageStatus.succeeded` even when the outcome
is `incomplete`.

**S-19: `already_solved` is unreachable.** `_OUTCOME_STATES` maps it and the
transition table allows it from `promising` and `human_review`
(`research/investigation/opportunity.py:90-96`), but `_aggregate` never produces
it, so an existing-solution finding can never drive the state.

**S-20: The scorers do not validate their input type.** `score_potential` has no
`@pydantic.validate_call`. Passing a `ConfidenceInputs` raises `AttributeError`,
not `ScoringError`, and a duck-typed input skips every model invariant, including
the `EvidenceItem` and `CounterEvidenceSummary` checks. The WTP cap survives
because it lives in the scorer.

**S-21: The severity factor is a max over un-deduplicated observations.**
`opportunity/scoring.py:479-482`. One asserted record sets it to 1.0.

**S-22: Two identity thresholds, two concentration regimes.**
`config/scoring_policy.yaml` uses `identity.min_confidence: 0.7` and a Herfindahl
penalty; `config/independence.yaml` uses `confident_threshold: 0.6` and four
hard share caps. Nothing reconciles them, and the score path uses only the first
file.

---

## 5. Findings: Nice

- **N-01**: The concentration index is item-weighted, not author-weighted, so it
  disagrees with the diversity factor it bounds (`signals.py:454-459`).
- **N-02**: `detect_propagation` can emit a self-edge for a repeated `doc_id`
  (`independence/propagation.py:130-171`); the store's `load_mentions` cannot
  produce it, but the function is public API.
- **N-03**: `hash()` on strings is salted per process in the propagation shingle
  screen (`independence/propagation.py:96-111`). The edge set is invariant; only
  performance is not. Documented in the docstring and accepted in the wave report.
- **N-04**: Two of the six confidence provenance rows (counter-evidence and
  extraction agreement) carry no record IDs, so half the confidence score has no
  record behind it in the provenance (`scoring.py:767-783`).
- **N-05**: `member_id` embeds the wall clock
  (`clustering/clusters.py:220-222`), so membership *row* IDs are not reproducible
  across runs. Defensible for an interval row, but it means "deterministic IDs"
  holds for cluster IDs only.
- **N-06**: Two unrelated "cluster" concepts share the database:
  `author_clusters` (independence, which deletes rows) and `clusters`
  (clustering, append-only).
- **N-07**: A dangling `alias_of` produces a phantom canonical community ID
  (`independence/identity.py:576-593`). Alias *cycles* are handled correctly.
- **N-08**: `authors.identity_confidence` and `identity_basis` go stale when an
  author is ungrouped: only `cluster_id` is cleared
  (`db/independence.py:495-502`).
- **N-09**: A document with several author links silently attributes to the
  lowest `author_id` (`db/independence.py:184-190`).
- **N-10**: `uncertain_author_count` and `uncertain_identity_groups` are the same
  number under two names, and it counts *groups*, not people
  (`independence/analytics.py:113-115`, `:146`, `:166`).
- **N-11**: `Assignment.basis` is free-form text rather than an enum
  (`clustering/clusters.py:92`).
- **N-12**: The quote is not re-verified at the moment of use, and `EvidenceItem`
  has no liveness field, so a span whose document has since changed can still back
  a severity (`evidence.py:310-359` exists but is only invoked on reprocess).
- **N-13**: `independence/` sums a set-derived list with plain `sum`
  (`identity.py:511-520`), so the float addition order varies with
  `PYTHONHASHSEED`. The result is rounded to 6 decimal places, so it is invariant
  in practice.
- **N-14**: Full-Spec §20 lists nine confidence considerations; the wired path
  implements six, and two potential considerations ("distribution accessibility",
  "retention or recurrence") have no component at all.

---

## 6. The four named hunts

### 6.1 Score contamination

**No cross-read between the two scorers.** Every field read by `score_potential`
(`scoring.py:466-509`) and every field read by `score_confidence` (`:681-815`) was
traced. `score_potential` reads only `pain_observations`, `wtp`, `market`,
`competition` and `buildability`. `score_confidence` reads only `evidence`,
`extraction_agreement` and `counter_evidence`. No factor on either side is
computed from the other score, and none is computed from its own score. The two
shared objects are policy constants (`policy.lifecycle.staleness_days` and
`policy.identity.min_confidence`), not score reads.

**But the separation is by type, not by evidence (M-10).** `PotentialInputs` and
`ConfidenceInputs` share no field and no nested type, so the structural guarantee
holds. The *id space* is shared: `WtpSignal.evidence_ids`, `CompetitiveAssessment.evidence_ids`
and `PainObservation.pain_id` are opaque strings that are never cross-checked
against `ConfidenceInputs.evidence`. An evidence-derived quantity can therefore
reach the potential side. That is the one contamination channel I found.

**Trend is clean.** `TrendSignal` is not a field of either input model
(`signals.py:292-304`, `:349-380`) and neither scorer reads it. It is reported
beside the two numbers (`scoring.py:177`). One consequence worth naming: trend and
confidence are both functions of the same evidence set, so a consumer that ranks on
`potential + trend` reintroduces exactly the double-count §20 forbids.

### 6.2 Circular scoring

**None found.** No factor is derived from a score, and the two scorers are called
separately with separate inputs (`scoring.py:818-834`). The lifecycle's promotion
gate reads the engine's own `ConfidenceScore`
(`lifecycle.py:265-297`), which is a consumer of a score, not a circular input.
`build_snapshot` (`opportunity/snapshot.py:201-215`) calls the two scorers
independently.

The one thing that resembles circularity is the shared staleness constant
(`policy.lifecycle.staleness_days`, 90 days) read by both `_persistence`
(`scoring.py:615`) and `_evidence_is_stale` (`lifecycle.py:188`). That is a shared
constant, and it is the correct design.

### 6.3 False independence

**This is the layer's weakest point.** Three independent routes to the same false
claim:

1. **Assertion (M-03).** `is_canonical`, `propagation_confidence`,
   `identity_confidence` and `community_id` are caller-supplied on `EvidenceItem`,
   with no join to the propagation graph, the identity resolution, or the canonical
   community map.
2. **No clustering (M-02).** The scored path counts `author_id` strings, so one
   person with one account per platform is N independent authors (S-05), and the
   identity clusters the `independence/` package computes are never consulted.
3. **Propagation blindness (S-04).** Reposts land in the same content group as the
   original, so the original's communities are counted as independent. A short
   quote of a long post is invisible to the propagation detector altogether
   (`independence/propagation.py:194-216`, `quote_min_overlap: 0.6` over the
   *shorter* text), so the quoting community counts too.

The `independence/` package itself gets most of this right. Its identity confidence
is deterministic, the model may not assert a `MERGED` edge
(`independence/policy.py:137-158`), uncertain identities are excluded before
counting, and the store refuses a hand-built metrics record
(`db/independence.py:524-528`). None of it reaches the score.

**No LLM declares independence inside the `independence/` package.** There is no
`llm` import anywhere in `src/product_hunter/independence/`. The one LLM call in
the intelligence layer is the clustering adjudicator
(`clustering/adjudication.py:147-182`), which decides *membership*, not
independence, and only inside the ambiguous band: above `merge_threshold` the
engine never calls the model, below `ambiguous_threshold` the pair is not a
candidate. A failed adjudication holds the pair and never merges
(`clustering/engine.py:453-460`).

### 6.4 Concentration bypasses

**Yes, in three forms.**

1. **The hard caps are not on the scored path (M-04).** Four share caps and two
   minimums are declared in `config/independence.yaml` and enforced in
   `independence/analytics.py`, whose verdict lands in the `independence_reports`
   table, which nothing reads.
2. **The penalty that does apply is item-weighted (N-01).** The Herfindahl index
   counts mentions, so it can disagree with the community-diversity factor it is
   meant to bound.
3. **There is no author-cluster control on the scored path at all.** The measured
   table in M-04 shows why this is the sharp edge: one community or one source
   holding everything *is* caught by the Herfindahl penalty (confidence 37.96 and
   42.96, both under the 55-point gate), and a source at a 50% share slips under
   the neutral index with no penalty (85.42). But twelve accounts belonging to one
   person produce index 0.0833, no penalty, and a confidence of 85.92. The
   `max_single_author_cluster_share: 0.4` cap that would catch this has nothing to
   bind to, because the scored path does no identity clustering.

The `independence/` package's own cap has the mirror-image weakness:
`max_single_propagation_chain_share: 0.3` is a share-of-mentions heuristic, so a
chain diluted below 30% of mentions is free.

---

## 7. What the layer gets right

Stated plainly, with evidence, because most of it is real:

**Clustering**
- Versioned clusters with insert-only snapshots; the history is never rewritten
  and is queryable.
- The one-live-cluster-per-pain-point rule is enforced three ways: a partial
  unique index (`db/migrations/0006_clustering.sql:96-97`), a Python guard
  (`db/clustering.py:398-402`), and write ordering that closes before it opens
  (`:1064-1073`, `:1163-1171`).
- Cluster IDs are content-derived `sha256` hashes with no uuid, random, or clock in
  the path (`clustering/clusters.py:194-217`); every sort has a total-order
  tie-break; the engine reads no clock and no database.
- Evidence is preserved across merges and splits. The clustering layer never
  writes `evidence`, `evidence_pain_links` or `pain_points`, and both the
  predecessor and the successor resolve to the same `span_id` and `quote`.
- The store validates the *shape* of a result before the transaction opens
  (`:377-423`), refuses a result from another vector space on all three write
  paths, and rolls back completely on a mid-apply failure.
- A failed adjudication never merges.

**Independence package**
- Identity confidence is deterministic: signal weights, a conflict cap, and a
  negative-signal table, with no model call anywhere in the package.
- The model may not assert a `MERGED` edge, and a caller may not supply a strong
  signal kind at all (`identity.py:546-552`).
- Uncertain identities are filtered *before* counting and reported beside the
  independent count, never folded into it.
- Alias cycles are detected and fall back safely; union-find cannot cycle.
- The store refuses any metrics record not built by `analyze()`.

**Scoring**
- Potential and confidence never read each other (section 6.1).
- Severity is deterministic; the population factor uses independent counts, not
  raw counts; the modal-frequency tie-break is documented and order-independent.
- The WTP tier is capped by the strongest signal and fails closed at score time.
- No competitor search yields the neutral factor, recorded as an override; zero
  competitors found is capped below the tailwind.
- The market mapping is logarithmic and clamped, and the bottom-up formula is
  implemented literally.
- The six confidence factors are all in 0..1, all deterministic, and each has a
  provenance row naming the records behind it.
- Persistence refuses future-dated evidence and zeroes out past the staleness
  window.
- The counter-evidence floor is `max(caller, policy)`, so a caller can raise the
  bar and never lower it.
- The niche override is triggered by three conditions, bounded by the policy, and
  touches only the market factor.
- The trend is reported beside the two numbers and read by neither scorer.
- No LLM call anywhere in the scoring path. `math.fsum` makes the float sums
  order-independent. Naive datetimes are refused.

**Lifecycle**
- All 11 states are present and match Full-Spec §23.
- The transition table is explicit and enforced; an illegal edge raises
  `TransitionRefused` with a reason code.
- The promotion gate reads the engine's own `ConfidenceScore` and refuses a
  promotion with no score at all (`confidence_score_missing`), and
  `TransitionContext` forbids the caller from asserting `independent_people` or
  `confidence` directly.
- `DEAD` and `ALREADY_SOLVED` are hard terminals.
- Resurrection requires new evidence and a reason, and re-enters at `VERIFYING`,
  so it must clear the promotion gate again.

**Evidence and validation**
- The extraction → quote-verification → persistence chain is genuinely strong. The
  quote is re-verified against the hash-bound stored text at persistence time
  (`evidence.py:555-591`, `:593-630`), a missing document text refuses the
  evidence, and a database trigger refuses a pain point with no span
  (`db/migrations/0005_evidence_pipeline.sql:96-109`).
- The counter-evidence family list is pinned in the pipeline, not left to the
  caller.
- There are no `TODO`, `FIXME` or `HACK` markers anywhere in the clustering,
  independence, scoring or lifecycle layers. The only markers are two `shortcut:`
  notes in clustering, both of which describe a limitation accurately, though the
  one at `db/clustering.py:1266-1268` describes a pruning path the foreign key
  makes impossible (S-02).

---

## 8. Not checked, and the risk the user must know

**Not checked**
- The live `sentence-transformers` path (`clustering/similarity.py:139-150`); the
  package is not installed in this environment.
- The clustering adjudicator against a live model. Only the scripted double ran.
- `SentenceTransformerEmbedder.embed` batch-composition effects on float results
  across runs with different batch sizes.
- Concurrency: more than one connection, or behaviour under WAL on a file-backed
  database (`db/connection.py:58`).
- The `eval/` harness, `projection/`, `collection/` and `discovery/` internals
  beyond what touches the validation path.
- The live contents of `db/product_hunter.db`.
- Whether any out-of-repo caller wires `investigate()`, `score_opportunity()`,
  `build_snapshot()` or `apply_transition()`. Inside this repository, none does
  (M-01).
- The eight Wave 3 branches beyond confirming that none of them touches this
  layer.

**Risk the user must know**

M-01 changes how to read this report. The scoring, confidence and lifecycle code
is well built, well tested and unwired; every number it produces today is a fixture
number. The gaps in M-02 through M-12 are therefore contract gaps: they are what
the producer, whoever writes it, will encode. Fixing the `independence/` package
alone will not change a single number the product emits, because the score reads a
different implementation. The most consequential single item is M-03: until the
four independence-defining fields on `EvidenceItem` are derived from the graph
rather than asserted by the caller, "independent people" in this system means
"records somebody labelled as different people", and the confidence score will
happily reward a sockpuppet ring.

---

## Appendix A: Evidence index

Every finding in this report is anchored to one of these. Files are relative to the
repository root.

| Area | Primary files |
|---|---|
| Clustering | `src/product_hunter/clustering/{engine,clusters,problems,similarity,adjudication,__init__}.py`, `src/product_hunter/db/clustering.py`, `db/migrations/0006_clustering.sql`, `config/clustering.yaml` |
| Independence | `src/product_hunter/independence/{identity,propagation,policy,graph,analytics,records}.py`, `src/product_hunter/db/independence.py`, `db/migrations/0007_independence.sql`, `config/independence.yaml` |
| Scoring | `src/product_hunter/opportunity/{scoring,signals,policy,snapshot}.py`, `config/scoring_policy.yaml` |
| Lifecycle | `src/product_hunter/opportunity/lifecycle.py`, `src/product_hunter/research/investigation/opportunity.py` |
| Validation | `src/product_hunter/extraction.py`, `src/product_hunter/evidence.py`, `src/product_hunter/quote.py`, `src/product_hunter/research/{gate,predicates,evidence,contract}.py`, `src/product_hunter/research/investigation/*.py`, `src/product_hunter/db/research_gate.py`, `db/migrations/0005_evidence_pipeline.sql`, `config/research_contract.yaml` |
| Requirements | `docs/reasonix/specs/Product-Hunter-Full-Spec.md` §11, §12, §20, §21, §23, §24; `docs/reasonix/decisions/2026-10-21-cluster-identity-and-lineage.md`; `docs/reasonix/specs/wave-2-{clustering,independence,scoring-validation,investigation}.md`; `docs/reasonix/reports/wave-2-*.md` |
| Tests | `tests/test_clustering_{core,engine,store}.py`, `tests/test_independence_{identity,propagation,analytics,store}.py`, `tests/test_opportunity_{scoring,lifecycle,snapshot}.py`, `tests/test_research_investigation_*.py`, `tests/test_extraction.py`, `tests/test_evidence_pipeline.py` |
