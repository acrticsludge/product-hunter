# Implementation Backlog — Product Hunter (post-S6)

## P0 (blocking full vision)

| ID | Requirement | State | Dependency | Owner | Acceptance | Verification |
|---|---|---|---|---|---|---|
| VISION-028 | Research Contract enforcement (require_promotable) | IMPLEMENTED-BUT-UNWIRED | handlers | S7 | Worker cannot promote without contract evidence | contract violation test |
| VISION-051 | OpenCode agents (5) | MISSING | .opencode/agents | S8 | 5 agent files defined | agent smoke test |
| VISION-001 | Multi-source discovery (Reddit/forums/blogs/search) | PARTIAL | registry + searchers | S9 | ≥3 additional sources producing docs | E2E run docs |
| VISION-005 | Saturation stopping | IMPLEMENTED-BUT-UNWIRED | frontier | S9 | Discovery stops on novelty threshold | unit + E2E |
| VISION-054 | Trend detection | IMPLEMENTED-BUT-UNWIRED | DuckDB | S9 | Trend queries return data | integration test |
| VISION-056 | Uncertainty ranges | MISSING | scoring | S9 | Confidence as range | unit test |
| VISION-123 | Phase 8 retention/backup/drift | MISSING | none | S10 | Retention job runs | integration test |

## P1 (major)

| ID | Requirement | State | Dependency | Owner | Acceptance | Verification |
|---|---|---|---|---|---|---|
| VISION-036 | Full Obsidian vault (idea folders) | PARTIAL | projection | S9 | 01-Ideas/… generated | file count |
| VISION-068 | Opportunity report (full idea folder) | PARTIAL | reporting | S9 | IDEA.md + Evidence/ etc. | file count |
| VISION-070 | Scraping ladder in production | IMPLEMENTED-BUT-UNWIRED | drivers | S9 | Playwright/Crawl4AI reachable | integration |
| VISION-060 | Validation experiments | IMPLEMENTED-BUT-UNWIRED | experiments | S9 | Experiment recorded | unit + E2E |
| VISION-026 | Counter-evidence mandatory | IMPLEMENTED | handler wired | S7 | Non-trivial zero enforced | contract test |

## P2 (meaningful)

| ID | Requirement | State | Dependency | Owner | Acceptance | Verification |
|---|---|---|---|---|---|---|
| VISION-038 | Autonomous scheduler (Windows) | IMPLEMENTED-BUT-UNWIRED | Task Scheduler | S9 | Recurring cadence runs | smoke test |
| VISION-053 | MCPs completion | PARTIAL | config | S9 | Qdrant MCP fixed | smoke test |
| VISION-061 | MVP designer | MISSING | none | S10 | Module exists | unit test |
| VISION-062 | Distribution research | MISSING | none | S10 | Module exists | unit test |

## Notes
- S7 = Research Contract, S8 = OpenCode Control Plane, S9 = Skills/MCPs/Integrations/Autonomous, S10 = Final Acceptance.
- Do not implement in this session.