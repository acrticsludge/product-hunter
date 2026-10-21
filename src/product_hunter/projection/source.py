"""Read the authoritative database into a snapshot.

The reader opens nothing: the connection is passed in and the caller decides
whether it is read-only. Every table is asked for its columns first, so a
table that arrives with a different shape degrades the projection instead of
breaking it.

Where the canonical tables exist they are the source. Where they do not, the
pain points are grouped by their normalized key, and the fields that cannot
be derived from them are reported as not recorded rather than as a zero.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from product_hunter.projection.contract import LIFECYCLES, select_columns
from product_hunter.projection.models import (
    CommunityRef,
    EvidenceRef,
    EvidenceSummary,
    Experiment,
    MarketReport,
    Opportunity,
    ResearchRequest,
    Snapshot,
    SourceRef,
)

# The lifecycle a decision text names. A text outside the vocabulary leaves
# the lifecycle at CANDIDATE rather than being guessed at.
_LIFECYCLE_LOOKUP = {lifecycle.lower(): lifecycle for lifecycle in LIFECYCLES}

# The lifecycle an opportunity has when nothing decided otherwise.
_DEFAULT_LIFECYCLE = "CANDIDATE"

# A request in one of these states is no longer a queued request.
_FINISHED_REQUEST_STATUSES = {
    "done",
    "completed",
    "succeeded",
    "failed",
    "cancelled",
    "closed",
}

# An author link below this confidence does not count as an independent
# author, because the identity behind it is not established.
_AUTHOR_CONFIDENCE = 0.5


@dataclass
class _Tables:
    """Every table the reader read, indexed the way it needs them."""

    documents: dict[str, dict[str, object]] = field(default_factory=dict)
    communities: dict[str, dict[str, object]] = field(default_factory=dict)
    sources: dict[str, dict[str, object]] = field(default_factory=dict)
    evidence: dict[str, dict[str, object]] = field(default_factory=dict)
    authors: dict[str, dict[str, object]] = field(default_factory=dict)
    pain_points: list[dict[str, object]] = field(default_factory=list)
    pain_links: list[dict[str, object]] = field(default_factory=list)
    author_links: list[dict[str, object]] = field(default_factory=list)
    edges: list[dict[str, object]] = field(default_factory=list)
    decisions: list[dict[str, object]] = field(default_factory=list)
    opportunity_rows: list[dict[str, object]] = field(default_factory=list)
    score_rows: list[dict[str, object]] = field(default_factory=list)
    claim_rows: list[dict[str, object]] = field(default_factory=list)
    opportunity_evidence: list[dict[str, object]] = field(default_factory=list)
    experiments: list[dict[str, object]] = field(default_factory=list)
    requests: list[dict[str, object]] = field(default_factory=list)


@dataclass
class _Draft:
    """The fields of an opportunity before its evidence is attached."""

    opportunity_id: str
    title: str
    statement: str
    domain: str
    actor: str
    action: str
    object: str
    consequence: str
    severity: float | None
    frequency: str
    wtp_tier: str | None
    competition: float | None
    potential: float | None
    confidence: float | None
    trend: str
    uncertainty: str | None
    lifecycle: str
    recommended_experiment: str | None
    first_seen: str
    last_updated: str
    span_ids: list[str] = field(default_factory=list)
    stances: dict[str, str] = field(default_factory=dict)


class SqliteProjectionSource:
    """Read the authoritative state into a snapshot."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def snapshot(self, *, as_of: str | None = None) -> Snapshot:
        """Read everything the projection shows."""
        tables = self._read_tables()
        groups, spans_by_pain = _group_pain_points(tables)
        drafts = self._drafts(tables, groups, spans_by_pain)
        opportunities = [self._finish(tables, draft) for draft in drafts]
        opportunities.sort(key=lambda item: item.opportunity_id)
        reference_date = as_of or self._reference_date(tables)
        return Snapshot(
            opportunities=opportunities,
            sources=self._sources(tables, opportunities),
            communities=self._communities(tables, opportunities),
            markets=self._markets(opportunities),
            experiments=self._experiments(tables),
            requests=self._requests(tables),
            reference_date=reference_date,
        )

    # --- the tables --------------------------------------------------------

    def _read_tables(self) -> _Tables:
        tables = _Tables()
        tables.documents = _by_id(self._rows("documents"), "doc_id")
        tables.communities = _by_id(self._rows("communities"), "community_id")
        tables.sources = _by_id(self._rows("sources"), "source_id")
        tables.evidence = _by_id(self._rows("evidence"), "span_id")
        tables.authors = _by_id(self._rows("authors"), "author_id")
        tables.pain_points = self._rows("pain_points")
        tables.pain_links = self._rows("evidence_pain_links")
        tables.author_links = self._rows("author_links")
        tables.edges = self._rows("propagation_edges")
        tables.decisions = self._rows("human_decisions")
        tables.opportunity_rows = self._rows("opportunities")
        tables.score_rows = self._rows("opportunity_scores")
        tables.claim_rows = self._rows("opportunity_claims")
        tables.opportunity_evidence = self._rows("opportunity_evidence")
        tables.experiments = self._rows("experiments")
        tables.requests = self._rows("research_requests")
        return tables

    def _rows(self, table: str) -> list[dict[str, object]]:
        return select_columns(self.conn, table)

    # --- the opportunities -------------------------------------------------

    def _drafts(
        self,
        tables: _Tables,
        groups: dict[str, list[dict[str, object]]],
        spans_by_pain: dict[str, set[str]],
    ) -> list[_Draft]:
        """The opportunities, from the canonical table when it has rows."""
        if tables.opportunity_rows:
            drafts = [
                self._row_draft(tables, row, groups, spans_by_pain)
                for row in sorted(
                    tables.opportunity_rows,
                    key=lambda row: _text(row.get("opportunity_id")),
                )
            ]
            return [draft for draft in drafts if draft.opportunity_id]
        drafts = [
            self._group_draft(tables, key, pains, spans_by_pain)
            for key, pains in sorted(groups.items())
        ]
        return drafts

    def _row_draft(
        self,
        tables: _Tables,
        row: dict[str, object],
        groups: dict[str, list[dict[str, object]]],
        spans_by_pain: dict[str, set[str]],
    ) -> _Draft:
        """An opportunity from a row of the canonical table."""
        opportunity_id = _text(row.get("opportunity_id"))
        title = _text(row.get("title"))
        statement = _text(row.get("normalized_problem"))
        spans, stances = self._row_spans(tables, opportunity_id)
        pains = self._matched_group(row, title, statement, groups) or []
        if not spans and pains:
            spans = sorted(
                {
                    span_id
                    for pain in pains
                    for span_id in spans_by_pain.get(_text(pain.get("pain_id")), set())
                }
            )
        statement = _text(_first_value(pains, "pain_statement", statement))
        if not title:
            title = statement
        severity = _float(row.get("severity"))
        if severity is None:
            # The severity of the group, when the row leaves it empty.
            severity = _max_number(pains, "severity")
            if severity == 0.0:
                severity = None
        scores = _scores_for(tables, opportunity_id)
        latest = scores[-1] if scores else None
        return _Draft(
            opportunity_id=opportunity_id,
            title=title,
            statement=statement,
            domain=_text(_first_value(pains, "domain", row.get("domain"))),
            actor=_text(_first_value(pains, "actor", row.get("actor"))),
            action=_text(_first_value(pains, "action", row.get("action"))),
            object=_text(_first_value(pains, "object", row.get("object"))),
            consequence=_text(
                _first_value(pains, "consequence", row.get("consequence"))
            ),
            severity=severity,
            frequency=_frequency(row.get("frequency")),
            wtp_tier=_optional_text(row.get("wtp_tier")),
            competition=_float(row.get("competition")),
            potential=_first_number(row.get("potential"), latest, "potential"),
            confidence=_first_number(row.get("confidence"), latest, "confidence"),
            trend=self._trend(row.get("trend"), scores),
            uncertainty=_optional_text(row.get("uncertainty")),
            lifecycle=self._lifecycle(
                tables, opportunity_id, row.get("lifecycle"), spans
            ),
            recommended_experiment=_optional_text(row.get("recommended_experiment")),
            first_seen=_text(row.get("first_seen")),
            last_updated=_text(row.get("last_updated")),
            span_ids=spans,
            stances=stances,
        )

    def _group_draft(
        self,
        tables: _Tables,
        key: str,
        pains: list[dict[str, object]],
        spans_by_pain: dict[str, set[str]],
    ) -> _Draft:
        """An opportunity from a group of pain points."""
        ordered = sorted(pains, key=lambda pain: _text(pain.get("pain_id")))
        opportunity_id = "opp_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        spans: set[str] = set()
        for pain in ordered:
            spans |= spans_by_pain.get(_text(pain.get("pain_id")), set())
        if not spans:
            spans = {
                _text(pain.get("span_id")) for pain in ordered if _text(pain.get("span_id"))
            }
        statement = _text(_first_value(ordered, "pain_statement"))
        title = statement or key
        severity = _max_number(pains, "severity")
        scores = _scores_for(tables, opportunity_id)
        return _Draft(
            opportunity_id=opportunity_id,
            title=title,
            statement=statement or title,
            domain=_text(_first_value(ordered, "domain")),
            actor=_text(_first_value(ordered, "actor")),
            action=_text(_first_value(ordered, "action")),
            object=_text(_first_value(ordered, "object")),
            consequence=_text(_first_value(ordered, "consequence")),
            severity=None if severity == 0.0 else severity,
            frequency=_frequency(_first_value(ordered, "frequency")),
            wtp_tier=None,
            competition=None,
            potential=None,
            confidence=None,
            trend=self._trend(None, scores),
            uncertainty=None,
            lifecycle=self._lifecycle(tables, opportunity_id, None, sorted(spans)),
            recommended_experiment=None,
            first_seen="",
            last_updated="",
            span_ids=sorted(spans),
            stances={},
        )

    def _row_spans(
        self, tables: _Tables, opportunity_id: str
    ) -> tuple[list[str], dict[str, str]]:
        """The spans of an opportunity and the stance of each."""
        spans: set[str] = set()
        stances: dict[str, str] = {}
        for row in tables.opportunity_evidence:
            if _text(row.get("opportunity_id")) != opportunity_id:
                continue
            span_id = _text(row.get("span_id"))
            if not span_id:
                continue
            spans.add(span_id)
            stances[span_id] = _stance(row.get("stance"))
        return sorted(spans), stances

    def _matched_group(
        self,
        row: dict[str, object],
        title: str,
        statement: str,
        groups: dict[str, list[dict[str, object]]],
    ) -> list[dict[str, object]] | None:
        """The pain points a canonical row stands for.

        Without the evidence table there is no link to follow, so the row is
        matched to the group that says the same thing.
        """
        wanted = {value for value in (title, statement) if value}
        for key in sorted(groups):
            if key in wanted:
                return groups[key]
            for pain in groups[key]:
                if _text(pain.get("pain_statement")) in wanted:
                    return groups[key]
        return None

    # --- the lifecycle, the trend and the scores ---------------------------

    def _lifecycle(
        self,
        tables: _Tables,
        opportunity_id: str,
        recorded: object,
        span_ids: list[str],
    ) -> str:
        """The lifecycle of an opportunity.

        The recorded value wins when the vocabulary knows it. Otherwise the
        latest human decision names it, and a decision the vocabulary does
        not know leaves the lifecycle at CANDIDATE.
        """
        text = _text(recorded)
        if text:
            return _LIFECYCLE_LOOKUP.get(text.lower(), _DEFAULT_LIFECYCLE)
        entity_ids = {opportunity_id}
        entity_ids |= self._pain_ids_of(tables, span_ids)
        decision = _latest_decision(tables.decisions, entity_ids)
        if decision is None:
            return _DEFAULT_LIFECYCLE
        return _LIFECYCLE_LOOKUP.get(decision.lower(), _DEFAULT_LIFECYCLE)

    def _pain_ids_of(self, tables: _Tables, span_ids: list[str]) -> set[str]:
        """The pain points that own the given spans."""
        wanted = set(span_ids)
        found: set[str] = set()
        for link in tables.pain_links:
            span_id = _text(link.get("span_id"))
            pain_id = _text(link.get("pain_id"))
            if span_id in wanted and pain_id:
                found.add(pain_id)
        for pain in tables.pain_points:
            if _text(pain.get("span_id")) in wanted:
                pain_id = _text(pain.get("pain_id"))
                if pain_id:
                    found.add(pain_id)
        return found

    def _trend(self, recorded: object, scores: list[dict[str, object]]) -> str:
        """The movement of an opportunity.

        The newest score is compared with the one before it. One score is
        not a movement, so it is reported as not recorded.
        """
        text = _text(recorded)
        if text:
            return text.lower()
        if len(scores) < 2:
            return "not recorded"
        newest = _float(scores[-1].get("potential"))
        previous = _float(scores[-2].get("potential"))
        if newest is None or previous is None:
            return "not recorded"
        if newest > previous:
            return "rising"
        if newest < previous:
            return "falling"
        return "flat"

    # --- the evidence ------------------------------------------------------

    def _finish(self, tables: _Tables, draft: _Draft) -> Opportunity:
        """Attach the evidence to an opportunity and count it."""
        refs, confidences = self._evidence(tables, draft)
        summary = _summary(refs, confidences, tables)
        communities = sorted(
            {ref.community_name for ref in refs if ref.community_name}
        )
        claims = _claims_for(tables, draft.opportunity_id)
        first_seen = draft.first_seen or _earliest(refs)
        last_updated = draft.last_updated or _latest(refs)
        return Opportunity(
            opportunity_id=draft.opportunity_id,
            title=draft.title,
            statement=draft.statement,
            domain=draft.domain,
            actor=draft.actor,
            action=draft.action,
            object=draft.object,
            consequence=draft.consequence,
            severity=draft.severity,
            frequency=draft.frequency,
            wtp_tier=draft.wtp_tier,
            competition=draft.competition,
            potential=draft.potential,
            confidence=draft.confidence,
            trend=draft.trend,
            uncertainty=draft.uncertainty,
            lifecycle=draft.lifecycle,
            recommended_experiment=draft.recommended_experiment,
            first_seen=first_seen,
            last_updated=last_updated,
            evidence=refs,
            evidence_summary=summary,
            claims=claims,
            communities=communities,
        )

    def _evidence(
        self, tables: _Tables, draft: _Draft
    ) -> tuple[list[EvidenceRef], dict[str, float]]:
        """The evidence of an opportunity, with the confidence of each author."""
        refs: list[EvidenceRef] = []
        confidences: dict[str, float] = {}
        for span_id in draft.span_ids:
            row = tables.evidence.get(span_id)
            if row is None:
                continue
            document = tables.documents.get(_text(row.get("doc_id")))
            document_id = _text(row.get("doc_id"))
            community_id = _text(document.get("community_id")) if document else ""
            community = tables.communities.get(community_id) if community_id else None
            source_id = _text(document.get("source_id")) if document else ""
            source = tables.sources.get(source_id) if source_id else None
            author_id, handle, confidence = self._author_of(tables, document_id)
            if author_id:
                # The best link counts: an author who is identified on one
                # document is identified, however weak the other link is.
                confidences[author_id] = max(confidences.get(author_id, 0.0), confidence)
            refs.append(
                EvidenceRef(
                    span_id=span_id,
                    quote=_quote(row.get("quote")),
                    stance=draft.stances.get(span_id, "supporting"),
                    source_id=source_id,
                    source_type=_text(source.get("source_type")) if source else "",
                    url=_text(document.get("url")) if document else "",
                    community_id=community_id or None,
                    community_name=_text(community.get("name")) if community else "",
                    author_id=author_id,
                    author_handle=handle,
                    document_id=document_id,
                    captured_at=_text(row.get("created_at")),
                    content_hash=_text(document.get("text_sha256")) if document else "",
                )
            )
        return refs, confidences

    def _author_of(
        self, tables: _Tables, document_id: str
    ) -> tuple[str | None, str, float]:
        """The author of a document, with the confidence of the link.

        The link with the highest confidence wins, and the smallest author id
        breaks a tie, so the answer does not depend on the row order.
        """
        best: tuple[tuple[float, str], str, float] | None = None
        for link in tables.author_links:
            if _text(link.get("doc_id")) != document_id:
                continue
            author_id = _text(link.get("author_id"))
            if not author_id:
                continue
            confidence = _float(link.get("confidence")) or 0.0
            key = (-confidence, author_id)
            if best is None or key < best[0]:
                best = (key, author_id, confidence)
        if best is None:
            return None, "", 0.0
        author = tables.authors.get(best[1])
        handle = ""
        if author:
            handle = _text(author.get("handle")) or _text(author.get("display_name"))
        return best[1], handle, best[2]

    # --- the notes the opportunities feed -----------------------------------

    def _sources(
        self, tables: _Tables, opportunities: list[Opportunity]
    ) -> list[SourceRef]:
        """One note per source that has a document."""
        evidence = _all_evidence(opportunities)
        by_source: dict[str, list[EvidenceRef]] = defaultdict(list)
        for ref in evidence:
            by_source[ref.source_id].append(ref)
        used = {
            _text(document.get("source_id"))
            for document in tables.documents.values()
            if _text(document.get("source_id"))
        }
        refs: list[SourceRef] = []
        for source_id in sorted(used):
            source = tables.sources.get(source_id, {})
            items = sorted(by_source.get(source_id, []), key=lambda ref: ref.span_id)
            refs.append(
                SourceRef(
                    source_id=source_id,
                    source_type=_text(source.get("source_type")),
                    terms_status=_text(source.get("terms_status")),
                    enabled=bool(source.get("enabled", 1)),
                    evidence=items,
                    communities=sorted(
                        {ref.community_name for ref in items if ref.community_name}
                    ),
                )
            )
        return refs

    def _communities(
        self, tables: _Tables, opportunities: list[Opportunity]
    ) -> list[CommunityRef]:
        """One note per community that has a document."""
        evidence = _all_evidence(opportunities)
        by_community: dict[str, list[EvidenceRef]] = defaultdict(list)
        for ref in evidence:
            if ref.community_id:
                by_community[ref.community_id].append(ref)
        used = {
            _text(document.get("community_id"))
            for document in tables.documents.values()
            if _text(document.get("community_id"))
        }
        refs: list[CommunityRef] = []
        for community_id in sorted(used):
            community = tables.communities.get(community_id, {})
            items = sorted(
                by_community.get(community_id, []), key=lambda ref: ref.span_id
            )
            refs.append(
                CommunityRef(
                    community_id=community_id,
                    name=_text(community.get("name")),
                    url=_text(community.get("url")),
                    source_id=_text(community.get("source_id")),
                    evidence=items,
                    authors=sorted(
                        {ref.author_handle for ref in items if ref.author_handle}
                    ),
                )
            )
        return refs

    def _markets(self, opportunities: list[Opportunity]) -> list[MarketReport]:
        """One research report per domain."""
        by_market: dict[str, list[Opportunity]] = defaultdict(list)
        for opportunity in opportunities:
            if opportunity.domain:
                by_market[opportunity.domain].append(opportunity)
        markets: list[MarketReport] = []
        for market in sorted(by_market):
            items = by_market[market]
            evidence = _all_evidence(items)
            markets.append(
                MarketReport(
                    market=market,
                    opportunity_ids=sorted(
                        opportunity.opportunity_id for opportunity in items
                    ),
                    evidence=evidence,
                    communities=sorted(
                        {ref.community_name for ref in evidence if ref.community_name}
                    ),
                    sources=sorted(
                        {ref.source_id for ref in evidence if ref.source_id}
                    ),
                )
            )
        return markets

    def _experiments(self, tables: _Tables) -> list[Experiment]:
        """The validation experiments the database holds."""
        experiments = [
            Experiment(
                experiment_id=_text(row.get("experiment_id")),
                opportunity_id=_optional_text(row.get("opportunity_id")),
                title=_text(row.get("title")),
                hypothesis=_optional_text(row.get("hypothesis")),
                status=_text(row.get("status")),
                started_at=_optional_text(row.get("started_at")),
                completed_at=_optional_text(row.get("completed_at")),
                result=_optional_text(row.get("result")),
            )
            for row in tables.experiments
        ]
        return sorted(experiments, key=lambda item: item.experiment_id)

    def _requests(self, tables: _Tables) -> list[ResearchRequest]:
        """The research requests that are still waiting."""
        requests = [
            ResearchRequest(
                request_id=_text(row.get("request_id")),
                title=_text(row.get("title")),
                body=_optional_text(row.get("body")),
                status=_text(row.get("status")),
                created_at=_text(row.get("created_at")),
            )
            for row in tables.requests
            if _text(row.get("status")).lower() not in _FINISHED_REQUEST_STATUSES
        ]
        return sorted(requests, key=lambda item: item.request_id)

    # --- the reference date -------------------------------------------------

    def _reference_date(self, tables: _Tables) -> str:
        """The newest date the data holds."""
        stamps: list[str] = []
        for document in tables.documents.values():
            stamps.append(_text(document.get("created_at")))
        for row in tables.evidence.values():
            stamps.append(_text(row.get("created_at")))
        for decision in tables.decisions:
            stamps.append(_text(decision.get("created_at")))
        stamps = [stamp for stamp in stamps if stamp]
        return max(stamps)[:10] if stamps else ""


# --- the helpers ------------------------------------------------------------


def _by_id(
    rows: list[dict[str, object]], key: str
) -> dict[str, dict[str, object]]:
    return {
        _text(row.get(key)): row for row in rows if _text(row.get(key))
    }


def _group_pain_points(
    tables: _Tables,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, set[str]]]:
    """The pain points grouped by their normalized key, and their spans."""
    spans_by_pain: dict[str, set[str]] = defaultdict(set)
    for link in tables.pain_links:
        pain_id = _text(link.get("pain_id"))
        span_id = _text(link.get("span_id"))
        if pain_id and span_id:
            spans_by_pain[pain_id].add(span_id)
    groups: dict[str, list[dict[str, object]]] = defaultdict(list)
    for pain in tables.pain_points:
        key = _text(pain.get("normalized_key")) or _text(pain.get("pain_id"))
        if not key:
            continue
        groups[key].append(pain)
    return dict(groups), dict(spans_by_pain)


def _scores_for(
    tables: _Tables, opportunity_id: str
) -> list[dict[str, object]]:
    """The score history of an opportunity, oldest first."""
    scores = [
        row
        for row in tables.score_rows
        if _text(row.get("opportunity_id")) == opportunity_id
    ]
    return sorted(
        scores,
        key=lambda row: (
            _text(row.get("created_at")),
            _text(row.get("score_id")),
        ),
    )


def _claims_for(tables: _Tables, opportunity_id: str) -> list[str]:
    """The claims of an opportunity, each with the span behind it."""
    claims: list[str] = []
    for row in tables.claim_rows:
        if _text(row.get("opportunity_id")) != opportunity_id:
            continue
        statement = _text(row.get("statement"))
        if not statement:
            continue
        span_id = _text(row.get("span_id"))
        claims.append(f"{statement} (spans: {span_id})" if span_id else statement)
    return sorted(claims)


def _summary(
    refs: list[EvidenceRef],
    confidences: dict[str, float],
    tables: _Tables,
) -> EvidenceSummary:
    """The counts of the evidence, counted and never estimated."""
    mentions = len(refs)
    canonical = len({ref.content_hash for ref in refs if ref.content_hash})
    independent = {
        ref.author_id
        for ref in refs
        if ref.author_id and confidences.get(ref.author_id, 0.0) >= _AUTHOR_CONFIDENCE
    }
    uncertain = {
        ref.author_id
        for ref in refs
        if ref.author_id and confidences.get(ref.author_id, 0.0) < _AUTHOR_CONFIDENCE
    }
    communities = {ref.community_id for ref in refs if ref.community_id}
    propagated = {
        _text(edge.get("to_doc_id")) for edge in tables.edges if edge.get("to_doc_id")
    }
    copies = sum(1 for ref in refs if ref.document_id in propagated)
    by_source: dict[str, int] = defaultdict(int)
    for ref in refs:
        by_source[ref.source_id] += 1
    busiest = max(by_source.values()) if by_source else 0
    concentration = busiest / mentions if mentions else 0.0
    return EvidenceSummary(
        mentions=mentions,
        canonical_documents=canonical,
        independent_authors=len(independent),
        uncertain_authors=len(uncertain),
        independent_communities=len(communities),
        propagated_copies=copies,
        source_concentration=concentration,
    )


def _all_evidence(opportunities: list[Opportunity]) -> list[EvidenceRef]:
    """The evidence of several opportunities, in a stable order."""
    refs: list[EvidenceRef] = []
    for opportunity in opportunities:
        refs.extend(opportunity.evidence)
    return sorted(refs, key=lambda ref: (ref.span_id, ref.document_id))


def _latest_decision(
    decisions: list[dict[str, object]], entity_ids: set[str]
) -> str | None:
    """The decision text of the latest decision about the given entities."""
    latest: tuple[tuple[str, str], str] | None = None
    for decision in decisions:
        entity_id = _text(decision.get("entity_id"))
        if entity_id not in entity_ids:
            continue
        key = (
            _text(decision.get("created_at")),
            _text(decision.get("decision_id")),
        )
        if latest is None or key > latest[0]:
            latest = (key, _text(decision.get("decision")))
    return latest[1] if latest else None


def _stance(value: object) -> str:
    """The stance a row names, as supporting or counter."""
    text = _text(value).lower()
    if "counter" in text or "against" in text or "contra" in text:
        return "counter"
    return "supporting"


def _first_value(
    rows: list[dict[str, object]], key: str, recorded: object = None
) -> object:
    """The recorded value, or the first the rows hold that is not empty.

    A blank string is empty: it says nothing.
    """
    for value in (recorded, *(row.get(key) for row in rows)):
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        return value
    return None


def _max_number(rows: list[dict[str, object]], key: str) -> float | None:
    """The highest value a column holds."""
    values = [_float(row.get(key)) for row in rows]
    numbers = [value for value in values if value is not None]
    return max(numbers) if numbers else None


def _first_number(
    recorded: object, latest: dict[str, object] | None, key: str
) -> float | None:
    """A number from the row, or from the newest score when the row has none."""
    rows = [latest] if latest is not None else []
    return _float(_first_value(rows, key, recorded))


def _earliest(refs: list[EvidenceRef]) -> str:
    stamps = [ref.captured_at for ref in refs if ref.captured_at]
    return min(stamps)[:10] if stamps else ""


def _latest(refs: list[EvidenceRef]) -> str:
    stamps = [ref.captured_at for ref in refs if ref.captured_at]
    return max(stamps)[:10] if stamps else ""


def _text(value: object) -> str:
    """A value as text, with nothing standing in for a missing one."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _optional_text(value: object) -> str | None:
    """A value as text, or None when there is nothing there."""
    text = _text(value)
    return text or None


def _frequency(value: object) -> str:
    """A frequency as text. The column default of zero carries no value."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0:
        return ""
    return _text(value)


def _float(value: object) -> float | None:
    """A value as a number, or None when it is not one."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _quote(value: object) -> str:
    """A quote as text, even when the bytes are not valid UTF-8."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None:
        return ""
    return str(value)
