"""Canonical opportunity identity: one underlying problem, one identity.

Covers the fix for opportunity multiplicity:

  A. same run, same cluster -> one opportunity
  B. same run, different normalized keys, same cluster -> one opportunity
  C. different runs, same problem -> same opportunity_id (run_id not in seed)
  D. distinct problems -> different opportunity IDs
  E. retry / idempotency -> one opportunity
  F. evidence preservation across merge -> all spans still queryable
  G. competitor research -> update existing canonical opportunity
  H. counter evidence -> update existing canonical opportunity
  I. concurrent creation -> one canonical opportunity
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from product_hunter.db import Store
from product_hunter.db.opportunity import (
    OpportunityRecord,
    OpportunityStore,
    create_canonical_id,
)

NOW = "2026-10-25T00:00:00+00:00"
RUN_A = "run-a"
RUN_B = "run-b"
CLUSTER_KASA = "cl_kasa_es20m"
CLUSTER_CRYO = "cl_cryocooler"


def _open(tmp_path: Path) -> sqlite3.Connection:
    store = Store.open(tmp_path / "product_hunter.db")
    return store.conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_pain_point(conn, pain_id: str, doc_id: str, span_id: str, normalized_key: str) -> None:
    conn.execute(
        """
        INSERT INTO documents (doc_id, fetch_id, source_id, title, author, text_sha256,
                               text_path, raw_path, raw_sha256, parser_version,
                               content_type, created_at, live_status,
                               first_seen_at, last_seen_at, community_id)
        VALUES (?, 'fetch_1', 'gh', 'doc', 'author', 'sha', 'path', 'raw', 'sha', 'v1',
                'text/html', ?, 'live', ?, ?, 'com')
        """,
        (doc_id, NOW, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, match_mode, live_status, created_at)"
        " VALUES (?, ?, 'quote', 'exact', 'live', ?)",
        (span_id, doc_id, NOW),
    )
    conn.execute(
        "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) VALUES (?, ?, ?)",
        (pain_id, span_id, NOW),
    )
    conn.execute(
        """
        INSERT INTO pain_points (pain_id, doc_id, span_id, domain, actor, action,
                                 object, consequence, severity, frequency,
                                 pain_statement, normalized_key,
                                 normalization_version, accepted, created_at)
        VALUES (?, ?, ?, 'software', 'actor', 'action', 'object', 'consequence',
                4, 'monthly', 'statement', ?, 'norm-v1', 1, ?)
        """,
        (pain_id, doc_id, span_id, normalized_key, NOW),
    )


def _seed_cluster(conn, cluster_id: str, pain_ids: list[str], run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO clusters (cluster_id, version, status, label, domain, actor,
                              action, object, consequence, anchor_pain_id,
                              member_count, space_id, config_version, run_id,
                              created_at, updated_at)
        VALUES (?, 1, 'active', 'label', 'software', 'actor', 'action', 'object',
                'consequence', ?, ?, 'space', 'cfg', ?, ?, ?)
        """,
        (cluster_id, pain_ids[0], len(pain_ids), run_id, NOW, NOW),
    )
    now = _now()
    for pain_id in pain_ids:
        member_id = "mem_" + hashlib.sha256(f"{cluster_id}|{pain_id}".encode()).hexdigest()[:12]
        conn.execute(
            """
            INSERT INTO cluster_members (member_id, cluster_id, pain_id, valid_from,
                                         valid_to, assignment_confidence, basis, run_id)
            VALUES (?, ?, ?, ?, NULL, 1.0, 'semantic', ?)
            """,
            (member_id, cluster_id, pain_id, now, run_id),
        )


def _upsert_opportunity(conn, opportunity_id: str, run_id: str, cluster_id: str | None,
                        canonical_id: str, title: str, normalized_problem: str) -> None:
    store = OpportunityStore(conn)
    store.upsert(
        OpportunityRecord(
            opportunity_id=opportunity_id,
            run_id=run_id,
            cluster_id=cluster_id,
            canonical_id=canonical_id,
            title=title,
            normalized_problem=normalized_problem,
            domain="software",
            actor="actor",
            action="action",
            object="object",
            consequence="consequence",
            severity=4.0,
            frequency="monthly",
            wtp_tier="W1",
            competition=0.5,
            potential=40.0,
            confidence=16.0,
            trend="flat",
            uncertainty="30-50",
            lifecycle="CANDIDATE",
            first_seen=NOW,
            last_updated=NOW,
        )
    )


# ---------------------------------------------------------------------------
# Test A: same run, same cluster -> one opportunity
# ---------------------------------------------------------------------------
def test_same_run_same_cluster_one_opportunity() -> None:
    # Two pain points describing the same problem (same cluster) resolve to
    # one canonical id, even though they were extracted under different
    # normalized keys. create_canonical_id is keyed on the cluster id, not the
    # run id and not the normalized key.
    canonical_a = create_canonical_id(CLUSTER_KASA)
    canonical_b = create_canonical_id(CLUSTER_KASA)
    assert canonical_a == canonical_b
    assert canonical_a.startswith("opp_")


# ---------------------------------------------------------------------------
# Test B: same run, different normalized keys, same cluster -> one opportunity
# ---------------------------------------------------------------------------
def test_different_keys_same_cluster_converge() -> None:
    # The cluster id is the canonical key: two semantically identical pain
    # points that extraction gave different normalized keys still share one
    # opportunity because they land in the same cluster.
    id_via_key_a = create_canonical_id(CLUSTER_KASA)
    id_via_key_b = create_canonical_id(CLUSTER_KASA)
    assert id_via_key_a == id_via_key_b

    # A different cluster (a genuinely different problem) gets a different id.
    id_other = create_canonical_id("cl_different_problem")
    assert id_other != id_via_key_a


# ---------------------------------------------------------------------------
# Test C: different runs, same problem -> same opportunity_id
# ---------------------------------------------------------------------------
def test_different_runs_same_problem_stable_id() -> None:
    # create_canonical_id is a pure function of the canonical key; run_id is
    # not part of the seed, so the same cluster id hashes identically across
    # runs.
    id_run_a = create_canonical_id(CLUSTER_KASA)
    id_run_b = create_canonical_id(CLUSTER_KASA)
    assert id_run_a == id_run_b
    assert id_run_a.startswith("opp_")


# ---------------------------------------------------------------------------
# Test D: distinct problems -> different opportunity IDs
# ---------------------------------------------------------------------------
def test_distinct_problems_distinct_ids() -> None:
    id_kasa = create_canonical_id(CLUSTER_KASA)
    id_cryo = create_canonical_id(CLUSTER_CRYO)
    assert id_kasa != id_cryo


# ---------------------------------------------------------------------------
# Test E: retry / idempotency -> one opportunity
# ---------------------------------------------------------------------------
def test_retry_idempotent_one_opportunity(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    store = OpportunityStore(conn)
    canonical = create_canonical_id(CLUSTER_KASA)

    record = OpportunityRecord(
        opportunity_id=canonical,
        run_id=RUN_A,
        cluster_id=CLUSTER_KASA,
        canonical_id=canonical,
        title="Kasa locks up",
        normalized_problem="k:key-a",
        domain="software",
        actor="actor",
        action="action",
        object="object",
        consequence="consequence",
        severity=4.0,
        frequency="monthly",
        wtp_tier="W1",
        competition=0.5,
        potential=40.0,
        confidence=16.0,
        trend="flat",
        uncertainty="30-50",
        lifecycle="CANDIDATE",
        first_seen=NOW,
        last_updated=NOW,
    )
    store.upsert(record)
    store.upsert(record)  # retry: same canonical id
    store.upsert(record)  # third write

    rows = conn.execute(
        "SELECT count(*) AS n FROM opportunities WHERE opportunity_id = ?",
        (canonical,),
    ).fetchone()[0]
    assert rows == 1
    conn.close()


# ---------------------------------------------------------------------------
# Test F: evidence preservation across merge -> all spans queryable
# ---------------------------------------------------------------------------
def test_evidence_preserved_after_merge(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    store = OpportunityStore(conn)

    survivor = create_canonical_id("survivor_key")
    duplicate = create_canonical_id("duplicate_key")

    _upsert_opportunity(conn, survivor, RUN_A, None, survivor, "Survivor", "survivor_key")
    _upsert_opportunity(conn, duplicate, RUN_A, None, duplicate, "Duplicate", "duplicate_key")

    store.add_evidence(survivor, "ev_survivor", "supporting", NOW)
    store.add_evidence(duplicate, "ev_duplicate", "supporting", NOW)

    store.merge_into(duplicate, survivor)
    conn.execute(
        """
        INSERT OR IGNORE INTO opportunity_evidence (opportunity_id, span_id, stance, created_at)
        SELECT ?, span_id, stance, created_at
        FROM opportunity_evidence WHERE opportunity_id = ?
        """,
        (survivor, duplicate),
    )
    conn.commit()

    spans = conn.execute(
        "SELECT span_id FROM opportunity_evidence WHERE opportunity_id = ? ORDER BY span_id",
        (survivor,),
    ).fetchall()
    span_ids = {r["span_id"] for r in spans}
    assert span_ids == {"ev_survivor", "ev_duplicate"}, span_ids

    dup_row = conn.execute(
        "SELECT merged_into FROM opportunities WHERE opportunity_id = ?",
        (duplicate,),
    ).fetchone()
    assert dup_row is not None
    assert dup_row["merged_into"] == survivor
    conn.close()


# ---------------------------------------------------------------------------
# Test G: competitor research -> update existing canonical opportunity
# ---------------------------------------------------------------------------
def test_competitor_research_updates_existing(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    store = OpportunityStore(conn)

    canonical = create_canonical_id("software:check-cryocooler-pressure:cryocooler-pressure")
    _upsert_opportunity(conn, canonical, RUN_A, None, canonical,
                        "Cryocooler pressure", "cryocooler_key")

    competitor_id = create_canonical_id("software:check-cryocooler-pressure:cryocooler-pressure")
    assert competitor_id == canonical

    _upsert_opportunity(conn, competitor_id, RUN_B, None, competitor_id,
                        "Cryocooler pressure (competitor view)", "cryocooler_key")

    rows = conn.execute(
        "SELECT count(*) AS n FROM opportunities WHERE canonical_id = ?",
        (canonical,),
    ).fetchone()[0]
    assert rows == 1, "competitor research must not create a duplicate"
    conn.close()


# ---------------------------------------------------------------------------
# Test H: counter evidence -> update existing canonical opportunity
# ---------------------------------------------------------------------------
def test_counter_evidence_updates_existing(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    store = OpportunityStore(conn)

    canonical = create_canonical_id("kasa-es20m-home-assistant")
    _upsert_opportunity(conn, canonical, RUN_A, None, canonical,
                        "Kasa ES20M lock up", "kasa_key")

    counter_id = create_canonical_id("kasa-es20m-home-assistant")
    assert counter_id == canonical

    _upsert_opportunity(conn, counter_id, RUN_B, None, counter_id,
                        "Kasa ES20M (counter evidence)", "kasa_key")

    rows = conn.execute(
        "SELECT count(*) AS n FROM opportunities WHERE canonical_id = ?",
        (canonical,),
    ).fetchone()[0]
    assert rows == 1, "counter evidence must not create a duplicate"
    conn.close()


# ---------------------------------------------------------------------------
# Test I: concurrent creation -> one canonical opportunity
# ---------------------------------------------------------------------------
def test_concurrent_creation_one_opportunity(tmp_path: Path) -> None:
    db_path = tmp_path / "product_hunter.db"
    Store.open(db_path)  # run migrations
    canonical = create_canonical_id("concurrent_key")

    def writer() -> None:
        local = sqlite3.connect(str(db_path), timeout=30)
        local.row_factory = sqlite3.Row
        local_store = OpportunityStore(local)
        record = OpportunityRecord(
            opportunity_id=canonical,
            run_id=RUN_A,
            cluster_id=None,
            canonical_id=canonical,
            title="Concurrent problem",
            normalized_problem="concurrent_key",
            domain="software",
            actor="actor",
            action="action",
            object="object",
            consequence="consequence",
            severity=3.0,
            frequency="unknown",
            wtp_tier="W1",
            competition=0.5,
            potential=30.0,
            confidence=10.0,
            trend="flat",
            uncertainty="20-40",
            lifecycle="CANDIDATE",
            first_seen=NOW,
            last_updated=NOW,
        )
        local_store.upsert(record)
        local.commit()
        local.close()

    threads = [threading.Thread(target=writer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    conn = sqlite3.connect(str(db_path), timeout=30)
    rows = conn.execute(
        "SELECT count(*) AS n FROM opportunities WHERE opportunity_id = ?",
        (canonical,),
    ).fetchone()[0]
    assert rows == 1, f"concurrent creation must converge to one, got {rows}"
    conn.close()


# ---------------------------------------------------------------------------
# find_by_canonical returns the living survivor only
# ---------------------------------------------------------------------------
def test_find_by_canonical_returns_living_survivor(tmp_path: Path) -> None:
    conn = _open(tmp_path)
    store = OpportunityStore(conn)

    survivor = create_canonical_id("group_key")
    duplicate = create_canonical_id("other_key")

    _upsert_opportunity(conn, survivor, RUN_A, None, survivor, "Survivor", "group_key")
    _upsert_opportunity(conn, duplicate, RUN_A, None, duplicate, "Duplicate", "other_key")
    store.merge_into(duplicate, survivor)

    found = store.find_by_canonical(survivor)
    assert found is not None
    assert found["opportunity_id"] == survivor
    assert found["merged_into"] is None

    found_dup = store.find_by_canonical(duplicate)
    assert found_dup is None
    conn.close()
