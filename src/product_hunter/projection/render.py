"""Render a snapshot as the markdown notes of a vault.

Every renderer here is a pure function: a snapshot in, a note body out. The
renderer never touches the filesystem, and the engine adds the frontmatter
and the hash, so a hash never changes a body.

Every string that came from a page passes through the sanitizer before it is
written. A value the database does not hold is written as "not recorded",
which a reader can tell apart from a zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from product_hunter.projection.models import (
    CommunityRef,
    EvidenceRef,
    Experiment,
    MarketReport,
    Opportunity,
    ResearchRequest,
    Snapshot,
    SourceRef,
)
from product_hunter.projection.sanitize import neutralize, safe_url, yaml_scalar
from product_hunter.projection.vault import DAILY_REPORTS, VaultLayout

# What a note says about a value the database does not hold.
NOT_RECORDED = "not recorded"

# The marker that says a note belongs to the projection.
SOURCE_MARKER = "product-hunter-projection"

# The order the frontmatter keys are written in.
FRONTMATTER_ORDER = (
    "id",
    "kind",
    "contract",
    "source",
    "content_hash",
    "status",
    "potential",
    "confidence",
    "first_seen",
    "last_updated",
)

# The lifecycles that put an opportunity in front of a person.
REVIEW_LIFECYCLE = "HUMAN_REVIEW"
DEFAULT_LIFECYCLE = "CANDIDATE"

# A run of backticks in a value. The code span around it needs a longer
# delimiter, or the value would close the span.
_BACKTICKS = re.compile(r"`+")


@dataclass(frozen=True)
class RenderedNote:
    """One note: where it goes, what it is, and what it says."""

    path: str
    kind: str
    id: str
    fields: dict[str, object] = field(default_factory=dict)
    body: str = ""


# --- the frontmatter -------------------------------------------------------


def render_frontmatter(fields: dict[str, object]) -> str:
    """The frontmatter of a note, with every string quoted."""
    lines = ["---"]
    for key in _ordered_keys(fields):
        lines.append(f"{key}: {_yaml_value(fields[key])}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _ordered_keys(fields: dict[str, object]) -> list[str]:
    ordered = [key for key in FRONTMATTER_ORDER if key in fields]
    rest = sorted(key for key in fields if key not in FRONTMATTER_ORDER)
    return ordered + rest


def _yaml_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.2f}"
    return yaml_scalar(str(value))


# --- the small helpers -----------------------------------------------------


def _prose(value: str | None) -> str:
    """A text value as it is written, or the marker that says it is absent."""
    if value is None or not value.strip():
        return NOT_RECORDED
    return neutralize(value)


def _number(value: float | None) -> str:
    """A number at a fixed precision, or the marker that says it is absent."""
    if value is None:
        return NOT_RECORDED
    return f"{value:.2f}"


def _code(value: str | None) -> str:
    """A value that names something, written as inline code.

    The delimiter is one backtick longer than the longest run of backticks
    the value holds, so a backtick in the value stays inside the code span
    and the value is not shortened. A value that begins or ends with a
    backtick is padded, so that backtick does not merge with the delimiter.
    """
    if value is None or not value.strip():
        return NOT_RECORDED
    text = neutralize(value).replace("\n", " ").replace("\t", " ")
    # The sanitizer put a backslash before every backtick. Inside a code
    # span a backslash is literal, so it goes and the backtick stays.
    text = text.replace("\\`", "`")
    delimiter = "`" * (_longest_backtick_run(value) + 1)
    if text.startswith("`") or text.endswith("`"):
        # A backtick at either end of the value would merge with the
        # delimiter and the span would not form. CommonMark strips one
        # space from each end of a code span, so the padding shows as
        # nothing and the value still reads as it is.
        return f"{delimiter} {text} {delimiter}"
    return f"{delimiter}{text}{delimiter}"


def _longest_backtick_run(value: str) -> int:
    """The length of the longest run of backticks in a value."""
    return max((len(run) for run in _BACKTICKS.findall(value)), default=0)


def _url(value: str | None) -> str:
    """A URL as inline code, with a refused scheme replaced."""
    if value is None or not value.strip():
        return NOT_RECORDED
    return _code(safe_url(value))


def _cell(value: str) -> str:
    """A value inside a table row, where a pipe would end the row."""
    return neutralize(value).replace("\n", " ").replace("|", "\\|")


def _item(value: str | None) -> str:
    """A value that stands on a line of its own."""
    if value is None or not value.strip():
        return NOT_RECORDED
    return _cell(value)


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# --- the opportunity note ---------------------------------------------------


def render_opportunity(opportunity: Opportunity) -> str:
    """The note of one opportunity, with the fifteen fields of the spec."""
    supporting = [ref for ref in opportunity.evidence if ref.stance != "counter"]
    counter = [ref for ref in opportunity.evidence if ref.stance == "counter"]
    lines: list[str] = []
    lines.append(f"# {_heading(opportunity.title)}")
    lines.append("")
    lines.append("## Opportunity")
    lines.append("")
    lines.append(_prose(opportunity.statement))
    lines.append("")
    if opportunity.claims:
        lines.append("### Claims")
        lines.append("")
        for claim in opportunity.claims:
            lines.append(f"- {_item(claim)}")
        lines.append("")
    lines.append("## Normalized problem")
    lines.append("")
    lines.append(f"- Domain: {_code(opportunity.domain)}")
    lines.append(f"- Actor: {_code(opportunity.actor)}")
    lines.append(f"- Action: {_code(opportunity.action)}")
    lines.append(f"- Object: {_code(opportunity.object)}")
    lines.append(f"- Consequence: {_code(opportunity.consequence)}")
    lines.append("")
    lines.append(f"Spans: {_spans(supporting)}")
    lines.append("")
    lines.append("## Evidence summary")
    lines.append("")
    summary = opportunity.evidence_summary
    lines.append(f"- Mentions: {summary.mentions}")
    lines.append(f"- Canonical documents: {summary.canonical_documents}")
    lines.append(f"- Independent authors: {summary.independent_authors}")
    lines.append(f"- Uncertain authors: {summary.uncertain_authors}")
    lines.append(f"- Independent communities: {summary.independent_communities}")
    lines.append(f"- Propagated copies: {summary.propagated_copies}")
    # With no mention at all the share cannot be measured, and a share of
    # zero would be a number the database does not hold.
    share = _number(summary.source_concentration) if summary.mentions else NOT_RECORDED
    lines.append(f"- Busiest source share: {share}")
    lines.append("")
    lines.append("## Independent evidence")
    lines.append("")
    lines.append(_independent_prose(opportunity))
    lines.append("")
    lines.append("## Communities")
    lines.append("")
    if opportunity.communities:
        for community in opportunity.communities:
            lines.append(f"- {_item(community)}")
    else:
        lines.append(NOT_RECORDED)
    lines.append("")
    lines.append("## Severity")
    lines.append("")
    lines.append(_number(opportunity.severity))
    lines.append("")
    lines.append("## WTP")
    lines.append("")
    lines.append(_prose(opportunity.wtp_tier))
    lines.append("")
    lines.append("## Competition")
    lines.append("")
    lines.append(_number(opportunity.competition))
    lines.append("")
    lines.append("## Counter-evidence")
    lines.append("")
    if counter:
        lines.extend(_quote_entries(counter))
    else:
        lines.append(NOT_RECORDED)
        lines.append("")
    lines.append("## Potential")
    lines.append("")
    lines.append(_number(opportunity.potential))
    lines.append("")
    lines.append("## Confidence")
    lines.append("")
    lines.append(_number(opportunity.confidence))
    lines.append("")
    lines.append("## Trend")
    lines.append("")
    lines.append(_prose(opportunity.trend))
    lines.append("")
    lines.append("## Uncertainty")
    lines.append("")
    lines.append(_prose(opportunity.uncertainty))
    lines.append("")
    lines.append("## Lifecycle")
    lines.append("")
    lines.append(_prose(opportunity.lifecycle))
    lines.append("")
    lines.append("## Recommended next validation experiment")
    lines.append("")
    lines.append(_prose(opportunity.recommended_experiment))
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    if opportunity.evidence:
        lines.extend(_evidence_entries(opportunity.evidence))
    else:
        lines.append(NOT_RECORDED)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _heading(title: str) -> str:
    if not title.strip():
        return "untitled"
    return neutralize(title).replace("\n", " ")


def _spans(refs: list[EvidenceRef]) -> str:
    if not refs:
        return NOT_RECORDED
    return ", ".join(_code(ref.span_id) for ref in refs)


def _independent_prose(opportunity: Opportunity) -> str:
    """The counts of section 6, stated as claims of their own."""
    summary = opportunity.evidence_summary
    mentions = _plural(summary.mentions, "mention", "mentions")
    people = _plural(summary.mentions, "person", "people")
    documents = _plural(summary.canonical_documents, "document", "documents")
    authors = _plural(summary.independent_authors, "author", "authors")
    communities = _plural(
        summary.independent_communities, "community", "communities"
    )
    uncertain = (
        f"{summary.uncertain_authors} of them uncertain"
        if summary.uncertain_authors
        else "none of them uncertain"
    )
    copies = (
        f"{summary.propagated_copies} of the mentions are copies of another document"
        if summary.propagated_copies
        else "None of the mentions are copies of another document"
    )
    verb = "is" if summary.mentions == 1 else "are"
    return (
        f"{mentions} {verb} not {people}. This opportunity rests on {documents} "
        f"from {authors}, {uncertain}, across {communities}. {copies}."
    )


def _quote_entries(refs: list[EvidenceRef]) -> list[str]:
    """The quotes of a section, each with the span behind it."""
    lines: list[str] = []
    for ref in refs:
        lines.append("```text")
        lines.append(neutralize(ref.quote))
        lines.append("```")
        lines.append("")
        lines.append(f"- Span: {_code(ref.span_id)}")
        lines.append(f"- Source: {_url(ref.url)}")
        lines.append(f"- Community: {_item(ref.community_name)}")
        lines.append("")
    return lines


def _evidence_entries(refs: list[EvidenceRef]) -> list[str]:
    """The evidence of an opportunity, one entry per captured quote."""
    lines: list[str] = []
    for ref in refs:
        lines.append(f"### {_url(ref.url)}")
        lines.append("")
        lines.append(f"- Community: {_item(ref.community_name)}")
        lines.append(f"- Author: {_code(ref.author_handle)}")
        lines.append(f"- Source: {_code(ref.source_id)}")
        lines.append(f"- Captured: {_prose(ref.captured_at)}")
        lines.append(f"- Stance: {_prose(ref.stance)}")
        if ref.live_status != "live":
            # A quote the source has taken down is still evidence, and the
            # reader is told that it is no longer live.
            lines.append(f"- Source status: {_prose(ref.live_status)}")
        lines.append("")
        lines.append("```text")
        lines.append(neutralize(ref.quote))
        lines.append("```")
        lines.append("")
        lines.append(f"Span: {_code(ref.span_id)}")
        lines.append("")
    return lines


def _evidence_section(refs: list[EvidenceRef]) -> list[str]:
    """The evidence section of a note: the entries, or the marker of absence."""
    lines = ["## Evidence", ""]
    if refs:
        lines.extend(_evidence_entries(refs))
    else:
        lines.append(NOT_RECORDED)
        lines.append("")
    return lines


# --- the dashboards ---------------------------------------------------------


def _wikilink(path: str) -> str:
    """The wikilink to a note of the vault.

    The path names the note without its extension, and the link closes on
    both sides, so Obsidian opens the note at that path.
    """
    target = path.removesuffix(".md")
    return f"[[{target}]]"


def _note_link(layout: VaultLayout, opportunity: Opportunity) -> str:
    """The wikilink to the note of an opportunity."""
    path = layout.opportunity_path(
        title=opportunity.title,
        opportunity_id=opportunity.opportunity_id,
        potential=opportunity.potential,
        lifecycle=opportunity.lifecycle,
    )
    return _wikilink(path)


def _rank_key(opportunity: Opportunity) -> tuple[int, float, str]:
    """The sort key of a ranked opportunity.

    The potential decides the rank, and the severity decides it when no
    potential was recorded.
    """
    if opportunity.potential is not None:
        return (0, -opportunity.potential, opportunity.opportunity_id)
    if opportunity.severity is not None:
        return (1, -opportunity.severity, opportunity.opportunity_id)
    return (2, 0.0, opportunity.opportunity_id)


def ranked_opportunities(snapshot: Snapshot) -> list[Opportunity]:
    """The opportunities of a snapshot, best first."""
    return sorted(snapshot.opportunities, key=_rank_key)


def _opportunity_table(
    layout: VaultLayout, opportunities: list[Opportunity]
) -> list[str]:
    lines = [
        "| Rank | Opportunity | Potential | Confidence | Evidence | Note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for rank, opportunity in enumerate(opportunities, start=1):
        lines.append(
            f"| {rank} | {_cell(opportunity.title)} "
            f"| {_number(opportunity.potential)} "
            f"| {_number(opportunity.confidence)} "
            f"| {opportunity.evidence_summary.mentions} "
            f"| {_note_link(layout, opportunity)} |"
        )
    return lines


def render_top_opportunities(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The dashboard of the opportunities with the highest potential."""
    lines = ["# Top opportunities", ""]
    ranked = ranked_opportunities(snapshot)
    if not ranked:
        lines.append("No opportunity is recorded.")
    else:
        lines.extend(_opportunity_table(layout, ranked[:20]))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_recent_discoveries(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The dashboard of the opportunities first seen in the last week."""
    lines = ["# Recent discoveries", ""]
    recent = _recent(snapshot)
    if not recent:
        lines.append(
            f"No opportunity was first seen in the seven days before "
            f"{snapshot.reference_date}."
        )
    else:
        lines.extend(_opportunity_table(layout, recent))
    return "\n".join(lines).rstrip("\n") + "\n"


def _recent(snapshot: Snapshot, days: int = 7) -> list[Opportunity]:
    """The opportunities first seen within the week of the reference date."""
    reference = _parse_date(snapshot.reference_date)
    if reference is None:
        return []
    recent: list[Opportunity] = []
    for opportunity in snapshot.opportunities:
        first_seen = _parse_date(opportunity.first_seen)
        if first_seen is None:
            continue
        age = (reference - first_seen).days
        if 0 <= age <= days:
            recent.append(opportunity)
    return sorted(recent, key=_rank_key)


def render_rising(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The dashboard of the opportunities whose score went up."""
    return _movement_note("Rising opportunities", snapshot, layout, "rising")


def render_falling(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The dashboard of the opportunities whose score went down."""
    return _movement_note("Falling opportunities", snapshot, layout, "falling")


def _movement_note(
    title: str,
    snapshot: Snapshot,
    layout: VaultLayout,
    movement: str,
) -> str:
    lines = [f"# {title}", ""]
    moving = [
        opportunity
        for opportunity in ranked_opportunities(snapshot)
        if opportunity.trend.lower() == movement
    ]
    if not moving:
        if _has_score_history(snapshot):
            lines.append(f"No opportunity is {movement}.")
        else:
            lines.append("No score history is recorded.")
    else:
        lines.extend(_opportunity_table(layout, moving))
    return "\n".join(lines).rstrip("\n") + "\n"


def _has_score_history(snapshot: Snapshot) -> bool:
    """Whether any opportunity carries a recorded movement."""
    return any(
        opportunity.trend.lower() not in {"", "not recorded"}
        for opportunity in snapshot.opportunities
    )


def render_dashboard_readme(layout: VaultLayout) -> str:
    """The readme of the dashboard folder."""
    lines = [
        "# Product Hunter",
        "",
        "This folder holds the dashboards of the vault.",
        "",
    ]
    for name in (
        "Top Opportunities",
        "Recent Discoveries",
        "Rising Opportunities",
        "Falling Opportunities",
        "Needs Human Review",
    ):
        lines.append(f"- {_wikilink(layout.dashboard_path(name))}")
    lines.append(f"- {_wikilink(layout.daily_briefs_path())}")
    lines.extend(
        [
            "",
            "The vault is a projection of the database. It is rebuilt from the",
            "database, and it can be deleted at any time.",
            "",
        ]
    )
    return "\n".join(lines)


def render_human_review(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The queue of the opportunities that need a person."""
    lines = ["# Needs human review", ""]
    queued = [
        (opportunity, reasons)
        for opportunity in ranked_opportunities(snapshot)
        if (reasons := review_reasons(opportunity))
    ]
    if not queued:
        lines.append("No opportunity is waiting for a person.")
        return "\n".join(lines) + "\n"
    for opportunity, reasons in queued:
        lines.append(f"## {_cell(opportunity.title)}")
        lines.append("")
        for reason in reasons:
            lines.append(f"- {reason}")
        lines.append(f"- Note: {_note_link(layout, opportunity)}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def review_reasons(opportunity: Opportunity) -> list[str]:
    """Why an opportunity is queued for a person."""
    reasons: list[str] = []
    if opportunity.lifecycle.upper() == REVIEW_LIFECYCLE:
        reasons.append("the recorded lifecycle is HUMAN_REVIEW")
    if any(ref.stance == "counter" for ref in opportunity.evidence):
        reasons.append("counter-evidence was recorded")
    if (
        opportunity.evidence_summary.independent_authors < 2
        and opportunity.lifecycle.upper() == DEFAULT_LIFECYCLE
    ):
        reasons.append(
            "fewer than two independent authors and no human decision"
        )
    return reasons


# --- the research notes -----------------------------------------------------


def render_source(source: SourceRef) -> str:
    """The note of one source and the evidence captured from it."""
    lines = [f"# Source {_code(source.source_id)}", ""]
    lines.append(f"- Type: {_prose(source.source_type)}")
    lines.append(f"- Terms status: {_prose(source.terms_status)}")
    lines.append(f"- Enabled: {'yes' if source.enabled else 'no'}")
    lines.append(f"- Documents: {len({ref.document_id for ref in source.evidence})}")
    lines.append(f"- Mentions: {len(source.evidence)}")
    lines.append("")
    lines.append("## Communities")
    lines.append("")
    if source.communities:
        for community in source.communities:
            lines.append(f"- {_item(community)}")
    else:
        lines.append(NOT_RECORDED)
    lines.append("")
    lines.extend(_evidence_section(source.evidence))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_community(community: CommunityRef) -> str:
    """The note of one community and the evidence captured in it."""
    lines = [f"# Community {_heading(community.name)}", ""]
    lines.append(f"- Source: {_code(community.source_id)}")
    lines.append(f"- URL: {_url(community.url)}")
    lines.append(f"- Mentions: {len(community.evidence)}")
    lines.append(f"- Authors: {len(community.authors)}")
    lines.append("")
    lines.extend(_evidence_section(community.evidence))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_market(
    market: MarketReport, by_id: dict[str, Opportunity], layout: VaultLayout
) -> str:
    """The research report of one market."""
    lines = [f"# Market {_heading(market.market)}", ""]
    lines.append(f"- Opportunities: {len(market.opportunity_ids)}")
    lines.append(f"- Mentions: {len(market.evidence)}")
    lines.append(f"- Sources: {len(market.sources)}")
    lines.append(f"- Communities: {len(market.communities)}")
    lines.append("")
    lines.append("## Opportunities")
    lines.append("")
    for opportunity_id in market.opportunity_ids:
        opportunity = by_id.get(opportunity_id)
        if opportunity is None:
            continue
        lines.append(f"- {_note_link(layout, opportunity)}")
    if not market.opportunity_ids:
        lines.append(NOT_RECORDED)
    lines.append("")
    lines.extend(_evidence_section(market.evidence))
    return "\n".join(lines).rstrip("\n") + "\n"


def render_trends(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The index of the movements the score history shows."""
    lines = ["# Trends", ""]
    moving = [
        opportunity
        for opportunity in ranked_opportunities(snapshot)
        if opportunity.trend.lower() in {"rising", "falling", "flat"}
    ]
    if not moving:
        lines.append("No score history is recorded.")
        return "\n".join(lines) + "\n"
    lines.append("| Opportunity | Potential | Movement | Note |")
    lines.append("| --- | --- | --- | --- |")
    for opportunity in moving:
        lines.append(
            f"| {_cell(opportunity.title)} | {_number(opportunity.potential)} "
            f"| {_cell(opportunity.trend)} | {_note_link(layout, opportunity)} |"
        )
    return "\n".join(lines).rstrip("\n") + "\n"


# --- the daily brief --------------------------------------------------------


def activity_dates(snapshot: Snapshot) -> list[str]:
    """The days the data shows activity on."""
    days: set[str] = set()
    for opportunity in snapshot.opportunities:
        for value in (opportunity.first_seen, opportunity.last_updated):
            if value:
                days.add(value[:10])
        for ref in opportunity.evidence:
            if ref.captured_at:
                days.add(ref.captured_at[:10])
    return sorted(days)


def render_daily_brief(snapshot: Snapshot, day: str, layout: VaultLayout) -> str:
    """The brief of one day of the data."""
    ranked = ranked_opportunities(snapshot)[:10]
    rising = _movement_list(snapshot, layout, "rising")
    falling = _movement_list(snapshot, layout, "falling")
    sections = [
        (
            "Top opportunities",
            _opportunity_table(layout, ranked)
            if ranked
            else ["No opportunity is recorded."],
        ),
        ("New evidence", _evidence_lines(snapshot, day, "supporting")),
        ("Moving up", rising or ["No opportunity moved up."]),
        ("Moving down", falling or ["No opportunity moved down."]),
        ("Rejected ideas", _rejected_lines(snapshot, day, layout)),
        # The competitors table does not exist yet. The rule for a table that
        # does not exist is the rule for a field that does not exist: say so.
        ("New competitors", [NOT_RECORDED]),
        ("Counter-evidence", _evidence_lines(snapshot, day, "counter")),
        ("Recommended validation experiments", _experiment_lines(snapshot, day)),
        ("Trends", (rising + falling) or ["No score history is recorded."]),
    ]
    lines = [f"# Daily brief — {day}", ""]
    for heading, body in sections:
        lines.append(f"## {heading}")
        lines.append("")
        lines.extend(body)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_daily_briefs_index(snapshot: Snapshot, layout: VaultLayout) -> str:
    """The index of the daily briefs, which live in the daily reports."""
    lines = [
        "# Daily briefs",
        "",
        f"The brief of every day of activity. The briefs live in `{DAILY_REPORTS}/`.",
        "",
    ]
    days = activity_dates(snapshot)
    if not days:
        lines.append("No daily brief is recorded.")
    else:
        for day in days:
            lines.append(f"- {_wikilink(layout.daily_path(day))}")
    return "\n".join(lines).rstrip("\n") + "\n"


def _evidence_lines(snapshot: Snapshot, day: str, stance: str) -> list[str]:
    """The evidence of a day, one line per quote, or the marker of absence."""
    refs = _evidence_of_day(snapshot, day, stance)
    if not refs:
        if stance == "counter":
            return ["No counter-evidence was captured on this day."]
        return ["No evidence was captured on this day."]
    return [f"- {_cell(ref.quote)} ({_code(ref.span_id)})" for ref in refs]


def _rejected_lines(snapshot: Snapshot, day: str, layout: VaultLayout) -> list[str]:
    """The ideas rejected on a day, or the marker of absence."""
    rejected = [
        opportunity
        for opportunity in ranked_opportunities(snapshot)
        if opportunity.lifecycle.upper() == "REJECTED"
        and opportunity.last_updated[:10] == day
    ]
    if not rejected:
        return ["No idea was rejected on this day."]
    return [
        f"- {_cell(opportunity.title)} {_note_link(layout, opportunity)}"
        for opportunity in rejected
    ]


def _experiment_lines(snapshot: Snapshot, day: str) -> list[str]:
    """The experiments recommended on a day, or the marker of absence."""
    experiments = [
        opportunity
        for opportunity in ranked_opportunities(snapshot)
        if opportunity.recommended_experiment and opportunity.first_seen[:10] == day
    ]
    if not experiments:
        return ["No experiment was recommended on this day."]
    return [
        f"- {_cell(opportunity.title)}: {_prose(opportunity.recommended_experiment)}"
        for opportunity in experiments
    ]


def _evidence_of_day(
    snapshot: Snapshot, day: str, stance: str
) -> list[EvidenceRef]:
    """The evidence captured on a day, with a given stance."""
    refs: list[EvidenceRef] = []
    for opportunity in snapshot.opportunities:
        for ref in opportunity.evidence:
            if ref.captured_at[:10] != day:
                continue
            if stance == "counter" and ref.stance != "counter":
                continue
            if stance == "supporting" and ref.stance == "counter":
                continue
            refs.append(ref)
    return sorted(refs, key=lambda ref: ref.span_id)


def _movement_list(
    snapshot: Snapshot, layout: VaultLayout, movement: str
) -> list[str]:
    """The opportunities that moved in one direction."""
    moving = [
        opportunity
        for opportunity in ranked_opportunities(snapshot)
        if opportunity.trend.lower() == movement
    ]
    if not moving:
        return []
    return [
        f"- {_cell(opportunity.title)} {_note_link(layout, opportunity)}"
        for opportunity in moving
    ]


# --- the experiment and request notes ---------------------------------------


def render_experiment(experiment: Experiment) -> str:
    """The note of one validation experiment."""
    lines = [f"# {_heading(experiment.title)}", ""]
    lines.append(f"- Opportunity: {_code(experiment.opportunity_id)}")
    lines.append(f"- Status: {_prose(experiment.status)}")
    lines.append(f"- Started: {_prose(experiment.started_at)}")
    lines.append(f"- Completed: {_prose(experiment.completed_at)}")
    lines.append("")
    lines.append("## Hypothesis")
    lines.append("")
    lines.append(_prose(experiment.hypothesis))
    lines.append("")
    lines.append("## Result")
    lines.append("")
    lines.append(_prose(experiment.result))
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_request(request: ResearchRequest) -> str:
    """The note of one queued research request."""
    lines = [f"# {_heading(request.title)}", ""]
    lines.append(f"- Status: {_prose(request.status)}")
    lines.append(f"- Created: {_prose(request.created_at)}")
    lines.append("")
    lines.append("## Request")
    lines.append("")
    lines.append(_prose(request.body))
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# --- the whole vault --------------------------------------------------------


def render_snapshot(snapshot: Snapshot, layout: VaultLayout) -> list[RenderedNote]:
    """Every note of one snapshot, in a stable order."""
    notes: list[RenderedNote] = []
    by_id = {opportunity.opportunity_id: opportunity for opportunity in snapshot.opportunities}
    for opportunity in snapshot.opportunities:
        notes.append(
            RenderedNote(
                path=layout.opportunity_path(
                    title=opportunity.title,
                    opportunity_id=opportunity.opportunity_id,
                    potential=opportunity.potential,
                    lifecycle=opportunity.lifecycle,
                ),
                kind="opportunity",
                id=opportunity.opportunity_id,
                fields={
                    "status": opportunity.lifecycle,
                    "potential": opportunity.potential,
                    "confidence": opportunity.confidence,
                    "first_seen": opportunity.first_seen,
                    "last_updated": opportunity.last_updated,
                },
                body=render_opportunity(opportunity),
            )
        )
    for source in snapshot.sources:
        notes.append(
            RenderedNote(
                path=layout.source_path(source.source_id),
                kind="source",
                id=source.source_id,
                fields={},
                body=render_source(source),
            )
        )
    for community in snapshot.communities:
        notes.append(
            RenderedNote(
                path=layout.community_path(community.community_id),
                kind="community",
                id=community.community_id,
                fields={},
                body=render_community(community),
            )
        )
    for market in snapshot.markets:
        notes.append(
            RenderedNote(
                path=layout.market_path(market.market),
                kind="market",
                id=market.market,
                fields={},
                body=render_market(market, by_id, layout),
            )
        )
    notes.append(
        RenderedNote(
            path=layout.trends_path(),
            kind="trends",
            id="trends",
            fields={},
            body=render_trends(snapshot, layout),
        )
    )
    for day in activity_dates(snapshot):
        notes.append(
            RenderedNote(
                path=layout.daily_path(day),
                kind="daily",
                id=day,
                fields={},
                body=render_daily_brief(snapshot, day, layout),
            )
        )
    for name, body in (
        ("Top Opportunities", render_top_opportunities(snapshot, layout)),
        ("Recent Discoveries", render_recent_discoveries(snapshot, layout)),
        ("Rising Opportunities", render_rising(snapshot, layout)),
        ("Falling Opportunities", render_falling(snapshot, layout)),
        ("Needs Human Review", render_human_review(snapshot, layout)),
    ):
        notes.append(
            RenderedNote(
                path=layout.dashboard_path(name),
                kind="dashboard",
                id=name,
                fields={},
                body=body,
            )
        )
    notes.append(
        RenderedNote(
            path=layout.dashboard_path("README"),
            kind="dashboard",
            id="README",
            fields={},
            body=render_dashboard_readme(layout),
        )
    )
    notes.append(
        RenderedNote(
            path=layout.daily_briefs_path(),
            kind="dashboard",
            id="Daily Briefs",
            fields={},
            body=render_daily_briefs_index(snapshot, layout),
        )
    )
    for experiment in snapshot.experiments:
        notes.append(
            RenderedNote(
                path=layout.experiment_path(
                    experiment.experiment_id, experiment.status
                ),
                kind="experiment",
                id=experiment.experiment_id,
                fields={"status": experiment.status},
                body=render_experiment(experiment),
            )
        )
    for request in snapshot.requests:
        notes.append(
            RenderedNote(
                path=layout.request_path(request.request_id),
                kind="request",
                id=request.request_id,
                fields={"status": request.status},
                body=render_request(request),
            )
        )
    return sorted(notes, key=lambda note: note.path)
