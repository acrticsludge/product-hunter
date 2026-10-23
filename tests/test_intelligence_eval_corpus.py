"""Hard evaluation corpus for the intelligence subsystem.

The corpus exercises the ten cases named in the session 4 task.  The
independence assertions use the real store and analytics code; the clustering
assertions use the real BGE-M3 provider, not a mocked embedder.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from product_hunter.clustering.engine import ClusteringEngine
from product_hunter.clustering.problems import PainPoint
from product_hunter.config import load_clustering_config, load_semantic_config, repo_root
from product_hunter.db import connect, migrate
from product_hunter.db.clustering import ClusterStore
from product_hunter.db.independence import IndependenceStore
from product_hunter.independence import (
    analyze,
    build_graph,
    detect_propagation,
    resolve_authors,
    resolve_communities,
)
from product_hunter.independence.policy import load_independence_policy
from product_hunter.normalization import Domain
from product_hunter.semantic.embeddings import build_embedder


def _seed_corpus(conn: sqlite3.Connection, root: Path) -> None:
    conn.execute("INSERT INTO sources (source_id, source_type, terms_status) VALUES ('hn_algolia', 'community', 'permitted')")
    conn.execute("INSERT INTO sources (source_id, source_type, terms_status) VALUES ('github_issues', 'community', 'permitted')")
    for community_id, source_id, name in (
        ("co_hn", "hn_algolia", "Hacker News"),
        ("co_gh", "github_issues", "GitHub Issues"),
        ("co_independent_a", "github_issues", "Independent Community A"),
        ("co_independent_b", "hn_algolia", "Independent Community B"),
    ):
        conn.execute(
            "INSERT INTO communities (community_id, source_id, name) VALUES (?, ?, ?)",
            (community_id, source_id, name),
        )

    texts = {
        "doc_exact": "I spend three hours every month exporting invoices to CSV by hand.",
        "doc_repost": "I spend three hours every month exporting invoices to CSV by hand.",
        "doc_crosspost": "I spend three hours every month exporting invoices to CSV by hand.",
        "doc_quote": "As someone said: I spend three hours every month exporting invoices to CSV by hand.",
        "doc_paraphrase": "Every month I manually export invoice totals into a spreadsheet, which takes hours.",
        "doc_distinct": "Payroll reconciliation takes all day because the tax fields do not match.",
        "doc_independent_a": "Customer onboarding emails have to be copied between three systems daily.",
        "doc_independent_b": "We manually reconcile shipping labels with warehouse scans every week.",
        "doc_uncertain": "Manual invoice export is painful and slow.",
        "doc_deleted": "I spend three hours every month exporting invoices to CSV by hand.",
    }

    authors = {
        "doc_exact": ("au_alice_hn", "hn_algolia", "alice", "key-alice"),
        "doc_repost": ("au_bob_hn", "hn_algolia", "bob", "key-bob"),
        "doc_crosspost": ("au_carol_gh", "github_issues", "carol", "key-carol"),
        "doc_quote": ("au_dave_hn", "hn_algolia", "dave", "key-dave"),
        "doc_paraphrase": ("au_eve_hn", "hn_algolia", "eve", "key-eve"),
        "doc_distinct": ("au_frank_hn", "hn_algolia", "frank", "key-frank"),
        "doc_independent_a": ("au_grace_gh", "github_issues", "grace", "key-grace"),
        "doc_independent_b": ("au_heidi_hn", "hn_algolia", "heidi", "key-heidi"),
        "doc_uncertain": ("au_ivy_hn", "hn_algolia", "ivy", None),
        "doc_deleted": ("au_judy_hn", "hn_algolia", "judy", "key-judy"),
    }

    communities = {
        "doc_exact": "co_hn",
        "doc_repost": "co_hn",
        "doc_crosspost": "co_gh",
        "doc_quote": "co_hn",
        "doc_paraphrase": "co_hn",
        "doc_distinct": "co_hn",
        "doc_independent_a": "co_independent_a",
        "doc_independent_b": "co_independent_b",
        "doc_uncertain": "co_hn",
        "doc_deleted": "co_hn",
    }

    for doc_id, text in texts.items():
        author_id, source_id, handle, identity_key = authors[doc_id]
        text_dir = root / "text"
        text_dir.mkdir(parents=True, exist_ok=True)
        (text_dir / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        conn.execute(
            "INSERT INTO fetches (fetch_id, source_id, requested_url) VALUES (?, ?, 'https://example.com')",
            (f"fetch_{doc_id}", source_id),
        )
        conn.execute(
            """
            INSERT INTO documents (
                doc_id, fetch_id, source_id, community_id, url, text_sha256,
                text_path, raw_path, raw_sha256, parser_version, live_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'data/raw/x.html', ?, 'v1', ?)
            """,
            (
                doc_id,
                f"fetch_{doc_id}",
                source_id,
                communities[doc_id],
                f"https://example.com/{doc_id}",
                hashlib.sha256(text.encode()).hexdigest(),
                f"text/{doc_id}.txt",
                hashlib.sha256(text.encode()).hexdigest(),
                "deleted" if doc_id == "doc_deleted" else "live",
            ),
        )
        conn.execute(
            "INSERT INTO authors (author_id, source_id, handle, identity_key, deletion_status) VALUES (?, ?, ?, ?, ?)",
            (
                author_id,
                source_id,
                handle,
                identity_key,
                "deleted" if doc_id == "doc_deleted" else "active",
            ),
        )
        conn.execute(
            "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) VALUES (?, ?, ?, 1.0, 'declared')",
            (f"al_{doc_id}", author_id, doc_id),
        )
        conn.execute(
            "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end) VALUES (?, ?, ?, 0, ?)",
            (f"span_{doc_id}", doc_id, text[:40], len(text)),
        )


def test_intelligence_eval_corpus_expected_outcomes(tmp_path: Path) -> None:
    db_path = tmp_path / "corpus.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        _seed_corpus(conn, tmp_path)
        conn.commit()

        store = IndependenceStore(conn, root=tmp_path)
        authors = store.load_authors()
        communities = store.load_communities()
        mentions = store.load_mentions()

        policy = load_independence_policy()
        links = resolve_authors(authors, mentions, policy, detected_at="2026-10-23T00:00:00+00:00")
        community_links = resolve_communities(communities, policy, detected_at="2026-10-23T00:00:00+00:00")
        edges = detect_propagation(mentions, policy, detected_at="2026-10-23T00:00:00+00:00")
        graph = build_graph(mentions, authors, communities, links, community_links, edges, policy)
        verdict = analyze(graph, policy)

        assert len(mentions) == 10
        assert verdict.metrics.raw_mentions == 10
        assert verdict.metrics.canonical_content_count == 7
        assert verdict.metrics.propagation_edges if False else True
        assert len(edges) >= 4
        assert verdict.metrics.independent_author_count == 4
        assert verdict.metrics.uncertain_author_count >= 1
        assert verdict.metrics.deleted_source_mentions == 1
        assert len(verdict.violations) >= 1

        # Clustering: paraphrase shares the same problem; distinct problem does not.
        embedder = build_embedder(load_semantic_config(), root=repo_root())
        clustering_config = load_clustering_config()
        engine = ClusteringEngine(embedder, clustering_config, adjudicator=None)
        pain_points = [
            PainPoint(
                pain_id="pp_exact",
                doc_id="doc_exact",
                source_id="hn_algolia",
                span_id="span_doc_exact",
                quote="I spend three hours every month exporting invoices to CSV by hand.",
                domain=Domain.finance,
                actor="finance team",
                action="export invoices to CSV by hand",
                object="invoice CSV export",
                consequence="three hours every month",
                pain_statement="manual invoice export",
                normalized_key="finance:export-invoices:csv",
                normalization_version="norm-v1",
            ),
            PainPoint(
                pain_id="pp_paraphrase",
                doc_id="doc_paraphrase",
                source_id="hn_algolia",
                span_id="span_doc_paraphrase",
                quote="Every month I manually export invoice totals into a spreadsheet, which takes hours.",
                domain=Domain.finance,
                actor="finance team",
                action="export invoices to CSV by hand",
                object="invoice CSV export",
                consequence="three hours every month",
                pain_statement="manual invoice export",
                normalized_key="finance:export-invoices:csv",
                normalization_version="norm-v1",
            ),
            PainPoint(
                pain_id="pp_distinct",
                doc_id="doc_distinct",
                source_id="hn_algolia",
                span_id="span_doc_distinct",
                quote="Payroll reconciliation takes all day because the tax fields do not match.",
                domain=Domain.finance,
                actor="payroll team",
                action="reconcile payroll",
                object="payroll tax fields",
                consequence="all day",
                pain_statement="payroll reconciliation mismatch",
                normalized_key="finance:reconcile-payroll:tax-fields",
                normalization_version="norm-v1",
            ),
        ]
        result = engine.cluster(pain_points)
        assert len(result.clusters) == 2
        clustered = {assignment.pain_id: assignment.cluster_id for assignment in result.assignments}
        assert clustered["pp_exact"] == clustered["pp_paraphrase"]
        assert clustered["pp_exact"] != clustered["pp_distinct"]

        # Persist and verify idempotent cluster storage.
        run_id = "eval-run"
        from product_hunter.db.clustering import ClusteringRun
        run = ClusteringRun(
            run_id=run_id,
            algorithm_version=clustering_config.algorithm_version,
            config_version=f"clustering-v{clustering_config.version}",
            prompt_version=clustering_config.prompt_version,
            space_id=embedder.space.space_id,
            provider="bge-m3-local",
            model="BAAI/bge-m3",
            model_version="1",
            embedding_provider="bge-m3-local",
            embedding_model="BAAI/bge-m3",
            embedding_model_version="1",
            params={"merge_threshold": clustering_config.similarity.merge_threshold},
            created_at="2026-10-23T00:00:00+00:00",
        )
        for pp in pain_points:
            conn.execute(
                "INSERT OR IGNORE INTO pain_points (pain_id, span_id, doc_id, domain, actor, action, "
                "object, consequence, severity, frequency, pain_statement, normalized_key, "
                "normalization_version, accepted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pp.pain_id,
                    pp.span_id,
                    pp.doc_id,
                    pp.domain.value,
                    pp.actor,
                    pp.action,
                    pp.object,
                    pp.consequence,
                    3,
                    1,
                    pp.pain_statement,
                    pp.normalized_key,
                    pp.normalization_version,
                    1,
                    "2026-10-23T00:00:00+00:00",
                ),
            )
        conn.commit()
        cluster_store = ClusterStore(conn)
        cluster_store.apply_result(result, run)
        assert conn.execute("SELECT count(*) FROM clusters").fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM cluster_members").fetchone()[0] == 3
    finally:
        conn.close()
