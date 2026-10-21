"""The renderers turn a snapshot into the markdown of a note.

The renderers are pure functions, so they are tested on strings. The body of
one opportunity is pinned exactly: a change in the order of the fifteen
fields, or in the way a number is written, has to show up here.
"""

from __future__ import annotations

from pathlib import Path

from product_hunter.projection.models import (
    CommunityRef,
    EvidenceRef,
    EvidenceSummary,
    Opportunity,
    Snapshot,
    SourceRef,
)
from product_hunter.projection.render import (
    render_community,
    render_daily_brief,
    render_daily_briefs_index,
    render_dashboard_readme,
    render_human_review,
    render_opportunity,
    render_recent_discoveries,
    render_source,
    render_top_opportunities,
)
from product_hunter.projection.vault import VaultLayout

NOW = "2026-10-20T09:00:00Z"


def _evidence(
    span_id: str = "ev_1",
    *,
    quote: str = "We paste the totals by hand every month.",
    stance: str = "supporting",
    url: str = "https://news.ycombinator.com/item?id=1",
    author: str = "alice",
) -> EvidenceRef:
    return EvidenceRef(
        span_id=span_id,
        quote=quote,
        stance=stance,
        source_id="hn_algolia",
        source_type="forum",
        url=url,
        community_id="co_1",
        community_name="Hacker News",
        author_id="au_1",
        author_handle=author,
        document_id="doc_1",
        captured_at=NOW,
        content_hash="hash-1",
    )


def _summary(**overrides: float) -> EvidenceSummary:
    base = {
        "mentions": 1,
        "canonical_documents": 1,
        "independent_authors": 1,
        "uncertain_authors": 0,
        "independent_communities": 1,
        "propagated_copies": 0,
        "source_concentration": 1.0,
    }
    base.update(overrides)
    return EvidenceSummary(**base)  # type: ignore[arg-type]


def _opportunity(**overrides: object) -> Opportunity:
    base: dict[str, object] = {
        "opportunity_id": "opp_1a2b3c4d5e6f7a8b",
        "title": "The team pastes invoice totals by hand",
        "statement": "the team pastes the invoice totals by hand every month",
        "domain": "operations",
        "actor": "finance lead",
        "action": "paste totals",
        "object": "spreadsheet",
        "consequence": "a lost Saturday",
        "severity": 4.0,
        "frequency": "monthly",
        "wtp_tier": None,
        "competition": None,
        "potential": None,
        "confidence": None,
        "trend": "not recorded",
        "uncertainty": None,
        "lifecycle": "CANDIDATE",
        "recommended_experiment": None,
        "first_seen": "2026-10-18",
        "last_updated": "2026-10-20",
        "evidence": [_evidence()],
        "evidence_summary": _summary(),
        "claims": [],
        "communities": ["Hacker News"],
    }
    base.update(overrides)
    return Opportunity(**base)  # type: ignore[arg-type]


def _snapshot(*opportunities: Opportunity, reference_date: str = "2026-10-20") -> Snapshot:
    return Snapshot(
        opportunities=list(opportunities),
        sources=[],
        communities=[],
        markets=[],
        experiments=[],
        requests=[],
        reference_date=reference_date,
    )


# --- the opportunity note ---------------------------------------------------

THE_FIFTEEN_FIELDS = (
    "Opportunity",
    "Normalized problem",
    "Evidence summary",
    "Independent evidence",
    "Communities",
    "Severity",
    "WTP",
    "Competition",
    "Counter-evidence",
    "Potential",
    "Confidence",
    "Trend",
    "Uncertainty",
    "Lifecycle",
    "Recommended next validation experiment",
)


def test_the_note_carries_the_fifteen_fields_in_order() -> None:
    body = render_opportunity(_opportunity())
    positions = [body.index(f"## {name}") for name in THE_FIFTEEN_FIELDS]
    assert positions == sorted(positions)


def test_a_field_the_database_does_not_hold_reads_not_recorded() -> None:
    body = render_opportunity(_opportunity())
    assert "## WTP\n\nnot recorded\n" in body
    assert "## Competition\n\nnot recorded\n" in body
    assert "## Potential\n\nnot recorded\n" in body
    assert "## Confidence\n\nnot recorded\n" in body
    assert "## Uncertainty\n\nnot recorded\n" in body


def test_a_missing_number_is_not_written_as_zero() -> None:
    body = render_opportunity(_opportunity(potential=None, confidence=None))
    assert "## Potential\n\nnot recorded\n" in body
    assert "0.00" not in body


def test_a_recorded_zero_is_not_the_same_as_a_missing_number() -> None:
    body = render_opportunity(_opportunity(potential=0.0))
    assert "## Potential\n\n0.00\n" in body


def test_numbers_are_written_at_a_fixed_precision() -> None:
    body = render_opportunity(
        _opportunity(severity=4.0, potential=91.0, confidence=0.87)
    )
    assert "## Severity\n\n4.00\n" in body
    assert "## Potential\n\n91.00\n" in body
    assert "## Confidence\n\n0.87\n" in body


def test_the_normalized_problem_names_the_spans_behind_it() -> None:
    body = render_opportunity(_opportunity())
    assert "Spans: `ev_1`" in body


def test_a_backtick_in_a_value_stays_inside_the_code_span() -> None:
    """The value is shown as the database holds it, not shortened."""
    body = render_opportunity(_opportunity(domain="a`b"))
    assert "- Domain: ``a`b``" in body


def test_a_run_of_backticks_gets_a_longer_delimiter() -> None:
    body = render_opportunity(_opportunity(domain="a``b"))
    assert "- Domain: ```a``b```" in body


def test_a_value_that_ends_with_a_backtick_forms_its_code_span() -> None:
    """The backtick would merge with the delimiter, so the value is padded.

    CommonMark strips one space from each end of a code span, so the value
    still shows as the database holds it.
    """
    body = render_opportunity(_opportunity(domain="a`"))
    assert "- Domain: `` a` ``" in body


def test_a_value_that_is_only_a_backtick_forms_its_code_span() -> None:
    body = render_opportunity(_opportunity(domain="`"))
    assert "- Domain: `` ` ``" in body


def test_the_counts_are_stated_separately_from_the_mentions() -> None:
    body = render_opportunity(
        _opportunity(
            evidence=[_evidence("ev_1"), _evidence("ev_2")],
            evidence_summary=_summary(
                mentions=2,
                canonical_documents=1,
                independent_authors=1,
                uncertain_authors=1,
                independent_communities=1,
                propagated_copies=1,
                source_concentration=0.5,
            ),
        )
    )
    assert "- Mentions: 2" in body
    assert "- Canonical documents: 1" in body
    assert "- Independent authors: 1" in body
    assert "- Uncertain authors: 1" in body
    assert "- Propagated copies: 1" in body
    assert "- Busiest source share: 0.50" in body
    assert "2 mentions are not 2 people" in body


def test_the_busiest_source_share_is_not_recorded_without_mentions() -> None:
    """A share cannot be measured with no mention, and a zero would be a lie."""
    body = render_opportunity(
        _opportunity(
            evidence=[],
            evidence_summary=_summary(mentions=0, source_concentration=0.0),
        )
    )
    assert "- Busiest source share: not recorded" in body
    assert "0.00" not in body


def test_a_quote_renders_inside_a_fenced_text_block() -> None:
    body = render_opportunity(_opportunity())
    assert "```text\nWe paste the totals by hand every month.\n```" in body


def test_a_hostile_quote_stays_inert() -> None:
    quote = "[[the note]] {{tp.date.now()}} <script>alert(1)</script> ```"
    body = render_opportunity(
        _opportunity(evidence=[_evidence(quote=quote)])
    )
    assert "<script>" not in body
    assert "{{tp.date.now()}}" not in body
    assert "[[" not in body.replace("\\[[", "")
    assert "```dataview" not in body
    assert body.count("```") == 2


def test_the_counter_evidence_links_each_quote_to_its_span() -> None:
    body = render_opportunity(
        _opportunity(
            evidence=[
                _evidence("ev_1"),
                _evidence("ev_2", quote="Nobody wants this.", stance="counter"),
            ]
        )
    )
    assert "## Counter-evidence\n\n```text\nNobody wants this.\n```" in body
    assert "- Span: `ev_2`" in body


def test_an_opportunity_without_counter_evidence_says_so() -> None:
    body = render_opportunity(_opportunity())
    assert "## Counter-evidence\n\nnot recorded\n" in body


def test_the_claims_name_the_spans_behind_them() -> None:
    body = render_opportunity(
        _opportunity(claims=["the team pastes totals (spans: ev_1)"])
    )
    assert "### Claims" in body
    assert "- the team pastes totals (spans: ev_1)" in body


def test_the_body_of_one_opportunity_is_pinned() -> None:
    body = render_opportunity(
        _opportunity(
            potential=91.0,
            confidence=0.87,
            trend="rising",
            lifecycle="PROMISING",
            wtp_tier="would pay 50 a month",
            competition=0.2,
            uncertainty="one community so far",
            recommended_experiment="a landing page for the paste tool",
        )
    )
    assert body == GOLDEN_OPPORTUNITY


# --- the dashboards ---------------------------------------------------------


def test_the_top_opportunities_are_ranked_by_potential(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    low = _opportunity(
        opportunity_id="opp_low", title="Low", potential=10.0, first_seen="2026-10-01"
    )
    high = _opportunity(
        opportunity_id="opp_high", title="High", potential=91.0, first_seen="2026-10-01"
    )
    body = render_top_opportunities(_snapshot(low, high), layout)
    assert body.index("High") < body.index("Low")
    assert "| 1 | High | 91.00 |" in body


def test_a_missing_potential_falls_back_to_severity(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    severe = _opportunity(
        opportunity_id="opp_a", title="Severe", potential=None, severity=5.0
    )
    mild = _opportunity(
        opportunity_id="opp_b", title="Mild", potential=None, severity=1.0
    )
    body = render_top_opportunities(_snapshot(severe, mild), layout)
    assert body.index("Severe") < body.index("Mild")


def test_a_note_link_is_a_wikilink_that_obsidian_can_resolve(tmp_path: Path) -> None:
    """A link that closes on one side only is text, and Obsidian cannot open it."""
    layout = VaultLayout(tmp_path)
    opportunity = _opportunity()
    body = render_top_opportunities(_snapshot(opportunity), layout)
    path = layout.opportunity_path(
        title=opportunity.title,
        opportunity_id=opportunity.opportunity_id,
        potential=opportunity.potential,
        lifecycle=opportunity.lifecycle,
    )
    target = path.removesuffix(".md")
    assert f"[[{target}]]" in body
    assert body.count("[[") == body.count("]]")


def test_the_dashboard_readme_links_the_notes_it_holds(tmp_path: Path) -> None:
    """The readme links every dashboard note, the daily briefs index included."""
    layout = VaultLayout(tmp_path)
    body = render_dashboard_readme(layout)
    assert "[[00-Dashboard/Top Opportunities]]" in body
    assert "[[00-Dashboard/Recent Discoveries]]" in body
    assert "[[00-Dashboard/Rising Opportunities]]" in body
    assert "[[00-Dashboard/Falling Opportunities]]" in body
    assert "[[00-Dashboard/Needs Human Review]]" in body
    assert "[[00-Dashboard/Daily Briefs/README]]" in body
    assert body.count("[[") == body.count("]]")


def test_recent_discoveries_hold_the_last_seven_days(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    fresh = _opportunity(opportunity_id="opp_a", title="Fresh", first_seen="2026-10-18")
    old = _opportunity(opportunity_id="opp_b", title="Old", first_seen="2026-10-01")
    body = render_recent_discoveries(_snapshot(fresh, old), layout)
    assert "Fresh" in body
    assert "Old" not in body


def test_the_review_queue_says_why_each_one_is_queued(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    queued = _opportunity(
        opportunity_id="opp_a",
        title="Queued",
        lifecycle="HUMAN_REVIEW",
        evidence_summary=_summary(independent_authors=3),
    )
    body = render_human_review(_snapshot(queued), layout)
    assert "the recorded lifecycle is HUMAN_REVIEW" in body


def test_the_review_queue_holds_an_opportunity_without_independent_authors(
    tmp_path: Path,
) -> None:
    layout = VaultLayout(tmp_path)
    lonely = _opportunity(
        opportunity_id="opp_a",
        title="Lonely",
        lifecycle="CANDIDATE",
        evidence_summary=_summary(independent_authors=1),
    )
    body = render_human_review(_snapshot(lonely), layout)
    assert "fewer than two independent authors and no human decision" in body


def test_the_review_queue_holds_an_opportunity_with_counter_evidence(
    tmp_path: Path,
) -> None:
    layout = VaultLayout(tmp_path)
    contested = _opportunity(
        opportunity_id="opp_a",
        title="Contested",
        lifecycle="PROMISING",
        evidence=[
            _evidence("ev_1"),
            _evidence("ev_2", quote="No.", stance="counter"),
        ],
        evidence_summary=_summary(independent_authors=3),
    )
    body = render_human_review(_snapshot(contested), layout)
    assert "counter-evidence was recorded" in body


# --- the daily brief --------------------------------------------------------


THE_NINE_SECTIONS = (
    "Top opportunities",
    "New evidence",
    "Moving up",
    "Moving down",
    "Rejected ideas",
    "New competitors",
    "Counter-evidence",
    "Recommended validation experiments",
    "Trends",
)


def test_the_brief_holds_the_nine_sections_of_the_spec(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    body = render_daily_brief(_snapshot(_opportunity()), "2026-10-20", layout)
    for name in THE_NINE_SECTIONS:
        assert f"## {name}\n" in body


def test_the_brief_holds_the_top_opportunities_and_not_the_new_ones(
    tmp_path: Path,
) -> None:
    layout = VaultLayout(tmp_path)
    new_today = _opportunity(
        opportunity_id="opp_new",
        title="New today",
        potential=10.0,
        first_seen="2026-10-20",
    )
    top = _opportunity(
        opportunity_id="opp_top",
        title="Top overall",
        potential=91.0,
        first_seen="2026-10-01",
    )
    body = render_daily_brief(_snapshot(new_today, top), "2026-10-20", layout)
    assert "| 1 | Top overall | 91.00 |" in body
    assert "| 2 | New today | 10.00 |" in body


def test_the_brief_says_the_competitors_are_not_recorded(tmp_path: Path) -> None:
    """The competitors table does not exist yet, so the section says so."""
    layout = VaultLayout(tmp_path)
    body = render_daily_brief(_snapshot(_opportunity()), "2026-10-20", layout)
    assert "## New competitors\n\nnot recorded\n" in body


def test_the_body_of_the_daily_brief_is_pinned(tmp_path: Path) -> None:
    """The whole brief is pinned, so a change of it has to show up here."""
    layout = VaultLayout(tmp_path)
    rising = _opportunity(
        opportunity_id="opp_rise",
        title="Rising idea",
        potential=91.0,
        confidence=0.8,
        trend="rising",
        first_seen="2026-10-18",
        last_updated="2026-10-20",
        evidence=[
            _evidence("ev_1"),
            _evidence("ev_2", quote="Nobody wants this.", stance="counter"),
        ],
    )
    falling = _opportunity(
        opportunity_id="opp_fall",
        title="Falling idea",
        potential=40.0,
        trend="falling",
        first_seen="2026-10-01",
        last_updated="2026-10-20",
        evidence=[],
    )
    rejected = _opportunity(
        opportunity_id="opp_reject",
        title="Rejected idea",
        potential=20.0,
        lifecycle="REJECTED",
        first_seen="2026-10-10",
        last_updated="2026-10-20",
        evidence=[],
    )
    fresh = _opportunity(
        opportunity_id="opp_fresh",
        title="Fresh idea",
        potential=60.0,
        first_seen="2026-10-20",
        last_updated="2026-10-20",
        recommended_experiment="a landing page for the paste tool",
        evidence=[],
    )
    body = render_daily_brief(
        _snapshot(rising, falling, rejected, fresh), "2026-10-20", layout
    )
    assert body == GOLDEN_DAILY_BRIEF


def test_the_empty_body_of_the_daily_brief_is_pinned(tmp_path: Path) -> None:
    """A brief of a day with nothing on it is pinned as well."""
    layout = VaultLayout(tmp_path)
    body = render_daily_brief(_snapshot(), "2026-10-20", layout)
    assert body == GOLDEN_EMPTY_DAILY_BRIEF


def test_the_index_of_the_daily_briefs_links_every_brief(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path)
    body = render_daily_briefs_index(_snapshot(_opportunity()), layout)
    assert "[[03-Daily-Reports/2026-10-18]]" in body
    assert "[[03-Daily-Reports/2026-10-20]]" in body
    assert body.count("[[") == body.count("]]")


def test_the_index_of_the_daily_briefs_says_when_there_is_none(
    tmp_path: Path,
) -> None:
    layout = VaultLayout(tmp_path)
    body = render_daily_briefs_index(_snapshot(), layout)
    assert "No daily brief is recorded." in body


# --- the source and community notes -----------------------------------------


def test_a_source_note_states_the_terms_and_the_evidence() -> None:
    source = SourceRef(
        source_id="hn_algolia",
        source_type="forum",
        terms_status="permitted",
        enabled=True,
        evidence=[_evidence()],
        communities=["Hacker News"],
    )
    body = render_source(source)
    assert "- Terms status: permitted" in body
    assert "- Mentions: 1" in body
    assert "We paste the totals by hand every month." in body


def test_a_community_note_writes_the_url_as_inline_code() -> None:
    community = CommunityRef(
        community_id="co_1",
        name="Hacker News",
        url="javascript:alert(1)",
        source_id="hn_algolia",
        evidence=[_evidence()],
        authors=["alice"],
    )
    body = render_community(community)
    assert "`blocked:javascript`" in body
    assert "javascript:alert(1)" not in body


GOLDEN_OPPORTUNITY = """\
# The team pastes invoice totals by hand

## Opportunity

the team pastes the invoice totals by hand every month

## Normalized problem

- Domain: `operations`
- Actor: `finance lead`
- Action: `paste totals`
- Object: `spreadsheet`
- Consequence: `a lost Saturday`

Spans: `ev_1`

## Evidence summary

- Mentions: 1
- Canonical documents: 1
- Independent authors: 1
- Uncertain authors: 0
- Independent communities: 1
- Propagated copies: 0
- Busiest source share: 1.00

## Independent evidence

1 mention is not 1 person. This opportunity rests on 1 document from 1 author, none of them uncertain, across 1 community. None of the mentions are copies of another document.

## Communities

- Hacker News

## Severity

4.00

## WTP

would pay 50 a month

## Competition

0.20

## Counter-evidence

not recorded

## Potential

91.00

## Confidence

0.87

## Trend

rising

## Uncertainty

one community so far

## Lifecycle

PROMISING

## Recommended next validation experiment

a landing page for the paste tool

## Evidence

### `https://news.ycombinator.com/item?id=1`

- Community: Hacker News
- Author: `alice`
- Source: `hn_algolia`
- Captured: 2026-10-20T09:00:00Z
- Stance: supporting

```text
We paste the totals by hand every month.
```

Span: `ev_1`
"""

GOLDEN_DAILY_BRIEF = """\
# Daily brief — 2026-10-20

## Top opportunities

| Rank | Opportunity | Potential | Confidence | Evidence | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | Rising idea | 91.00 | 0.80 | 1 | [[01-Ideas/🔥 90-100 Exceptional/rising-idea--8a5b67be]] |
| 2 | Fresh idea | 60.00 | not recorded | 1 | [[01-Ideas/⚪ 50-69 Weak Evidence/fresh-idea--8f747d56]] |
| 3 | Falling idea | 40.00 | not recorded | 1 | [[01-Ideas/🔴 00-49 Low Potential/falling-idea--afaaaf92]] |
| 4 | Rejected idea | 20.00 | not recorded | 1 | [[99-Archive/Rejected/rejected-idea--7fd84810]] |

## New evidence

- We paste the totals by hand every month. (`ev_1`)

## Moving up

- Rising idea [[01-Ideas/🔥 90-100 Exceptional/rising-idea--8a5b67be]]

## Moving down

- Falling idea [[01-Ideas/🔴 00-49 Low Potential/falling-idea--afaaaf92]]

## Rejected ideas

- Rejected idea [[99-Archive/Rejected/rejected-idea--7fd84810]]

## New competitors

not recorded

## Counter-evidence

- Nobody wants this. (`ev_2`)

## Recommended validation experiments

- Fresh idea: a landing page for the paste tool

## Trends

- Rising idea [[01-Ideas/🔥 90-100 Exceptional/rising-idea--8a5b67be]]
- Falling idea [[01-Ideas/🔴 00-49 Low Potential/falling-idea--afaaaf92]]
"""

GOLDEN_EMPTY_DAILY_BRIEF = """\
# Daily brief — 2026-10-20

## Top opportunities

No opportunity is recorded.

## New evidence

No evidence was captured on this day.

## Moving up

No opportunity moved up.

## Moving down

No opportunity moved down.

## Rejected ideas

No idea was rejected on this day.

## New competitors

not recorded

## Counter-evidence

No counter-evidence was captured on this day.

## Recommended validation experiments

No experiment was recommended on this day.

## Trends

No score history is recorded.
"""
