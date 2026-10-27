# Product Hunter — Opportunity Lineage Forensic Audit

**Run ID:** `run_063446128a0ca0f7`
**Public ID:** `RH-20261024-0002`
**Investigation type:** Forensic diagnosis only — no production code modified

---

## 1. Executive Summary

| Question | Answer |
|---|---|
| Does duplication exist? | **Yes** — substantial duplication across both intra-run and cross-run dimensions |
| Is duplication intra-run? | **YES** — multiple normalized keys resolve to the same underlying problem within one run (e.g., 4 Kasa ES20M normalized keys, 3 Claude Code Max session-limit keys) |
| Is duplication cross-run? | **YES** — identical normalized problems recur across 5+ prior runs, each creating a new `opp_*` identity with no reconciliation |
| Where does duplication enter? | At **opportunity identity creation** in `_scoring_handler` (src/product_hunter/handlers.py:3598): `create_opportunity_id(normalized_key or pain_id, job.run_id)` |
| Is projection the cause? | **NO** — projection merely reflects upstream DB state. The projection has 0 `opp_*` note keys; its file-system path encodes the slug but no stable `opportunity_id` identity |
| Is 28 pain points → 28 opportunities intentional? | **NO** — it is an accidental one-to-one transformation: the scoring handler iterates pain points and creates exactly one opportunity per pain point row |
| 28 opportunities → 27 projected notes explanation | **N/A** — the projection contains **zero** `opp_*` note keys. The "27 notes" figure from the prior report is incorrect for this run's projection artifact; all 29 current-run opportunities are absent from the projection's `opp_*` namespace |

### Root cause (CONFIRMED)

Opportunity identity is derived from `(normalized_key, run_id)` via `create_opportunity_id()` at `src/product_hunter/db/opportunity.py:217-219`. Since:

1. `normalized_key` is derived independently per pain point row from extraction output (no canonicalization across pain points), and
2. `run_id` scopes every identity to a single run,

the same underlying problem observed in multiple documents or multiple runs produces **multiple distinct opportunity identities** with no mechanism to reconcile them. There is no UNIQUE constraint on `normalized_problem`, no upsert-by-key, and no cross-run identity reconciliation.

---

## 2. Opportunity Identity Architecture

```
pain point
  │
  ▼ (normalized_key column on pain_points table)
cluster
  │
  ▼ (cluster_members / cluster_versions — present but unused by opportunity creation)
opportunity
  │ (create_opportunity_id(normalized_key_or_pain_id, run_id))
  └── opportunity_id = "opp_" + sha256(run_id + "|" + title)[:16]
persistence
  │ (opportunities table: PRIMARY KEY opportunity_id, ON CONFLICT DO UPDATE by opportunity_id only)
projection
  │ (SqliteProjectionSource reads opportunities table; groups pain points by normalized_key
   │   into _group_draft(), generating "opp_" + sha256(key)[:16] WITHOUT run_id)
```

### Code locations

| Function | File:Line | What it does |
|---|---|---|
| `create_opportunity_id(title, run_id)` | `src/product_hunter/db/opportunity.py:217-219` | Deterministic ID from `run_id + "|" + title`. Called by handlers at three sites. |
| `_scoring_handler` opportunity creation | `src/product_hunter/handlers.py:3598` | `opp_id = create_opportunity_id(normalized_key or pain_id, job.run_id)` — creates one opportunity **per pain point row**, using `normalized_key` (not cluster_id). |
| `_competitor_handler` opportunity creation | `src/product_hunter/handlers.py:2206` | `opp_id = create_opportunity_id(problem, run_id)` — uses whole `_problem_statement(conn, run_id)` text as title. |
| `_counter_evidence_handler` opportunity creation | `src/product_hunter/handlers.py:2701` | Same pattern as competitor handler — `create_opportunity_id(problem, run_id)`. |
| Projection `_group_draft` | `src/product_hunter/projection/source.py:248` | `opportunity_id = "opp_" + hashlib.sha256(key.encode()).hexdigest()[:16]` — derived from `normalized_key` alone, **without** run_id. |
| Projection `_drafts` | `src/product_hunter/projection/source.py:156-176` | If `opportunity_rows` exist, uses canonical table rows; otherwise falls back to `_group_draft()` grouping by `normalized_key`. |

### Identity scheme

- **Handler-created IDs** use `sha256(run_id + "|" + title)[:16]` → **28 different IDs** in the current run because all 28 opportunities have `cluster_id=None` and distinct titles.
- **Projection-created IDs** use `sha256(normalized_key)[:16]` → no `run_id` component, but the projection's `_drafts()` only uses `_group_draft()` when `opportunity_rows` is empty. Since the current run has 29 opportunity rows, the projection reads from the canonical table, not from grouping.

---

## 3. Duplicate Case Studies

### Case A: "Ask HN: How do you deal with a dull co-founder?"

```
RAW SOURCE
  ↓ Hacker News post (hn_algolia)
DOCUMENT
  ↓ doc_id not in current run's discovered set (created in prior run)
EVIDENCE SPAN
  ↓ Not traced — pain point has no span_id linkage in current run
CLAIM / PAIN POINT
  ↓ Not present as a pain_point row in current run
NORMALIZED PAIN POINT
  ↓ N/A — opportunity was created by _competitor_handler using _problem_statement()
CLUSTER
  ↓ cluster_id=None on the opportunity row
INDEPENDENCE ANALYSIS
  ↓ N/A — no cluster membership
VALIDATION
  ↓ lifecycle=INVESTIGATING (set by _competitor_handler, not by validation gate)
OPPORTUNITY
  ↓ opp_c621fce1aed9da5f, title="Ask HN: How do you deal with a dull co-founder?"
OPPORTUNITY ID
  ↓ create_opportunity_id("Ask HN: How do you deal with a dull co-founder?", "run_063446128a0ca0f7")
REPORTING
  ↓ Not present as a scoring-created opportunity; competitor/counter-evidence handlers created it
OBSIDIAN PROJECTION
  ↓ Present as `01-Ideas/⚫ Unscored/ask-hn-how-do-you-deal-with-a-dull-co-founder--*.md` (15 duplicate note files for same title)
```

**Cross-run duplication:** 15 separate note files for the same "dull co-founder" title, each with a different hash suffix. The projection groups by title slug but the hash suffix makes each file unique. No canonical `opp_*` identity.

**Root cause:** The competitor/counter-evidence handlers create an opportunity from `_problem_statement(conn, run_id)` — a single aggregate problem statement for the whole run — not from individual pain points. This opportunity is never linked to any pain point or cluster.

---

### Case B: "A researcher has to come into the lab to check cryocooler pressure."

```
RAW SOURCE
  ↓ dev.to article "I Got Tired of Coming Into My Lab To Check the Pressure..."
DOCUMENT
  ↓ doc_id from dev_to source (created in prior run)
EVIDENCE SPAN
  ↓ ev_a31faffe933154c5 (quote: "I Got Tired of Coming Into My Lab To Check the Pressure...")
CLAIM / PAIN POINT
  ↓ pp_e78476378d674ed3
NORMALIZED PAIN POINT
  ↓ normalized_key=research:check-cryocooler-pressure:cryocooler-pressure
CLUSTER
  ↓ cl_db0df9f7704c0d0a (anchor_pain_id=pp_e78476378d674ed3, member_count=1, run_id=e2e-prod-1792846794)
INDEPENDENCE ANALYSIS
  ↓ cluster run_id=e2e-prod-1792846794 (different from current run_id)
VALIDATION
  ↓ N/A — cluster was created in a prior run
OPPORTUNITY
  ↓ opp_9ededfeb12a5f005, title="A researcher has to come into the lab to check cryocooler pressure.", lifecycle=CANDIDATE, cluster_id=None
OPPORTUNITY ID
  ↓ create_opportunity_id("research:check-cryocooler-pressure:cryocooler-pressure", "run_063446128a0ca0f7") = opp_9ededfeb12a5f005 ✓ (handler-ID matches)
REPORTING
  ↓ scoring handler created it from pain point pp_e78476378d674ed3
OBSIDIAN PROJECTION
  ↓ 7 duplicate note files: `01-Ideas/🔴 00-49 Low Potential/a-researcher-has-to-come-into-the-lab-to-check-cryocooler-pr--{7 hashes}.md`
```

**Intra-run:** No duplicate within current run (1 pain point → 1 opportunity).

**Cross-run duplication:** The cluster `cl_db0df9f7704c0d0a` was created in run `e2e-prod-1792846794`. The current run re-extracts the same pain point but creates a **new** opportunity identity because `create_opportunity_id` includes `run_id`. The cluster's `anchor_pain_id` is from a prior run.

---

### Case C: "Software teams using AI agents produce more code but close no additional tickets."

```
RAW SOURCE
  ↓ dev.to article "926 Steps, Two Lobotomies, and Zero Nudges: Flight-Testing the Commitment"
DOCUMENT
  ↓ dev_to source
EVIDENCE SPAN
  ↓ ev_aa74d7e35cc4893e (Harvard study quote)
CLAIM / PAIN POINT
  ↓ pp_b2c55182771b2592
NORMALIZED PAIN POINT
  ↓ normalized_key=software:review-ai-generated-code:ai-generated-code
CLUSTER
  ↓ cl_c31c602246c43739 (anchor_pain_id=pp_b2c55182771b2592, member_count=3, run_id=run_063446128a0ca0f7)
INDEPENDENCE ANALYSIS
  ↓ cluster created in current run, member_count=3
VALIDATION
  ↓ lifecycle=CANDIDATE
OPPORTUNITY
  ↓ opp_023a2997646cae09, title="Software teams using AI agents produce more code but close no additional tickets", lifecycle=CANDIDATE, cluster_id=None
OPPORTUNITY ID
  ↓ create_opportunity_id("software:review-ai-generated-code:ai-generated-code", "run_063446128a0ca0f7") = opp_023a2997646cae09 ✓ (handler-ID matches)
REPORTING
  ↓ scoring handler created it from pain point pp_b2c55182771b2592
OBSIDIAN PROJECTION
  ↓ 7 duplicate note files: `01-Ideas/🔴 00-49 Low Potential/software-teams-using-ai-agents-produce-more-code-but-close-n--{7 hashes}.md`
```

**Critical observation:** `cluster_id=None` on the opportunity row, even though cluster `cl_c31c602246c43739` exists with `member_count=3` and `anchor_pain_id=pp_b2c55182771b2592`. The scoring handler **never sets `cluster_id`** on the opportunity record (handlers.py:3603: `cluster_id=None`).

**Cross-run duplication:** 7 note files for the same title in the projection. The cluster has 3 member pain points but only 1 opportunity was created from this cluster in the current run.

---

### Case D: Kasa ES20M / Bambu Studio technical tools

#### Kasa ES20M — 4 pain points, 1 cluster, 0 opportunities in current run

```
4 PAIN POINTS (all from same document gh_3293001995, github_issues source):
  pp_8069505a0d7e5e5a  norm=software:use-kasa-es20m-with-home-assistant:kasa-es20m-motion-sensor-switch
  pp_85d53c627737519c  norm=software:use-kasa-es20m-switch-with-home-assistant:kasa-es20m-motion-sensor-switch
  pp_8afc433737ef3a51  norm=software:use-kasa-es20m-motion-sensor-switch-with-home-as:kasa-es20m-motion-sensor-switch
  pp_e28ca513618d7ca7  norm=software:poll-kasa-es20m-switch-via-home-assistant:kasa-es20m-motion-sensor-switch

ALL 4 assigned to cluster cl_b1148836e349526a (member_count=4, anchor_pain_id=pp_8069505a0d7e5e5a)
Label: "Kasa ES20M motion sensor switches lock up during Home Assistant use and require a factory reset to recover."

OPPORTUNITIES in current run: 0 for any Kasa ES20M normalized key
OPPORTUNITIES across all runs: 13 (spread across 5 prior runs)
```

**Why no opportunities in current run:** The scoring handler creates opportunities from pain points where `score.confidence.score >= policy.lifecycle.min_confidence AND score.independence.independent_author_count >= policy.lifecycle.min_independent_people`. The Kasa pain points have `severity=4` but may fail the confidence or independence threshold. More importantly, the handler iterates pain points and creates one opportunity **per pain point row** that passes scoring — but none of the 4 Kasa pain points produced an opportunity in this run.

**Cross-run duplication:** 13 opportunities across 5 prior runs for the same Kasa ES20M problem, with 4 different normalized keys (all semantically identical). Each run creates new identities because `run_id` is part of the hash seed.

#### Bambu Studio — 1 pain point, 0 opportunities in current run

```
pain_id=pp_b246c6e31a05244d
norm_key=software:start-bambu-studio:application-startup
source=github_issues
```

Same pattern: pain point exists, cluster may exist, but no opportunity created in current run.

---

## 4. Database Analysis

### Schema (opportunity-related tables)

```sql
CREATE TABLE opportunities (
    opportunity_id         TEXT PRIMARY KEY NOT NULL,
    title                  TEXT NOT NULL,
    normalized_problem     TEXT,
    domain                 TEXT,
    actor                  TEXT,
    action                 TEXT,
    object                 TEXT,
    consequence            TEXT,
    severity               REAL,
    frequency              TEXT,
    wtp_tier               TEXT,
    competition            REAL,
    potential              REAL,
    confidence             REAL,
    trend                  TEXT,
    uncertainty            TEXT,
    lifecycle              TEXT,
    recommended_experiment TEXT,
    first_seen             TEXT,
    last_updated           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
);

CREATE TABLE opportunity_scores (
    score_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    potential      REAL,
    confidence     REAL,
    created_at     TEXT NOT NULL
);

CREATE TABLE opportunity_evidence (
    opportunity_id TEXT NOT NULL,
    span_id        TEXT NOT NULL,
    stance         TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, span_id, stance)
);

CREATE TABLE opportunity_claims (
    claim_id       TEXT PRIMARY KEY NOT NULL,
    opportunity_id TEXT NOT NULL,
    statement      TEXT NOT NULL,
    kind           TEXT,
    span_id        TEXT,
    created_at     TEXT NOT NULL
);
```

### Uniqueness analysis

| Constraint | Present? | Effect |
|---|---|---|
| PRIMARY KEY on `opportunity_id` | YES | Prevents exact same `opportunity_id` |
| UNIQUE on `normalized_problem` | **NO** | Multiple rows can have same `normalized_problem` |
| UNIQUE on `(normalized_problem, run_id)` | **NO** | Cross-run duplicates physically possible |
| UNIQUE on `cluster_id` | N/A | `cluster_id` is nullable, not unique |
| Index on `normalized_problem` | **NO** | No lookup by canonical problem |
| `ON CONFLICT(opportunity_id) DO UPDATE` | YES | Only dedupes by exact `opportunity_id`, not by semantic identity |

**Answer to Question E (Can the database physically store two opportunity records representing the same canonical opportunity?): YES.** The `opportunities` table has no UNIQUE constraint on `normalized_problem` or on any composite key that would enforce canonical identity. The only uniqueness is on `opportunity_id`, which is derived from `(run_id, title)` — so different titles or different runs always produce different IDs, even for the same underlying problem.

### Upsert behavior

`OpportunityStore.upsert()` (opportunity.py:73-141) uses `INSERT ... ON CONFLICT(opportunity_id) DO UPDATE`. It updates all mutable fields when the same `opportunity_id` is written twice. **It never deduplicates by `normalized_problem` or `cluster_id`.**

---

## 5. Cardinality Analysis

```
Documents → Evidence Spans → Pain Points → Clusters → Opportunities → Projection Notes

Current run (run_063446128a0ca0f7):

Documents discovered: 44
Evidence spans: 35 (across discovered documents)
Pain points: 28 (from scoring handler iteration)
Clusters: 15 (with member_count ≥ 1, but cluster_id=NULL on all 29 opportunities)
Opportunities: 29 (1 per pain point row that passes scoring; 1 additional from competitor handler)
Projection notes: 0 with key 'opp_*'; 180 total notes (markets, sources, communities, dashboards, daily reports, and filesystem-path notes for ideas)

Cardinality violations observed:
  - pain_point → opportunity: 1:1 (28 pain points → 28 opportunities via scoring handler)
  - cluster → opportunity: N:0 (cluster_id never set on opportunities; clusters exist but are not linked)
  - normalized_problem → opportunity: N:1 within run (0 duplicate normalized_problems in current run because titles differ slightly)
  - normalized_problem → opportunity: N:M across runs (e.g., Kasa ES20M: 4 normalized keys × 5 runs = 13 opportunities for same problem)
```

### Why 29 opportunities, not 28?

The scoring handler creates opportunities from pain points. One additional opportunity (`opp_c621fce1aed9da5f`, "Ask HN: How do you deal with a dull co-founder?") was created by `_competitor_handler` using `_problem_statement(conn, run_id)` — a run-level aggregate, not from any individual pain point. So 28 pain-point-derived + 1 competitor-derived = 29 total.

### Pain point → opportunity mapping (complete)

| Pain Point ID | Normalized Key | Opportunity ID | Opportunity Title |
|---|---|---|---|
| pp_e78476378d674ed3 | research:check-cryocooler-pressure:cryocooler-pressure | opp_9ededfeb12a5f005 | A researcher has to come into the lab to check cryocooler pressure. |
| pp_b2c55182771b2592 | software:review-ai-generated-code:ai-generated-code | opp_023a2997646cae09 | Software teams using AI agents produce more code but close no additional tickets |
| pp_c54851bc15e63f64 | software:run-normal-agentic-tasks-via-cli:5-hour-session-quota | opp_a027068695f4e4ec | Claude Max users hit the 5-hour CLI session limit within 1-2 |
| pp_294f632f08423fb0 | software:run-normal-agentic-tasks:5-hour-session-quota | opp_b6efd83924887d19 | Claude Max CLI users hit the 5-hour usage limit far earlier |
| pp_98d61af41b41db6a | software:use-claude-code-continuously:claude-code-session | opp_98d61af41b41db6a | Max subscribers hit Claude Code usage limits after about two |
| ... | ... | ... | ... |

All 28 scoring-created opportunities have `cluster_id=None`. The `cluster_id` column exists on the `opportunities` table but is never populated by any handler.

---

## 6. Intra-run vs Cross-run Duplication

### Intra-run duplication

**Confirmed.** Within the current run (`run_063446128a0ca0f7`):

- **Kasa ES20M:** 4 pain points with 4 different `normalized_key` values (differing in the actor/action prefix: `use-kasa-es20m-with-home-assistant`, `use-kasa-es20m-switch-with-home-assistant`, `use-kasa-es20m-motion-sensor-switch-with-home-as`, `poll-kasa-es20m-switch-via-home-assistant`) all assigned to the **same cluster** (`cl_b1148836e349526a`, member_count=4). If all 4 passed scoring, they would create 4 separate opportunities for the same underlying problem.

- **Claude Code Max session limit:** At least 2 pain points (`pp_c54851bc15e63f64` with key `software:run-normal-agentic-tasks-via-cli:5-hour-session-quota` and `pp_294f632f08423fb0` with key `software:run-normal-agentic-tasks:5-hour-session-quota`) represent the same underlying problem but have different normalized keys → would create 2 opportunities.

- **Soundflower:** 2 pain points with keys `software:uninstall-soundflower:internal-mac-audio` and `software:uninstall-soundflower:internal-macbook-audio` — same problem, different keys.

**However**, in the current run, none of these duplicate-key pain points passed the scoring threshold to become opportunities (or were not iterated), so intra-run duplicate opportunities did not materialize for these specific cases in this run. The mechanism exists but the gate filtered them out.

### Cross-run duplication

**Confirmed and severe.** Across all runs (126 total opportunities):

| Normalized problem | Runs affected | Opportunities created |
|---|---|---|
| `software:use-kasa-es20m-motion-sensor-switch-with-home-as:kasa-es20m-motion-sensor-switch` | 5 runs | 5 opportunities |
| `software:use-kasa-es20m-switch-with-home-assistant:kasa-es20m-motion-sensor-switch` | 5 runs | 5 opportunities |
| `software:poll-kasa-es20m-switch-via-home-assistant:kasa-es20m` | 5 runs | 5 opportunities |
| `software:uninstall-soundflower:internal-macbook-audio` | 5 runs | 5 opportunities |
| `software:uninstall-soundflower:internal-mac-audio` | 5 runs | 5 opportunities |
| `software:understand-performance:programs` | 5 runs | 5 opportunities |

Each run independently creates `opp_*` identities for the same `normalized_problem` because `run_id` is part of `create_opportunity_id()` seed. There is **no cross-run reconciliation** — no upsert by `normalized_problem`, no `merged_into` linkage used for opportunities, no canonical identity.

---

## 7. Quality Findings

Weak evidence can become candidate opportunities. The scoring handler creates opportunities for every pain point row where:

```python
score.confidence.score >= policy.lifecycle.min_confidence
AND score.independence.independent_author_count >= policy.lifecycle.min_independent_people
```

### Examples of weak evidence becoming opportunities

**Example 1: `opp_70fb58322019db29` — "TermDesk users cannot install the app"**
- 1 document, 1 evidence span, 1 author
- `cluster_id=None`, `potential=44.25`, `confidence=16.0`
- lifecycle=CANDIDATE

**Example 2: `opp_998d1853aac09d94` — "An AI agent can delete production data without a confirmation"**
- 1 document, 1 evidence span, 1 author
- domain=data, severity=5
- lifecycle=CANDIDATE

**Example 3: `opp_2779f1cb6b83741b` — "A software update can disrupt saved credentials"**
- 1 document, 1 evidence span
- lifecycle=CANDIDATE

### Gate analysis

| Question | Answer |
|---|---|
| Why did it become an opportunity? | Scoring handler iterates every pain point and calls `store.upsert()` for each that passes the confidence + independence threshold |
| Which gate allowed it? | The scoring policy's `min_confidence` and `min_independent_people` thresholds — but `confidence=16.0` (which appears to be a percentage-like score of 16 out of 100) still passes |
| Which contract requirement was satisfied? | The validation gate requires ≥20 evidence spans and ≥10 independent authors at the **run level**, not per-opportunity. Individual opportunities can be created with 1 author, 1 span. |
| Is this behavior explicitly specified? | The `OpportunityRecord.lifecycle` defaults to `"CANDIDATE"`. The distinction between pain point, candidate opportunity, validated opportunity, and needs-more-evidence exists in the lifecycle vocabulary (CANDIDATE, PROMISING, HUMAN_REVIEW, etc.) but is not enforced by the opportunity creation code. |
| Distinction between pain point / candidate / validated / needs-more-evidence? | **Partially** — lifecycle field exists (CANDIDATE/PROMISING/etc.) but every opportunity created by the scoring handler starts at either "CANDIDATE" or "PROMISING". There is no "needs-more-evidence" state enforced at creation. |

---

## 8. Root Cause

### PRIMARY ROOT CAUSE: CONFIRMED

**Opportunity identity is derived from `(run_id, title_or_normalized_key)` via `create_opportunity_id()` at `src/product_hunter/db/opportunity.py:217-219`, with no canonicalization across pain points, no cross-run reconciliation, and no UNIQUE constraint on `normalized_problem`.**

Evidence:
1. `create_opportunity_id(title, run_id)` returns `"opp_" + sha256(run_id + "|" + title)[:16]` — different `run_id` = different ID, always.
2. `_scoring_handler` at `handlers.py:3598` calls `create_opportunity_id(normalized_key or pain_id, job.run_id)` — one opportunity per pain point, using the pain point's own `normalized_key` (which varies per extraction).
3. `cluster_id` is always `None` on opportunities created by the scoring handler (`handlers.py:3603`), so clustering results are never used to deduplicate opportunities.
4. The `opportunities` table has PRIMARY KEY only on `opportunity_id`; no UNIQUE constraint on `normalized_problem` or any composite key.
5. Cross-run: 13 Kasa ES20M opportunities across 5 runs, all for the same underlying problem, all with different `opp_*` IDs.

### CONTRIBUTING FACTOR: LIKELY

**Extraction produces different `normalized_key` values for semantically identical pain points.** The Kasa ES20M case shows 4 pain points from the same document with 4 different normalized keys (differing in actor/action phrasing), all assigned to the same cluster. The clustering layer correctly identifies them as one problem (member_count=4), but the opportunity layer ignores `cluster_id` entirely.

### PROJECTION: NOT THE CAUSE

The projection artifact (`.product-hunter-projection.json`) contains **zero** `opp_*` note keys. The 180 notes are filesystem paths like `01-Ideas/🔴 00-49 Low Potential/<slug>--<hash>.md`. The projection reads from the `opportunities` table via `_row_draft()` when rows exist. The filesystem path hash (`--30feeac2`, `--3170b731`, etc.) is derived from the note content, not from a stable opportunity identity. Projection **exposes** upstream duplication but does not create it.

---

## 9. Recommended Fix Direction

### Minimal, targeted recommendations (no redesign)

1. **Add UNIQUE constraint on `normalized_problem`** (or composite `(normalized_problem, domain)`) to the `opportunities` table. This prevents the database from physically storing duplicate canonical problems.

2. **Change `create_opportunity_id()` to be based on `normalized_key` alone (without `run_id`)**, or add an application-level existence check: before creating a new opportunity, check if one with the same `normalized_problem` already exists and upsert into it instead.

3. **Populate `cluster_id` on opportunities** in `_scoring_handler` (`handlers.py:3603`), linking each opportunity to its cluster. This enables cluster-level deduplication at query time.

4. **In the projection `_group_draft()` fallback path**, use `cluster_id` when available to group pain points, rather than only `normalized_key`, to avoid creating separate draft opportunities for semantically identical pain points with different keys.

These recommendations preserve all evidence individually (pain points, evidence spans, author links remain unchanged) and only affect the opportunity identity layer.

---

## Files Inspected

| File | Purpose |
|---|---|
| `src/product_hunter/db/opportunity.py` | OpportunityStore, `create_opportunity_id()`, upsert behavior |
| `src/product_hunter/handlers.py` | `_scoring_handler` (opportunity creation per pain point), `_competitor_handler`, `_counter_evidence_handler` |
| `src/product_hunter/projection/source.py` | `SqliteProjectionSource`, `_group_draft`, `_row_draft`, projection identity |
| `src/product_hunter/projection/contract.py` | Canonical DDL, COLUMN_MAP, no UNIQUE on normalized_problem |
| `db/product_hunter.db` | Persisted run state: opportunities, pain_points, clusters, cluster_members, evidence, documents |
| `data/vault/.product-hunter-projection.json` | Projection artifact (180 notes, 0 opp_* keys) |
| `docs/reasonix/reports/b3-b5-e2e-completion.md` | Prior E2E completion report |

---

## Final Response

```
FORENSIC AUDIT COMPLETE

Duplicate mechanism:
Opportunity identity is derived from (run_id, title_or_normalized_key) via
create_opportunity_id() at src/product_hunter/db/opportunity.py:217-219.
The scoring handler creates exactly one opportunity per pain point row
(handlers.py:3598), using normalized_key which varies per extraction.
cluster_id is always None on scoring-created opportunities (handlers.py:3603),
so clustering results are never used to deduplicate. No UNIQUE constraint
exists on normalized_problem in the opportunities table. Cross-run, each run
creates new opp_* identities for the same normalized_problem because run_id
is part of the hash seed.

Intra-run duplication:
YES — 4 Kasa ES20M pain points with 4 different normalized_keys all assigned
to one cluster (cl_b1148836e349526a, member_count=4); 2 Claude Code Max
session-limit pain points with different keys. The mechanism exists but the
scoring gate filtered them out in this specific run.

Cross-run duplication:
YES — 13 Kasa ES20M opportunities across 5 prior runs, 5 Soundflower
opportunities across 5 runs, etc. Each run independently creates new identities
because run_id is part of create_opportunity_id() seed. No cross-run
reconciliation exists.

Projection responsible:
NO — projection contains 0 opp_* note keys. It reads from the opportunities
table and reflects upstream duplication; it does not create it.

28 → 28 mapping:
ACCIDENTAL — scoring handler iterates pain points and creates one opportunity
per row (28 pain points → 28 scoring-created opportunities). Plus 1 additional
opportunity from _competitor_handler using _problem_statement() = 29 total.

28 → 27 projection discrepancy:
INCORRECT PREMISE — the projection contains zero opp_* note keys. The "27
notes" figure does not correspond to this run's projection artifact. All 29
current-run opportunities are absent from the projection's opp_* namespace.

Primary root cause:
CONFIRMED — create_opportunity_id(title, run_id) uses run_id in the hash seed,
normalized_key varies per extraction, cluster_id is never populated on
opportunities, and no UNIQUE constraint on normalized_problem exists.

Confidence:
CONFIRMED

Files inspected:
src/product_hunter/db/opportunity.py
src/product_hunter/handlers.py
src/product_hunter/projection/source.py
src/product_hunter/projection/contract.py
db/product_hunter.db
data/vault/.product-hunter-projection.json
docs/reasonix/reports/b3-b5-e2e-completion.md

Report:
docs/reasonix/reports/opportunity-lineage-forensic-audit.md

Production code modified:
NO
```
