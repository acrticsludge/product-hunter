GOAL: Fix opportunity name truncation ([:80] -> [:200]) so titles no longer cut mid-word, and adjust the probe objective to website-service-worthy tech problems.

TIER: free (all models on opencode-go/free pool; orchestrator = Nemotron 3.5 Super VL 120B, implement/review = Nemotron 3 Ultra 550B, harden/report = Beam free)

RISK: declared=medium floor=medium (high-risk paths not touched: no auth/schema/payment/secret/infra changes)

ADAPTER: Product Hunter [repo], local

STAGES: 10/11 complete, skipped: 2 ARCHITECT (no architecture change needed), 5b VISUAL (no UI files touched)

GATES: uv run pytest -n auto — all failures pre-existing environmental (test_llm_contract x3, test_model_routing x3, test_smoke x1, test_observability_metrics x2); zero new failures introduced by the [:80]->[:200] change

CHANGES:
- src/product_hunter/handlers.py:1 line — title truncation [:80] -> [:200] in _scoring_handler (line 4072)
- adjusted_objective.txt: rewritten — website-service-worthy tech problems (manual admin work, fragmented tools, booking, subscription tracking, service comparison)
- adjusted_objective_v2.txt: same rewrite + clustering/validation variant note
- adjusted_objective_v3.txt: same rewrite + third probe variant note
- adjusted_objective_v4.txt: same rewrite + fourth probe variant note

HARDEN: Verdict pass. No findings. opportunities.title is TEXT (no DB length limit); all writes parameterized; no secrets, shell, or raw HTTP introduced. Other title truncations in handlers.py (:120 for competitor/market-signal titles) left as-is per ponytail minimal-change principle.

REVIEW: Verdict: ship. Rubric (correct/safe/load/tested/fast/lean): all pass. No Must/Should/Nice findings. Lean: -0 lines possible (already minimal). Blast radius matches Stage 0 declaration (5 files).

WAIVERS: none

STOPPED BECAUSE: all pass and review ship

NEXT: Start the 120s probe loop — new run RH-20261028-0032 (run_1072db77789cc800) created with the website-service objective, status QUEUED at PLAN stage. Monitor with `uv run python -m product_hunter.cli research status RH-20261028-0032` every 120s, then create follow-up runs as each completes.
