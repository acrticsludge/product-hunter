from __future__ import annotations

import json
from pathlib import Path

from product_hunter.cli import db_init_command, db_status_command


def test_db_status_reports_every_table_in_the_schema(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "product_hunter.db"
    assert db_init_command(db_path=db_path, repo_root=tmp_path) == 0
    capsys.readouterr()

    assert db_status_command(db_path=db_path, repo_root=tmp_path) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["version"] == 15

    tables = payload["tables"]
    # The tables the wave 1 schema shipped.
    assert {"sources", "documents", "evidence", "pain_points", "jobs"} <= set(tables)
    # The tables the wave 2 migrations shipped. A hard-coded list of the
    # tables would have to be edited for each of them, and this is the test
    # that notices when it is not.
    assert {
        "collection_items",
        "collection_run_state",
        "collection_source_state",
        "document_versions",
        "evidence_observations",
        "evidence_lineage",
        "clustering_runs",
        "clusters",
        "cluster_versions",
        "cluster_members",
        "cluster_lineage",
        "cluster_decisions",
        "author_identity_links",
        "community_links",
        "author_clusters",
        "author_cluster_members",
        "independence_reports",
        "source_candidates",
        "source_reviews",
        "model_comparisons",
    } <= set(tables)
    # The tables the wave 3 migration shipped.
    assert {
        "worker_health",
        "operation_runs",
        "operation_events",
        "operation_budget",
        "source_pacing",
    } <= set(tables)
    assert all(count == 0 for count in tables.values())


def test_db_status_refuses_a_database_that_does_not_exist(tmp_path: Path, capsys) -> None:
    assert (
        db_status_command(db_path=tmp_path / "missing.db", repo_root=tmp_path) == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
