# Independence contract minima: repair the 10-vs-1 drift

RUN_ID: `ses_e8e0b71f5ffefYohw2fcrls90Y-20261025`
Date: 2026-10-25
Risk: medium (repo adapter floor). Config only.

## The defect

`tests/test_independence_identity.py::test_the_policy_minima_mirror_the_research_contract`
failed with `assert 10 == 1`. Two files disagree about the same number:

| File | Value |
|---|---|
| `config/independence.yaml:37` | `min_independent_authors: 10` |
| `config/research_contract.yaml:70` (`evidence_thresholds.validation`) | `1` |
| `config/research_contract.yaml:200` (`stopping`) | `10` |

The test compares the first to the second. The validation-stage gate reads the
second: `research/gate.py:502` resolves the stage's `EvidenceThreshold`, and
`_threshold_violations` (`gate.py:511`) fails a stage whose measured
`independent_authors` is below the minimum. At `1`, one author satisfied the
gate that exists to prove independent corroboration.

## Which side was wrong

The contract, not the policy and not the test.

| Evidence | Says |
|---|---|
| `git show 2730434:config/research_contract.yaml` | `validation.min_independent_authors: 10` |
| `git show 6bec1e8:config/research_contract.yaml` | `1` |
| `6bec1e8` ("updates") changed 4 files, no doc or spec among them | the lowering was undocumented |
| `docs/reasonix/specs/wave-2-independence.md:146` | the policy minima mirror `evidence_thresholds.validation`, and a test prevents silent drift |
| `docs/reasonix/specs/phase-4-research-contract.md:137`, `docs/reasonix/specs/Product-Hunter-Full-Spec.md:510`, `docs/reasonix/reports/final-audit-03-research-contract.md:232` | 10 |
| `src/product_hunter/research/contract.py:125` (`StoppingPolicy`) | default 10 |
| any doc stating 1 | none |

The parsed-data comparison between `2730434` and `6bec1e8` shows exactly two
threshold moves in that rewrite, both downward:
`validation.min_independent_authors` 10 → 1 and
`clustering.min_neighbors_checked_per_cluster` 5 → 1, alongside every budget
raised to 999999 or 9999999. This change restores the author minimum only.

`6bec1e8` was HEAD when the drift was measured. A later commit `73cebc1`
landed while the review was running, which moved every `HEAD~n` reference; the
reviewer read `git show HEAD~1` after that commit and saw `1`, then concluded
the value had never been 10. Against fixed commit hashes, `2730434` has 10 and
`6bec1e8` has 1. The review's finding is a stale-ref artifact, not a
disagreement with the fix.

## The change

One line in `config/research_contract.yaml`:

```
-    min_independent_authors: 1
+    min_independent_authors: 10
```

No test was edited. `tests/` is untouched by this change; the failing test
becomes the regression test that guards the invariant.

## Evidence

```
uv run pytest tests/test_independence_identity.py tests/test_llm_subq.py tests/test_llm_decision.py -q
137 passed, 1 skipped in 1.15s

uv run pytest
2765 passed, 1 skipped, 2 warnings in 354.76s

python: load_research_contract().evidence_thresholds['validation']
        10 3 10        (authors, communities, stopping)
python: validate_contract(load_research_contract())
        []
git diff --stat -- config/research_contract.yaml
 1 file changed, 1 insertion(+), 1 deletion(-)
```

The two warnings are `huggingface_hub` symlink notices from the embeddings
test, not failures.

## Scope and commit boundary

This commit holds `config/research_contract.yaml` and this report. Nothing
else. The five SubQ migration edits stay in the working tree untouched and are
committed separately, after this one, so either can be reverted alone.

## Out of scope, recorded

`6bec1e8` also lowered `evidence_thresholds.clustering.min_neighbors_checked_per_cluster`
from 5 to 1 and raised every budget in the file to 999999 or 9999999. Neither
touches the validation author gate, and neither is fixed here. The budgets in
particular deserve a separate look: an effectively unlimited budget turns the
spend guards into decoration.

## Rollback

Revert one line. No schema, no data.
