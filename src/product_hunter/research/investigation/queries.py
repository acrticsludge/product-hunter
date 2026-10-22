"""The query families an investigation searches, and the templates behind them.

The query set comes from code, not from the model: the mandatory competitor
and counter-evidence families are here, so a caller may add a family but
cannot quietly drop one of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The literal placeholder every template carries.
PLACEHOLDER = "{problem}"


class QueryFamily(str, Enum):
    """What a search is trying to establish."""

    competitor_alternative = "competitor_alternative"
    competitor_existing_solution = "competitor_existing_solution"
    competitor_software = "competitor_software"
    competitor_pricing = "competitor_pricing"
    counter_not_worth = "counter_not_worth"
    counter_complaints = "counter_complaints"
    counter_workaround = "counter_workaround"
    # The areas that are not the mission's own families search their own
    # query, so the plan holds no duplicate search.
    area_existing_solution = "area_existing_solution"
    area_alternative = "area_alternative"
    area_workaround = "area_workaround"
    market_validation = "market_validation"
    willingness_to_pay = "willingness_to_pay"
    distribution = "distribution"
    # The killer_* families name the ten falsification dimensions the startup
    # killer attacks, so a dimension's coverage can be judged on its own.
    killer_problem_existence = "killer_problem_existence"
    killer_recurrence = "killer_recurrence"
    killer_severity = "killer_severity"
    killer_independent_population = "killer_independent_population"
    killer_willingness_to_pay = "killer_willingness_to_pay"
    killer_market_size = "killer_market_size"
    killer_competition = "killer_competition"
    killer_distribution = "killer_distribution"
    killer_buildability = "killer_buildability"
    killer_timing = "killer_timing"


class QueryTemplate(BaseModel):
    """One family and the query text that searches it."""

    model_config = ConfigDict(extra="forbid")

    family: QueryFamily
    template: str = Field(min_length=1)
    purpose: str = Field(min_length=1)

    @field_validator("template")
    @classmethod
    def _template_carries_the_placeholder(cls, value: str) -> str:
        # A template without the placeholder cannot be rendered against a
        # problem, so it would search for a literal brace expression.
        if PLACEHOLDER not in value:
            raise ValueError(f"the template must contain the {PLACEHOLDER} placeholder")
        return value


class Query(BaseModel):
    """A template rendered against a problem statement."""

    model_config = ConfigDict(extra="forbid")

    family: QueryFamily
    text: str = Field(min_length=1)
    template: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


_ALTERNATIVE_TEMPLATE = QueryTemplate(
    family=QueryFamily.competitor_alternative,
    template="{problem} alternative",
    purpose="find what a user does instead of paying for this",
)
_EXISTING_SOLUTION_TEMPLATE = QueryTemplate(
    family=QueryFamily.competitor_existing_solution,
    template="{problem} existing solution",
    purpose="find the solutions that already ship for this",
)
_SOFTWARE_TEMPLATE = QueryTemplate(
    family=QueryFamily.competitor_software,
    template="{problem} software",
    purpose="find the software that competes with this",
)
_PRICING_TEMPLATE = QueryTemplate(
    family=QueryFamily.competitor_pricing,
    template="{problem} pricing",
    purpose="find what the alternatives charge",
)
_NOT_WORTH_TEMPLATE = QueryTemplate(
    family=QueryFamily.counter_not_worth,
    template="{problem} not worth",
    purpose="find the people who say this is not worth paying for",
)
_COMPLAINTS_TEMPLATE = QueryTemplate(
    family=QueryFamily.counter_complaints,
    template="{problem} complaints",
    purpose="find the complaints that argue against the problem",
)
_WORKAROUND_TEMPLATE = QueryTemplate(
    family=QueryFamily.counter_workaround,
    template="{problem} workaround",
    purpose="find the workarounds that make the problem tolerable",
)

_COMPETITOR_TEMPLATES: tuple[QueryTemplate, ...] = (
    _ALTERNATIVE_TEMPLATE,
    _EXISTING_SOLUTION_TEMPLATE,
    _SOFTWARE_TEMPLATE,
    _PRICING_TEMPLATE,
)

_COUNTER_TEMPLATES: tuple[QueryTemplate, ...] = (
    _NOT_WORTH_TEMPLATE,
    _COMPLAINTS_TEMPLATE,
    _WORKAROUND_TEMPLATE,
)

_MARKET_VALIDATION_TEMPLATE = QueryTemplate(
    family=QueryFamily.market_validation,
    template="{problem} market demand",
    purpose="find whether anyone is already buying for this",
)

# The areas that search something of their own. The mission's families stay
# in the competitor and counter-evidence areas, and these three areas search
# their own query, so no two planned searches ask the same question.
_AREA_EXISTING_SOLUTION_TEMPLATE = QueryTemplate(
    family=QueryFamily.area_existing_solution,
    template="{problem} tools that already do this",
    purpose="find the tools that already cover this",
)

_AREA_ALTERNATIVE_TEMPLATE = QueryTemplate(
    family=QueryFamily.area_alternative,
    template="{problem} what people do instead",
    purpose="find what a user does when they do not pay for this",
)

_AREA_WORKAROUND_TEMPLATE = QueryTemplate(
    family=QueryFamily.area_workaround,
    template="{problem} how people work around it",
    purpose="find the workarounds that make the problem tolerable",
)
_WILLINGNESS_TO_PAY_TEMPLATE = QueryTemplate(
    family=QueryFamily.willingness_to_pay,
    template="{problem} pricing willingness to pay",
    purpose="find a stated budget or an intent to pay",
)

_DISTRIBUTION_TEMPLATE = QueryTemplate(
    family=QueryFamily.distribution,
    template="{problem} where to find customers",
    purpose="find where the people who have this problem can be reached",
)

_STARTUP_KILLER_TEMPLATES: tuple[QueryTemplate, ...] = (
    QueryTemplate(
        family=QueryFamily.killer_problem_existence,
        template="{problem} is this a real problem",
        purpose="try to disprove that the problem exists",
    ),
    QueryTemplate(
        family=QueryFamily.killer_recurrence,
        template="{problem} how often does this happen",
        purpose="try to disprove that the problem recurs",
    ),
    QueryTemplate(
        family=QueryFamily.killer_severity,
        template="{problem} how bad is this problem",
        purpose="try to disprove that the problem is severe",
    ),
    QueryTemplate(
        family=QueryFamily.killer_independent_population,
        template="{problem} who else has this problem",
        purpose="try to disprove that the problem reaches an independent population",
    ),
    QueryTemplate(
        family=QueryFamily.killer_willingness_to_pay,
        template="{problem} would anyone pay for this",
        purpose="try to disprove that anyone would pay",
    ),
    QueryTemplate(
        family=QueryFamily.killer_market_size,
        template="{problem} how big is the market",
        purpose="try to disprove that the market is large",
    ),
    QueryTemplate(
        family=QueryFamily.killer_competition,
        template="{problem} competitors and alternatives",
        purpose="try to disprove that the idea can win against what exists",
    ),
    QueryTemplate(
        family=QueryFamily.killer_distribution,
        template="{problem} where can the customers be reached",
        purpose="try to disprove that the customers can be reached",
    ),
    QueryTemplate(
        family=QueryFamily.killer_buildability,
        template="{problem} can this be built",
        purpose="try to disprove that this can be built",
    ),
    QueryTemplate(
        family=QueryFamily.killer_timing,
        template="{problem} is now the right time",
        purpose="try to disprove that now is the time",
    ),
)

# The area names the templates of an area are filed under. A research area is
# a string enum, so the area and its name are the same key.
_AREA_TEMPLATES: dict[str, tuple[QueryTemplate, ...]] = {
    "competitor": _COMPETITOR_TEMPLATES,
    "existing_solution": (_AREA_EXISTING_SOLUTION_TEMPLATE,),
    "alternative": (_AREA_ALTERNATIVE_TEMPLATE,),
    "workaround": (_AREA_WORKAROUND_TEMPLATE,),
    "market_validation": (_MARKET_VALIDATION_TEMPLATE,),
    "willingness_to_pay": (_WILLINGNESS_TO_PAY_TEMPLATE,),
    "distribution": (_DISTRIBUTION_TEMPLATE,),
    "counter_evidence": _COUNTER_TEMPLATES,
    "startup_killer": _STARTUP_KILLER_TEMPLATES,
}


def _area_name(area: str) -> str:
    """Return the plain name of a research area."""
    return str(getattr(area, "value", area))


def competitor_templates() -> tuple[QueryTemplate, ...]:
    """Return the four competitor families every investigation has to search."""
    return _COMPETITOR_TEMPLATES


def counter_evidence_templates() -> tuple[QueryTemplate, ...]:
    """Return the three counter-evidence families every investigation has to search."""
    return _COUNTER_TEMPLATES


def startup_killer_templates() -> tuple[QueryTemplate, ...]:
    """Return one family per falsification dimension."""
    return _STARTUP_KILLER_TEMPLATES


def mandatory_templates() -> tuple[QueryTemplate, ...]:
    """Return the competitor and counter-evidence families together."""
    return _COMPETITOR_TEMPLATES + _COUNTER_TEMPLATES


def area_templates(area: str) -> tuple[QueryTemplate, ...]:
    """Return the templates of one research area."""
    templates = _AREA_TEMPLATES.get(_area_name(area))
    if templates is None:
        raise ValueError("no templates are defined for this area")
    return templates


def render(template: QueryTemplate, problem: str) -> Query:
    """Render a template against a problem statement."""
    text = problem.strip()
    if not text:
        # The reason is fixed and never quotes the problem: a problem
        # statement is free text and can carry anything.
        raise ValueError("the problem statement must not be empty")
    return Query(
        family=template.family,
        text=template.template.replace(PLACEHOLDER, text),
        template=template.template,
        purpose=template.purpose,
    )


def build_queries(problem: str, templates: Sequence[QueryTemplate]) -> list[Query]:
    """Render every template against the problem, in template order."""
    return [render(template, problem) for template in templates]
