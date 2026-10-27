# Harden prompt: Phase 4 Wave 1, research contract and tool gate foundation

**RUN_ID:** phase4-20261021-a307a42
**Node:** HARDEN (stage 6)
**You are:** a fresh-context security and observability reviewer. You report gaps. You do not edit code.

## The change under review

New package `src/product_hunter/research/`:

| File | What it does |
|---|---|
| `evidence.py` | Provenance, artifact records, stage executions, claims. Data plus validation. |
| `predicates.py` | Metric and predicate registries. Pure functions over a stage execution. |
| `budget.py` | Budget dimensions, limits, tracker that blocks the next call. |
| `contract.py` | Contract schema, contract validation, YAML loading. |
| `gate.py` | Violation codes, stage/run/promotion evaluators. |
| `persistence.py` | Two store Protocols for a future SQLite wave. No implementation. |

Plus `config/research_contract.yaml` and four test files under `tests/test_research_*.py`.

The design of record is `docs/reasonix/specs/phase-4-research-contract.md`. Read it, then read the code.

This is a re-harden. The package was reviewed once already and the findings
were folded in, so do not assume the earlier pass was thorough. Read the code
as it stands now.

The spec and the code are known to disagree in four places. Judge the code on
its own merits, then say which side is wrong:

1. Spec section 10 says the extraction booleans become
   `hard_requirements.require_quote_verbatim` and `require_source_span`.
   Spec section 4.3, the shipped YAML, and the code all say they are absent
   until the extraction wave.
2. The metrics in spec section 6.1 read a fixed set of artifact kinds
   (`document`, `candidate_url`, `author`, `community`, `evidence_span`,
   `counter_evidence`, `cluster_neighbors_checked`, `neighbor_check`,
   `duplicate_review`). The `required_outputs` in the shipped contract name a
   different set. Only `counter_evidence` is in both. Ask what a stage has to
   produce for its own thresholds to be satisfiable.
3. Spec section 9 and the `research/persistence.py` docstring name the planned
   migration `db/migrations/0002_research_gate.sql`. `0002` is taken by
   `0002_model_routing.sql`.
4. The code is stricter than the spec in three places: a stage that has not
   produced a result reports `stage_missing`, a budget overrun is attached to
   every stage including one that failed or never ran, and promotion is blocked
   when the run is over budget.

## The trust boundary

This code sits on the path between untrusted content and a promotion decision. The inputs it judges are model- or collector-produced: stage names, tool names, artifact kinds, query text, result references, and a YAML contract. Its output decides whether a research result may be promoted.

## What to look for

1. **Secret or untrusted-text leakage into persisted output.** Violation messages and violation `detail` mappings may be written to a database by a later wave. Does any of them copy raw `query_or_operation` text, a URL with credentials, or any other untrusted string? Check the predicate detail strings in `predicates.py` and every `detail=` in `gate.py`.
2. **Fail-open paths.** Every place the gate can return `passed=True` when it should not. In particular: a stage that declares no required tools, a stage with an empty `required_outputs` list, a threshold block that is absent for a gate key, a `None` metric, a missing artifact, a zero-result outcome whose query families were never attempted, and a budget dimension with no limit.
3. **Inert requirements.** `HardRequirements` declares eight flags. Find every one the gate or the contract validation never reads. For each, say whether it is enforced elsewhere (for example through `required_tools`), or genuinely dead. A flag that promises a check nobody performs is a finding.
4. **Type confusion and crash paths.** The contract is YAML, so every field is attacker-shaped. Look for a value that reaches an arithmetic comparison, a dict lookup, or a regex without a type check. `EvidenceThreshold` allows extra fields; trace what happens when an extra field carries a string instead of an integer, and when `extra="allow"` carries a field whose name collides with a declared one.
5. **Path handling.** `load_research_contract(path)` reads a file. `persistence.py` names a future migration. Is there any path traversal, any write, any shell, any network, any `eval` of config text?
6. **Observability.** Does a gate failure leave a trace that an operator can act on, or does the result object vanish into a caller? Check against the repo's existing convention: does the rest of `src/product_hunter/` use the `logging` module, or does it return structured values and let the caller decide? Report the house convention, then judge whether this package follows it.
7. **Resource bounds.** Unbounded lists or strings in the models (a contract with 100k stages, a query of 10 MB). Note the practical risk, and whether it matters for a local single-operator tool.

## Rules

- Do not edit any file. Report only.
- Another agent is concurrently working in this same tree on unrelated files (`src/product_hunter/llm/`, `src/product_hunter/eval/`, `src/product_hunter/db/`, `src/product_hunter/registry.py`, `db/migrations/0002_model_routing.sql`, and the tests that go with them). Ignore them; they are not under review.
- The tree is shared and moving. The suite was 791 tests at the time this prompt was written. Do not read the total as a fixed number.
- Judge against the never-cut list: validation at trust boundaries, error handling that prevents data loss, security, accessibility, and anything the user asked for. A gate that fails open is the worst outcome here.
- For each finding give: the file and line, what breaks, the smallest fix, and the severity (Must fix / Should fix / Nice to have).
- Do not invent findings. If a category is clean, say it is clean and why.
- End with a verdict line: `Verdict: ship` or `Verdict: fix first`, and a list of the Must-fix items.

---

# Re-harden

**RUN_ID:** phase4-20261021-resume-01

Your first pass returned `fix first` with two Must-fix items. Both are fixed.
This is the re-harden: judge the code as it stands now.

## What changed

| Fix | File | Change |
|---|---|---|
| F1 | `research/gate.py` | The `stage_failed` and `stage_timed_out` violation details no longer carry `execution.error`. Both are now `{"stage": stage}`. |
| F2 | `research/gate.py` | The invalid-artifact `artifact_invalid` detail no longer carries `artifact.invalid_reason`. It keeps `artifact_id` and `kind`. |
| F3 | `research/gate.py` | `evaluate_promotion` blocks on a failed counter-evidence or competitor gate when the hard requirement flag OR the promotion flag is set, following the pattern already used for `require_claim_evidence_links`. |
| F4 | `research/persistence.py` | The planned migration is now `0003_research_gate.sql`. `0002` was taken by `0002_model_routing.sql`. |

Four regression tests were added to `tests/test_research_gate.py`, one per fix.

## What you must do now

1. **Check the fixes are complete.** Read the changed code. Is there any other
   path where free text from the evidence reaches a violation message or a
   violation detail? The implementer noted that `promotion_blocked` reasons
   interpolate a quality-predicate detail string. Audit every `detail=` and
   every `reasons.append` in `gate.py` yourself and say which are safe and why.
   Ids, names, counts, and fixed phrases are what the spec allows.
2. **Check the new F3 logic.** The shipped contract sets both the hard flags
   and the promotion flags true, so its behaviour did not change. Is the
   combination correct, and does it match the `require_claim_evidence_links`
   pattern in the same function?
3. **Challenge three dispositions.** I did not accept every finding from your
   first pass. Say whether you agree, and argue it if you do not.

   **Finding 6.1, no logging. Declined.** Only 2 of the 48 modules under
   `src/product_hunter/` use `logging`: `db/scheduler.py` and `db/worker.py`.
   Both are unattended process loops with no caller to log for them. Every
   other module, including every pure library module, raises or returns
   structured values and lets the caller decide. The research package follows
   that convention, and its consumer, the worker handler, does not exist yet.
   Adding a logger to a synchronous library that returns a structured result
   would duplicate the caller's log.

   **Finding 7.1, unbounded strings and lists. Declined.** You rated it nice to
   have and agreed the practical risk is low for a local single-operator tool
   where the contract is operator-controlled YAML and the evidence comes from
   internal collectors. Adding `max_length` and `max_items` everywhere is
   speculative. Say if you think it rises above nice to have.

   **The artifact-kind vocabulary, where you said the code is right.** You
   wrote that each stage with thresholds has required outputs of kinds those
   metrics can read. Check that against the shipped `config/research_contract.yaml`
   yourself. The metrics in spec section 6.1 read the kinds `document`,
   `candidate_url`, `author`, `community`, `evidence_span`, `counter_evidence`,
   and `cluster_neighbors_checked`. Say which threshold blocks are satisfiable
   by their declared required outputs and which are not, and whether that is a
   code defect, a contract defect, or a spec defect.

4. **Re-judge the whole package**, not only the diff. The first pass is the
   baseline; do not anchor on it.

## Rules for this round

- Do not edit any file. Report only.
- If you still find a Must-fix, say so plainly. Do not lower a severity to
  reach agreement, and do not invent a finding to justify a second round.
- A clean category is a valid result. Say it is clean and say why.
