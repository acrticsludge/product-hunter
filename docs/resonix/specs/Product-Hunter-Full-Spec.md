# Product Hunter --- Full System Specification

> **Status:** Full-build architecture baseline; implementation details may evolve through explicit ADRs and verified audits.\
> **Purpose:** Build an OpenCode-native, 24/7 autonomous
> product-opportunity research agent.\
> **Primary interface:** OpenCode\
> **Human output:** Obsidian vault\
> **Machine source of truth:** SQLite\
> **Build philosophy:** Build the complete intended system, not a
> throwaway MVP.

------------------------------------------------------------------------

# 1. Mission

Product Hunter continuously searches the public internet for **real,
recurring, independently reported problems** that could become viable
software products.

It should not behave like a generic startup-idea generator.

It must:

1.  Discover real complaints and inefficient workflows.
2.  Capture evidence and provenance.
3.  Identify independent people experiencing the problem.
4.  Normalize different descriptions of the same underlying problem.
5.  Cluster related evidence.
6.  Validate whether the pain is recurring and meaningful.
7.  Research existing alternatives and competitors.
8.  Actively search for counter-evidence.
9.  Score opportunities separately from confidence.
10. Track how opportunities change over time.
11. Generate concrete validation experiments.
12. Continuously update an Obsidian vault.
13. Run autonomously 24/7 with persistent jobs, caching, retries, rate
    limits, and recovery.

Core principle:

> **Evidence graph first, opportunity thesis second.**

No evidence-backed problem should be promoted simply because an LLM
thinks it sounds like a startup.

------------------------------------------------------------------------

# 2. OpenCode-Native Design

The system is built **for OpenCode itself**.

The user should be able to use the Product Hunter through OpenCode's
agent system, e.g.:

``` text
/agents
```

and have a custom:

``` text
product-hunter
```

agent available.

OpenCode is the primary agent interface/orchestrator.

The supporting Python services are infrastructure used by the agent
rather than a separate product the user has to operate manually.

Conceptually:

``` text
YOU
 |
 v
OpenCode
 |
 +-- Product Hunter agent
 |     +-- research subagents
 |     +-- skills
 |     +-- MCP tools
 |     +-- model routing
 |
 +-- normal OpenCode work
```

The user's normal `/models` selection should remain independent from the
Product Hunter's internal model routing.

------------------------------------------------------------------------

# 3. Model Strategy

Do not use one expensive model for everything.

## Fast/simple model

Example: Cat Jam Free

Use for:

-   extraction
-   classification
-   summarization
-   tagging
-   filtering
-   simple source analysis
-   basic deduplication decisions

The exact model name must remain configurable because free-model
availability can change.

## SubQ Snap

Use for:

-   high-volume structured decisions
-   classification
-   ranking
-   filtering
-   large batches of evidence
-   other tasks where structured decisions are more efficient than
    free-form reasoning

SubQ is a decision/processing layer, not the primary OpenCode chat
model.

## Deep reasoning model

Example: DeepSeek V4.1 Flash

Use selectively for:

-   deep investigation
-   competitor analysis
-   ambiguous clustering
-   counter-evidence
-   market reasoning
-   opportunity thesis
-   hard research decisions

## User's interactive model

The model selected by the user in OpenCode's model selector is
independent.

The Product Hunter agent should have configurable model routing.

Example:

``` text
Product Hunter
 |
 +-- extraction --------> fast/free model
 +-- bulk decisions ----> SubQ
 +-- clustering --------> fast/medium model
 +-- investigation -----> DeepSeek
 +-- counter-evidence --> DeepSeek
 +-- final thesis ------> DeepSeek
```

------------------------------------------------------------------------

# 4. OpenCode Agents

Planned agents/subagents:

``` text
product-hunter
researcher
investigator
validator
startup-killer
```

Potential responsibilities:

### product-hunter

Top-level orchestrator.

### researcher

Collects and researches evidence.

### investigator

Deeply investigates promising clusters.

### validator

Tests evidence strength, customer reality, market assumptions, and
validation paths.

### startup-killer

Attempts to disprove promising opportunities.

The exact agent decomposition can be adjusted during implementation, but
the system must preserve separation between broad discovery and
expensive deep investigation.

------------------------------------------------------------------------

# 5. OpenCode Skills

Core skills:

``` text
source-discovery/
web-research/
pain-point-extraction/
pain-point-normalization/
problem-clustering/

evidence-validation/
source-independence/
evidence-provenance/
source-reliability/
entity-resolution/

frequency-analysis/
severity-analysis/
willingness-to-pay/
trend-detection/
temporal-analysis/

competitor-discovery/
competitor-analysis/
counter-evidence/

opportunity-scoring/
uncertainty-estimation/
startup-killer/

validation-experiment/
mvp-designer/
distribution-research/
pricing-research/

research-planner/
research-budgeting/
research-stopping/

opportunity-investigator/
opportunity-report/
```

Important principle:

> Skills encode reusable methodology, not giant prompts.

------------------------------------------------------------------------

# 6. MCP / Tool Layer

## Core integration/tooling

Autonomous research execution is **worker-owned**. The Python Product Hunter
worker calls explicit adapters/clients for collection, analytics, storage,
and model execution. MCPs are an OpenCode integration surface for interactive
exploration and operator-assisted research; they are not the authoritative
execution mechanism for unattended runs.

Interactive OpenCode integrations may include:

``` text
Search MCP
Qdrant MCP
DuckDB MCP
Apify MCP
Playwright MCP
GitHub MCP
Fetch MCP
Filesystem / Git
```

Do not treat Memory or Time MCPs as required infrastructure. Persistent
research memory belongs in SQLite and runtime artifacts; timestamps and
scheduling use the worker/system clock.

### Required tool roles

- **Search MCP** — evidence discovery, competitor discovery, and
  counter-evidence/falsification searches.
- **Qdrant MCP** — semantic evidence retrieval, similarity checks, duplicate
  detection, related-complaint retrieval, and problem clustering.
- **DuckDB MCP** — quantitative analysis of evidence frequency, independent
  people, community diversity, source concentration, historical movement,
  and trends.
- **Apify MCP** — managed/specialized/large-scale crawling when direct
  collection is insufficient.
- **Playwright MCP** — JavaScript-heavy sites requiring a real browser.
- **GitHub MCP** — GitHub Issues, Discussions, PRs, repositories, and
  developer-workflow evidence.
- **Fetch MCP** — direct HTTP/page retrieval for ordinary public pages.
- **Filesystem** — controlled project, runtime, research, and Obsidian file
  access.
- **Git** — versioning and research-history tracking.
- **Memory / Time** — not required MCP dependencies; persistent state and
  timestamps are owned by SQLite/runtime and the worker clock.

### Tool-selection rule

The worker should use the simplest appropriate collection mechanism first:

``` text
Official API / RSS
      ↓
worker Search adapter / direct HTTP
      ↓
worker Fetch adapter
      ↓
Crawl4AI / local crawler
      ↓
worker Playwright adapter
      ↓
worker Apify adapter
```

OpenCode MCP equivalents may be used for interactive exploration, but an
unattended run records and enforces the worker-owned adapter invocation.

The more expensive or complex mechanism must not be used merely because it
is available. However, a required research stage **must** execute its mandated
worker-owned adapter/capability even if the model believes it already knows the
answer.

Tool requirements are enforced by the **Research Contract + Tool Gate**
defined in Section 6A. Agent prompts are not considered sufficient
enforcement.

------------------------------------------------------------------------

------------------------------------------------------------------------

# 6A. Tool Enforcement and Research Contracts

This system must **not rely on agent instructions alone** to make important
tools mandatory. The autonomous research pipeline therefore has a
code-enforced Research Contract and Tool Gate.

The critical architectural distinction is:

``` text
OpenCode
  = interaction / operator interface

Product Hunter Worker
  = authoritative pipeline orchestrator

Research Contract + Tool Gate
  = execution enforcement

SQLite
  = authoritative state
```

OpenCode agents may assist with reasoning, exploration, and user interaction,
but they must not be the sole authority for whether a research stage is
complete.

## Research Contract

Every research job declares:

- required stages
- required tools or tool capabilities
- required artifacts
- minimum evidence thresholds
- quality predicates
- budgets
- stopping conditions
- retry policy
- promotion requirements

Example:

``` yaml
research_contract:
  required_stages:
    - discovery
    - evidence_collection
    - normalization
    - clustering
    - validation
    - counter_evidence
    - scoring
    - investigation

  discovery:
    required_tools:
      - search
    required_outputs:
      - source_records
      - provenance
    quality_checks:
      - non_empty_queries
      - source_diversity

  clustering:
    required_tools:
      - qdrant
    required_outputs:
      - cluster_assignments
      - similarity_checks
    quality_checks:
      - nearest_neighbor_checked
      - duplicate_candidates_reviewed

  validation:
    required_tools:
      - search
      - qdrant
      - duckdb
    required_outputs:
      - independent_source_count
      - independent_person_count
      - community_count
      - frequency_analysis
      - trend_analysis
      - evidence_summary

  investigation:
    required_tools:
      - search
      - qdrant
      - duckdb
    required_outputs:
      - competitors
      - alternatives
      - counter_evidence
      - opportunity_score
      - confidence

  hard_requirements:
    require_provenance: true
    require_counter_evidence: true
    require_competitor_search: true
    require_qdrant_check: true
    require_duckdb_analysis: true
    require_claim_evidence_links: true
```

## Worker-owned execution

The Product Hunter Python worker owns autonomous stage execution.

The worker:

1. loads the Research Contract;
2. acquires the job lease;
3. invokes deterministic collectors/analysis code;
4. invokes LLMs only through explicit stage interfaces;
5. records tool calls and artifacts;
6. validates outputs;
7. commits the authoritative SQLite transaction;
8. emits downstream projection tasks;
9. advances the checkpoint only after the stage passes.

OpenCode can enqueue, inspect, pause, resume, or interact with a research run.
It is not the transactional owner of the research pipeline.

This architecture preserves the OpenCode-native user experience while making
the autonomous worker independently restartable and testable.

## Tool Gate

The Tool Gate must validate **results and derivation**, not merely activity.

A tool call log such as:

``` text
search -> result_count=27
```

is not sufficient evidence that validation happened.

The gate validates:

- the required tool actually executed;
- the execution belongs to the current run/stage;
- its result is referenced by a produced artifact;
- the artifact was produced by the worker from the tool result;
- required schemas validate;
- required minimum counts/thresholds are met;
- deterministic quality predicates pass;
- provenance links are intact;
- contradictory evidence was considered where required;
- the stage stayed within its budget.

### Quality predicates

The shipped baseline uses concrete, testable defaults. Contracts may override
these only by versioned configuration with an explicit reason.

``` yaml
baseline_gate_thresholds:
  discovery:
    min_distinct_queries: 8
    min_source_domains: 3
    min_new_documents: 20
  competitor_search:
    min_distinct_queries: 6
    min_query_families: 3
    min_candidate_urls: 10
  validation:
    min_independent_authors: 10
    min_independent_communities: 3
    min_query_families: 4
    min_evidence_spans: 20
  counter_evidence:
    min_distinct_queries: 6
    min_query_families: 3
    min_counter_evidence_items_or_explicit_zero: 1
  extraction:
    quote_verbatim_required: true
    source_span_required: true
  clustering:
    min_neighbors_checked_per_cluster: 5
  promotion:
    require_claim_evidence_links: true
    require_competitor_gate: true
    require_counter_evidence_gate: true
```

`min_counter_evidence_items_or_explicit_zero: 1` means the gate accepts either
at least one normalized counter-evidence item **or** a recorded zero-result
outcome after all required query families completed. It does not permit an
unsearched or failed search to count as zero evidence.

Examples:

``` text
Required competitor search
    ↓
at least 6 distinct search queries
    ↓
queries include at least 3 query families: problem, solution/competitor,
and alternative/negative formulations
    ↓
results normalized into competitor candidates
    ↓
candidate URLs retained
    ↓
duplicate/copy sources removed
    ↓
"no strong competitor" claim is allowed only after these checks
```

For extraction:

``` text
LLM extracts pain point + quote
              ↓
Pydantic schema validation
              ↓
quote must exist verbatim in source document
              ↓
source/document IDs must exist
              ↓
normalization must reference source evidence
              ↓
accepted
```

LLM prose cannot satisfy a gate by itself.

## OpenCode enforcement boundary

Current OpenCode documentation verifies granular allow/ask/deny permissions
for built-in, custom, and MCP tools, custom tools, interactive `question`
prompts, and plugin hooks such as `tool.execute.before` / `tool.execute.after`.
These are integration and safety surfaces only; they are **not** the hard
research gate. The worker remains authoritative for autonomous execution.

Official references checked for this baseline:

- https://docs.opencode.ai/docs/tools/
- https://docs.opencode.ai/docs/permissions/
- https://docs.opencode.ai/docs/agents/
- https://docs.opencode.ai/docs/custom-tools/
- https://dev.opencode.ai/docs/plugins/

The implementation should therefore use two layers:

``` text
OpenCode plugin / permissions
    ↓
interactive safety + command routing + audit hooks

Product Hunter worker
    ↓
authoritative research contract + state + quality gate
```

Where practical, an OpenCode plugin can reject or annotate prohibited
tool usage, attach the current research/run ID, and forward audit events to
the worker. The worker remains authoritative.

## Restricted toolsets

Subagents receive only the tools required for their role.

``` text
researcher
  Search, Fetch, GitHub, Playwright, Apify

validator
  Search, Fetch, Qdrant, DuckDB

investigator
  Search, Fetch, GitHub, Playwright, Qdrant, DuckDB

startup-killer
  Search, Fetch, GitHub, Qdrant, DuckDB

Filesystem is not granted to agents that process untrusted web content.
If an implementation requires filesystem access for such a stage, expose
only a worker-owned, read-only adapter scoped to `runtime/` and deny arbitrary
paths, writes, shell access, and credential access.
```

For autonomous execution, these capabilities should preferably be exposed
through worker-owned adapters rather than unrestricted raw tool access.

## Tool-call provenance

Every material tool operation records:

``` json
{
  "run_id": "RH-20261018-0017",
  "stage": "validation",
  "agent": "validator",
  "tool": "search",
  "purpose": "independent evidence discovery",
  "timestamp": "...",
  "query_or_operation": "...",
  "result_ref": "...",
  "result_count": 27,
  "status": "success"
}
```

The `result_ref` points to a durable, content-addressed artifact or normalized
tool result. The system never treats a hand-written summary as the original
tool result.

## Mandatory evidence loop

For a serious opportunity:

``` text
Search
  -> collect evidence
  -> quarantine / sanitize
  -> normalize + deduplicate
  -> Qdrant similarity check
  -> cluster
  -> independence analysis
  -> DuckDB frequency/trend analysis
  -> competitor/alternative searches
  -> counter-evidence searches
  -> claims + evidence links
  -> score + confidence
  -> deep investigation
  -> Tool Gate
```

A serious opportunity cannot be promoted when a mandatory stage failed,
timed out, exceeded its budget, or produced invalid artifacts.

------------------------------------------------------------------------

# 6B. LLM Output Validation and Evaluation

LLMs are probabilistic components, not trusted databases.

All structured LLM outputs must:

- use Pydantic/JSON-schema validation;
- contain stable IDs for referenced evidence;
- retain model/provider/version;
- retain prompt/config version where appropriate;
- pass deterministic consistency checks;
- fail closed when required fields are missing.

For evidence extraction, every quoted span must be verified against the
captured source text.

For high-impact operations such as clustering, entity resolution,
independence classification, and pain severity classification, the project
must maintain a small hand-labeled evaluation set.

The evaluation set should cover at least:

- extraction correctness
- duplicate/repost detection
- author independence
- problem normalization
- cluster merge/split decisions
- competitor identification
- counter-evidence classification

Model changes must be evaluated against this set before becoming the default
for autonomous runs.

# 7. Discovery Sources

Potential evidence sources:

-   Reddit
-   GitHub Issues
-   GitHub Discussions
-   GitHub PRs
-   Hacker News
-   product reviews
-   app reviews
-   niche forums
-   community sites
-   public discussion boards
-   blogs/comments where useful
-   public web pages
-   RSS/feeds
-   APIs
-   SubQ-supported discovery

High-signal search patterns can include:

``` text
"how do you deal with"
"there has to be a better way"
"I hate having to"
"manually"
"spreadsheet"
"takes me hours"
"is there a tool"
"workaround"
"manual"
"doesn't support"
"feature request"
```

Keyword search is only discovery. Semantic normalization and clustering
determine whether different wording describes the same problem.

## Discovery legality and source registry

Every source definition must include at least:

``` yaml
source_id: reddit
source_type: community
terms_status: unknown | reviewed | permitted | restricted | prohibited
allowed_use: discovery | retrieval | analysis | storage | none
reviewed_at: ...
review_basis: ...
robots_or_api_notes: ...
collector: api | rss | http | browser | managed
rate_limit_policy: ...
```

Collectors must not launch against a source whose `terms_status` or
`allowed_use` disallows the planned operation. A source requiring manual legal
or platform-policy review remains blocked until that status is updated.
Reddit/API terms, robots/access rules, and any Apify actor's permitted use
must be reviewed for the intended collection mode; an available scraper is
not automatically authorized.

## 7A. Query Frontier and Exploration Strategy

Discovery uses a persistent query frontier rather than a fixed list of
keywords. Each query stores a parent query, query family, novelty yield,
duplicate yield, source yield, independent-author yield, and last-seen time.

Initial query families include:

``` text
1. direct pain / complaint
2. manual workflow / repetitive work
3. workaround / hack / spreadsheet
4. alternative-to / replacement
5. failed solution / dissatisfaction
6. feature request / missing capability
7. switching / migration pain
8. cost / time / error consequence
9. team / role-specific workflow
10. trigger / incident / deadline-driven pain
```

For each promising branch, generate controlled variants rather than arbitrary
LLM paraphrases. A branch may expand into:

``` text
problem wording
  -> synonym / domain wording
  -> workaround wording
  -> alternative / competitor wording
  -> failed-solution wording
  -> consequence / cost wording
  -> role / industry wording
  -> source/community-specific wording
```

The frontier records novelty and saturation. Prefer queries that are expected
to discover new authors, communities, or problem variants; deprioritize queries
whose recent result sets are dominated by already-seen documents or propagated
copies. Negative and alternative-to queries are mandatory for validation so the
system does not only collect people agreeing with the initial thesis.

A query frontier is complete only when the run's budget or saturation rule is
met; a fixed number of popular search results is not a sufficient stopping
criterion.

------------------------------------------------------------------------

# 8. Collection / Crawling Stack

Use the cheapest appropriate collection mechanism.

``` text
simple HTTP
    -> httpx

HTML extraction
    -> BeautifulSoup / lxml

local larger-scale crawling
    -> Crawl4AI

JavaScript-heavy sites
    -> Playwright MCP

specialized / managed / large scraping
    -> Apify
```

Order of preference:

1.  Official APIs
2.  RSS/feeds
3.  Direct HTTP
4.  Local crawler
5.  Browser automation
6.  Managed scraper

Do not design around bypassing rate limits or access controls.

------------------------------------------------------------------------

# 9. Rate Limiting / Caching

A source registry should track:

``` text
source
query
last_seen
cursor
ETag
Last-Modified
last_fetch
HTTP status
request budget
```

The collector should support:

-   per-source rate limits
-   retries
-   exponential backoff
-   429 handling
-   ETag / Last-Modified
-   content caching
-   URL deduplication
-   content hashing
-   source-specific budgets

Never repeatedly fetch content that has not changed.

------------------------------------------------------------------------

# 10. Data Processing Pipeline

Full pipeline:

``` text
DISCOVER
   |
COLLECT
   |
RAW EVIDENCE
   |
NORMALIZE
   |
DEDUPLICATE
   |
ENTITY RESOLUTION
   |
PAIN EXTRACTION
   |
EMBEDDING
   |
PROBLEM CLUSTERING
   |
INDEPENDENT EVIDENCE VALIDATION
   |
SCORING
   |
COMPETITOR RESEARCH
   |
COUNTER-EVIDENCE
   |
DEEP INVESTIGATION
   |
OPPORTUNITY
   |
VALIDATION EXPERIMENT
```

------------------------------------------------------------------------

# 11. Evidence Model

Every important claim should be traceable to evidence.

Evidence is an immutable observation plus provenance. Interpretations such as
"this is severe" or "these are independent users" are separate derived facts.

Evidence should retain:

- source URL
- source type
- source/community
- author reference when publicly available and appropriate
- publication date
- capture date
- original captured text or retained excerpt
- exact span/quote used for extraction
- normalized problem reference
- source reliability
- evidence strength
- extraction model/version
- extraction run
- linked cluster
- linked opportunity
- content hash
- live/deleted status

The system must distinguish:

``` text
RAW SOURCE
   ↓
DOCUMENT
   ↓
EVIDENCE SPAN
   ↓
CLAIM / PAIN POINT
   ↓
CLUSTER
   ↓
OPPORTUNITY
```

No downstream conclusion should depend on an untraceable free-form summary.

Do not count repeated copies of the same content as independent evidence.

------------------------------------------------------------------------

# 12. Independent Evidence

The system must distinguish:

``` text
147 total mentions
83 independent people
12 independent communities
```

from:

``` text
147 copies of one viral complaint
```

Independence is a **computed property**, not an LLM-provided number.

## Independence graph

The schema must support:

``` text
author
  ├── same-person-suspect ──> author
  │
evidence
  ├── repost ──> evidence
  ├── quote ──> evidence
  ├── crosspost ──> evidence
  └── syndication ──> evidence
```

Each author record should include:

- source/platform
- stable handle or pseudonymous key when available
- hashed identity key where appropriate
- identity confidence
- first/last seen
- deletion status

Each propagation edge should include:

- source evidence
- derived evidence
- propagation type
- detection method
- confidence

The system must discount suspected propagation and uncertain identity rather
than treating every visible account as a new independent person.

## Independence calculation

A deterministic analytics layer computes:

- raw mention count
- canonical-content count
- independent author count
- uncertain-author count
- independent community count
- source concentration
- propagation-adjusted evidence count

Example:

``` text
147 mentions
→ 91 canonical observations
→ 63 high-confidence independent authors
→ 8 uncertain authors
→ 12 communities
→ 4 propagation chains
```

The confidence model can then discount uncertain identities.

Never claim that an exact number of real-world people exists when the source
only provides weak pseudonymous identity evidence.

------------------------------------------------------------------------

# 13. Strong vs Weak Pain

Weak signal:

> "I wish this app had feature X."

Strong signal:

> "I spend three hours every week manually doing X."

The system should prioritize:

-   recurring workflows
-   time-consuming manual work
-   expensive workarounds
-   repeated frustration
-   revenue loss
-   operational risk
-   compliance burden
-   tasks people already pay to solve
-   problems where people actively search for solutions

------------------------------------------------------------------------

# 14. Semantic Memory

## Embeddings

Use local embeddings, initially with:

``` text
sentence-transformers
```

## Vector database

Use:

``` text
Qdrant + Qdrant MCP
```

Qdrant is a **required part of the evidence loop**, not an optional enhancement.

Purpose:

-   semantic similarity
-   problem clustering
-   related evidence retrieval
-   duplicate/similar complaint detection
-   evidence recall during investigation
-   nearest-neighbor checks before creating or merging clusters
-   semantic retrieval of supporting and contradictory evidence

Before a new problem cluster is created or a serious opportunity is promoted, the system must perform the configured Qdrant checks.

Example:

``` text
"I spend 3 hours fixing inventory every Sunday"

should match:

"Every week I manually reconcile my spreadsheet with WhatsApp orders."
```

------------------------------------------------------------------------

## Cluster Lineage and Versioning

Clustering is versioned. Re-clustering may merge or split clusters, but prior
cluster identities remain immutable historical records.

``` text
clusters
  id
  version
  merged_into
  split_from
  status
  created_at

cluster_members
  cluster_id
  pain_point_id
  valid_from
  valid_to
  assignment_confidence
  clustering_run_id
```

An opportunity stores the cluster version(s) it was based on. If a cluster is
merged, split, or superseded, score history remains attached to the historical
cluster and a lineage edge points to the successor. No report may silently
reinterpret an old score as belonging to a new cluster.

------------------------------------------------------------------------

# 15. Database

## SQLite

SQLite is the primary machine source of truth.

Initial authoritative tables:

``` text
sources
fetches
documents
authors
author_links
communities
evidence
propagation_edges
pain_points
evidence_pain_links
clusters
cluster_members
competitors
opportunities
claims
claim_evidence
scores
score_snapshots
research_runs
research_checkpoints
research_events
research_artifacts
jobs
stage_attempts
tool_calls
projection_tasks
spend_ledger
human_decisions
```

`people` is not a separate canonical table; author identity is represented by
`authors` plus explicit identity-confidence/link records. `events` is not a
second generic event store; research lifecycle events use `research_events`.

The filesystem and Obsidian are projections of database state, not the
canonical state machine. Research continuity is stored in the persistent
registry, not in OpenCode chat history.

------------------------------------------------------------------------

# 15A. Cross-Store Consistency and Transactional Outbox

SQLite is the authoritative transactional store. Qdrant, DuckDB, Obsidian,
runtime files, and Git are downstream projections or analytical surfaces.

There is **no distributed transaction** across these systems.

Therefore the system uses:

``` text
SQLite transaction
      ↓
projection_tasks / outbox
      ↓
idempotent worker
      ↓
Qdrant / DuckDB / Obsidian / reports
```

A logical research mutation must first commit its authoritative SQLite state
and an associated downstream projection task in the same SQLite transaction.

Each projection task has:

``` text
id
target
entity_type
entity_id
entity_version
idempotency_key
status
attempts
last_error
created_at
completed_at
```

Downstream writers use deterministic IDs/content hashes and idempotent
upserts.

If the process crashes:

``` text
SQLite committed + projection not completed
        ↓
outbox task remains pending
        ↓
worker retries
        ↓
projection converges
```

A downstream failure must never cause the authoritative evidence row to be
rolled back after the SQLite transaction has committed.

## Qdrant consistency

Every vector stores:

- source/entity ID
- embedding model
- embedding version
- content hash
- created_at

Changing the embedding model creates a re-embedding job rather than silently
mixing vector spaces.

## DuckDB consistency

DuckDB reads authoritative SQLite state or approved analytical snapshots.
It must not become a second operational source of truth.

## Obsidian consistency

Obsidian is generated from authoritative state. Generated notes include stable
IDs and content hashes so the projection can be reconciled safely.

# 16. Analytics

Use:

``` text
DuckDB + DuckDB MCP
```

DuckDB is a **required verification and analytics layer**. SQLite remains the operational source of truth; DuckDB is responsible for analytical questions that should not be answered by model intuition.

For serious validation, the agent should use DuckDB to verify counts and trends such as independent people, communities, source concentration, evidence growth, and opportunity movement.

Analytical workloads include:

-   historical trend analysis
-   source-quality analysis
-   frequency analysis
-   growth of problem clusters
-   opportunity movement
-   cohort analysis
-   large research datasets

Architecture:

``` text
SQLite
= application state

DuckDB
= analytical engine
```

------------------------------------------------------------------------

# 17. Queue

Use a persistent SQLite job queue. SQLite is also the scheduler state, so the
worker does not depend on a second queue service for correctness.

``` text
jobs
-----
id
run_id
stage
step
payload_ref
priority
status
attempts
max_attempts
idempotency_key
worker_id
lease_epoch
lease_expires_at
created_at
scheduled_for
completed_at
last_error
```

Every job is idempotent and fenced. The queue is consumed by one supervised
worker process at a time; multiple scheduler invocations must not create
overlapping workers.

Later alternatives such as Redis/BullMQ should only be introduced if measured
workload or reliability requirements demonstrate that SQLite cannot meet the
need.

------------------------------------------------------------------------

# 18. Scheduler

Target recurring behavior:

``` text
Every 15 minutes
    source collection

Every hour
    evidence processing

Every 6 hours
    re-clustering / re-evaluation

Nightly
    deep investigation

Morning
    daily brief
```

The exact intervals are configurable, but scheduling must not launch a fresh
worker process every interval. Use **one supervised long-running worker** with
a single-instance lock. The SQLite job queue is the scheduler of record.

On Windows, Task Scheduler may start/restart the supervisor after boot or
crash, but the supervisor must first acquire the single-instance lock and then
run the persistent worker. If the laptop was asleep or powered off, missed
work becomes overdue queued work; the worker catches up according to priority
and budgets rather than pretending the jobs ran while offline.

------------------------------------------------------------------------

# 19. Event System

Use internal events such as:

``` text
NEW_SOURCE
NEW_EVIDENCE
NEW_PAIN_POINT
CLUSTER_UPDATED
OPPORTUNITY_PROMOTED
OPPORTUNITY_REJECTED
COMPETITOR_FOUND
EVIDENCE_CHANGED
SCORE_CHANGED
```

Initially these can live in SQLite.

------------------------------------------------------------------------

# 20. Opportunity Scoring

Opportunity **Potential** and **Confidence** are separate dimensions and must
not share evidence-quality or trend terms in a way that double-counts the
quality of evidence.

Example:

``` text
Potential: 96
Confidence: 27%
```

is not equivalent to:

``` text
Potential: 94
Confidence: 91%
```

## Potential

Potential estimates the attractiveness of solving the problem **if the
problem is real and sufficiently prevalent**. It should consider:

- severity / consequence
- frequency of the underlying workflow
- willingness-to-pay tier
- reachable market size
- current alternatives and switching friction
- competitive gap / differentiation
- buildability / technical feasibility
- distribution accessibility
- retention or recurrence potential

Potential must **not** directly include evidence quality, source reliability,
independence, extraction agreement, or confidence. Trend may be reported as a
separate signal; it must not silently inflate potential.

### Market-size method

Market potential must use an explicit bottom-up range where possible:

``` text
reachable population
  × plausible problem incidence
  × reachable adoption range
  × annualized realistic price range
  = annual opportunity range
```

Store low / base / high assumptions and cite the evidence supporting each
material assumption. When a reliable market-size estimate is unavailable,
report an uncertainty-bounded proxy rather than inventing TAM.

### Willingness-to-pay tiers

Use explicit WTP evidence tiers:

``` text
W4  demonstrated payment / paid solution / stated budget
W3  explicit intent to pay or active search for paid solution
W2  costly workaround / meaningful time or money already spent
W1  strong complaint with no payment signal
W0  weak or incidental complaint
```

WTP is a potential input, not proof that a market exists.

### Niche override

A small reachable market may still rank highly when severity and WTP are
exceptionally strong. Do not impose a universal market-size floor that rejects
profitable niche products. Record the reason for any niche override.

## Confidence

Confidence estimates how strongly the collected evidence supports the thesis.
It may consider:

- independent-author count, discounted for identity uncertainty
- independent community diversity
- source-reliability mix
- temporal persistence
- propagation-adjusted evidence
- extraction/model agreement
- successful counter-evidence search coverage
- contradiction rate
- freshness / staleness

Confidence is evidence quality; Potential is opportunity attractiveness. They
are stored and explained separately.

## Output

Reports should provide a plausible Potential range, a Confidence value/range,
key assumptions, WTP tier, market-size range, competition direction, and the
claims/evidence that materially drove each dimension.

Exact weights are configurable and versioned. Every score snapshot records the
scoring-policy version and input snapshot IDs.

------------------------------------------------------------------------

# 21. Counter-Evidence

Every promising opportunity must have a deliberate attempt to kill it.

Search for:

-   existing solutions
-   strong competitors
-   free alternatives
-   low willingness to pay
-   weak pain
-   infrequent problem
-   market saturation
-   technical blockers
-   regulatory constraints
-   people saying the problem is not important
-   evidence contradicting the initial thesis

The agent should not become an idea-confirmation machine.

------------------------------------------------------------------------

# 22. Research Stopping and Budgets

Add a dedicated:

``` text
research-stopping
```

capability. The agent should know when it has sufficient evidence and stop
spending resources on a rabbit hole. Stopping is a worker-enforced contract,
not an LLM suggestion.

## Budget dimensions

Every research run has a budget object with at least:

``` text
wall_time_seconds
tool_calls_total
search_queries
fetch_requests
browser_minutes
crawl_items
llm_input_tokens
llm_output_tokens
model_calls
subq_calls
embedding_items
usd_cost
source_requests_per_source
saturation_threshold
```

Each dimension can be scoped at:

``` text
per-stage
per-run
global daily / rolling window
per-source
per-model/provider
```

The worker enforces budgets before scheduling work and again before expensive
external/model calls. A call that would exceed a hard limit is not started.
Soft thresholds may trigger strategy changes or early stopping.

Budget state is persisted in SQLite and checkpoints. Every spend-bearing tool
or model call writes a `spend_ledger` record containing run/stage, provider or
tool, quantity, estimated/actual cost, timestamp, and budget dimension.

## Saturation stopping

Research should stop expanding a query frontier when incremental searches
produce little or no new independent evidence, entities, communities, or
meaningful counter-evidence for a configurable number of consecutive batches.
Saturation is evidence about search yield, not proof that the market is
exhausted.

Example default contract values are configurable but concrete in the shipped
baseline:

``` text
minimum independent authors before validation: 10
minimum independent communities: 3
minimum validation query families: 4
maximum consecutive low-yield batches before frontier stop: 3
maximum retries per stage attempt: 2
```

A research contract may raise or lower these values with an explicit reason.

------------------------------------------------------------------------

# 23. Opportunity Lifecycle

Opportunity state is separate from pipeline stage and research-run state.

## Opportunity states

``` text
CANDIDATE
VERIFYING
PROMISING
HUMAN_REVIEW
VALIDATED
REJECTED
ALREADY_SOLVED
INSUFFICIENT_EVIDENCE
STALE
DEAD
RESURRECTED
```

An opportunity may move through research stages repeatedly without changing
its business state.

Examples:

``` text
CANDIDATE
  -> VERIFYING
  -> PROMISING
  -> HUMAN_REVIEW
  -> VALIDATED

PROMISING
  -> ALREADY_SOLVED
  -> REJECTED
  -> INSUFFICIENT_EVIDENCE
  -> STALE

STALE
  -> VERIFYING
  -> RESURRECTED
```

"Already solved" and "bad problem" remain separate concepts.

A problem can have extremely high pain but poor opportunity because competition
is overwhelming.

The exact transition rules, required evidence, and terminal behavior are
defined in the opportunity state machine implementation and tested as code.

------------------------------------------------------------------------

# 24. Opportunity History

Track score and confidence over time.

Example:

``` text
Oct 17 -> 61
Oct 18 -> 69
Oct 20 -> 78
Oct 24 -> 91
```

Also record why the score changed.

Ideas can rise, fall, become stale, be rejected, or later be resurrected
when new evidence appears.

------------------------------------------------------------------------

# 25. Competitor / Market Research

Serious opportunities should receive deeper research into:

``` text
competitors
existing alternatives
pricing
customer segments
market
distribution
workflow
switching costs
technical feasibility
```

The system should explicitly ask:

> Why has this not already been solved?

and:

> Why could it be solved now?

------------------------------------------------------------------------

# 26. Validation Experiments

The system should not end at:

> "This could be a \$10M startup."

It should produce concrete next actions.

Examples:

-   interview 10 target users
-   find 20 people actively requesting a solution
-   run a landing-page test
-   test willingness to pay
-   manually deliver the proposed service
-   prototype a workflow
-   measure time/cost saved

Every serious opportunity should have a recommended validation
experiment.

------------------------------------------------------------------------

# 27. Obsidian Output

Obsidian is the human-facing research interface.

``` text
Product-Hunter-Vault/
|
+-- 00-Dashboard/
|   +-- README.md
|   +-- Top Opportunities.md
|   +-- Recent Discoveries.md
|   +-- Rising Opportunities.md
|   +-- Falling Opportunities.md
|   +-- Needs Human Review.md
|   +-- Daily Briefs/
|
+-- 01-Ideas/
|   +-- 🔥 90-100 Exceptional/
|   +-- 🟢 80-89 Strong/
|   +-- 🟡 70-79 Interesting/
|   +-- ⚪ 50-69 Weak Evidence/
|   +-- 🔴 00-49 Low Potential/
|
+-- 02-Research/
|   +-- Sources/
|   +-- Communities/
|   +-- Markets/
|   +-- Trends/
|
+-- 03-Daily-Reports/
|
+-- 04-Experiments/
|   +-- Active/
|   +-- Completed/
|   +-- Failed/
|
+-- 05-Research-Requests/
|
+-- 99-Archive/
    +-- Already-Solved/
    +-- Rejected/
    +-- Insufficient-Evidence/
    +-- Stale/
    +-- Dead/
```

------------------------------------------------------------------------

# 28. Individual Idea Folder

Example:

``` text
001 - WhatsApp Inventory Reconciliation/
|
+-- IDEA.md
+-- Evidence/
+-- Competitors/
+-- Research/
+-- Experiments/
+-- History/
```

`IDEA.md` contains current state.

Suggested frontmatter:

``` yaml
---
id: 001
name: WhatsApp Inventory Reconciliation
status: INVESTIGATING

score: 91
confidence: 0.87
rank: 1

evidence:
  total: 147
  independent_people: 83
  communities: 12

severity: 8.7
frequency: 9.1
willingness_to_pay: 7.8
competition: 5.2
buildability: 9.0

first_seen: 2026-10-18
last_updated: 2026-10-18
---
```

------------------------------------------------------------------------

# 29. Dashboard

Dashboard should show:

``` text
# Product Hunter

Last scan: ...

## TOP OPPORTUNITIES

| Rank | Opportunity | Score | Confidence | Evidence |

## NEW TODAY

...

## MOVING UP

...

## LOSING CONFIDENCE

...

## REJECTED TODAY

...

## NEEDS HUMAN ATTENTION

...
```

------------------------------------------------------------------------

# 30. Daily Brief

Example:

``` text
03-Daily-Reports/2026-10-18.md
```

Include:

-   top opportunities
-   new evidence
-   opportunities moving up
-   opportunities moving down
-   rejected ideas
-   new competitors
-   major counter-evidence
-   recommended validation experiments
-   notable trends

------------------------------------------------------------------------

# 31. Research Requests

Obsidian should support two-way interaction.

User can create:

``` text
05-Research-Requests/
```

with notes such as:

``` text
#research-more
```

or:

``` text
Investigate problems for small retailers in India.
```

The agent can pick these up as research jobs.

------------------------------------------------------------------------

# 32. Git

Git tracks changes to:

-   agent configuration
-   skills
-   code
-   scoring configuration
-   Obsidian notes
-   opportunity evolution

This provides historical visibility into how the research changed.

------------------------------------------------------------------------

# 33. Runtime / Checkpoints

Autonomous runs need recoverable state.

``` text
runtime/
+-- researches/
|   +-- <research_id>/
|       +-- manifest.json
|       +-- checkpoints/
|       +-- artifacts/
|       +-- logs/
|
+-- runs/
|   +-- <run_id>/
|       +-- plan.json
|       +-- discoveries.json
|       +-- intermediate.json
|       +-- result.json
|
+-- failed/
+-- checkpoints/
```

SQLite is authoritative; runtime files are durable, content-addressed mirrors
used for recovery, debugging, and inspection.

A crash, laptop shutdown, OpenCode restart, or model-context loss must never
force a complete restart.

Checkpointing is based on **idempotent stage boundaries**, not an assumption
that an LLM generation can be resumed token-for-token. If an LLM call is
interrupted, the current step is safely re-executed using its idempotency key.

Every checkpoint records:

- completed item IDs
- pending item IDs
- source cursors
- artifact references
- contract status
- retry state
- budget state
- stage attempt
- worker lease epoch

# 34. Observability

Initial observability is worker-owned and persisted in SQLite/runtime:

``` text
research_runs
stage_attempts
tool_calls
spend_ledger
research_events
structured logs
checkpoint artifacts
```

Track:

- LLM traces and model/version metadata
- tool calls and result references
- token/cost usage
- latency
- failures/retries
- stage/gate outcomes
- score snapshots

Optional external observability such as Langfuse may be added only after the
core evidence pipeline is correct and the Phase 7 benchmark/requirement
justifies the added dependency. It must remain a downstream projection and
never become the source of truth for research state.

Example trace:

``` text
Research run
 |
 +-- 312 sources
 +-- 1,927 extracted complaints
 +-- 1,103 unique
 +-- 183 clusters
 +-- 21 promising
 +-- 5 investigated
 +-- 2 survived counter-evidence
```

------------------------------------------------------------------------

# 35. Infrastructure

Initial local infrastructure:

``` text
Windows
|
+-- OpenCode
+-- Python workers
+-- SQLite
+-- DuckDB
+-- Qdrant
+-- Docker Desktop
+-- Obsidian
+-- Git
+-- Windows Task Scheduler
```

Docker Compose should manage supporting services where appropriate.

------------------------------------------------------------------------

# 36. Python Stack

Core libraries:

``` text
Python
Pydantic
httpx
BeautifulSoup
lxml
sentence-transformers
pytest
```

Potential supporting libraries:

``` text
tenacity
```

for retry logic.

Additional libraries should only be added when they solve a concrete
requirement.

------------------------------------------------------------------------

# 37. Security / Permissions

Because the system is autonomous, separate safe actions from dangerous
actions.

Automatic:

``` text
read public web
search
analyze
write research DB
write Obsidian through the projection worker
update research notes through controlled writers
```

Approval required:

``` text
destructive shell commands
Git push
external account actions
external posting
irreversible operations
```

API keys and secrets must stay outside committed source.

OpenCode permissions should deny unnecessary tools for research agents and use
`ask` for actions requiring human approval. OpenCode supports allow/ask/deny
permissions and agent-specific permission policies.

# 37A. Untrusted Content and Prompt-Injection Boundary

All internet-derived content is **untrusted data**, including:

- webpages
- Reddit/forum posts
- GitHub issues
- repository files
- comments
- documentation
- search snippets
- scraped metadata

Untrusted content must never be treated as system instructions.

## Trust boundary

``` text
                INTERNET
                   |
                   v
          Raw content capture
                   |
                   v
             QUARANTINE
                   |
          sanitize / parse / hash
                   |
                   v
       structured evidence records
                   |
                   v
       LLM analysis with NO tools
                   |
                   v
        validated structured output
                   |
                   v
       worker-owned authoritative DB
                   |
                   v
      deep reasoning on sanitized data
```

An LLM that is processing raw/untrusted content must not simultaneously have
unrestricted write, shell, credential, or external-account capabilities.

Raw pages may contain text such as:

``` text
IGNORE PREVIOUS INSTRUCTIONS
RUN THIS COMMAND
REVEAL YOUR ENVIRONMENT VARIABLES
```

Such text is evidence content, not an instruction.

The system must:

1. preserve raw content separately;
2. extract only the required content into structured records;
3. fence source text clearly as untrusted;
4. use schema-constrained outputs;
5. verify all referenced source spans;
6. prevent source content from becoming tool arguments automatically;
7. prevent secrets/system prompts from entering research context;
8. require explicit code paths for any external side effect.

**Validation does not make source-derived text trusted.** Quotes, summaries,
metadata, URLs, titles, and any other fields derived from internet content stay
marked as untrusted when passed to later reasoning stages. The deep-reasoning
model may reason over them as evidence, but must not treat them as executable
instructions or tool arguments.

## SSRF and URL safety

Any worker component that fetches a model-proposed or source-derived URL must
apply an explicit network policy before the request:

``` text
Allowed schemes: https, http (http only where source policy explicitly allows)
Blocked: file:, data:, javascript:, ftp:, gopher:, custom schemes
Blocked hosts: localhost, loopback, link-local, multicast, metadata endpoints
Blocked networks: RFC1918/private ranges and other non-public address space
DNS rebinding protection: resolve + validate destination before connect and
re-check after redirects
Redirect limit: bounded and revalidated at every hop
```

Do not fetch arbitrary URLs simply because an LLM emitted them. URL admission
is a deterministic worker function, not an LLM decision.

## Browser isolation

Playwright runs that touch untrusted sites use a throwaway browser context or
profile with:

- no user cookies
- no saved credentials
- no extension access
- downloads disabled unless an explicit collector contract requires them
- bounded navigation timeouts
- bounded page/resource sizes
- no access to unrelated local files
- no privileged browser APIs

Browser output is treated as untrusted evidence and enters the same quarantine
pipeline as HTTP responses.

## Filesystem boundary

Agents processing untrusted pages do not receive arbitrary Filesystem access.
If a stage needs filesystem access, it uses a read-only worker adapter scoped to
`runtime/` and content-addressed artifacts. It cannot write arbitrary paths,
read secrets, or invoke shell commands.

## Obsidian projection safety

Scraped text written into Obsidian must be rendered as inert Markdown data.
Projection code must neutralize or escape HTML, raw links, embedded images,
frontmatter injection, callouts, dataview/plugin syntax, and other plugin-like
constructs originating from external content. Source URLs and quotes are
stored as data fields with deterministic escaping; scraped content must never
be allowed to execute Obsidian plugin syntax.

No scraped page may directly cause a shell command, filesystem mutation,
credential use, Git push, external post, or arbitrary tool invocation.

------------------------------------------------------------------------

# 38. Components Deliberately NOT Required in the Initial Core

These are possible future infrastructure upgrades, not missing
architecture:

``` text
LangChain
LangGraph
LangSmith
Redis
BullMQ
Temporal
LiteLLM
OpenTelemetry
Prometheus
```

Reasons:

-   OpenCode handles the agent/user interaction surface.
-   The Product Hunter worker owns pipeline orchestration.
-   SQLite can handle the initial persistent queue/state.
-   Structured SQLite/runtime observability is sufficient for the initial core.
-   Model routing can remain simple/configurable.
-   More infrastructure should only be introduced when current
    architecture cannot meet a real requirement.

This is an intentional design decision, not an unfinished system.

------------------------------------------------------------------------

# 38A. Implementation Phases and Acceptance Criteria

The system is intentionally a complete architecture, but implementation is
staged so each layer proves its correctness before expensive infrastructure is
introduced. A later phase may not be marked complete merely because its code
exists; its acceptance criteria must pass.

## Phase 1 — Hand-prove extraction and normalization

Scope:

- a small manually selected source set
- raw capture + quarantine
- pain-point extraction
- quote-verbatim validation
- normalization and deterministic IDs
- basic provenance

Acceptance:

- at least 100 manually inspected evidence spans processed
- quote-verbatim check passes for >=99% of accepted evidence
- invalid/missing source spans fail closed
- duplicate document/content hashes are deterministic
- every accepted pain point traces to a source span

Do not optimize for scale yet.

## Phase 2 — Evidence core

Scope:

- SQLite schema
- fetch/source/document records
- authors and communities
- propagation edges
- evidence links
- idempotent jobs/checkpoints
- leases/fencing
- transactional outbox

Acceptance:

- crash/restart tests recover without duplicate authoritative mutations
- stale worker writes are rejected by fencing
- replaying the same job produces the same authoritative IDs/state
- every material evidence record has provenance

## Phase 3 — Semantics and independence

Scope:

- embeddings/Qdrant adapter
- clustering
- cluster lineage
- entity resolution
- propagation-aware independence
- DuckDB analytics

Acceptance:

- evaluation set passes cluster merge/split checks
- repost/propagation cases do not inflate independent-author counts
- analytical counts reconcile with SQLite fixtures
- cluster lineage preserves historical score references

## Phase 4 — Research gate and adversarial validation

Scope:

- Research Contract
- Tool Gate
- counter-evidence
- competitor/alternative search
- budget enforcement
- scoring
- confidence

Acceptance:

- missing mandatory tool/result/artifact fails the gate
- missing counter-evidence fails promotion
- "no strong competitor" and "no counter-evidence found" require contract checks
- budget overrun prevents the next expensive call
- score explanation traces every material input to claims/evidence

Before implementation of this phase, re-check the current OpenCode docs.
OpenCode hooks/permissions are integration safeguards, not the hard gate.

## Phase 5 — OpenCode interface

Scope:

- `product-hunter` agent
- persistent commands
- custom tool/plugin bridge
- `/list-research`
- `/continue` and cross-chat resume

Acceptance:

- a run created in one chat can be resumed from another chat
- bare `/continue` asks when multiple runs are eligible
- selector actions never create duplicate Research IDs
- OpenCode failure does not corrupt worker state

## Phase 6 — Controlled scale and projections

Scope:

- wider source set
- source-specific collectors
- Qdrant/DuckDB production adapters
- Obsidian projection
- daily briefs
- model routing

Acceptance:

- projections converge after induced failures
- source budgets and rate limits are enforced
- Obsidian output contains no executable scraped syntax
- model fallback preserves schema/version/provenance records

## Phase 7 — Optional heavy infrastructure

Only after measured need:

- Apify
- Crawl4AI / Playwright at larger scale
- SubQ Snap
- Langfuse / richer observability
- cloud worker
- Redis/BullMQ/Temporal
- Obsidian two-way workflows

Each component requires a benchmark or operational requirement that justifies
its cost/complexity. The architecture remains complete without these optional
components.

## SubQ Snap verification gate

SubQ Snap is **not assumed to be beneficial merely because it is available**.
Before promotion into the autonomous default path, benchmark it on the same
§6B evaluation set and compare against the configured cheap model on:

- schema validity
- classification accuracy
- duplicate/propagation decisions
- cluster-related decisions where applicable
- latency
- cost per accepted decision
- failure/retry rate

If it does not materially beat the cheap model on the relevant workload, keep
it optional rather than adding it to the critical path.

## Phase 8 — Operational hardening

Add retention, backups, parser-drift detection, and disk protection before
long-term unattended operation.

Acceptance:

- SQLite backup can be restored into a clean instance
- raw-data retention tiers are enforced
- disk watermark pauses collection before exhaustion
- parser/schema canary fixtures detect extraction drift
- migration tests run from the previous schema version to current

------------------------------------------------------------------------

# 38B. Retention, Backup, Migration, and Drift Protection

Raw content is retained by tier rather than forever at full size:

``` text
hot       recent source captures needed for active investigations
warm      compressed evidence-supporting captures
cold      content-addressed archive where retention is justified
expired   deleted according to configured retention policy
```

Retention policy is configurable per source and evidence class. Deleting a raw
capture must not delete the structured evidence or provenance record unless the
policy explicitly requires it; the system records `deleted_at` and retention
reason.

SQLite must use its online backup mechanism on a scheduled basis. Backups are
versioned and tested by restore, not merely created. A restore canary should
verify schema version, row counts for key tables, foreign-key integrity, and a
sample of content hashes.

Operational watchdogs should monitor:

- free disk space
- SQLite WAL growth
- queue age
- projection backlog
- repeated parser failures
- source HTTP-status drift
- extraction/schema failure rate
- model/provider error rate

Parser drift is detected with fixed canary fixtures and expected structural
predicates. A sudden drop in extracted fields, quote validation, or source
coverage pauses the affected collector and raises an operator event instead of
silently poisoning the evidence store.

Migrations are versioned under `db/migrations/`. Each migration has forward
and rollback/recovery notes, a fixture test, and a restore/upgrade canary.

------------------------------------------------------------------------

# 39. Full Repository Structure

``` text
product-hunter/
|
+-- .opencode/
|   +-- agents/
|   |   +-- product-hunter.md
|   |   +-- researcher.md
|   |   +-- investigator.md
|   |   +-- validator.md
|   |   +-- startup-killer.md
|   |
|   +-- skills/
|   |   +-- <research skills>
|   |
|   +-- config/
|       +-- agents.json
|       +-- models.json
|       +-- sources.yaml
|       +-- scoring.yaml
|
+-- src/
|   +-- collectors/
|   +-- processing/
|   +-- research/
|   +-- scoring/
|   +-- models/
|   +-- database/
|   +-- queue/
|   +-- scheduler/
|   +-- cache/
|   +-- embeddings/
|   +-- vector_store/
|   +-- analytics/
|   +-- research_contracts/
|   +-- tool_gate/
|   +-- provenance/
|   +-- events/
|   +-- obsidian/
|   +-- observability/
|
+-- data/
|   +-- raw/
|   +-- processed/
|   +-- cache/
|   +-- exports/
|
+-- db/
|   +-- product_hunter.db
|   +-- migrations/
|
+-- runtime/
|   +-- runs/
|   +-- failed/
|   +-- checkpoints/
|
+-- tests/
+-- scripts/
+-- docker/
|   +-- docker-compose.yml
|
+-- logs/
+-- .env.example
+-- .gitignore
+-- README.md
+-- pyproject.toml
```

------------------------------------------------------------------------

# 40. Complete End-to-End Architecture

The system is OpenCode-native at the interaction layer, but the autonomous
pipeline is worker-owned.

``` text
                         ANY OPENCODE CHAT
                                |
                                v
                    Product Hunter interface
                                |
                    +-----------+-----------+
                    |                       |
              commands / status       interactive review
                    |                       |
                    +-----------+-----------+
                                |
                    Product Hunter local API
                         / CLI / plugin
                                |
                                v
                     Persistent Registry
                           SQLite
                                |
                         Stable Research ID
                                |
                       Research Contract
                                |
                          Stage Worker
                                |
          +---------------------+---------------------+
          |                     |                     |
      Collectors             LLM stages          Analytics
          |                     |                     |
   Search / Fetch /        extraction /        DuckDB
   GitHub / Playwright     normalization       Qdrant
   / Apify                 investigation
          |                     |                     |
          +-----------> Quarantine <-----------------+
                           |
                    Structured Evidence
                           |
                normalize / deduplicate
                           |
                    entity resolution
                           |
                     pain extraction
                           |
                         Qdrant
                           |
                      clustering
                           |
                 independence analysis
                           |
                        DuckDB
                           |
                 frequency / trend
                           |
                 competitors + alternatives
                           |
                   counter-evidence
                           |
                     claims + links
                           |
                 score + confidence
                           |
                    Research Gate
                           |
                   +-------+-------+
                   |               |
                  FAIL            PASS
                   |               |
             retry / expand      SQLite
                                   |
                         transactional outbox
                                   |
                  +----------------+----------------+
                  |                |                |
                Qdrant           Obsidian        reports
                  |                |                |
                  +----------------+----------------+
                                   |
                                  Git
```

## Architectural ownership

### OpenCode

OpenCode is responsible for:

- human interaction
- commands
- interactive questions/selections
- showing status
- initiating/resuming research
- optional interactive investigation
- approval prompts
- presenting reports

### Product Hunter worker

The worker is responsible for:

- scheduling
- job leases
- stage execution
- tool adapters
- Research Contracts
- Tool Gates
- LLM routing
- evidence writes
- checkpoints
- idempotency
- outbox processing
- budget enforcement
- recovery

### SQLite

SQLite is the operational source of truth.

### Qdrant

Qdrant is semantic state and retrieval, never the sole source of truth.

### DuckDB

DuckDB is analytical state and historical analysis, not operational state.

### Obsidian

Obsidian is the human-readable projection.

### Git

Git tracks code/config/architectural decisions. Generated research history
should not automatically produce an enormous commit for every observation.

## OpenCode integration

OpenCode supports custom commands, custom tools, plugins, interactive
question prompts, and tool-execution hooks. These should be used as the
interaction/integration layer rather than assuming a slash command itself is
an arbitrary application UI. See the verified OpenCode documentation
references in Section 6A.

The `/list-research` UX therefore uses the best available OpenCode
interactive mechanism. The underlying selection always resolves to a stable
Research ID and a worker-side resume operation.

# 41. Research Registry, Persistence, and Continuation

A research run is a **persistent first-class object immediately when research
starts**. Product Hunter never depends on the current OpenCode chat, model
context window, or laptop process remaining alive.

The canonical state lives in SQLite. Runtime checkpoints and artifacts are
durable mirrors. Obsidian is a projection.

## 41.1 Research lifecycle

Before meaningful research begins:

1. create `research_run`;
2. assign stable public Research ID;
3. store original request and normalized objective;
4. create Research Contract;
5. record creating chat/session if available;
6. set `QUEUED`;
7. create first checkpoint;
8. emit `RESEARCH_STARTED`;
9. enqueue the first job.

Only the worker may transition authoritative run state.

## 41.2 Three separate state machines

Run state, pipeline stage, and opportunity state are separate.

### Run state

``` text
QUEUED
  -> RUNNING
  -> PAUSE_REQUESTED
  -> PAUSED

RUNNING
  -> WAITING_HUMAN
  -> BLOCKED_TOOL
  -> FAILED_RETRYABLE
  -> FAILED_PERMANENT
  -> COMPLETED
  -> CANCEL_REQUESTED
  -> CANCELLED
```

`INTERRUPTED` is a derived condition: an active lease expired without a
terminal transition. Recovery requeues the run rather than requiring a human
to manually move it through an `INTERRUPTED` state.

### Pipeline stage

``` text
PLAN
DISCOVERY
COLLECTION
NORMALIZATION
DEDUPLICATION
ENTITY_RESOLUTION
PAIN_EXTRACTION
EMBEDDING
CLUSTERING
INDEPENDENCE_ANALYSIS
VALIDATION
COMPETITOR_RESEARCH
COUNTER_EVIDENCE
SCORING
REPORTING
OBSIDIAN_SYNC
```

Budget exhaustion is a completion reason, not a stage.

### Opportunity state

Defined in Section 23 and persisted independently of the run that discovered
or investigated it.

## 41.3 Research registry

At minimum:

``` text
research_runs
--------------
id
public_id
objective
original_request
status
current_stage
current_step
priority
created_at
started_at
updated_at
last_heartbeat
completed_at
last_error
contract_version
agent_version
model_policy_version
chat_id
workspace_id
parent_run_id
resume_count
progress_percent
lease_epoch
budget_json

jobs
----
id
run_id
stage
step
status
priority
attempts
max_attempts
idempotency_key
worker_id
lease_epoch
lease_expires_at
created_at
scheduled_for
completed_at
last_error

research_checkpoints
--------------------
id
run_id
stage
step
checkpoint_version
state_ref
input_refs
output_refs
created_at
completed_at

research_events
---------------
id
run_id
event_type
stage
tool
agent
payload_ref
created_at

research_artifacts
------------------
id
run_id
artifact_type
path_or_uri
content_hash
created_at
```

`public_id` is the user-facing identifier.

## 41.4 Idempotency and leases

Every job has an idempotency key.

Every worker lease has a monotonically increasing `lease_epoch` (fencing
token). A worker may write only when its lease epoch is still current.

Conceptually:

``` sql
UPDATE jobs
SET ...
WHERE id = :job_id
  AND worker_id = :worker_id
  AND lease_epoch = :my_epoch;
```

If zero rows are updated, the worker has lost its lease and must stop writing.

This prevents a slow/stale worker from writing after another worker has
reclaimed the job.

SQLite should use WAL mode, appropriate `busy_timeout`, and a controlled
single-writer architecture.

## 41.5 Checkpointing

Checkpoints are written after meaningful stages and expensive/external
batches.

A checkpoint contains enough state to resume without hidden LLM memory:

- pending item IDs
- completed item IDs
- source cursors
- artifact references
- contract status
- retry state
- budget state
- stage attempt
- lease epoch
- deterministic input hashes

LLM inference is not assumed to be resumable token-for-token. An interrupted
LLM call is safely re-executed using its idempotency key.

## 41.6 `continue` semantics

`continue` is a persistence operation.

1. Query SQLite for eligible resumable runs.
2. Ignore terminal runs.
3. If exactly one is eligible, resume it.
4. If multiple are eligible, open the research selector or request an ID.
5. Acquire a fenced job lease.
6. Load the latest valid checkpoint.
7. Validate artifacts and contract state.
8. Reconcile partially completed outputs.
9. Resume the first incomplete step.
10. Emit `RESEARCH_RESUMED`.

The system must never restart from stage one merely because the current chat
has no context.

## 41.7 Research commands

Required semantic commands:

``` text
/research <objective>
/list-research
/research-status <ID>
/continue-research <ID>
/continue
/pause-research <ID>
/resume-research <ID>
/retry-research <ID>
/cancel-research <ID>
/research-log <ID>
```

The exact command registration is an OpenCode integration detail. OpenCode
custom commands are prompt templates, while custom tools/plugins can provide
the deterministic bridge to the worker.

## 41.8 Cross-chat continuity

Research state must never depend on conversation history.

This sequence is valid:

``` text
Chat A
  -> /research <objective>
  -> RH-20261018-0017 starts
  -> laptop shuts down

Later...

Chat B
  -> /list-research
  -> select RH-20261018-0017
  -> Resume
  -> persisted checkpoint restored
```

## 41.9 Crash recovery

On worker startup:

1. detect expired leases;
2. identify interrupted jobs;
3. validate latest checkpoints;
4. reconcile partial outputs using hashes/idempotency keys;
5. requeue unfinished work;
6. resume according to priority and budget.

No worker may continue writing after losing its fencing token.

## 41.10 Priority and scheduling

Suggested priorities:

``` text
P0  user-requested continuation
P1  high-potential investigation
P2  scheduled validation
P3  background discovery
P4  maintenance/reprocessing
```

User-requested continuation takes precedence over background work while
respecting global source/model/rate budgets.

## 41.11 Portability

The worker location is an implementation detail.

``` text
OpenCode
   |
Product Hunter local API / CLI
   |
SQLite + runtime + artifacts
   |
Research workers
```

The same interface can later point at a cloud worker without changing Research
IDs or research semantics.

# 42. 24/7 Autonomous Loop

The final system should be capable of:

``` text
COLLECT
  -> PROCESS
  -> CLUSTER
  -> SCORE
  -> INVESTIGATE
  -> UPDATE
  -> SCHEDULE NEXT WORK
  -> REPEAT
```

The scheduler should enqueue durable jobs into SQLite. It should not rely on
one giant OpenCode conversation remaining alive.

Recommended initial local architecture:

``` text
Windows Task Scheduler
        |
        v
Product Hunter Worker
        |
   internal loop
        |
   SQLite job queue
        |
   stage execution
```

There should be one supervised worker process with a single-instance lock.
Task Scheduler is used to start/restart the worker, not to independently launch
overlapping research jobs.

Suggested cadence:

``` text
every 15 min  source collection
hourly         processing
every 6 hours  clustering/re-evaluation
nightly        deep investigation
morning        daily brief
```

The scheduler must respect:

- source-specific rate limits
- model budgets
- per-run budgets
- global spend caps
- backoff windows
- maintenance windows
- worker availability

If the laptop sleeps or shuts down, scheduled work simply becomes overdue
durable jobs. On restart, the worker reconciles and executes them according to
priority rather than pretending they ran while offline.

# 43. Interactive Research Command UX

Research management works across arbitrary OpenCode chats and sessions because
the persistent registry is the source of truth.

## `/list-research`

`/list-research` should provide an interactive selection experience where the
current OpenCode capabilities permit it.

OpenCode provides interactive `question` prompts and custom tools/plugins;
the implementation should use those supported primitives or a Product Hunter
custom tool/plugin rather than assuming that a Markdown slash-command itself
can render an arbitrary application-specific picker. See the verified OpenCode
documentation references in Section 6A.

Conceptually:

``` text
+------------------------------------------------------+
|              Product Hunter - Research               |
+------------------------------------------------------+
|                                                      |
| > RH-20261018-0017  WhatsApp Inventory               |
|   VALIDATING | 67% | Last active 2h ago              |
|                                                      |
|   RH-20261018-0016  GitHub Dev Pain Points           |
|   INVESTIGATING | 42% | Last active 5h ago           |
|                                                      |
|   RH-20261017-0009  SMB Payment Workflows            |
|   PAUSED | 81% | Last active yesterday               |
|                                                      |
|   Up/Down Navigate | Enter Resume | Esc Back          |
+------------------------------------------------------+
```

The exact rendering may vary. The semantic behavior must not.

Each entry should expose:

- stable Research ID
- topic/name
- current stage
- status
- measurable progress
- last activity
- evidence count
- opportunities found
- error/failure indicator

Default ordering:

``` text
RUNNING
PAUSED
FAILED_RETRYABLE
WAITING_HUMAN
BLOCKED_TOOL
recently completed
```

Selecting an item and choosing Resume invokes the equivalent of:

``` text
/continue-research <RESEARCH_ID>
```

The UI must never create a second run for an existing Research ID.

## `/research`

Starting a new research may use an interactive question flow:

``` text
Topic: [________________________]

Depth:
  Quick
  Standard
  Deep

[ Start Research ]
```

The Research ID and initial registry record are created before discovery.

## Quick command behavior

``` text
/research <objective>            create + start tracked research
/list-research                   open research selector
/research-status <ID>            inspect one run
/continue                        resume only eligible run; ask if ambiguous
/continue-research <ID>          resume exact run
/pause-research <ID>             checkpoint and pause
/resume-research <ID>            resume paused run
/retry-research <ID>             retry failed stage
/cancel-research <ID>            cancel without deleting history
/research-log <ID>               show run/checkpoint/tool history
```

A bare `/continue` must never guess when multiple runs are resumable.

## Cross-chat behavior

``` text
Chat A
  -> /research "find X"
  -> RH-20261018-0017
  -> research executes
  -> laptop stops

Chat B
  -> /list-research
  -> select RH-20261018-0017
  -> Resume
  -> persisted checkpoint restored
```

The chat is an interaction surface, never a source of truth.

------------------------------------------------------------------------

# 44. Definition of Done

The system is considered complete when:

1. `product-hunter` is available inside OpenCode.
2. OpenCode can create, inspect, pause, resume, retry, and cancel persistent
   research runs.
3. A worker owns autonomous research execution independently of chat lifetime.
4. It can autonomously discover public evidence.
5. It can collect and preserve provenance.
6. It can identify independence with propagation-aware deterministic analysis.
7. It can extract and normalize pain points.
8. It can semantically cluster problems.
9. It can score opportunities with separate potential and confidence.
10. It can use SubQ for high-volume structured decisions where appropriate.
11. It can route simple tasks to fast models.
12. It can route hard tasks to a reasoning model.
13. It can research competitors and alternatives.
14. It can actively seek counter-evidence.
15. It can reject weak/already-solved opportunities.
16. It can track opportunity history and cluster lineage.
17. It can detect stale opportunities.
18. It can generate validation experiments.
19. It can write and continuously update the Obsidian vault as a projection.
20. It can run scheduled jobs without manual intervention.
21. It can recover from failures, crashes, shutdowns, and stale workers.
22. Research state is idempotent and protected by leases/fencing.
23. Cross-store projections converge through a transactional outbox.
24. Untrusted internet content cannot directly trigger tools or side effects.
25. Structured LLM outputs pass schema and deterministic validation.
26. The system records spend, tool provenance, evidence provenance, and
   checkpoint history.
27. Research IDs remain stable when the worker moves from laptop to cloud.
28. `/list-research` and cross-chat continuation operate from persistent state.
29. The system can explain why an opportunity received its score by tracing
   claims back to source evidence.
30. Required research gates fail closed when mandatory evidence or quality
   requirements are missing.
31. The implementation phases in Section 38A have passed their stated
   acceptance criteria before the system is considered production-ready.
32. Source legality status, retention, backup/restore, parser-drift canaries,
   and budget enforcement are operationally verified before unattended
   long-term operation.

The system is complete when it is not merely autonomous, but **auditable,
recoverable, adversarial, provenance-preserving, and safe to run
continuously**.
