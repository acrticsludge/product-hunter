# Session 1 — Requirement Matrix (Product Hunter)

Baseline: `docs/reasonix/specs/Product-Hunter-Full-Spec.md` and
`docs/reasonix/reports/original-vision-reality-audit.md`.

This matrix is the authoritative baseline for subsequent sessions. A
capability counts as IMPLEMENTED only when the authoritative worker invokes
it, it consumes real upstream state, produces real downstream state, persists
output and provenance, handles failure, has tests, and has bounded real
execution proof.

## Authoritative execution model

- SQLite is the single source of truth.
- Qdrant, DuckDB, Obsidian, runtime artifacts and generated reports are
  downstream/derived unless the spec says otherwise.
- Ownership: research_runs, jobs, leases, stage_attempts, evidence, claims,
  clusters, opportunities, scores (score_snapshots / opportunity_scores),
  tool_calls, projection_tasks, spend_ledger, human_decisions, checkpoints.
- No subsystem may become a competing source of truth; derived stores are
  rebuilt from SQLite.

## Requirement matrix

| ID | Requirement | Subsystem | Status (pre-session) | Production caller | Persistence | Session-1 change |
|---|---|---|---|---|---|---|
| VISION-001 | Multi-source discovery | discovery | MISSING | none | documents | unchanged (later session) |
| VISION-002 | Persistent query frontier | discovery | IMPLEMENTED-BUT-UNWIRED | none | discovery frontier | unchanged |
| VISION-003 | Controlled query variants | discovery | IMPLEMENTED-BUT-UNWIRED | none | queries | unchanged |
| VISION-004 | Novelty yield tracking | discovery | IMPLEMENTED-BUT-UNWIRED | none | frontier | unchanged |
| VISION-005 | Saturation stopping | discovery | IMPLEMENTED-BUT-UNWIRED | none | policy | unchanged |
| VISION-006 | Collection ladder | collection | IMPLEMENTED-BUT-UNWIRED | none | collection_items | unchanged |
| VISION-007 | Source registry legality | sources | IMPLEMENTED | discovery handler | sources | unchanged |
| VISION-008 | SSRF protection | safety | IMPLEMENTED | fetch | safety | unchanged |
| VISION-009 | Rate limiting/caching | collection | IMPLEMENTED-BUT-UNWIRED | none | fetches | unchanged |
| VISION-010 | Evidence model (span/claim) | evidence | PARTIAL | extraction | evidence, claims | unchanged |
| VISION-011 | Evidence span extraction | evidence | PARTIAL | handler | evidence | unchanged |
| VISION-012 | Pain point extraction via LLM | extraction | PARTIAL | pain extraction handler | pain_points | unchanged |
| VISION-013 | Normalization (stable IDs) | normalization | IMPLEMENTED-BUT-UNWIRED | none | documents | unchanged |
| VISION-014 | Deduplication | evidence | IMPLEMENTED-BUT-UNWIRED | none | evidence | unchanged |
| VISION-015 | Entity resolution | independence | IMPLEMENTED-BUT-UNWIRED | none | author_links | unchanged |
| VISION-016 | Propagation edges | independence | IMPLEMENTED-BUT-UNWIRED | none | propagation_edges | unchanged |
| VISION-017 | Independence calculation | independence | IMPLEMENTED-BUT-UNWIRED | none | independence_reports | unchanged |
| VISION-018 | Concentration checks | independence | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-019 | Semantic clustering (Qdrant/BGE-M3) | semantic | IMPLEMENTED-BUT-UNWIRED | none | Qdrant (derived) | unchanged |
| VISION-020 | Cluster lineage versioning | clustering | IMPLEMENTED-BUT-UNWIRED | none | cluster_versions | unchanged |
| VISION-021 | Opportunity scoring (Potential x Confidence) | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunity_scores | unchanged |
| VISION-022 | WTP tiers W0-W4 | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunities | unchanged |
| VISION-023 | Niche override | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunities | unchanged |
| VISION-024 | Market-size method | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunities | unchanged |
| VISION-025 | Competitor discovery | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-026 | Counter-evidence | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-027 | Non-trivial zero rule | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-028 | Research Contract (code-enforced) | research | IMPLEMENTED-BUT-UNWIRED | none | research_gate | enforcement interface + production integration point added |
| VISION-029 | Tool Gate | research | IMPLEMENTED-BUT-UNWIRED | none | tool_calls | foundation present |
| VISION-030 | Budget enforcement | research/autonomy | IMPLEMENTED-BUT-UNWIRED | none | spend_ledger | foundation present |
| VISION-031 | Saturation stopping | discovery | IMPLEMENTED-BUT-UNWIRED | none | frontier | unchanged |
| VISION-032 | Validation experiments | opportunity | IMPLEMENTED-BUT-UNWIRED | none | experiments | table now authoritative |
| VISION-033 | Opportunity lifecycle states | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunities | table now authoritative |
| VISION-034 | Opportunity history | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunity_scores | table now authoritative |
| VISION-035 | DuckDB analytics | analytics | IMPLEMENTED-BUT-UNWIRED | none | DuckDB (derived) | unchanged |
| VISION-036 | Obsidian projection (full vault) | projection | PARTIAL | none | Obsidian (derived) | unchanged |
| VISION-037 | Daily briefs | projection | IMPLEMENTED-BUT-UNWIRED | none | Obsidian | unchanged |
| VISION-038 | Autonomous scheduler | autonomy | IMPLEMENTED-BUT-UNWIRED | none | scheduler | unchanged |
| VISION-039 | Single-worker lock | runtime | IMPLEMENTED | worker | runtime/lock | unchanged |
| VISION-040 | Leases/fencing | db | IMPLEMENTED | worker | jobs | fenced writes enforced |
| VISION-041 | Crash recovery | autonomy | IMPLEMENTED | worker | jobs | unchanged |
| VISION-042 | Checkpointing | db | IMPLEMENTED | explicit | research_checkpoints | unchanged |
| VISION-043 | Cross-chat continuation | registry | IMPLEMENTED | CLI/OpenCode | registry | unchanged |
| VISION-044 | Model routing | llm | IMPLEMENTED | extraction | model_routing | unchanged |
| VISION-045 | OpenCode generate bridge | llm | IMPLEMENTED | extraction | - | unchanged |
| VISION-046 | SubQ decision provider | llm | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-047 | BGE-M3 embeddings | semantic | IMPLEMENTED | none (yet) | Qdrant | unchanged |
| VISION-048 | Qdrant semantic retrieval | semantic | IMPLEMENTED-BUT-UNWIRED | none | Qdrant | unchanged |
| VISION-049 | Prompt-injection boundary | safety | IMPLEMENTED | projection | - | unchanged |
| VISION-050 | SSRF protection | safety | IMPLEMENTED | fetch | - | unchanged |
| VISION-051 | OpenCode agents (5) | opencode | MISSING | - | - | out of scope this session |
| VISION-052 | Skills (30) | opencode | PARTIAL | - | - | out of scope |
| VISION-053 | MCPs (9) | opencode | PARTIAL | - | - | out of scope |
| VISION-054 | Trend detection | analytics | IMPLEMENTED-BUT-UNWIRED | none | DuckDB | unchanged |
| VISION-055 | Temporal analysis | analytics | IMPLEMENTED-BUT-UNWIRED | none | DuckDB | unchanged |
| VISION-056 | Uncertainty estimation | opportunity | MISSING | - | - | out of scope |
| VISION-057 | Market sizing | opportunity | IMPLEMENTED-BUT-UNWIRED | none | opportunities | unchanged |
| VISION-058 | Already-solved detection | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-059 | Startup-killer/falsification | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-060 | Validation experiments | opportunity | IMPLEMENTED-BUT-UNWIRED | none | experiments | table now authoritative |
| VISION-061 | MVP designer | - | MISSING | - | - | out of scope |
| VISION-062 | Distribution research | - | MISSING | - | - | out of scope |
| VISION-063 | Pricing research | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-064 | Research planning | research | IMPLEMENTED-BUT-UNWIRED | none | query_plan | unchanged |
| VISION-065 | Research budgeting | research | IMPLEMENTED | none | spend_ledger | unchanged |
| VISION-066 | Research stopping | discovery | IMPLEMENTED-BUT-UNWIRED | none | frontier | unchanged |
| VISION-067 | Opportunity investigator | investigation | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-068 | Opportunity report (full idea folder) | reporting | PARTIAL | none | Obsidian | unchanged |
| VISION-069 | Source expansion | sources | IMPLEMENTED-BUT-UNWIRED | none | source_candidates | unchanged |
| VISION-070 | Scraping ladder | collection | IMPLEMENTED-BUT-UNWIRED | none | - | unchanged |
| VISION-071 | Retention/backup/drift | ops | MISSING | - | - | out of scope |

## Session-1 foundation changes (summary)

- Added migration `0011_opportunities.sql` creating authoritative
  `opportunities`, `opportunity_scores`, `opportunity_evidence`,
  `opportunity_claims`, `experiments`, `research_requests` tables.
- Updated `tests/test_db_migrations.py` EXPECTED_VERSION to 11 and table set.
- Added `StageNotImplemented` / `StageOutputMissing` and `STAGE_OUTPUT_REQUIREMENTS`
  in `product_hunter/research/stages.py`; worker now rejects pass-through
  success (a stage returning `None` or missing required keys fails).
- Unimplemented pipeline handlers now raise `StageNotImplemented` instead of
  silently enqueuing the next stage.
- Added `block_job` (permanent, no-retry) for unimplemented stages.
- Added `product_hunter/research/promotion.py` as the production integration
  point for `require_promotable` (fail-closed promotion).
- Wired a guarded promotion attempt into the worker after a run exhausts
  its queued work.
- Made `CANONICAL_DDL` idempotent (`CREATE TABLE IF NOT EXISTS`) and updated
  the projection test that created a differently-shaped table.

## Unresolved (deliberately, later sessions)

All VISION-001..071 items marked IMPLEMENTED-BUT-UNWIRED or MISSING remain
unwired except the foundation items above. This session does NOT certify
Product Hunter as READY.
