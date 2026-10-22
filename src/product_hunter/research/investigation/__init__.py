"""The investigation engine: the models and rules that keep a research run honest.

The package holds the data model of an investigation: the query families, the
coverage rule, the provenance, the claims, the opportunity transition, the
per-area budget, the analyses of what the search found, the checkpoint that
makes a research run resumable, and the pipeline that drives them. Nothing here
calls a tool; the executor is the seam.
"""

from __future__ import annotations

from product_hunter.research.investigation.areas import (
    AreaFinding,
    AreaResearch,
    ResearchArea,
    WtpTier,
    strongest_wtp_tier,
)
from product_hunter.research.investigation.budget import InvestigationBudget
from product_hunter.research.investigation.checkpoint import (
    InvestigationCheckpoint,
    InvestigationStep,
    StepStatus,
    apply_step,
    build_checkpoint,
    completed_steps,
    input_hash_for,
    pending_steps,
    step_id_for,
)
from product_hunter.research.investigation.claims import (
    ClaimEvidence,
    ClaimStatus,
    InvestigationClaim,
    derive_claim_status,
)
from product_hunter.research.investigation.competitors import (
    CONFIDENCE_VALUES,
    Competitor,
    CompetitorAnalysis,
    CompetitorAnalysisStatus,
    CompetitorConfidence,
    analyse_competitors,
)
from product_hunter.research.investigation.counter_evidence import (
    CounterEvidenceItem,
    CounterEvidenceKind,
    CounterEvidenceResult,
    CounterEvidenceStatus,
    assess_counter_evidence,
)
from product_hunter.research.investigation.coverage import (
    CoverageRequirements,
    CoverageStatus,
    ResearchCoverage,
    SearchAttempt,
    assess_coverage,
)
from product_hunter.research.investigation.opportunity import (
    HumanDecision,
    InvalidTransition,
    InvestigationOutcome,
    OpportunityState,
    allowed_transitions,
    apply_human_decision,
    next_state_for_outcome,
)
from product_hunter.research.investigation.pipeline import (
    InvestigationPlan,
    InvestigationRequest,
    InvestigationResult,
    PlanStep,
    ResearchExecutor,
    SearchOutcome,
    build_plan,
    investigate,
    investigation_stage_execution,
)
from product_hunter.research.investigation.provenance import InvestigationProvenance
from product_hunter.research.investigation.queries import (
    Query,
    QueryFamily,
    QueryTemplate,
    area_templates,
    build_queries,
    competitor_templates,
    counter_evidence_templates,
    mandatory_templates,
    render,
    startup_killer_templates,
)
from product_hunter.research.investigation.startup_killer import (
    DIMENSION_CLAIM_KINDS,
    DIMENSION_FAMILIES,
    FAMILY_DIMENSIONS,
    DimensionAssessment,
    DimensionStatus,
    FalsificationDimension,
    StartupKillerReport,
    StartupKillerVerdict,
    derive_killer_verdict,
)

__all__ = [
    "CONFIDENCE_VALUES",
    "DIMENSION_CLAIM_KINDS",
    "DIMENSION_FAMILIES",
    "FAMILY_DIMENSIONS",
    "AreaFinding",
    "AreaResearch",
    "ClaimEvidence",
    "ClaimStatus",
    "Competitor",
    "CompetitorAnalysis",
    "CompetitorAnalysisStatus",
    "CompetitorConfidence",
    "CounterEvidenceItem",
    "CounterEvidenceKind",
    "CounterEvidenceResult",
    "CounterEvidenceStatus",
    "CoverageRequirements",
    "CoverageStatus",
    "DimensionAssessment",
    "DimensionStatus",
    "FalsificationDimension",
    "HumanDecision",
    "InvalidTransition",
    "InvestigationBudget",
    "InvestigationCheckpoint",
    "InvestigationClaim",
    "InvestigationOutcome",
    "InvestigationPlan",
    "InvestigationProvenance",
    "InvestigationRequest",
    "InvestigationResult",
    "InvestigationStep",
    "OpportunityState",
    "PlanStep",
    "Query",
    "QueryFamily",
    "QueryTemplate",
    "ResearchArea",
    "ResearchCoverage",
    "ResearchExecutor",
    "SearchAttempt",
    "SearchOutcome",
    "StartupKillerReport",
    "StartupKillerVerdict",
    "StepStatus",
    "WtpTier",
    "allowed_transitions",
    "analyse_competitors",
    "apply_human_decision",
    "apply_step",
    "area_templates",
    "assess_counter_evidence",
    "assess_coverage",
    "build_checkpoint",
    "build_plan",
    "build_queries",
    "competitor_templates",
    "completed_steps",
    "counter_evidence_templates",
    "derive_claim_status",
    "derive_killer_verdict",
    "input_hash_for",
    "investigate",
    "investigation_stage_execution",
    "mandatory_templates",
    "next_state_for_outcome",
    "pending_steps",
    "render",
    "startup_killer_templates",
    "step_id_for",
    "strongest_wtp_tier",
]
