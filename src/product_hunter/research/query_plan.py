"""The query frontier the worker owns: the families, the variants, the plan.

Section 7A of the spec puts the frontier in the worker's hands. Ten initial
query families, eight controlled variants of a branch, and negative and
alternative-to queries that a run has to make so it does not only collect the
people who agree with the initial thesis.

This module holds that plan as data and gives it pure functions. It never
calls a tool, never reads a file, and never looks at the clock: like the rest
of the research package, it only says what a search is allowed to be.
"""

from __future__ import annotations

from dataclasses import dataclass

from product_hunter.research.evidence import StageExecution

# --- the ten query families of section 7A ---------------------------------

FAMILY_DIRECT_PAIN = "direct_pain"
FAMILY_MANUAL_WORKFLOW = "manual_workflow"
FAMILY_WORKAROUND = "workaround"
FAMILY_ALTERNATIVE_TO = "alternative_to"
FAMILY_FAILED_SOLUTION = "failed_solution"
FAMILY_FEATURE_REQUEST = "feature_request"
FAMILY_SWITCHING_PAIN = "switching_pain"
FAMILY_COST_CONSEQUENCE = "cost_consequence"
FAMILY_ROLE_WORKFLOW = "role_workflow"
FAMILY_TRIGGER_INCIDENT = "trigger_incident"

# The families in the order the spec lists them. The order is the plan's
# order, so it is part of what the worker does and not a detail.
QUERY_FAMILIES: tuple[str, ...] = (
    FAMILY_DIRECT_PAIN,
    FAMILY_MANUAL_WORKFLOW,
    FAMILY_WORKAROUND,
    FAMILY_ALTERNATIVE_TO,
    FAMILY_FAILED_SOLUTION,
    FAMILY_FEATURE_REQUEST,
    FAMILY_SWITCHING_PAIN,
    FAMILY_COST_CONSEQUENCE,
    FAMILY_ROLE_WORKFLOW,
    FAMILY_TRIGGER_INCIDENT,
)

# --- the eight controlled variants of section 7A ---------------------------

VARIANT_PROBLEM_WORDING = "problem_wording"
VARIANT_SYNONYM_DOMAIN_WORDING = "synonym_domain_wording"
VARIANT_WORKAROUND_WORDING = "workaround_wording"
VARIANT_ALTERNATIVE_COMPETITOR_WORDING = "alternative_competitor_wording"
VARIANT_FAILED_SOLUTION_WORDING = "failed_solution_wording"
VARIANT_CONSEQUENCE_COST_WORDING = "consequence_cost_wording"
VARIANT_ROLE_INDUSTRY_WORDING = "role_industry_wording"
VARIANT_SOURCE_COMMUNITY_WORDING = "source_community_wording"

# The variants in the order the spec lists them: a branch expands from the
# problem wording outwards, and this is that order.
QUERY_VARIANTS: tuple[str, ...] = (
    VARIANT_PROBLEM_WORDING,
    VARIANT_SYNONYM_DOMAIN_WORDING,
    VARIANT_WORKAROUND_WORDING,
    VARIANT_ALTERNATIVE_COMPETITOR_WORDING,
    VARIANT_FAILED_SOLUTION_WORDING,
    VARIANT_CONSEQUENCE_COST_WORDING,
    VARIANT_ROLE_INDUSTRY_WORDING,
    VARIANT_SOURCE_COMMUNITY_WORDING,
)

# --- what a stage has to search -------------------------------------------

# Every stage that searches has to search the family the thesis rests on, the
# negative family, and the alternative-to family. The spec makes the negative
# and the alternative-to query mandatory for validation, so that a run does
# not only collect the people who agree with the initial thesis; the same
# rule is applied to the stages that search for competitors and for
# counter-evidence. Evidence collection fetches URLs without running a
# search, so it does not declare query families.
MANDATORY_FAMILIES_BY_STAGE: dict[str, tuple[str, ...]] = {
    "discovery": (
        FAMILY_DIRECT_PAIN,
        FAMILY_FAILED_SOLUTION,
        FAMILY_ALTERNATIVE_TO,
    ),
    "validation": (
        FAMILY_DIRECT_PAIN,
        FAMILY_FAILED_SOLUTION,
        FAMILY_ALTERNATIVE_TO,
    ),
    "evidence_collection": (),
    "counter_evidence": (
        FAMILY_DIRECT_PAIN,
        FAMILY_FAILED_SOLUTION,
        FAMILY_ALTERNATIVE_TO,
    ),
    "investigation": (
        FAMILY_DIRECT_PAIN,
        FAMILY_FAILED_SOLUTION,
        FAMILY_ALTERNATIVE_TO,
    ),
    "normalization": (),
    "clustering": (),
    "scoring": (),
}

# The families some stage has to search. A template of one of them is
# mandatory, and the flag is derived from this set so the two cannot drift.
_MANDATORY_FAMILIES = frozenset(
    family for families in MANDATORY_FAMILIES_BY_STAGE.values() for family in families
)


@dataclass(frozen=True)
class QueryTemplate:
    """One query the worker owns: a family, a variant, and its wording."""

    family: str
    variant: str
    template: str
    mandatory: bool

    def render(self, topic: str) -> str:
        """Return the query this template makes for a topic."""
        return render_query(self.template, topic)


# The wordings of the frontier. A template is a template, not a finished
# query: the subject of the run is the topic, and the template says how the
# family and the variant word it. Every wording holds one {topic}.
_WORDINGS: tuple[tuple[str, str, str], ...] = (
    (
        FAMILY_DIRECT_PAIN,
        VARIANT_PROBLEM_WORDING,
        "{topic} is a real pain and nobody has solved it for us",
    ),
    (
        FAMILY_DIRECT_PAIN,
        VARIANT_ROLE_INDUSTRY_WORDING,
        "how {topic} hurts our team every single week",
    ),
    (
        FAMILY_MANUAL_WORKFLOW,
        VARIANT_PROBLEM_WORDING,
        "how do people handle {topic} by hand every week",
    ),
    (
        FAMILY_MANUAL_WORKFLOW,
        VARIANT_ROLE_INDUSTRY_WORDING,
        "the manual {topic} process our support team runs",
    ),
    (
        FAMILY_WORKAROUND,
        VARIANT_WORKAROUND_WORDING,
        "we work around {topic} with a spreadsheet",
    ),
    (
        FAMILY_WORKAROUND,
        VARIANT_PROBLEM_WORDING,
        "there is no tool for {topic} so we built our own",
    ),
    (
        FAMILY_ALTERNATIVE_TO,
        VARIANT_ALTERNATIVE_COMPETITOR_WORDING,
        "an alternative to the tools we use for {topic}",
    ),
    (
        FAMILY_ALTERNATIVE_TO,
        VARIANT_SYNONYM_DOMAIN_WORDING,
        "replace our {topic} software with something better",
    ),
    (
        FAMILY_FAILED_SOLUTION,
        VARIANT_FAILED_SOLUTION_WORDING,
        "we tried a {topic} tool and it did not work for us",
    ),
    (
        FAMILY_FAILED_SOLUTION,
        VARIANT_PROBLEM_WORDING,
        "every {topic} product we tried disappointed us",
    ),
    (
        FAMILY_FEATURE_REQUEST,
        VARIANT_PROBLEM_WORDING,
        "I wish a tool existed for {topic}",
    ),
    (
        FAMILY_FEATURE_REQUEST,
        VARIANT_FAILED_SOLUTION_WORDING,
        "the {topic} tools we used are missing the feature we need",
    ),
    (
        FAMILY_SWITCHING_PAIN,
        VARIANT_PROBLEM_WORDING,
        "migrating away from our {topic} vendor was painful",
    ),
    (
        FAMILY_SWITCHING_PAIN,
        VARIANT_ALTERNATIVE_COMPETITOR_WORDING,
        "what should we switch to for {topic} instead",
    ),
    (
        FAMILY_COST_CONSEQUENCE,
        VARIANT_CONSEQUENCE_COST_WORDING,
        "what {topic} costs us in time and money",
    ),
    (
        FAMILY_COST_CONSEQUENCE,
        VARIANT_SYNONYM_DOMAIN_WORDING,
        "the hidden cost of running {topic} by hand",
    ),
    (
        FAMILY_ROLE_WORKFLOW,
        VARIANT_ROLE_INDUSTRY_WORDING,
        "the {topic} workflow our support agents run",
    ),
    (
        FAMILY_ROLE_WORKFLOW,
        VARIANT_WORKAROUND_WORDING,
        "how support teams hack around {topic}",
    ),
    (
        FAMILY_TRIGGER_INCIDENT,
        VARIANT_PROBLEM_WORDING,
        "every time {topic} goes wrong we lose a day",
    ),
    (
        FAMILY_TRIGGER_INCIDENT,
        VARIANT_CONSEQUENCE_COST_WORDING,
        "what {topic} incidents cost us in deadlines",
    ),
    (
        FAMILY_DIRECT_PAIN,
        VARIANT_SOURCE_COMMUNITY_WORDING,
        "the {topic} problems people describe in the community forum",
    ),
    (
        FAMILY_ALTERNATIVE_TO,
        VARIANT_SOURCE_COMMUNITY_WORDING,
        "the {topic} tool the community forum says replaced the old one",
    ),
    (
        FAMILY_WORKAROUND,
        VARIANT_SOURCE_COMMUNITY_WORDING,
        "the {topic} workaround the community forum keeps recommending",
    ),
)

# The templates the worker owns, in a fixed order.
QUERY_TEMPLATES: tuple[QueryTemplate, ...] = tuple(
    QueryTemplate(family, variant, wording, family in _MANDATORY_FAMILIES)
    for family, variant, wording in _WORDINGS
)


def render_query(template: str, topic: str) -> str:
    """Return the query a template makes for a topic.

    A query without a topic says nothing, and a query that broke over two
    lines would be two queries to the search tool, so both are refused.
    """
    subject = topic.strip()
    if not subject:
        raise ValueError("a rendered query needs a topic")
    rendered = template.replace("{topic}", subject)
    if not rendered.strip():
        raise ValueError("the template rendered an empty query")
    if "\n" in rendered or "\r" in rendered:
        raise ValueError("a rendered query must be one line")
    return rendered


# The families the worker owns, as a set for the membership test.
_KNOWN_FAMILIES = frozenset(QUERY_FAMILIES)

# The characters that carry nothing: the zero-width characters a copy and
# paste leaves behind, and the byte order mark. They are not whitespace,
# so str.strip() keeps them, and a query made of them says nothing.
_INVISIBLE_CHARACTERS = "\u200b\u200c\u200d\u2060\ufeff"


def has_visible_text(text: str) -> bool:
    """Report whether the text holds at least one visible character.

    The rule lives here so the query plan, the predicates and the metrics
    answer the same question: a query of whitespace and zero-width
    characters carries nothing, and a search that carries one is not a
    search.
    """
    return any(
        character not in _INVISIBLE_CHARACTERS and not character.isspace()
        for character in text
    )


def known_families() -> frozenset[str]:
    """Every query family the worker owns."""
    return _KNOWN_FAMILIES


def is_known_family(name: str) -> bool:
    """Report whether a name is a family the worker owns."""
    # Investigation searches also carry specialist labels (competitor
    # alternatives, pricing, and counter-evidence dimensions). They share
    # the same provenance field as the ten discovery families, so the
    # contract's known-family predicate must recognize both vocabularies.
    from product_hunter.research.investigation.queries import QueryFamily

    investigation_families = frozenset(family.value for family in QueryFamily)
    return name.strip() in _KNOWN_FAMILIES or name.strip() in investigation_families


def plan_for_stage(stage: str) -> tuple[QueryTemplate, ...]:
    """Return the deterministic query plan of one stage.

    The plan is the templates of the families the stage has to search, then
    the templates it may search. A stage with no mandatory family has no
    plan: a stage that does not search has nothing to search for.
    """
    required = MANDATORY_FAMILIES_BY_STAGE.get(stage, ())
    if not required:
        return ()
    families = frozenset(required)
    mandatory = tuple(
        template
        for template in QUERY_TEMPLATES
        if template.mandatory and template.family in families
    )
    optional = tuple(
        template for template in QUERY_TEMPLATES if not template.mandatory
    )
    return mandatory + optional


def covered_families(execution: StageExecution) -> frozenset[str]:
    """The families the stage's successful search calls covered.

    A search that carries no visible query is not a search, so it covers no
    family: the emptiness test is the shared one, the same test the
    distinct_queries metric and the non_empty_queries predicate use.

    The stage rule and the success rule live in predicates, so a call filed
    under another stage cannot count here. The import is deferred because
    predicates imports this module.
    """
    from product_hunter.research.predicates import (
        TOOL_SEARCH,
        _successful,
        stage_calls,
    )

    families = {
        call.query_family.strip()
        for call in _successful(stage_calls(execution), TOOL_SEARCH)
        if call.query_family and has_visible_text(call.query_or_operation)
    }
    families.discard("")
    return frozenset(families)


def uncovered_mandatory_families(
    execution: StageExecution, stage: str
) -> tuple[str, ...]:
    """The mandatory families of a stage the execution did not cover."""
    required = MANDATORY_FAMILIES_BY_STAGE.get(stage, ())
    if not required:
        return ()
    covered = covered_families(execution)
    return tuple(family for family in required if family not in covered)
