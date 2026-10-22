"""The clustering store: the runs, the clusters and the history of both.

The store owns every write. It never deletes a cluster row, a membership
row, a version row or a lineage edge: a membership that ends is closed with
a valid_to, and a successor carries a lineage edge to its predecessor.

The engine proposes; the store decides. A proposal is matched to the stored
active cluster with the largest member overlap, so a cluster keeps its id
across a re-clustering even when its anchor changes.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from product_hunter.clustering.clusters import (
    Assignment,
    Cluster,
    ClusterProfile,
    ClusterStatus,
    ClusterVersion,
    LineageEdge,
    Membership,
    hash_id,
    member_id,
    merged_cluster_id,
    split_cluster_id,
)
from product_hunter.clustering.engine import ClusteringResult
from product_hunter.clustering.problems import PainPoint
from product_hunter.clustering.similarity import (
    MixedVectorSpaceError,
    require_same_space,
)
from product_hunter.fetch import utcnow
from product_hunter.normalization import Domain

# The relations a lineage edge carries.
RELATION_MERGED_INTO = "merged_into"
RELATION_SPLIT_FROM = "split_from"

# The fields of a run row that a second write of the same run must match.
_RUN_FIELDS = (
    "algorithm_version",
    "config_version",
    "prompt_version",
    "provider",
    "model",
    "model_version",
    "embedding_provider",
    "embedding_model",
    "embedding_model_version",
    "space_id",
)


class ClusterStoreError(Exception):
    """The store could not accept the input."""

    reason_code = "cluster_store_error"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(detail or self.reason_code)


class ClusterStoreConflict(ClusterStoreError):
    """An existing row disagrees with the incoming content."""

    reason_code = "cluster_store_conflict"


class ClusteringRun(BaseModel):
    """One clustering run: the versions, the models and the space it ran in."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    space_id: str = Field(min_length=1)
    provider: str = ""
    model: str = ""
    model_version: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    embedding_model_version: str = ""
    params: dict[str, object] = Field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class ClusteringRunRecord:
    """One run row, as it was read back."""

    run_id: str
    algorithm_version: str
    config_version: str
    prompt_version: str
    provider: str
    model: str
    model_version: str
    embedding_provider: str
    embedding_model: str
    embedding_model_version: str
    space_id: str
    params: dict[str, object]
    created_at: str


@dataclass(frozen=True)
class ApplySummary:
    """What one apply wrote."""

    clusters_written: int = 0
    clusters_unchanged: int = 0
    memberships_opened: int = 0
    memberships_closed: int = 0
    versions_written: int = 0
    decisions_written: int = 0
    held_written: int = 0
    cluster_ids: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidenceLineageRow:
    """One evidence span behind a cluster, and the membership that carried it."""

    pain_id: str
    span_id: str
    doc_id: str
    quote: str
    valid_from: str
    valid_to: str | None


@contextmanager
def _owned_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """The transaction a write owns, or the caller's own.

    A connection that is already in a transaction keeps it: the write joins
    the caller's transaction and leaves the commit to the caller. A
    connection that is free gets one, and the write commits it, or rolls it
    back when the write fails.
    """
    owns = not conn.in_transaction
    try:
        if owns:
            conn.execute("BEGIN IMMEDIATE")
        yield
        if owns:
            conn.execute("COMMIT")
    except BaseException:
        if owns:
            _rollback(conn)
        raise


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:
        pass


class ClusterStore:
    """The SQLite state of the clusters: the rows and the history of them."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # --- the run ---------------------------------------------------------

    def record_run(self, run: ClusteringRun) -> None:
        """Write the run row.

        A run that is written twice keeps its first row. A run row that
        disagrees with the incoming run is a conflict, not an overwrite.
        The parameters are written as they are: a value that cannot be
        serialised fails the write instead of being turned into a string.
        """
        with _owned_transaction(self.conn):
            self.conn.execute(
                """
                INSERT INTO clustering_runs (
                    run_id, algorithm_version, config_version, prompt_version,
                    provider, model, model_version, embedding_provider,
                    embedding_model, embedding_model_version, space_id,
                    params_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (
                    run.run_id,
                    run.algorithm_version,
                    run.config_version,
                    run.prompt_version,
                    run.provider,
                    run.model,
                    run.model_version,
                    run.embedding_provider,
                    run.embedding_model,
                    run.embedding_model_version,
                    run.space_id,
                    _params_json(run),
                    run.created_at or utcnow().isoformat(),
                ),
            )
            row = self.conn.execute(
                "SELECT * FROM clustering_runs WHERE run_id = ?", (run.run_id,)
            ).fetchone()
            if row is None:
                raise ClusterStoreConflict(
                    f"the run {run.run_id!r} could not be written"
                )
            for field_name in _RUN_FIELDS:
                if row[field_name] != getattr(run, field_name):
                    raise ClusterStoreConflict(
                        f"the run {run.run_id!r} already exists with a "
                        f"different {field_name}"
                    )

    def run(self, run_id: str) -> ClusteringRunRecord | None:
        """One run row, as it was written."""
        row = self.conn.execute(
            "SELECT * FROM clustering_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return ClusteringRunRecord(
            run_id=row["run_id"],
            algorithm_version=row["algorithm_version"],
            config_version=row["config_version"],
            prompt_version=row["prompt_version"],
            provider=row["provider"] or "",
            model=row["model"] or "",
            model_version=row["model_version"] or "",
            embedding_provider=row["embedding_provider"] or "",
            embedding_model=row["embedding_model"] or "",
            embedding_model_version=row["embedding_model_version"] or "",
            space_id=row["space_id"],
            params=json.loads(row["params_json"] or "{}"),
            created_at=row["created_at"],
        )

    # --- applying a result -----------------------------------------------

    def apply_result(
        self, result: ClusteringResult, run: ClusteringRun
    ) -> ApplySummary:
        """Write the clusters, the memberships, the versions and the decisions.

        The write is idempotent: applying the same result twice changes
        nothing. A cluster whose membership did not change gets no new
        version, no new membership row and no new snapshot. A result that
        was built in another vector space is refused.
        """
        self._refuse_another_space(result, run)
        assignments = self._assignments_by_pain(result)
        with _owned_transaction(self.conn):
            self.record_run(run)
            mapping = self._match_clusters(result)
            wanted_by_cluster = self._wanted_by_cluster(assignments, mapping)
            now = utcnow().isoformat()

            open_before = self._open_members()
            moved = self._close_moved(assignments, mapping, now)
            held_closed, affected = self._close_held(result, now)

            written = 0
            unchanged = 0
            opened = 0
            closed = 0
            versions = 0
            for cluster in result.clusters:
                cluster_id = mapping[cluster.cluster_id]
                wanted = set(wanted_by_cluster.get(cluster_id, {}))
                stored = open_before.get(cluster_id, set())
                if stored == wanted:
                    unchanged += 1
                    continue
                row = self._cluster_row(cluster_id)
                if row is None:
                    self._insert_cluster(cluster, cluster_id, run, now)
                else:
                    version = int(row["version"]) + 1
                    self._update_cluster(cluster, cluster_id, version, run, now)
                versions += 1
                opened += self._open_members_for(
                    cluster_id,
                    wanted_by_cluster.get(cluster_id, {}),
                    run,
                    now,
                )
                closed += self._close_members_of(cluster_id, wanted, now)
                written += 1

            # A cluster that is not in the result may have lost a member to
            # a held pain point. Its version moves too, so the version the
            # current row carries names a snapshot that agrees with it. A
            # cluster that lost its last member holds nothing, and a
            # cluster that holds nothing is dissolved.
            for cluster_id in sorted(affected - set(wanted_by_cluster)):
                version = self._next_version(cluster_id)
                members = self._remaining_members(cluster_id)
                status = (
                    ClusterStatus.dissolved
                    if not members
                    else ClusterStatus(self._cluster_row(cluster_id)["status"])
                )
                self.conn.execute(
                    "UPDATE clusters SET version = ?, member_count = ?, "
                    "status = ?, updated_at = ? WHERE cluster_id = ?",
                    (version, len(members), status.value, now, cluster_id),
                )
                label, profile = self._meaning_of(cluster_id)
                self._write_version(
                    cluster_id,
                    version,
                    status,
                    label,
                    profile,
                    members,
                    run,
                    now,
                )
                versions += 1

            decisions = self._write_decisions(result, run, now, mapping)
            held = self._write_held(result, run, now)
            return ApplySummary(
                clusters_written=written,
                clusters_unchanged=unchanged,
                memberships_opened=opened,
                memberships_closed=closed + moved + held_closed,
                versions_written=versions,
                decisions_written=decisions,
                held_written=held,
                cluster_ids=dict(mapping),
            )

    def _wanted_by_cluster(
        self,
        assignments: Mapping[str, tuple[str, Assignment]],
        mapping: Mapping[str, str],
    ) -> dict[str, dict[str, Assignment]]:
        """The members every durable cluster ends up with."""
        wanted: dict[str, dict[str, Assignment]] = {}
        for pain_id, (provisional, assignment) in assignments.items():
            cluster_id = mapping[provisional]
            wanted.setdefault(cluster_id, {})[pain_id] = assignment
        return wanted

    def _refuse_another_space(
        self, result: ClusteringResult, run: ClusteringRun
    ) -> None:
        """Refuse a result that was built in another vector space."""
        if run.space_id and run.space_id != result.space.space_id:
            raise MixedVectorSpaceError(
                f"the run {run.run_id!r} names the space {run.space_id} and the "
                f"result lives in the space {result.space.space_id}"
            )
        stored = self.embedding_spaces()
        if stored and result.space.space_id not in stored:
            raise MixedVectorSpaceError(
                f"the stored clusters live in the space {stored[0]} and the "
                f"result lives in the space {result.space.space_id}"
            )

    def _assignments_by_pain(
        self, result: ClusteringResult
    ) -> dict[str, tuple[str, Assignment]]:
        """The membership every pain point ends up with.

        A pain point in two clusters is a malformed result, and the store
        refuses it: a pain point is in one cluster at a time. A cluster that
        names a member it does not assign is refused too: the member count
        and the snapshot of such a cluster would disagree with the
        memberships the store wrote. A pain point that is both assigned and
        held is refused: a held pain point belongs to no cluster, and the
        member count would name a member the store does not have.
        """
        known = {cluster.cluster_id for cluster in result.clusters}
        assignments: dict[str, tuple[str, Assignment]] = {}
        for assignment in result.assignments:
            if assignment.cluster_id not in known:
                raise ClusterStoreError(
                    f"the assignment names the cluster {assignment.cluster_id!r}, "
                    "which the result does not hold"
                )
            if assignment.pain_id in assignments:
                raise ClusterStoreError(
                    f"the pain point {assignment.pain_id!r} is assigned to two "
                    "clusters"
                )
            assignments[assignment.pain_id] = (assignment.cluster_id, assignment)
        for cluster in result.clusters:
            assigned = {
                pain_id
                for pain_id, (owner, _) in assignments.items()
                if owner == cluster.cluster_id
            }
            if assigned != set(cluster.member_ids):
                raise ClusterStoreError(
                    f"the cluster {cluster.cluster_id!r} names "
                    f"{len(cluster.member_ids)} members and "
                    f"{len(assigned)} assignments"
                )
        held = {held.pain_id for held in result.held}
        for pain_id in assignments:
            if pain_id in held:
                raise ClusterStoreError(
                    f"the pain point {pain_id!r} is both assigned to a cluster "
                    "and held"
                )
        return assignments

    def _match_clusters(self, result: ClusteringResult) -> dict[str, str]:
        """The durable id of every proposed cluster.

        A proposal takes over the stored active cluster it overlaps most.
        An unmatched proposal becomes a new cluster under its own id.
        """
        stored = {
            cluster_id: set(members)
            for cluster_id, members in self._open_members().items()
            if cluster_id in self._active_cluster_ids()
        }
        proposals = [
            (cluster.cluster_id, set(cluster.member_ids))
            for cluster in result.clusters
        ]
        pairs: list[tuple[int, str, str]] = []
        for provisional, members in proposals:
            for cluster_id, stored_members in stored.items():
                overlap = len(members & stored_members)
                if overlap:
                    pairs.append((-overlap, provisional, cluster_id))
        pairs.sort()
        mapping: dict[str, str] = {}
        used: set[str] = set()
        for _, provisional, cluster_id in pairs:
            if provisional in mapping or cluster_id in used:
                continue
            mapping[provisional] = cluster_id
            used.add(cluster_id)
        for provisional, _ in proposals:
            mapping.setdefault(provisional, provisional)
        return mapping

    def _close_moved(
        self,
        assignments: Mapping[str, tuple[str, Assignment]],
        mapping: Mapping[str, str],
        now: str,
    ) -> int:
        """Close the membership of a pain point that moved to another cluster."""
        closed = 0
        for pain_id, (provisional, _) in assignments.items():
            wanted = mapping[provisional]
            rows = self.conn.execute(
                "SELECT member_id, cluster_id FROM cluster_members "
                "WHERE pain_id = ? AND valid_to IS NULL",
                (pain_id,),
            ).fetchall()
            for row in rows:
                if row["cluster_id"] == wanted:
                    continue
                self.conn.execute(
                    "UPDATE cluster_members SET valid_to = ? WHERE member_id = ?",
                    (now, row["member_id"]),
                )
                closed += 1
        return closed

    def _close_held(self, result: ClusteringResult, now: str) -> tuple[int, set[str]]:
        """Close the membership of the pain points the run held.

        A held pain point belongs to no cluster, so the store closes the
        membership it had. The clusters that lose a member this way get a
        new version and a snapshot that records the change.
        """
        closed = 0
        affected: set[str] = set()
        for held in result.held:
            rows = self.conn.execute(
                "SELECT member_id, cluster_id FROM cluster_members "
                "WHERE pain_id = ? AND valid_to IS NULL",
                (held.pain_id,),
            ).fetchall()
            for row in rows:
                self.conn.execute(
                    "UPDATE cluster_members SET valid_to = ? WHERE member_id = ?",
                    (now, row["member_id"]),
                )
                closed += 1
                affected.add(row["cluster_id"])
        return closed, affected

    def _open_members_for(
        self,
        cluster_id: str,
        wanted: Mapping[str, Assignment],
        run: ClusteringRun,
        now: str,
    ) -> int:
        """Open the membership of every pain point that joined the cluster."""
        opened = 0
        for pain_id in sorted(wanted):
            row = self.conn.execute(
                "SELECT member_id FROM cluster_members "
                "WHERE cluster_id = ? AND pain_id = ? AND valid_to IS NULL",
                (cluster_id, pain_id),
            ).fetchone()
            if row is not None:
                continue
            assignment = wanted[pain_id]
            self.conn.execute(
                """
                INSERT INTO cluster_members (
                    member_id, cluster_id, pain_id, valid_from, valid_to,
                    assignment_confidence, basis, run_id
                ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    member_id(cluster_id, pain_id, now),
                    cluster_id,
                    pain_id,
                    now,
                    assignment.confidence,
                    assignment.basis,
                    run.run_id,
                ),
            )
            opened += 1
        return opened

    def _close_members_of(
        self, cluster_id: str, wanted: set[str], now: str
    ) -> int:
        """Close the membership of every pain point that left the cluster."""
        rows = self.conn.execute(
            "SELECT member_id, pain_id FROM cluster_members "
            "WHERE cluster_id = ? AND valid_to IS NULL",
            (cluster_id,),
        ).fetchall()
        closed = 0
        for row in rows:
            if row["pain_id"] in wanted:
                continue
            self.conn.execute(
                "UPDATE cluster_members SET valid_to = ? WHERE member_id = ?",
                (now, row["member_id"]),
            )
            closed += 1
        return closed

    def _insert_cluster(
        self, cluster: Cluster, cluster_id: str, run: ClusteringRun, now: str
    ) -> None:
        """Write a new cluster and its first snapshot."""
        profile = cluster.profile
        self.conn.execute(
            """
            INSERT INTO clusters (
                cluster_id, version, status, label, domain, actor, action,
                object, consequence, anchor_pain_id, member_count, merged_into,
                split_from, space_id, config_version, run_id, created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                cluster_id,
                1,
                cluster.status.value,
                cluster.label,
                profile.domain.value,
                profile.actor,
                profile.action,
                profile.object,
                profile.consequence,
                cluster.anchor_pain_id,
                len(cluster.member_ids),
                run.space_id,
                run.config_version,
                run.run_id,
                now,
                now,
            ),
        )
        self._write_version(
            cluster_id,
            1,
            cluster.status,
            cluster.label,
            cluster.profile,
            cluster.member_ids,
            run,
            now,
        )

    def _update_cluster(
        self,
        cluster: Cluster,
        cluster_id: str,
        version: int,
        run: ClusteringRun,
        now: str,
    ) -> None:
        """Write the current state of a cluster whose membership changed."""
        profile = cluster.profile
        self.conn.execute(
            """
            UPDATE clusters SET
                version = ?, status = ?, label = ?, domain = ?, actor = ?,
                action = ?, object = ?, consequence = ?, anchor_pain_id = ?,
                member_count = ?, space_id = ?, config_version = ?,
                run_id = ?, updated_at = ?
            WHERE cluster_id = ?
            """,
            (
                version,
                cluster.status.value,
                cluster.label,
                profile.domain.value,
                profile.actor,
                profile.action,
                profile.object,
                profile.consequence,
                cluster.anchor_pain_id,
                len(cluster.member_ids),
                run.space_id,
                run.config_version,
                run.run_id,
                now,
                cluster_id,
            ),
        )
        self._write_version(
            cluster_id,
            version,
            cluster.status,
            cluster.label,
            cluster.profile,
            cluster.member_ids,
            run,
            now,
        )

    def _write_version(
        self,
        cluster_id: str,
        version: int,
        status: ClusterStatus,
        label: str,
        profile: ClusterProfile,
        member_ids: Sequence[str],
        run: ClusteringRun,
        now: str,
    ) -> None:
        """Write one snapshot of a cluster. A snapshot is never updated.

        The status is part of the snapshot: a version names what the cluster
        meant at that version, and a merged or a split cluster means
        something different from the cluster it was.
        """
        self.conn.execute(
            """
            INSERT INTO cluster_versions (
                cluster_id, version, status, label, domain, actor, action,
                object, consequence, member_ids_json, member_count, run_id,
                config_version, space_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_id, version) DO NOTHING
            """,
            (
                cluster_id,
                version,
                status.value,
                label,
                profile.domain.value,
                profile.actor,
                profile.action,
                profile.object,
                profile.consequence,
                json.dumps(list(member_ids)),
                len(member_ids),
                run.run_id,
                run.config_version,
                run.space_id,
                now,
            ),
        )

    def _write_decisions(
        self,
        result: ClusteringResult,
        run: ClusteringRun,
        now: str,
        mapping: Mapping[str, str],
    ) -> int:
        """Write the decisions of the run. A decision is written once.

        The row id is scoped to the run: two runs that reach the same
        decision about the same pair write two rows, so the second run's
        model call leaves a record of its own. The cluster id of the row is
        the durable one the store allocated.
        """
        written = 0
        for decision in result.decisions:
            cluster_id = decision.cluster_id
            if cluster_id is not None:
                cluster_id = mapping.get(cluster_id, cluster_id)
            cursor = self.conn.execute(
                """
                INSERT INTO cluster_decisions (
                    decision_id, run_id, kind, relation, pain_a, pain_b,
                    cluster_id, decision, score, confidence, basis, detail,
                    provider, model, model_version, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO NOTHING
                """,
                (
                    _scoped_decision_id(run.run_id, decision.decision_id),
                    run.run_id,
                    decision.kind.value,
                    decision.relation,
                    decision.pain_a,
                    decision.pain_b,
                    cluster_id,
                    decision.decision,
                    decision.score,
                    decision.confidence,
                    decision.basis,
                    None,
                    decision.provider,
                    decision.model,
                    decision.model_version,
                    decision.prompt_version,
                    now,
                ),
            )
            written += max(0, cursor.rowcount)
        return written

    def _write_held(
        self, result: ClusteringResult, run: ClusteringRun, now: str
    ) -> int:
        """Write the pain points the run held. A held pain point is not lost.

        A held pain point has no answer, so its row carries no confidence.
        The detail is a fixed phrase or the name of an exception type: the
        text of a model answer never reaches a row.
        """
        written = 0
        for held in result.held:
            cursor = self.conn.execute(
                """
                INSERT INTO cluster_decisions (
                    decision_id, run_id, kind, relation, pain_a, pain_b,
                    cluster_id, decision, score, confidence, basis, detail,
                    provider, model, model_version, prompt_version, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, ?, ?, NULL,
                          NULL, NULL, ?, ?)
                ON CONFLICT(decision_id) DO NOTHING
                """,
                (
                    _scoped_decision_id(
                        run.run_id, _held_decision_id(held.pain_id, held.reason.value)
                    ),
                    run.run_id,
                    "ambiguous",
                    "held",
                    held.pain_id,
                    "held",
                    held.reason.value,
                    held.detail,
                    run.prompt_version,
                    now,
                ),
            )
            written += max(0, cursor.rowcount)
        return written

    # --- reading the state ------------------------------------------------

    def current_memberships(self) -> list[Membership]:
        """The open memberships: the pain point in a cluster, right now."""
        rows = self.conn.execute(
            "SELECT * FROM cluster_members WHERE valid_to IS NULL "
            "ORDER BY cluster_id, pain_id"
        ).fetchall()
        return [
            Membership(
                member_id=row["member_id"],
                cluster_id=row["cluster_id"],
                pain_id=row["pain_id"],
                valid_from=row["valid_from"],
                valid_to=row["valid_to"],
                assignment_confidence=float(row["assignment_confidence"]),
                basis=row["basis"],
                run_id=row["run_id"],
            )
            for row in rows
        ]

    def cluster(self, cluster_id: str) -> Cluster | None:
        """One cluster, as it is now."""
        row = self._cluster_row(cluster_id)
        if row is None:
            return None
        members = self.conn.execute(
            "SELECT pain_id FROM cluster_members "
            "WHERE cluster_id = ? AND valid_to IS NULL ORDER BY pain_id",
            (cluster_id,),
        ).fetchall()
        if not row["domain"]:
            raise ClusterStoreError(
                f"the cluster {cluster_id!r} has no domain and cannot be read"
            )
        return Cluster(
            cluster_id=row["cluster_id"],
            version=int(row["version"]),
            status=ClusterStatus(row["status"]),
            label=row["label"] or "",
            profile=ClusterProfile(
                domain=Domain(row["domain"]),
                actor=row["actor"] or "",
                action=row["action"] or "",
                object=row["object"] or "",
                consequence=row["consequence"] or "",
            ),
            anchor_pain_id=row["anchor_pain_id"] or "",
            member_ids=[member["pain_id"] for member in members],
        )

    def cluster_versions(self, cluster_id: str) -> list[ClusterVersion]:
        """The snapshots of one cluster, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM cluster_versions WHERE cluster_id = ? ORDER BY version",
            (cluster_id,),
        ).fetchall()
        return [
            ClusterVersion(
                cluster_id=row["cluster_id"],
                version=int(row["version"]),
                status=ClusterStatus(row["status"]),
                label=row["label"] or "",
                profile=(
                    ClusterProfile(
                        domain=Domain(row["domain"]),
                        actor=row["actor"] or "",
                        action=row["action"] or "",
                        object=row["object"] or "",
                        consequence=row["consequence"] or "",
                    )
                    if row["domain"]
                    else None
                ),
                member_ids=json.loads(row["member_ids_json"] or "[]"),
                member_count=int(row["member_count"]),
                run_id=row["run_id"],
                config_version=row["config_version"],
                space_id=row["space_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def lineage(self, cluster_id: str) -> list[LineageEdge]:
        """The edges of the history of one cluster, in both directions."""
        rows = self.conn.execute(
            "SELECT * FROM cluster_lineage "
            "WHERE from_cluster_id = ? OR to_cluster_id = ? "
            "ORDER BY created_at, edge_id",
            (cluster_id, cluster_id),
        ).fetchall()
        return [
            LineageEdge(
                edge_id=row["edge_id"],
                from_cluster_id=row["from_cluster_id"],
                to_cluster_id=row["to_cluster_id"],
                relation=row["relation"],
                reason=row["reason"],
                run_id=row["run_id"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def evidence_lineage(self, cluster_id: str) -> list[EvidenceLineageRow]:
        """The evidence spans behind a cluster, and the memberships that carried them."""
        rows = self.conn.execute(
            """
            SELECT m.pain_id, e.span_id, e.doc_id, e.quote,
                   m.valid_from, m.valid_to
            FROM cluster_members m
            JOIN pain_points p ON p.pain_id = m.pain_id
            JOIN evidence_pain_links l ON l.pain_id = p.pain_id
            JOIN evidence e ON e.span_id = l.span_id
            WHERE m.cluster_id = ?
            ORDER BY m.pain_id, e.span_id
            """,
            (cluster_id,),
        ).fetchall()
        return [
            EvidenceLineageRow(
                pain_id=row["pain_id"],
                span_id=row["span_id"],
                doc_id=row["doc_id"],
                quote=row["quote"],
                valid_from=row["valid_from"],
                valid_to=row["valid_to"],
            )
            for row in rows
        ]

    def embedding_spaces(self) -> list[str]:
        """The distinct spaces the stored clusters were built in."""
        rows = self.conn.execute(
            "SELECT DISTINCT space_id FROM clusters WHERE space_id IS NOT NULL "
            "ORDER BY space_id"
        ).fetchall()
        return [row["space_id"] for row in rows]

    def pain_points(self) -> list[PainPoint]:
        """The accepted pain points of the store, ready for the engine.

        A pain point that was not accepted, or that never went through
        normalisation, is not an input: it has no versioned identity to
        compare.
        """
        rows = self.conn.execute(
            """
            SELECT p.pain_id, p.doc_id, d.source_id, p.span_id, e.quote,
                   p.domain, p.actor, p.action, p.object, p.consequence,
                   p.pain_statement, p.normalized_key, p.normalization_version,
                   p.severity, p.frequency
            FROM pain_points p
            JOIN evidence e ON e.span_id = p.span_id
            JOIN documents d ON d.doc_id = p.doc_id
            WHERE p.accepted = 1
              AND p.normalized_key IS NOT NULL
              AND p.normalized_key != ''
            ORDER BY p.pain_id
            """
        ).fetchall()
        points: list[PainPoint] = []
        for row in rows:
            try:
                domain = Domain(row["domain"])
            except ValueError as exc:
                raise ClusterStoreError(
                    f"the pain point {row['pain_id']!r} has a domain that is not "
                    "one of the controlled domains"
                ) from exc
            points.append(
                PainPoint(
                    pain_id=row["pain_id"],
                    doc_id=row["doc_id"],
                    source_id=row["source_id"] or "",
                    span_id=row["span_id"],
                    quote=row["quote"] or "",
                    domain=domain,
                    actor=row["actor"] or "",
                    action=row["action"] or "",
                    object=row["object"] or "",
                    consequence=row["consequence"] or "",
                    pain_statement=row["pain_statement"] or "",
                    normalized_key=row["normalized_key"],
                    normalization_version=row["normalization_version"] or "",
                    severity=int(row["severity"] or 0),
                    frequency="" if row["frequency"] is None else str(row["frequency"]),
                )
            )
        return points

    # --- merging and splitting --------------------------------------------

    def merge_clusters(
        self, left: str, right: str, *, run: ClusteringRun
    ) -> str:
        """Merge two clusters into a successor, and return the successor's id.

        The predecessors keep their rows and their version history: a score
        attached to a predecessor stays attached to that predecessor. The
        successor is a new cluster, and both predecessors get a lineage edge
        to it. A predecessor whose status changed gets a new version, so the
        version the current row carries names a snapshot that agrees with it.

        The merge runs in one vector space: the space of the rows it touches
        has to be the space of the run.
        """
        left_cluster = self.cluster(left)
        right_cluster = self.cluster(right)
        if left_cluster is None or right_cluster is None:
            raise ClusterStoreError(
                f"the merge needs two clusters: {left!r} and {right!r} are not "
                "both in the store"
            )
        if (
            left_cluster.status is not ClusterStatus.active
            or right_cluster.status is not ClusterStatus.active
        ):
            raise ClusterStoreError(
                "a merge needs two active clusters"
            )
        self._require_space((left, right), run)
        members = sorted(set(left_cluster.member_ids) | set(right_cluster.member_ids))
        if not members:
            raise ClusterStoreError("a merge needs clusters with members")
        anchor = members[0]
        successor_id = merged_cluster_id(left, right, anchor)
        label, profile = self._anchor_of(anchor, left_cluster, right_cluster)

        with _owned_transaction(self.conn):
            self.record_run(run)
            now = utcnow().isoformat()
            self.conn.execute(
                """
                INSERT INTO clusters (
                    cluster_id, version, status, label, domain, actor, action,
                    object, consequence, anchor_pain_id, member_count,
                    merged_into, split_from, space_id, config_version, run_id,
                    created_at, updated_at
                ) VALUES (?, 1, 'active', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?,
                          ?, ?, ?, ?)
                ON CONFLICT(cluster_id) DO NOTHING
                """,
                (
                    successor_id,
                    label,
                    profile.domain.value,
                    profile.actor,
                    profile.action,
                    profile.object,
                    profile.consequence,
                    anchor,
                    len(members),
                    run.space_id,
                    run.config_version,
                    run.run_id,
                    now,
                    now,
                ),
            )
            self._write_version(
                successor_id,
                1,
                ClusterStatus.active,
                label,
                profile,
                members,
                run,
                now,
            )
            for predecessor in (left, right):
                # The predecessor's memberships are closed before the
                # successor's are opened: a pain point is in one cluster at a
                # time, and the database holds that rule with a unique index
                # over the open rows.
                self.conn.execute(
                    "UPDATE cluster_members SET valid_to = ? "
                    "WHERE cluster_id = ? AND valid_to IS NULL",
                    (now, predecessor),
                )
                # The status is part of what the cluster means, so the
                # version moves with it and the new snapshot says 'merged'.
                version = self._next_version(predecessor)
                self.conn.execute(
                    "UPDATE clusters SET status = 'merged', merged_into = ?, "
                    "version = ?, member_count = 0, updated_at = ? "
                    "WHERE cluster_id = ?",
                    (successor_id, version, now, predecessor),
                )
                label, profile = self._meaning_of(predecessor)
                self._write_version(
                    predecessor,
                    version,
                    ClusterStatus.merged,
                    label,
                    profile,
                    [],
                    run,
                    now,
                )
                self._write_edge(predecessor, successor_id, RELATION_MERGED_INTO, run, now)
            for pain_id in members:
                self.conn.execute(
                    """
                    INSERT INTO cluster_members (
                        member_id, cluster_id, pain_id, valid_from, valid_to,
                        assignment_confidence, basis, run_id
                    ) VALUES (?, ?, ?, ?, NULL, 1.0, 'merged', ?)
                    ON CONFLICT(cluster_id, pain_id, valid_from) DO NOTHING
                    """,
                    (
                        member_id(successor_id, pain_id, now),
                        successor_id,
                        pain_id,
                        now,
                        run.run_id,
                    ),
                )
        return successor_id

    def split_cluster(
        self,
        parent: str,
        groups: Sequence[Sequence[str]],
        *,
        run: ClusteringRun,
    ) -> list[str]:
        """Split a cluster into children, and return the children's ids.

        The groups have to cover the parent's members exactly: a split never
        drops a pain point and never gives a child a member the parent never
        held. The parent keeps its rows and its version history, and its
        memberships are closed. Each child gets open memberships of its own
        and a lineage edge that points at the parent. A parent whose status
        changed gets a new version, so the version the current row carries
        names a snapshot that agrees with it.

        The split runs in one vector space, on the same rule as the merge.
        """
        parent_cluster = self.cluster(parent)
        if parent_cluster is None:
            raise ClusterStoreError(f"the cluster {parent!r} is not in the store")
        if parent_cluster.status is not ClusterStatus.active:
            raise ClusterStoreError("a split needs an active cluster")
        self._require_space((parent,), run)
        held = set(parent_cluster.member_ids)
        covered: set[str] = set()
        for group in groups:
            covered.update(group)
        missing = held - covered
        strangers = covered - held
        if missing or strangers:
            raise ClusterStoreError(
                f"the groups of the split of the cluster {parent!r} must cover "
                f"its members exactly: {len(missing)} are left out and "
                f"{len(strangers)} are not members of the cluster"
            )
        children: list[str] = []
        for group in groups:
            members = sorted(set(group))
            if not members:
                raise ClusterStoreError("a child of a split needs members")
            children.append(split_cluster_id(parent, members[0]))
        if len(set(children)) != len(children):
            raise ClusterStoreError("two children of a split would share an id")

        with _owned_transaction(self.conn):
            self.record_run(run)
            now = utcnow().isoformat()
            # The parent's memberships are closed before the children's are
            # opened: a pain point is in one cluster at a time, and the
            # database holds that rule with a unique index over the open
            # rows. Opening a child first would hit that index.
            self.conn.execute(
                "UPDATE cluster_members SET valid_to = ? "
                "WHERE cluster_id = ? AND valid_to IS NULL",
                (now, parent),
            )
            # The status is part of what the cluster means, so the version
            # moves with it and the new snapshot says 'split'.
            version = self._next_version(parent)
            self.conn.execute(
                "UPDATE clusters SET status = 'split', version = ?, "
                "member_count = 0, updated_at = ? WHERE cluster_id = ?",
                (version, now, parent),
            )
            label, profile = self._meaning_of(parent)
            self._write_version(
                parent,
                version,
                ClusterStatus.split,
                label,
                profile,
                [],
                run,
                now,
            )
            for group, child in zip(groups, children):
                members = sorted(set(group))
                label, profile = self._anchor_of(members[0], parent_cluster)
                self.conn.execute(
                    """
                    INSERT INTO clusters (
                        cluster_id, version, status, label, domain, actor,
                        action, object, consequence, anchor_pain_id,
                        member_count, merged_into, split_from, space_id,
                        config_version, run_id, created_at, updated_at
                    ) VALUES (?, 1, 'active', ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
                              ?, ?, ?, ?)
                    ON CONFLICT(cluster_id) DO NOTHING
                    """,
                    (
                        child,
                        label,
                        profile.domain.value,
                        profile.actor,
                        profile.action,
                        profile.object,
                        profile.consequence,
                        members[0],
                        len(members),
                        parent,
                        run.space_id,
                        run.config_version,
                        run.run_id,
                        now,
                        now,
                    ),
                )
                self._write_version(
                    child,
                    1,
                    ClusterStatus.active,
                    label,
                    profile,
                    members,
                    run,
                    now,
                )
                self._write_edge(child, parent, RELATION_SPLIT_FROM, run, now)
                for pain_id in members:
                    self.conn.execute(
                        """
                        INSERT INTO cluster_members (
                            member_id, cluster_id, pain_id, valid_from, valid_to,
                            assignment_confidence, basis, run_id
                        ) VALUES (?, ?, ?, ?, NULL, 1.0, 'split', ?)
                        ON CONFLICT(cluster_id, pain_id, valid_from) DO NOTHING
                        """,
                        (
                            member_id(child, pain_id, now),
                            child,
                            pain_id,
                            now,
                            run.run_id,
                        ),
                    )
        return children

    # --- helpers ----------------------------------------------------------

    def _anchor_of(
        self,
        pain_id: str,
        *fallbacks: Cluster,
    ) -> tuple[str, ClusterProfile]:
        """The label and the profile of an anchor pain point.

        The store reads them from the pain point it belongs to. When the
        pain point is no longer in the store, the predecessor's own label
        and profile are used.

        shortcut: a merge or a split falls back to the predecessor's label
        when the anchor's pain point row is gone; read the pain point from
        the store once a wave prunes the pain points a cluster points at.
        """
        row = self.conn.execute(
            "SELECT pain_statement, domain, actor, action, object, consequence "
            "FROM pain_points WHERE pain_id = ?",
            (pain_id,),
        ).fetchone()
        if row is not None and row["domain"]:
            return (
                row["pain_statement"] or "",
                ClusterProfile(
                    domain=Domain(row["domain"]),
                    actor=row["actor"] or "",
                    action=row["action"] or "",
                    object=row["object"] or "",
                    consequence=row["consequence"] or "",
                ),
            )
        for fallback in fallbacks:
            if fallback.anchor_pain_id == pain_id:
                return fallback.label, fallback.profile
        if fallbacks:
            return fallbacks[0].label, fallbacks[0].profile
        raise ClusterStoreError(
            f"the pain point {pain_id!r} is not in the store"
        )

    def _write_edge(
        self,
        from_cluster_id: str,
        to_cluster_id: str,
        relation: str,
        run: ClusteringRun,
        now: str,
    ) -> None:
        """Write one lineage edge. An edge is written once."""
        edge_id = hash_id(
            "edge_", f"{from_cluster_id}|{to_cluster_id}|{relation}"
        )
        self.conn.execute(
            """
            INSERT INTO cluster_lineage (
                edge_id, from_cluster_id, to_cluster_id, relation, reason,
                run_id, created_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(from_cluster_id, to_cluster_id, relation) DO NOTHING
            """,
            (edge_id, from_cluster_id, to_cluster_id, relation, run.run_id, now),
        )

    def _cluster_row(self, cluster_id: str) -> sqlite3.Row | None:
        """The current row of one cluster."""
        return self.conn.execute(
            "SELECT * FROM clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone()

    def _require_space(
        self, cluster_ids: Iterable[str], run: ClusteringRun
    ) -> None:
        """Refuse a merge or a split that runs in another vector space.

        The rows being touched have to live in the space of the run: a
        cluster built in one space cannot be merged with one built in
        another, and the result would compare vectors that do not live in
        the same space.
        """
        for cluster_id in cluster_ids:
            row = self._cluster_row(cluster_id)
            if row is None or row["space_id"] is None:
                continue
            require_same_space(row["space_id"], run.space_id)

    def _next_version(self, cluster_id: str) -> int:
        """The version a cluster moves to when its meaning changes."""
        row = self._cluster_row(cluster_id)
        if row is None:
            raise ClusterStoreError(
                f"the cluster {cluster_id!r} is not in the store"
            )
        return int(row["version"]) + 1

    def _meaning_of(self, cluster_id: str) -> tuple[str, ClusterProfile]:
        """The label and the profile a cluster carries."""
        row = self._cluster_row(cluster_id)
        if row is None or not row["domain"]:
            raise ClusterStoreError(
                f"the cluster {cluster_id!r} is not in the store or has no profile"
            )
        return (
            row["label"] or "",
            ClusterProfile(
                domain=Domain(row["domain"]),
                actor=row["actor"] or "",
                action=row["action"] or "",
                object=row["object"] or "",
                consequence=row["consequence"] or "",
            ),
        )

    def _open_members(self) -> dict[str, set[str]]:
        """The open memberships, by cluster."""
        rows = self.conn.execute(
            "SELECT cluster_id, pain_id FROM cluster_members WHERE valid_to IS NULL"
        ).fetchall()
        members: dict[str, set[str]] = {}
        for row in rows:
            members.setdefault(row["cluster_id"], set()).add(row["pain_id"])
        return members

    def _remaining_members(self, cluster_id: str) -> list[str]:
        """The pain points that are still members of a cluster."""
        rows = self.conn.execute(
            "SELECT pain_id FROM cluster_members "
            "WHERE cluster_id = ? AND valid_to IS NULL ORDER BY pain_id",
            (cluster_id,),
        ).fetchall()
        return [row["pain_id"] for row in rows]

    def _active_cluster_ids(self) -> set[str]:
        """The clusters that are still active."""
        rows = self.conn.execute(
            "SELECT cluster_id FROM clusters WHERE status = 'active'"
        ).fetchall()
        return {row["cluster_id"] for row in rows}


def _held_decision_id(pain_id: str, reason: str) -> str:
    """The id of the decision row that records a held pain point."""
    return hash_id("dec_", f"ambiguous|held|{pain_id}||held|{reason}")


def _scoped_decision_id(run_id: str, decision_id: str) -> str:
    """The id of a decision row, scoped to the run.

    Two runs that reach the same decision write two rows, so the second
    run's model call leaves a record of its own. The same run applied twice
    writes one row, so the apply stays idempotent.
    """
    return hash_id("dec_", f"{run_id}|{decision_id}")


def _params_json(run: ClusteringRun) -> str:
    """The parameters of a run, as json.

    A value that cannot be serialised fails the write. The store does not
    turn it into a string, because a stringified value reads back as a
    parameter that was measured.
    """
    try:
        return json.dumps(run.params, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ClusterStoreError(
            f"the parameters of the run {run.run_id!r} cannot be written as "
            f"json: {type(exc).__name__}"
        ) from exc
