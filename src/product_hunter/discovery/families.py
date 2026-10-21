"""The query family taxonomy, the templates, and the marker table.

This module owns the search strategy: which families exist, how a query of
each family is worded, and how a query of unknown origin is classified. The
templates and the markers are module constants on purpose, because the
strategy is worker-side code: the same objective produces the same frontier
in every run.

This module refuses to call a model, read a config file, or look at an
environment variable. A family is never decided by anything that runs at
request time.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# The slot every template carries. The wording of a variant comes from
# substituting this one slot, never from paraphrasing a query.
SUBJECT_SLOT = "{subject}"


class QueryFamily(str, Enum):
    """The families a search query can belong to."""

    problem = "problem"
    alternative_wording = "alternative_wording"
    severity = "severity"
    workflow_pain = "workflow_pain"
    workaround = "workaround"
    existing_solution = "existing_solution"
    competitor = "competitor"
    counter_evidence = "counter_evidence"


class FamilySpec(BaseModel):
    """One family: what it looks for, how it is worded, and how it is weighted."""

    model_config = ConfigDict(extra="forbid")

    family: QueryFamily
    description: str
    templates: tuple[str, ...]
    mandatory: bool
    requires_normalized_problem: bool
    priority_weight: float = Field(ge=0.0, le=1.0)


def _spec(
    family: QueryFamily,
    description: str,
    templates: tuple[str, ...],
    *,
    mandatory: bool,
    requires_normalized_problem: bool,
    priority_weight: float,
) -> FamilySpec:
    """Build one family spec, refusing a template without its slot."""
    for template in templates:
        if template.count(SUBJECT_SLOT) != 1:
            raise ValueError(
                f"the {family.value} template {template!r} must carry exactly one "
                f"{SUBJECT_SLOT} slot"
            )
    return FamilySpec(
        family=family,
        description=description,
        templates=templates,
        mandatory=mandatory,
        requires_normalized_problem=requires_normalized_problem,
        priority_weight=priority_weight,
    )


FAMILY_SPECS: dict[QueryFamily, FamilySpec] = {
    spec.family: spec
    for spec in (
        _spec(
            QueryFamily.problem,
            "a plain statement of the problem",
            (
                "{subject} problem",
                "how do people solve {subject}",
                "{subject} frustration",
                "struggling with {subject}",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=1.0,
        ),
        _spec(
            QueryFamily.alternative_wording,
            "the same problem said another way",
            (
                "{subject} alternative wording",
                "another way to describe {subject}",
                "{subject} known as",
                "how do others call {subject}",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=0.5,
        ),
        _spec(
            QueryFamily.severity,
            "how much the problem hurts",
            (
                "how bad is {subject}",
                "cost of {subject} in time",
                "how often does {subject} happen",
                "how much does {subject} hurt",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=0.8,
        ),
        _spec(
            QueryFamily.workflow_pain,
            "the manual step in the workflow",
            (
                "manual step in {subject}",
                "how do teams do {subject} by hand",
                "workflow around {subject}",
                "repetitive work in {subject}",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=0.9,
        ),
        _spec(
            QueryFamily.workaround,
            "the spreadsheet, script or hack people use instead",
            (
                "spreadsheet workaround for {subject}",
                "script or hack used for {subject}",
                "excel workaround for {subject}",
                "homegrown tool for {subject}",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=0.7,
        ),
        _spec(
            QueryFamily.existing_solution,
            "the tool people already pay for or live with",
            (
                "tool currently used for {subject}",
                "we already pay for a tool for {subject}",
                "existing software for {subject}",
                "what do teams use today for {subject}",
            ),
            mandatory=True,
            requires_normalized_problem=False,
            priority_weight=0.6,
        ),
        _spec(
            QueryFamily.competitor,
            "the products that claim to solve it",
            (
                "alternatives to {subject}",
                "competitors for {subject}",
                "apps that solve {subject}",
                "{subject} versus alternatives",
            ),
            mandatory=False,
            requires_normalized_problem=True,
            priority_weight=0.4,
        ),
        _spec(
            QueryFamily.counter_evidence,
            "the reasons the problem is not real",
            (
                "is {subject} a real problem",
                "evidence that {subject} is not a problem",
                "why {subject} is not worth solving",
                "does {subject} actually happen",
            ),
            mandatory=False,
            requires_normalized_problem=True,
            priority_weight=0.3,
        ),
    )
}

# The families a frontier can execute from a raw objective. A frontier that
# has executed every one of them has covered its family obligation.
MANDATORY_FAMILIES: tuple[QueryFamily, ...] = tuple(
    family for family, spec in FAMILY_SPECS.items() if spec.mandatory
)

# The families that need a normalized problem first, because a competitor
# query needs to know which product category to search and a counter-evidence
# query needs to know which claim to push against.
DERIVED_FAMILIES: tuple[QueryFamily, ...] = tuple(
    family
    for family, spec in FAMILY_SPECS.items()
    if spec.requires_normalized_problem
)

# The family a query of unknown origin falls back to: the plain statement of
# the problem, which is the family every other one refines.
DEFAULT_FAMILY = QueryFamily.problem

# The marker table, in specificity order. The first family whose marker
# appears in the case-folded text wins, so a marker that is a more specific
# reading of a query sits above a marker that is a general one. Every marker
# is case-folded, because the table is matched against case-folded text.
_MARKER_TABLE: dict[QueryFamily, tuple[str, ...]] = {
    QueryFamily.counter_evidence: (
        "a real problem",
        "not a problem",
        "not worth solving",
        "actually happen",
        "evidence that",
    ),
    QueryFamily.competitor: (
        "competitor",
        "alternatives to",
        "alternative to",
        "apps that solve",
        "versus",
        " vs ",
    ),
    QueryFamily.workaround: (
        "workaround",
        "spreadsheet",
        "excel",
        "homegrown",
        "shell script",
        "hack",
    ),
    QueryFamily.existing_solution: (
        "currently used",
        "already pay for",
        "existing software",
        "existing tool",
        "what do teams use today",
    ),
    QueryFamily.severity: (
        "how bad",
        "cost of",
        "hours lost",
        "how often",
        "how much does",
        "burnout",
    ),
    QueryFamily.workflow_pain: (
        "manual step",
        "manually",
        "by hand",
        "workflow",
        "repetitive work",
        "copy and paste",
    ),
    QueryFamily.alternative_wording: (
        "alternative wording",
        "another way to",
        "known as",
        "different name for",
        "how do others call",
        "synonym",
    ),
    QueryFamily.problem: (
        "problem",
        "issue",
        "struggle",
        "pain point",
        "frustration",
        "hassle",
    ),
}

FAMILY_MARKERS: tuple[tuple[QueryFamily, tuple[str, ...]], ...] = tuple(
    _MARKER_TABLE.items()
)


def assign_family(text: str) -> tuple[QueryFamily, str]:
    """Return the family of a query of unknown origin, and why it was chosen.

    The reason is the marker that matched, or ``"default"`` when no marker
    did. The function is pure: the same text always returns the same pair.
    """
    folded = text.casefold()
    for family, markers in FAMILY_MARKERS:
        for marker in markers:
            if marker in folded:
                return family, f"marker:{marker}"
    return DEFAULT_FAMILY, "default"
