"""The clustering engine: which pain points describe the same problem.

The engine is a pure function of its inputs. It reads no clock and no
database, and it writes nothing: the store owns every write, and the caller
owns the clock.

Membership is decided on meaning. A shared word never merges two pain
points, and a different wording never splits one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.clustering.adjudication import (
    Adjudication,
    AdjudicationDecision,
    Adjudicator,
)
from product_hunter.clustering.clusters import (
    Assignment,
    Cluster,
    ClusterDecision,
    ClusterProfile,
    ClusterStatus,
    DecisionKind,
    HeldPainPoint,
    HeldReason,
    NeighborCheck,
    decision_id,
    provisional_cluster_id,
)
from product_hunter.clustering.problems import (
    CanonicalProblem,
    PainPoint,
    canonicalize,
)
from product_hunter.clustering.similarity import (
    Embedder,
    MixedVectorSpaceError,
    VectorSpace,
    centroid,
    cosine_similarity,
)
from product_hunter.config import ClusteringConfig

# The bases a membership carries.
BASIS_IDENTICAL = "identical_normalized_fields"
BASIS_SEMANTIC = "semantic"
BASIS_SEMANTIC_PENALIZED = "semantic_domain_penalty"
BASIS_ADJUDICATED_SAME = "adjudicated_same"
BASIS_ADJUDICATED_DIFFERENT = "adjudicated_different"
BASIS_ADJUDICATED_AMBIGUOUS = "adjudicated_ambiguous"
BASIS_UNADJUDICATED = "unadjudicated"
BASIS_NEW_CLUSTER = "new_cluster"

# The relations a duplicate review records.
RELATION_DUPLICATE = "duplicate"
RELATION_REPOST = "repost"
RELATION_REPEAT_WORDING = "repeat_wording"
RELATION_RELATED = "related"

# The relation an adjudication between two pain points carries.
RELATION_SAME_PROBLEM = "same_problem"

# The relation a held pain point carries.
RELATION_HELD = "held"


class ClusteringResult(BaseModel):
    """What one run decided.

    The result carries the proposed clusters, the proposed memberships, the
    decisions the run made, the neighbour checks of every new cluster and
    the pain points it held. It carries no count of mentions, authors,
    communities or people: independence is another wave's job.
    """

    model_config = ConfigDict(extra="forbid")

    space: VectorSpace
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    clusters: list[Cluster] = Field(default_factory=list)
    assignments: list[Assignment] = Field(default_factory=list)
    decisions: list[ClusterDecision] = Field(default_factory=list)
    neighbor_checks: list[NeighborCheck] = Field(default_factory=list)
    held: list[HeldPainPoint] = Field(default_factory=list)


@dataclass
class _WorkingCluster:
    """A cluster while the run builds it."""

    cluster_id: str
    members: list[CanonicalProblem] = field(default_factory=list)
    vectors: list[tuple[float, ...]] = field(default_factory=list)

    def add(self, problem: CanonicalProblem, vector: tuple[float, ...]) -> None:
        """Put one pain point into the cluster."""
        self.members.append(problem)
        self.vectors.append(vector)

    @property
    def anchor(self) -> CanonicalProblem:
        """The smallest pain id among the members."""
        return min(self.members, key=lambda member: member.pain_id)

    @property
    def member_ids(self) -> list[str]:
        """The pain ids of the members, sorted."""
        return sorted(member.pain_id for member in self.members)

    def profile(self) -> ClusterProfile:
        """The controlled fields of the anchor."""
        anchor = self.anchor
        return ClusterProfile(
            domain=anchor.domain,
            actor=anchor.actor,
            action=anchor.action,
            object=anchor.object,
            consequence=anchor.consequence,
        )

    def center(self) -> tuple[float, ...]:
        """The centroid of the members."""
        return centroid(self.vectors)


@dataclass
class _Run:
    """The state of one run. The engine keeps it in one place, not in itself."""

    clusters: list[_WorkingCluster] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    decisions: list[ClusterDecision] = field(default_factory=list)
    held: list[HeldPainPoint] = field(default_factory=list)
    neighbor_checks: list[NeighborCheck] = field(default_factory=list)
    answers: dict[tuple[str, str], Adjudication] = field(default_factory=dict)
    calls: int = 0


@dataclass
class _Candidate:
    """One cluster a pain point may join, with the score it earned."""

    cluster: _WorkingCluster
    score: float
    basis: str
    member: CanonicalProblem | None


@dataclass
class _Placement:
    """Where one pain point ended up."""

    cluster: _WorkingCluster | None = None
    basis: str = ""
    confidence: float = 0.0
    new: bool = False
    held: bool = False
    reason: HeldReason | None = None
    detail: str = ""


class ClusteringEngine:
    """Decides which pain points describe the same problem.

    shortcut: the engine scores every pain point against every other one, so
    a run is quadratic in the corpus; add a nearest-neighbour index (the
    Qdrant path) when a corpus outgrows one run.
    """

    def __init__(
        self,
        embedder: Embedder,
        config: ClusteringConfig,
        adjudicator: Adjudicator | None = None,
    ) -> None:
        self.embedder = embedder
        self.config = config
        self.adjudicator = adjudicator

    def cluster(self, pain_points: Sequence[PainPoint]) -> ClusteringResult:
        """Cluster the pain points, and say what the run decided."""
        ordered = sorted(pain_points, key=lambda pain: pain.pain_id)
        problems = [canonicalize(pain) for pain in ordered]
        vectors = self._embed([problem.canonical_text for problem in problems])
        run = _Run()

        for index, problem in enumerate(problems):
            vector = vectors[index]
            placement = self._place(problem, vector, run)
            if placement.held or placement.cluster is None:
                continue
            cluster = placement.cluster
            cluster.add(problem, vector)
            run.assignments.append(
                Assignment(
                    cluster_id=cluster.cluster_id,
                    pain_id=problem.pain_id,
                    basis=placement.basis,
                    confidence=placement.confidence,
                )
            )
            if placement.new:
                run.clusters.append(cluster)
                run.neighbor_checks.extend(
                    self._neighbors(cluster, problems, vectors)
                )

        self._review_duplicates(problems, vectors, run)

        return ClusteringResult(
            space=self.embedder.space,
            algorithm_version=self.config.algorithm_version,
            config_version=self.config.config_version,
            prompt_version=self.config.prompt_version,
            clusters=[self._cluster(cluster) for cluster in run.clusters],
            assignments=run.assignments,
            decisions=run.decisions,
            neighbor_checks=run.neighbor_checks,
            held=run.held,
        )

    # --- the pipeline ----------------------------------------------------

    def _place(
        self,
        problem: CanonicalProblem,
        vector: tuple[float, ...],
        run: _Run,
    ) -> _Placement:
        """Put one pain point into a cluster, or hold it.

        The candidates are read strongest first. A score at or above the
        merge threshold takes the pain point. A score between the bands goes
        to the adjudicator, and the answer decides. A score below the
        ambiguous threshold is not a candidate for a merge.
        """
        similarity = self.config.similarity
        for candidate in self._candidates(problem, vector, run.clusters):
            score = candidate.score
            basis = candidate.basis
            best = candidate.member
            if score >= similarity.merge_threshold:
                return _Placement(
                    cluster=candidate.cluster,
                    basis=basis,
                    confidence=self._confidence(basis, score),
                )
            if score < similarity.ambiguous_threshold or best is None:
                continue
            answer, reason, detail = self._adjudicate(problem, best, run)
            if reason is not None:
                run.held.append(
                    HeldPainPoint(
                        pain_id=problem.pain_id, reason=reason, detail=detail
                    )
                )
                return _Placement(held=True, reason=reason, detail=detail)
            assert answer is not None
            if answer.decision is AdjudicationDecision.same:
                run.decisions.append(
                    self._decision(
                        DecisionKind.adjudication,
                        RELATION_SAME_PROBLEM,
                        problem.pain_id,
                        best.pain_id,
                        "same",
                        BASIS_ADJUDICATED_SAME,
                        score,
                        answer,
                        cluster_id=candidate.cluster.cluster_id,
                    )
                )
                return _Placement(
                    cluster=candidate.cluster,
                    basis=BASIS_ADJUDICATED_SAME,
                    confidence=answer.confidence,
                )
            if answer.decision is AdjudicationDecision.ambiguous:
                run.decisions.append(
                    self._decision(
                        DecisionKind.adjudication,
                        RELATION_SAME_PROBLEM,
                        problem.pain_id,
                        best.pain_id,
                        "ambiguous",
                        BASIS_ADJUDICATED_AMBIGUOUS,
                        score,
                        answer,
                        cluster_id=candidate.cluster.cluster_id,
                    )
                )
                run.held.append(
                    HeldPainPoint(
                        pain_id=problem.pain_id,
                        reason=HeldReason.ambiguous_unresolved,
                        detail="the adjudicator could not tell the two apart",
                    )
                )
                return _Placement(
                    held=True, reason=HeldReason.ambiguous_unresolved
                )
            run.decisions.append(
                self._decision(
                    DecisionKind.adjudication,
                    RELATION_SAME_PROBLEM,
                    problem.pain_id,
                    best.pain_id,
                    "different",
                    BASIS_ADJUDICATED_DIFFERENT,
                    score,
                    answer,
                    cluster_id=candidate.cluster.cluster_id,
                )
            )
        # No candidate took the pain point: it starts a cluster of its own.
        return _Placement(
            cluster=_WorkingCluster(provisional_cluster_id(problem.pain_id)),
            basis=BASIS_NEW_CLUSTER,
            confidence=1.0,
            new=True,
        )

    def _candidates(
        self,
        problem: CanonicalProblem,
        vector: tuple[float, ...],
        clusters: list[_WorkingCluster],
    ) -> list[_Candidate]:
        """The clusters a pain point may join, strongest first.

        A cluster is a candidate when it holds a pain point whose similarity
        to this one is at or above the ambiguous threshold, and when it holds
        a pain point with the same fingerprint. At most the configured number
        of candidates is kept.
        """
        similarity = self.config.similarity
        scored: list[tuple[float, str, _Candidate]] = []
        for cluster in clusters:
            best_score: float | None = None
            best_member: CanonicalProblem | None = None
            same_fingerprint = False
            for member, member_vector in zip(cluster.members, cluster.vectors):
                if member.fingerprint == problem.fingerprint:
                    same_fingerprint = True
                score = cosine_similarity(vector, member_vector)
                if best_score is None or score > best_score:
                    best_score = score
                    best_member = member
            if not same_fingerprint and (
                best_score is None or best_score < similarity.ambiguous_threshold
            ):
                continue
            score, basis, member = self._score(problem, vector, cluster)
            scored.append(
                (
                    score,
                    cluster.cluster_id,
                    _Candidate(
                        cluster=cluster, score=score, basis=basis, member=member
                    ),
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[: similarity.max_candidates]]

    def _score(
        self,
        problem: CanonicalProblem,
        vector: tuple[float, ...],
        cluster: _WorkingCluster,
    ) -> tuple[float, str, CanonicalProblem | None]:
        """The score of one candidate, its basis and the member behind it.

        A member with the same fingerprint raises the score to the merge
        threshold. A member in another domain has the domain penalty taken
        off its score, and the centroid of the cluster is scored the same
        way, so a penalty cannot be walked around through the centroid. The
        centroid carries the penalty when any member sits in another domain:
        the penalty must not depend on which member has the smallest id.
        """
        similarity = self.config.similarity
        penalty = similarity.domain_mismatch_penalty
        best: tuple[float, CanonicalProblem, bool] | None = None
        same_fingerprint = False
        for member, member_vector in zip(cluster.members, cluster.vectors):
            if member.fingerprint == problem.fingerprint:
                same_fingerprint = True
            score = cosine_similarity(vector, member_vector)
            penalized = member.domain != problem.domain
            if penalized:
                score -= penalty
            if best is None or score > best[0]:
                best = (score, member, penalized)
        assert best is not None, "a cluster always holds a member"
        if same_fingerprint:
            return similarity.merge_threshold, BASIS_IDENTICAL, best[1]
        center_score = cosine_similarity(vector, cluster.center())
        center_penalized = any(
            member.domain != problem.domain for member in cluster.members
        )
        if center_penalized:
            center_score -= penalty
        if center_score > best[0]:
            return (
                center_score,
                BASIS_SEMANTIC_PENALIZED if center_penalized else BASIS_SEMANTIC,
                best[1],
            )
        return (
            best[0],
            BASIS_SEMANTIC_PENALIZED if best[2] else BASIS_SEMANTIC,
            best[1],
        )

    def _adjudicate(
        self,
        left: CanonicalProblem,
        right: CanonicalProblem,
        run: _Run,
    ) -> tuple[Adjudication | None, HeldReason | None, str]:
        """The answer for one pair, or the reason there is none.

        A pair is asked once: the answer of the candidate pass is the answer
        of the duplicate review.
        """
        key = tuple(sorted((left.pain_id, right.pain_id)))
        if key in run.answers:
            return run.answers[key], None, ""
        if self.adjudicator is None or not self.config.adjudication.enabled:
            return None, HeldReason.ambiguous_unadjudicated, "no adjudicator was configured"
        if run.calls >= self.config.adjudication.max_calls:
            return (
                None,
                HeldReason.ambiguous_unadjudicated,
                "the adjudication call budget is exhausted",
            )
        run.calls += 1
        try:
            answer = self.adjudicator.adjudicate(left, right)
        except Exception as exc:  # a failed model call never merges a pair
            return (
                None,
                HeldReason.adjudicator_failed,
                f"the adjudicator call failed: {type(exc).__name__}",
            )
        run.answers[key] = answer
        return answer, None, ""

    def _review_duplicates(
        self,
        problems: list[CanonicalProblem],
        vectors: list[tuple[float, ...]],
        run: _Run,
    ) -> None:
        """Record the duplicate candidates the run examined.

        A pair is a duplicate candidate when the two pain points share a
        normalised key, or when their quotes are the same once the case and
        the spacing are folded away.
        """
        for index, left in enumerate(problems):
            for offset in range(index + 1, len(problems)):
                right = problems[offset]
                relation = _duplicate_relation(left, right)
                if relation is None:
                    continue
                score = cosine_similarity(vectors[index], vectors[offset])
                if relation == RELATION_DUPLICATE:
                    run.decisions.append(
                        self._decision(
                            DecisionKind.duplicate,
                            RELATION_DUPLICATE,
                            left.pain_id,
                            right.pain_id,
                            RELATION_DUPLICATE,
                            BASIS_IDENTICAL,
                            score,
                            None,
                        )
                    )
                    continue
                answer, reason, _ = self._adjudicate(left, right, run)
                if reason is not None:
                    run.decisions.append(
                        self._decision(
                            DecisionKind.duplicate,
                            relation,
                            left.pain_id,
                            right.pain_id,
                            "ambiguous",
                            BASIS_UNADJUDICATED,
                            score,
                            None,
                        )
                    )
                    continue
                assert answer is not None
                if answer.decision is AdjudicationDecision.same:
                    decision, basis = RELATION_DUPLICATE, BASIS_ADJUDICATED_SAME
                elif answer.decision is AdjudicationDecision.different:
                    decision, basis = "different", BASIS_ADJUDICATED_DIFFERENT
                else:
                    decision, basis = "ambiguous", BASIS_ADJUDICATED_AMBIGUOUS
                run.decisions.append(
                    self._decision(
                        DecisionKind.duplicate,
                        relation,
                        left.pain_id,
                        right.pain_id,
                        decision,
                        basis,
                        score,
                        answer,
                    )
                )

    def _neighbors(
        self,
        cluster: _WorkingCluster,
        problems: list[CanonicalProblem],
        vectors: list[tuple[float, ...]],
    ) -> list[NeighborCheck]:
        """The nearest pain points of the corpus, with their scores.

        A cluster is not its own neighbour, so the members are left out: the
        check is there to show what else was near the cluster. A neighbour is
        recorded whether or not it was above the threshold.
        """
        limit = self.config.similarity.min_neighbors_checked
        center = cluster.center()
        members = set(cluster.member_ids)
        scored: list[tuple[float, str]] = []
        for problem, vector in zip(problems, vectors):
            if problem.pain_id in members:
                continue
            scored.append((cosine_similarity(center, vector), problem.pain_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            NeighborCheck(
                cluster_id=cluster.cluster_id,
                pain_id=pain_id,
                score=score,
                above_threshold=score
                >= self.config.similarity.ambiguous_threshold,
            )
            for score, pain_id in scored[:limit]
        ]

    # --- the records -----------------------------------------------------

    def _embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """The vectors of the canonical texts, in one call.

        The space of the returned vectors has to be the space of the
        embedder. A vector of another length is a vector of another space,
        and the run stops rather than compare the two.
        """
        if not texts:
            return []
        space = self.embedder.space
        vectors = [tuple(vector) for vector in self.embedder.embed(texts)]
        if len(vectors) != len(texts):
            raise MixedVectorSpaceError(
                f"the embedder returned {len(vectors)} vectors for "
                f"{len(texts)} texts"
            )
        for vector in vectors:
            if len(vector) != space.dimensions:
                raise MixedVectorSpaceError(
                    f"the space {space.space_id} declares {space.dimensions} "
                    f"dimensions and the embedder returned a vector of "
                    f"{len(vector)}"
                )
        return vectors

    def _cluster(self, cluster: _WorkingCluster) -> Cluster:
        """The record of one finished cluster."""
        anchor = cluster.anchor
        return Cluster(
            cluster_id=cluster.cluster_id,
            version=1,
            status=ClusterStatus.active,
            label=anchor.pain_statement,
            profile=cluster.profile(),
            anchor_pain_id=anchor.pain_id,
            member_ids=cluster.member_ids,
        )

    def _decision(
        self,
        kind: DecisionKind,
        relation: str,
        pain_a: str,
        pain_b: str | None,
        decision: str,
        basis: str,
        score: float | None,
        answer: Adjudication | None,
        cluster_id: str | None = None,
    ) -> ClusterDecision:
        """One decision row, with the provenance of the call behind it.

        The cluster id is the one the engine knows, the provisional id of
        the cluster the decision was about. The store replaces it with the
        durable id it allocated.
        """
        first, second = sorted((pain_a, pain_b)) if pain_b else (pain_a, "")
        return ClusterDecision(
            decision_id=decision_id(
                kind.value, relation, first, second, decision, basis
            ),
            kind=kind,
            relation=relation,
            pain_a=first,
            pain_b=second or None,
            cluster_id=cluster_id,
            decision=decision,
            score=score,
            confidence=answer.confidence if answer else None,
            basis=basis,
            provider=answer.provider if answer else None,
            model=answer.model if answer else None,
            model_version=answer.model_version if answer else None,
            prompt_version=answer.prompt_version if answer else None,
        )

    def _confidence(self, basis: str, score: float) -> float:
        """The confidence a membership carries."""
        if basis == BASIS_IDENTICAL:
            return 1.0
        return max(0.0, min(1.0, score))


def _duplicate_relation(
    left: CanonicalProblem, right: CanonicalProblem
) -> str | None:
    """The relation of a duplicate candidate, or None when it is not one.

    A pair that names the same problem by construction is a duplicate
    whatever else it shares. A pair with the same quote in two documents is
    a repost, the same quote in one document is repeated wording, and a pair
    with the same normalised key is related.
    """
    if left.fingerprint == right.fingerprint:
        return RELATION_DUPLICATE
    left_quote = _quote_key(left.quote)
    if left_quote and left_quote == _quote_key(right.quote):
        if left.doc_id != right.doc_id:
            return RELATION_REPOST
        return RELATION_REPEAT_WORDING
    if left.normalized_key == right.normalized_key:
        return RELATION_RELATED
    return None


def _quote_key(quote: str) -> str:
    """The quote with the case and the spacing folded away."""
    return " ".join(quote.lower().split())
