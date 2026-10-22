"""The candidate workflow: what was noticed, what it is worth, what was decided.

This module owns the signals a discovered source carries, the score that
ranks candidates and the communities of a source, the discovery pass that
reads what the collectors already saw, and the approval that records a
person's decision.

Nothing here writes ``config/sources.yaml``. An approval returns the
registry entry as text for an operator to paste, and the file stays the
human's. The measurement is deterministic and never calls a model.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import yaml
from pydantic import ValidationError

from product_hunter.communities import CommunityConfig, CommunityRegistry
from product_hunter.discovery.queries import CandidateSignals, SourceCandidate
from product_hunter.fetch import utcnow
from product_hunter.opportunity.signals import (
    SourceReliability as SourceReliabilityTier,
)
from product_hunter.source_policy import reliability_weight
from product_hunter.sources import (
    AllowedUse,
    Collector,
    RateLimit,
    RobotsPolicy,
    SourceRegistry,
    SourcesFile,
    SourceStatus,
    SourceType,
    TermsStatus,
)

# The identifier prefix of a candidate, and of a review.
CANDIDATE_ID_PREFIX = "sc_"
REVIEW_ID_PREFIX = "rev_"

# The weights of the seven components. The six positive ones sum to one, and
# the duplication risk is subtracted from the total.
WEIGHT_RECURRING_PROBLEM_DENSITY = 0.25
WEIGHT_EVIDENCE_YIELD = 0.20
WEIGHT_INDEPENDENCE = 0.20
WEIGHT_COMMUNITY_DIVERSITY = 0.15
WEIGHT_FRESHNESS = 0.10
WEIGHT_SOURCE_RELIABILITY = 0.10
WEIGHT_DUPLICATION_RISK = 0.15

# The floors a candidate has to clear. A candidate that fails one is
# reported with the reason, whatever its score is.
MIN_RECURRING_PROBLEM_DENSITY = 0.20
MIN_EVIDENCE_YIELD = 0.10
MAX_DUPLICATION_RISK = 0.60
MIN_TOTAL = 0.40

# The window a sample has to be in for the sample to count as fresh.
FRESHNESS_WINDOW_DAYS = 30

# What the discovery pass is, when a candidate says who found it.
DISCOVERED_BY = "discovery"


@dataclass(frozen=True)
class CandidateScore:
    """The score of a candidate, its components, and the floors it failed."""

    total: float
    components: dict[str, float]
    basis: tuple[str, ...]
    eligible: bool
    reasons: tuple[str, ...]
    volume: int = 0


@dataclass(frozen=True)
class ReviewPolicy:
    """The decision a person made about a candidate.

    Every field is optional, because a decision that is missing one of them
    is refused, and the refusal names what is missing.
    """

    terms_status: TermsStatus | None = None
    allowed_use: list[AllowedUse] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    schemes: list[str] = field(default_factory=lambda: ["https"])
    collector: Collector | None = None
    rate_limit: RateLimit | None = None
    robots: RobotsPolicy | None = None
    review_basis: str = ""
    reviewed_at: date | None = None
    expires_at: date | None = None
    robots_or_api_notes: str = ""


@dataclass(frozen=True)
class ApprovalOutcome:
    """What the review of a candidate produced."""

    candidate_id: str
    source_id: str
    approved: bool
    reason_code: str
    detail: str
    missing: tuple[str, ...] = ()
    review_id: str = ""
    registry_entry: str = ""


def score_candidate(signals: CandidateSignals) -> CandidateScore:
    """The score of a candidate, and the floors it failed.

    The volume is not a component. It is the tie-break between two
    candidates with the same score, and nothing else.
    """
    components = {
        "recurring_problem_density": (
            WEIGHT_RECURRING_PROBLEM_DENSITY * signals.recurring_problem_density
        ),
        "evidence_yield": WEIGHT_EVIDENCE_YIELD * signals.evidence_yield,
        "independence": WEIGHT_INDEPENDENCE * signals.independence,
        "community_diversity": (
            WEIGHT_COMMUNITY_DIVERSITY * signals.community_diversity
        ),
        "freshness": WEIGHT_FRESHNESS * signals.freshness,
        "source_reliability": WEIGHT_SOURCE_RELIABILITY * signals.source_reliability,
        "duplication_risk": -WEIGHT_DUPLICATION_RISK * signals.duplication_risk,
    }
    total = sum(components.values())
    reasons: list[str] = []
    if signals.recurring_problem_density < MIN_RECURRING_PROBLEM_DENSITY:
        reasons.append(
            f"recurring_problem_density below {MIN_RECURRING_PROBLEM_DENSITY}"
        )
    if signals.evidence_yield < MIN_EVIDENCE_YIELD:
        reasons.append(f"evidence_yield below {MIN_EVIDENCE_YIELD}")
    if signals.duplication_risk > MAX_DUPLICATION_RISK:
        reasons.append(f"duplication_risk above {MAX_DUPLICATION_RISK}")
    if total < MIN_TOTAL:
        reasons.append(f"total below {MIN_TOTAL}")
    basis = (
        f"recurring_problem_density={signals.recurring_problem_density:.4f}",
        f"evidence_yield={signals.evidence_yield:.4f}",
        f"independence={signals.independence:.4f}",
        f"community_diversity={signals.community_diversity:.4f}",
        f"freshness={signals.freshness:.4f}",
        f"source_reliability={signals.source_reliability:.4f}",
        f"duplication_risk={signals.duplication_risk:.4f}",
        f"volume={signals.volume}",
    )
    return CandidateScore(
        total=total,
        components=components,
        basis=basis,
        eligible=not reasons,
        reasons=tuple(reasons),
        volume=signals.volume,
    )


def select_candidates(
    scored: list[CandidateScore], *, limit: int
) -> list[CandidateScore]:
    """The candidates to act on: the eligible ones, best first.

    A candidate that failed a floor is kept out, whatever its score is, so
    only the eligible ones are returned. The order is the total, then the
    volume as the tie-break. The limit is applied after the sort.
    """
    if limit <= 0:
        return []
    eligible = [score for score in scored if score.eligible]
    ordered = sorted(eligible, key=lambda score: (-score.total, -score.volume))
    return ordered[:limit]


def select_communities(
    communities: Sequence[CommunityConfig],
    scores: Mapping[str, CandidateScore],
    *,
    per_group: int = 1,
    per_source: int = 2,
) -> list[CommunityConfig]:
    """The communities to collect from, best first.

    At most ``per_group`` community comes from one independence group and at
    most ``per_source`` from one source, so a single busy forum cannot fill
    a batch. A community with no score is not ranked and is not taken, and a
    community whose score is not eligible is not taken either.
    """
    ranked = sorted(
        (
            community
            for community in communities
            if community.community_id in scores
            and scores[community.community_id].eligible
        ),
        key=lambda community: (
            -scores[community.community_id].total,
            -scores[community.community_id].volume,
            community.community_id,
        ),
    )
    groups: dict[str, int] = {}
    sources: dict[str, int] = {}
    chosen: list[CommunityConfig] = []
    for community in ranked:
        group = community.independence_group or community.community_id
        if groups.get(group, 0) >= per_group:
            continue
        if sources.get(community.source_id, 0) >= per_source:
            continue
        groups[group] = groups.get(group, 0) + 1
        sources[community.source_id] = sources.get(community.source_id, 0) + 1
        chosen.append(community)
    return chosen


def candidate_id(source_id: str, domain: str, community: str) -> str:
    """The identifier of a candidate.

    It is the source, the domain and the community, joined by pipes, so the
    same proposal is the same candidate in every run.
    """
    basis = f"{source_id}|{domain}|{community}"
    return CANDIDATE_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def discover_candidates(
    conn: sqlite3.Connection,
    registry: SourceRegistry,
    *,
    communities: CommunityRegistry | None = None,
    now: datetime | None = None,
) -> list[SourceCandidate]:
    """The hosts and communities the collectors saw that neither registry holds.

    A host the source registry already allows, or that the community
    registry already knows, is not a proposal. Everything else is one, and
    it is written to the candidate table so a person can review it.

    The signals are measured from the fetches, the documents, the evidence
    and the pain points the store already holds. No model is called.
    """
    moment = now if now is not None else utcnow()
    known_hosts = {
        domain.lower() for source in registry.list() for domain in source.domains
    }
    if communities is not None:
        known_hosts |= {
            community.host.lower() for community in communities.list() if community.host
        }
    fetched = _fetched_hosts(conn)
    reliability = reliability_weight(SourceReliabilityTier.unrated)
    candidates: list[SourceCandidate] = []
    for (host, community), documents in sorted(_samples(conn).items()):
        if not host or host in known_hosts or host not in fetched:
            continue
        signals = _signals(conn, documents, moment, reliability)
        candidate = SourceCandidate(
            candidate_id=candidate_id(host, host, community),
            source_id=host,
            domain=host,
            community=community,
            discovered_by=DISCOVERED_BY,
            discovered_at=moment,
            signals=signals,
        )
        _save_candidate(conn, candidate, moment)
        candidates.append(candidate)
    return candidates


def list_candidates(conn: sqlite3.Connection) -> list[SourceCandidate]:
    """The candidates the store holds, in identifier order."""
    rows = conn.execute(
        "SELECT * FROM source_candidates ORDER BY candidate_id"
    ).fetchall()
    return [_candidate_from_row(row) for row in rows]


def approve_candidate(
    conn: sqlite3.Connection,
    candidate: SourceCandidate,
    policy: ReviewPolicy,
    *,
    reviewer: str,
    now: datetime | None = None,
) -> ApprovalOutcome:
    """Record the decision a person made about a candidate.

    The decision needs the whole policy. A decision that is missing any of
    it is refused, and the refusal names what is missing. A decision whose
    terms status is unknown or prohibited cannot make a source collectible,
    whatever else it carries. A decision whose entry the registry would
    refuse is refused too, and nothing is written.

    A successful approval writes the review row, updates the candidate row,
    and returns the registry entry as the text the operator pastes into
    ``config/sources.yaml``. It does not write the file.
    """
    moment = now if now is not None else utcnow()
    missing = _missing_policy(policy)
    if missing:
        return ApprovalOutcome(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            approved=False,
            reason_code="incomplete_policy",
            detail=f"the decision is missing: {', '.join(missing)}",
            missing=tuple(missing),
        )
    terms = policy.terms_status
    collector = policy.collector
    rate_limit = policy.rate_limit
    robots = policy.robots
    reviewed_at = policy.reviewed_at
    if terms in (TermsStatus.unknown, TermsStatus.prohibited):
        return ApprovalOutcome(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            approved=False,
            reason_code="terms_not_permitted",
            detail=(
                f"terms_status {terms.value!r} cannot make a source collectible"
            ),
        )
    entry = _entry_dict(candidate, policy, reviewer)
    try:
        # The entry is checked the way the registry file is checked, so an
        # approval never hands the operator a file the registry would refuse.
        SourcesFile(version=1, sources=[entry])
    except ValidationError as exc:
        return ApprovalOutcome(
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            approved=False,
            reason_code="invalid_entry",
            detail=exc.errors()[0]["msg"],
        )
    review_id = _review_id(candidate.candidate_id, "approved", reviewer, reviewed_at)
    conn.execute(
        """
        INSERT INTO source_reviews (
            review_id, candidate_id, source_id, outcome, terms_status,
            allowed_use_json, domains_json, collector, rate_limit_json, robots,
            review_basis, reviewed_at, reviewer, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            candidate.candidate_id,
            candidate.source_id,
            "approved",
            terms.value,
            json.dumps([use.value for use in policy.allowed_use]),
            json.dumps(list(policy.domains)),
            collector.value,
            rate_limit.model_dump_json(),
            robots.value,
            policy.review_basis,
            reviewed_at.isoformat(),
            reviewer,
            "",
            moment.isoformat(),
        ),
    )
    _set_candidate_decision(conn, candidate, "approved", moment)
    return ApprovalOutcome(
        candidate_id=candidate.candidate_id,
        source_id=candidate.source_id,
        approved=True,
        reason_code="approved",
        detail="the decision was recorded",
        review_id=review_id,
        registry_entry=_render_entry(entry),
    )


def reject_candidate(
    conn: sqlite3.Connection,
    candidate: SourceCandidate,
    *,
    reviewer: str,
    reason: str,
    now: datetime | None = None,
) -> ApprovalOutcome:
    """Record the refusal of a candidate."""
    moment = now if now is not None else utcnow()
    review_id = _review_id(candidate.candidate_id, "rejected", reviewer, moment.date())
    conn.execute(
        """
        INSERT INTO source_reviews (
            review_id, candidate_id, source_id, outcome, terms_status,
            allowed_use_json, domains_json, collector, rate_limit_json, robots,
            review_basis, reviewed_at, reviewer, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            review_id,
            candidate.candidate_id,
            candidate.source_id,
            "rejected",
            TermsStatus.unknown.value,
            "[]",
            "[]",
            None,
            None,
            None,
            "",
            None,
            reviewer,
            reason,
            moment.isoformat(),
        ),
    )
    _set_candidate_decision(conn, candidate, "rejected", moment)
    return ApprovalOutcome(
        candidate_id=candidate.candidate_id,
        source_id=candidate.source_id,
        approved=False,
        reason_code="rejected",
        detail=reason,
        review_id=review_id,
    )


# --- the measurement ------------------------------------------------------


def _fetched_hosts(conn: sqlite3.Connection) -> set[str]:
    """The hosts the collectors went to."""
    rows = conn.execute(
        "SELECT requested_url, final_url FROM fetches"
    ).fetchall()
    hosts: set[str] = set()
    for row in rows:
        for column in ("requested_url", "final_url"):
            host = _host(row[column])
            if host:
                hosts.add(host)
    return hosts


def _samples(conn: sqlite3.Connection) -> dict[tuple[str, str], list[sqlite3.Row]]:
    """The documents of every host and community the store holds."""
    rows = conn.execute(
        "SELECT d.doc_id, d.source_id, d.url, d.text_sha256, d.published_at, "
        "       d.created_at, d.community_id, d.author, c.name AS community_name "
        "FROM documents d "
        "LEFT JOIN communities c ON c.community_id = d.community_id"
    ).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = (_host(row["url"]), str(row["community_name"] or ""))
        grouped.setdefault(key, []).append(row)
    return grouped


def _signals(
    conn: sqlite3.Connection,
    documents: list[sqlite3.Row],
    moment: datetime,
    reliability: float,
) -> CandidateSignals:
    """The measured signals of one sample.

    A proposal has no terms status, so it derives no tier of its own: it
    carries the weight the policy gives an unrated source.
    """
    doc_ids = [str(row["doc_id"]) for row in documents]
    marks = ",".join("?" * len(doc_ids))
    pains = conn.execute(
        "SELECT doc_id, COUNT(DISTINCT pain_id) AS pains "
        f"FROM pain_points WHERE doc_id IN ({marks}) GROUP BY doc_id",
        doc_ids,
    ).fetchall()
    spans = conn.execute(
        f"SELECT doc_id, COUNT(*) AS spans FROM evidence "
        f"WHERE doc_id IN ({marks}) GROUP BY doc_id",
        doc_ids,
    ).fetchall()
    actors = conn.execute(
        "SELECT DISTINCT doc_id, actor FROM pain_points "
        f"WHERE doc_id IN ({marks}) AND actor IS NOT NULL AND actor != ''",
        doc_ids,
    ).fetchall()
    total = len(documents)
    pain_count = sum(int(row["pains"]) for row in pains)
    span_count = sum(int(row["spans"]) for row in spans)
    roles = {(str(row["actor"] or "").strip()) for row in actors}
    return CandidateSignals(
        recurring_problem_density=min(1.0, pain_count / total),
        evidence_yield=min(1.0, span_count / total),
        independence=_independence(conn, documents),
        community_diversity=min(1.0, len(roles) / total),
        freshness=_freshness(documents, moment),
        source_reliability=reliability,
        duplication_risk=_duplication_risk(conn, documents),
        volume=total,
    )


def _independence(
    conn: sqlite3.Connection, documents: list[sqlite3.Row]
) -> float:
    """The share of the sample's authors that no other source has seen."""
    handles = {
        str(row["author"] or "").strip() for row in documents
    } - {""}
    if not handles:
        return 0.0
    marks = ",".join("?" * len(handles))
    rows = conn.execute(
        "SELECT handle, COUNT(DISTINCT source_id) AS places FROM authors "
        f"WHERE handle IN ({marks}) GROUP BY handle",
        sorted(handles),
    ).fetchall()
    places = {str(row["handle"]): int(row["places"]) for row in rows}
    alone = sum(1 for handle in handles if places.get(handle, 0) <= 1)
    return alone / len(handles)


def _freshness(documents: list[sqlite3.Row], moment: datetime) -> float:
    """The share of the sample that was published inside the fresh window."""
    cutoff = moment - timedelta(days=FRESHNESS_WINDOW_DAYS)
    dated = 0
    fresh = 0
    for row in documents:
        stamp = _timestamp(row["published_at"]) or _timestamp(row["created_at"])
        if stamp is None:
            continue
        dated += 1
        if stamp >= cutoff:
            fresh += 1
    return (fresh / dated) if dated else 0.0


def _duplication_risk(
    conn: sqlite3.Connection, documents: list[sqlite3.Row]
) -> float:
    """The share of the sample whose content also appears somewhere else."""
    digests = [
        str(row["text_sha256"]) for row in documents if row["text_sha256"]
    ]
    if not digests:
        return 0.0
    marks = ",".join("?" * len(set(digests)))
    rows = conn.execute(
        "SELECT text_sha256, COUNT(DISTINCT community_id || '|' || source_id) "
        "AS places FROM documents "
        f"WHERE text_sha256 IN ({marks}) GROUP BY text_sha256",
        sorted(set(digests)),
    ).fetchall()
    places = {str(row["text_sha256"]): int(row["places"]) for row in rows}
    shared = sum(1 for digest in digests if places.get(digest, 1) > 1)
    return shared / len(documents)


def _host(url: object) -> str:
    """The host of a URL, lowercased, or an empty string."""
    text = str(url or "")
    if "://" not in text:
        text = f"https://{text}"
    host = urlparse(text).hostname or ""
    return host.lower()


def _timestamp(value: object) -> datetime | None:
    """The timestamp of a stored date, or None when there is none."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return stamp.replace(tzinfo=timezone.utc)
    return stamp


# --- the writes -----------------------------------------------------------


def _save_candidate(
    conn: sqlite3.Connection, candidate: SourceCandidate, moment: datetime
) -> None:
    """Write the proposal, or refresh the signals of the one already there."""
    conn.execute(
        """
        INSERT INTO source_candidates (
            candidate_id, source_id, domain, community, source_type,
            discovered_by, discovered_at, status, terms_status, signals_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            signals_json = excluded.signals_json,
            updated_at = excluded.updated_at
        """,
        (
            candidate.candidate_id,
            candidate.source_id,
            candidate.domain,
            candidate.community,
            candidate.source_type,
            candidate.discovered_by,
            candidate.discovered_at.isoformat(),
            candidate.decision,
            candidate.terms_status,
            json.dumps(asdict(candidate.signals), sort_keys=True),
            moment.isoformat(),
            moment.isoformat(),
        ),
    )


def _set_candidate_decision(
    conn: sqlite3.Connection,
    candidate: SourceCandidate,
    decision: str,
    moment: datetime,
) -> None:
    """Record the reviewer's decision on the candidate row."""
    conn.execute(
        """
        INSERT INTO source_candidates (
            candidate_id, source_id, domain, community, source_type,
            discovered_by, discovered_at, status, terms_status, signals_json,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(candidate_id) DO UPDATE SET
            status = excluded.status,
            updated_at = excluded.updated_at
        """,
        (
            candidate.candidate_id,
            candidate.source_id,
            candidate.domain,
            candidate.community,
            candidate.source_type,
            candidate.discovered_by,
            candidate.discovered_at.isoformat(),
            decision,
            candidate.terms_status,
            json.dumps(asdict(candidate.signals), sort_keys=True),
            moment.isoformat(),
            moment.isoformat(),
        ),
    )


def _candidate_from_row(row: sqlite3.Row) -> SourceCandidate:
    """The candidate a row holds."""
    signals = json.loads(row["signals_json"] or "{}")
    return SourceCandidate(
        candidate_id=str(row["candidate_id"]),
        source_id=str(row["source_id"]),
        domain=str(row["domain"] or ""),
        community=str(row["community"] or ""),
        source_type=str(row["source_type"] or ""),
        discovered_by=str(row["discovered_by"]),
        discovered_at=datetime.fromisoformat(str(row["discovered_at"])),
        decision=str(row["status"] or "proposed"),
        signals=CandidateSignals(**signals) if signals else CandidateSignals(),
    )


def _review_id(
    candidate_id: str, outcome: str, reviewer: str, moment: date
) -> str:
    """The identifier of a review: the candidate, the decision and the day."""
    basis = f"{candidate_id}|{outcome}|{reviewer}|{moment.isoformat()}"
    return REVIEW_ID_PREFIX + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _missing_policy(policy: ReviewPolicy) -> list[str]:
    """The policy fields the decision does not carry."""
    missing: list[str] = []
    if policy.terms_status is None:
        missing.append("terms_status")
    if not policy.allowed_use:
        missing.append("allowed_use")
    if not policy.domains:
        missing.append("domains")
    if policy.collector is None:
        missing.append("collector")
    if policy.rate_limit is None:
        missing.append("rate_limit")
    if policy.robots is None:
        missing.append("robots")
    if not policy.review_basis:
        missing.append("review_basis")
    if policy.reviewed_at is None:
        missing.append("reviewed_at")
    return missing


def _entry_dict(
    candidate: SourceCandidate, policy: ReviewPolicy, reviewer: str
) -> dict[str, object]:
    """The registry entry the decision produces.

    The entry is built once and used twice: once to check that the registry
    would accept it, and once to render it for the operator.
    """
    return {
        "source_id": candidate.source_id,
        "source_type": candidate.source_type or SourceType.other.value,
        "status": SourceStatus.active.value,
        "terms_status": policy.terms_status.value,
        "allowed_use": [use.value for use in policy.allowed_use],
        "reviewed_at": policy.reviewed_at.isoformat(),
        "review_expires_at": (
            policy.expires_at.isoformat() if policy.expires_at else None
        ),
        "last_reviewed_by": reviewer,
        "review_basis": policy.review_basis,
        "robots_or_api_notes": policy.robots_or_api_notes,
        "collector": policy.collector.value,
        "rate_limit_policy": policy.rate_limit.model_dump(),
        "domains": list(policy.domains),
        "schemes": list(policy.schemes),
        "robots": policy.robots.value,
    }


def _render_entry(entry: dict[str, object]) -> str:
    """The entry as the text the operator pastes into ``config/sources.yaml``.

    The text is the entry and nothing else: the file is the human's, and
    nothing here writes it.
    """
    # The entry is indented the way the registry file indents its sources, so
    # it can be pasted into the list without being reflowed by hand.
    return textwrap.indent(
        yaml.safe_dump([entry], sort_keys=False, default_flow_style=False), "  "
    )
