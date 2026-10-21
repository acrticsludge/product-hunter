"""The engine writes the vault, and only the vault.

The engine is tested on a temporary directory and a temporary database. A
second run over unchanged state touches nothing, a note of an opportunity
that is gone is removed, and a projection that fails leaves the outbox task
pending for the retry.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from product_hunter.cli import main
from product_hunter.db import connect, migrate
from product_hunter.db.outbox import (
    enqueue_projection,
    list_projection_tasks,
    process_outbox,
)
from product_hunter.fetch import utcnow
from product_hunter.projection import Projector, SqliteProjectionSource
from product_hunter.projection.contract import CANONICAL_DDL
from product_hunter.projection.engine import MANIFEST_NAME
from product_hunter.projection.models import Snapshot
from product_hunter.projection.render import RenderedNote, render_snapshot
from product_hunter.projection.vault import (
    BAND_EXCEPTIONAL,
    BAND_UNSCORED,
    TOP_LEVEL_FOLDERS,
    VaultLayout,
)

KEY = "operations|finance lead|paste totals|spreadsheet|a lost Saturday"
OTHER_KEY = "marketing|marketer|write reports|spreadsheet|another lost evening"


def _database(tmp_path: Path) -> Path:
    """A database with the tables the projection reads, and rows in them."""
    path = tmp_path / "store.db"
    conn = connect(path)
    migrate(conn)
    conn.execute(
        "INSERT INTO sources (source_id, source_type, terms_status, enabled) "
        "VALUES ('hn_algolia', 'forum', 'permitted', 1)"
    )
    conn.execute(
        "INSERT INTO fetches (fetch_id, source_id, requested_url, status) "
        "VALUES ('f1', 'hn_algolia', 'https://news.ycombinator.com/item?id=1', 200)"
    )
    conn.execute(
        "INSERT INTO communities (community_id, source_id, name, url) "
        "VALUES ('co_1', 'hn_algolia', 'Hacker News', 'https://news.ycombinator.com')"
    )
    conn.execute(
        "INSERT INTO documents (doc_id, fetch_id, source_id, community_id, url,"
        " title, author, text_sha256, text_path, raw_path, raw_sha256,"
        " parser_version, created_at) VALUES ('doc_1', 'f1', 'hn_algolia', 'co_1',"
        " 'https://news.ycombinator.com/item?id=1', 'a thread', 'alice', 'hash-1',"
        " 'p', 'p', 'r', 'v1', '2026-10-18T08:00:00Z')"
    )
    conn.execute(
        "INSERT INTO authors (author_id, source_id, handle, display_name) "
        "VALUES ('au_1', 'hn_algolia', 'alice', 'Alice')"
    )
    conn.execute(
        "INSERT INTO author_links (link_id, author_id, doc_id, confidence, basis) "
        "VALUES ('al_1', 'au_1', 'doc_1', 0.9, 'declared')"
    )
    conn.execute(
        "INSERT INTO evidence (span_id, doc_id, quote, char_start, char_end,"
        " match_mode, created_at) VALUES ('ev_1', 'doc_1', 'We paste the totals by"
        " hand.', 0, 10, 'exact', '2026-10-18T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, domain, actor, action,"
        " object, consequence, severity, frequency, pain_statement, normalized_key,"
        " accepted, created_at) VALUES ('pp_1', 'ev_1', 'doc_1', 'operations',"
        " 'finance lead', 'paste totals', 'spreadsheet', 'a lost Saturday', 4,"
        " 'monthly', 'the team pastes invoice totals by hand', ?, 1,"
        " '2026-10-18T09:00:00Z')",
        (KEY,),
    )
    conn.execute(
        "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) "
        "VALUES ('pp_1', 'ev_1', '2026-10-18T09:00:00Z')"
    )
    conn.execute(
        "INSERT INTO pain_points (pain_id, span_id, doc_id, domain, actor, action,"
        " object, consequence, severity, frequency, pain_statement, normalized_key,"
        " accepted, created_at) VALUES ('pp_2', 'ev_1', 'doc_1', 'marketing',"
        " 'marketer', 'write reports', 'spreadsheet', 'another lost evening', 5,"
        " 'weekly', 'a marketer writes the weekly report by hand', ?, 1,"
        " '2026-10-19T09:00:00Z')",
        (OTHER_KEY,),
    )
    conn.execute(
        "INSERT INTO evidence_pain_links (pain_id, span_id, created_at) "
        "VALUES ('pp_2', 'ev_1', '2026-10-19T09:00:00Z')"
    )
    conn.commit()
    conn.close()
    return path


def _projector(db_path: Path, vault: Path) -> Projector:
    """A projector that reads the database without writing to it."""
    conn = connect(db_path, read_only=True)
    projector = Projector(vault, source=SqliteProjectionSource(conn))
    return projector


def _files(vault: Path) -> dict[str, bytes]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }


def _opportunity_notes(vault: Path) -> list[Path]:
    return sorted((vault / "01-Ideas").rglob("*.md"))


# --- the projection ---------------------------------------------------------


def test_the_vault_holds_the_notes_of_the_state(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    result = projector.project()
    assert len(result.written) == len(result.paths)
    assert result.removed == []
    assert len(_opportunity_notes(vault)) == 2
    assert (vault / "00-Dashboard" / "Top Opportunities.md").is_file()
    assert (vault / "02-Research" / "Sources" / "hn_algolia.md").is_file()
    assert (vault / "02-Research" / "Communities" / "co_1.md").is_file()
    assert (vault / "02-Research" / "Trends" / "README.md").is_file()
    assert (vault / "03-Daily-Reports" / "2026-10-18.md").is_file()


def test_two_runs_over_the_same_state_produce_the_same_bytes(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"
    _projector(db_path, first).project()
    _projector(db_path, second).project()
    assert _files(first) == _files(second)


def test_a_second_run_writes_nothing(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    before = {
        path: path.stat().st_mtime_ns for path in sorted(vault.rglob("*")) if path.is_file()
    }
    result = projector.project()
    assert result.written == []
    assert result.removed == []
    assert result.unchanged == sorted(result.paths)
    after = {
        path: path.stat().st_mtime_ns for path in sorted(vault.rglob("*")) if path.is_file()
    }
    assert after == before


def test_a_changed_opportunity_changes_the_note(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    note = _opportunity_notes(vault)[0]
    before = note.read_bytes()
    conn = connect(db_path)
    conn.execute("UPDATE pain_points SET severity = severity + 1")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.written
    after = note.read_bytes()
    assert after != before
    assert b"content_hash" in after


def test_a_lone_surrogate_does_not_stop_the_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value that cannot be encoded must not fail the same row forever.

    The sanitizer and the frontmatter drop a lone surrogate at the root. One
    that reaches the note anyway is replaced by the write, and the hash still
    describes the bytes on disk.
    """
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    read_snapshot = SqliteProjectionSource.snapshot

    def snapshot(self, *, as_of: str | None = None) -> Snapshot:
        """The state, with a lone surrogate in a title and in a first seen."""
        state = read_snapshot(self, as_of=as_of)
        for opportunity in state.opportunities:
            opportunity.title = opportunity.title + "\ud800"
            opportunity.first_seen = opportunity.first_seen + "\ud800"
        return state

    def notes(state: Snapshot, layout: VaultLayout) -> list[RenderedNote]:
        """The notes, one of them still holding a lone surrogate."""
        return [
            replace(note, body=note.body + "\nsurrogate \ud800\n")
            if note.kind == "opportunity"
            else note
            for note in render_snapshot(state, layout)
        ]

    monkeypatch.setattr(SqliteProjectionSource, "snapshot", snapshot)
    monkeypatch.setattr("product_hunter.projection.engine.render_snapshot", notes)

    projector = _projector(db_path, vault)
    projector.project()

    assert len(_opportunity_notes(vault)) == 2
    note = _opportunity_notes(vault)[0]
    assert note.is_file()
    text = note.read_text(encoding="utf-8")

    # The root dropped the surrogates of the title and of the frontmatter, so
    # neither of them carries the question mark the write would have put there.
    lines = text.split("\n")
    heading = next(line for line in lines if line.startswith("# "))
    assert "?" not in heading
    assert 'first_seen: "2026-10-18"' in lines

    # The one that reached the note anyway was replaced, not fatal.
    assert "surrogate ?" in lines

    # The hash describes the bytes on disk.
    manifest = json.loads((vault / MANIFEST_NAME).read_text(encoding="utf-8"))
    relative = note.relative_to(vault).as_posix()
    assert manifest["notes"][relative]["hash"] == hashlib.sha256(
        note.read_bytes()
    ).hexdigest()

    # A second run over the same state writes nothing.
    before = {
        path: path.stat().st_mtime_ns
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }
    result = projector.project()
    assert result.written == []
    after = {
        path: path.stat().st_mtime_ns
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }
    assert after == before


def test_a_deleted_opportunity_is_removed_from_the_vault(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    assert len(_opportunity_notes(vault)) == 2
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert len(_opportunity_notes(vault)) == 1


def test_a_note_is_pruned_even_without_a_manifest(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    assert (vault / MANIFEST_NAME).is_file()
    (vault / MANIFEST_NAME).unlink()
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert len(_opportunity_notes(vault)) == 1


def test_a_note_is_pruned_even_with_a_manifest_of_the_wrong_shape(
    tmp_path: Path,
) -> None:
    """A manifest that parses but holds no notes must not disable the prune."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    assert len(_opportunity_notes(vault)) == 2
    (vault / MANIFEST_NAME).write_text(
        '{"contract": "projection-v1"}', encoding="utf-8"
    )
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert len(_opportunity_notes(vault)) == 1


def test_a_note_is_pruned_with_a_manifest_of_an_empty_notes_object(
    tmp_path: Path,
) -> None:
    """An empty notes object is no notes, so the scan finds them."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    assert len(_opportunity_notes(vault)) == 2
    (vault / MANIFEST_NAME).write_text(
        '{"contract": "projection-v1", "notes": {}}', encoding="utf-8"
    )
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert len(_opportunity_notes(vault)) == 1


def test_a_note_with_a_deep_frontmatter_does_not_stop_the_run(
    tmp_path: Path,
) -> None:
    """A frontmatter that cannot be parsed is not the projection's note.

    A deeply nested frontmatter takes the YAML parser past the recursion
    limit. The parse fails, the note is not ours, and the run still ends:
    the manifest is written, so the outbox task does not retry for ever.
    """
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    # Without the manifest the engine finds its notes by their frontmatter.
    (vault / MANIFEST_NAME).unlink()
    deep = vault / "a note with a deep frontmatter.md"
    deep.write_text(
        "---\nsource: " + "[" * 3000 + "]" * 3000 + "\n---\n\nText.\n",
        encoding="utf-8",
    )
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert len(_opportunity_notes(vault)) == 1
    assert deep.is_file()
    assert (vault / MANIFEST_NAME).is_file()


def test_the_prune_leaves_a_note_it_did_not_write_alone(tmp_path: Path) -> None:
    """A hand-written note is not the projection's note, whatever it says."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    (vault / MANIFEST_NAME).unlink()
    hand_written = vault / "01-Ideas" / "a hand-written note.md"
    hand_written.write_text(
        "---\n"
        'title: "a hand-written note"\n'
        'note: "the product-hunter-projection did not write this"\n'
        "---\n\n"
        "My own words.\n",
        encoding="utf-8",
    )
    foreign = vault / "01-Ideas" / "a note of another tool.md"
    foreign.write_text(
        "---\n"
        'source: "some-other-tool"\n'
        "---\n\n"
        "Not mine.\n",
        encoding="utf-8",
    )
    broken = vault / "01-Ideas" / "a note with a broken frontmatter.md"
    broken.write_text(
        "---\nsource: [unclosed\n---\n\nText.\n", encoding="utf-8"
    )
    conn = connect(db_path)
    conn.execute("DELETE FROM evidence_pain_links WHERE pain_id = 'pp_2'")
    conn.execute("DELETE FROM pain_points WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    assert hand_written.is_file()
    assert foreign.is_file()
    assert broken.is_file()


def test_the_manifest_records_what_the_vault_holds(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    text = (vault / MANIFEST_NAME).read_text(encoding="utf-8")
    assert "projection-v1" in text
    assert "opp_" in text


def test_the_projection_never_writes_to_the_database(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    before = db_path.read_bytes()
    _projector(db_path, tmp_path / "vault").project()
    assert db_path.read_bytes() == before


def test_the_file_name_and_the_id_are_stable(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    first = _opportunity_notes(vault)[0]
    path = first.relative_to(vault).as_posix()
    projector.project()
    assert _opportunity_notes(vault)[0].relative_to(vault).as_posix() == path
    identifier = _frontmatter(first)["id"]
    assert identifier.startswith("opp_")


def test_a_changed_title_keeps_the_id_and_moves_the_note(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    projector = _projector(db_path, vault)
    projector.project()
    before = {note: _frontmatter(note)["id"] for note in _opportunity_notes(vault)}
    conn = connect(db_path)
    conn.execute(
        "UPDATE pain_points SET pain_statement = 'a different statement' "
        "WHERE pain_id = 'pp_1'"
    )
    conn.commit()
    conn.close()
    result = projector.project()
    assert result.removed
    after = {note: _frontmatter(note)["id"] for note in _opportunity_notes(vault)}
    assert set(after.values()) & set(before.values())


def _frontmatter(note: Path) -> dict[str, str]:
    """The frontmatter of a note, read as plain key and value pairs."""
    fields: dict[str, str] = {}
    lines = note.read_text(encoding="utf-8").splitlines()
    for line in lines[1:]:
        if line == "---":
            break
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"')
    return fields


# --- the outbox -------------------------------------------------------------


def test_the_handler_serves_the_obsidian_target(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    conn = connect(db_path)
    try:
        enqueue_projection(
            conn,
            target="obsidian",
            entity_type="run",
            entity_id="run-1",
            idempotency_key="run_imported:run-1",
            payload={"run_id": "run-1"},
        )
        projector = _projector(db_path, vault)
        processed = process_outbox(
            conn, {"obsidian": projector.handle_projection_task}, "worker-1", now=utcnow()
        )
        assert processed == 1
        assert len(_opportunity_notes(vault)) == 2
        task = list_projection_tasks(conn)[0]
        assert task.status == "done"
    finally:
        conn.close()


def test_two_tasks_for_the_same_run_leave_one_note(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    conn = connect(db_path)
    try:
        for key in ("run_imported:run-1", "run_imported:run-2"):
            enqueue_projection(
                conn,
                target="obsidian",
                entity_type="run",
                entity_id=key.split(":")[1],
                idempotency_key=key,
                payload={"run_id": key.split(":")[1]},
            )
        projector = _projector(db_path, vault)
        processed = process_outbox(
            conn, {"obsidian": projector.handle_projection_task}, "worker-1", now=utcnow()
        )
        assert processed == 2
        assert len(_opportunity_notes(vault)) == 2
    finally:
        conn.close()


def test_a_failed_projection_leaves_the_task_pending_and_the_retry_succeeds(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    # A file where the vault should be: every write below it fails.
    vault.write_text("not a directory", encoding="utf-8")
    conn = connect(db_path)
    try:
        enqueue_projection(
            conn,
            target="obsidian",
            entity_type="run",
            entity_id="run-1",
            idempotency_key="run_imported:run-1",
            payload={"run_id": "run-1"},
        )
        projector = _projector(db_path, vault)
        now = utcnow()
        processed = process_outbox(
            conn, {"obsidian": projector.handle_projection_task}, "worker-1", now=now
        )
        assert processed == 1
        task = list_projection_tasks(conn)[0]
        assert task.status == "pending"
        assert task.attempts == 1
        assert task.last_error is not None
        vault.unlink()
        processed = process_outbox(
            conn,
            {"obsidian": projector.handle_projection_task},
            "worker-1",
            now=now + timedelta(seconds=10),
        )
        assert processed == 1
        task = list_projection_tasks(conn)[0]
        assert task.status == "done"
        assert len(_opportunity_notes(vault)) == 2
    finally:
        conn.close()


def test_the_projection_does_not_need_a_writable_database(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    conn = connect(db_path, read_only=True)
    try:
        projector = Projector(vault, source=SqliteProjectionSource(conn))
        result = projector.project()
        assert result.written
    finally:
        conn.close()


def test_the_command_builds_the_vault(tmp_path: Path, capsys) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    code = main(
        [
            "project",
            "--db",
            str(db_path),
            "--vault",
            str(vault),
        ]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["written"] > 0
    assert summary["vault"] == str(vault)
    assert len(_opportunity_notes(vault)) == 2


def test_the_command_reports_a_failure(tmp_path: Path, capsys) -> None:
    code = main(["project", "--db", str(tmp_path / "missing.db")])
    assert code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_the_command_drains_the_outbox(tmp_path: Path, capsys) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    conn = connect(db_path)
    try:
        enqueue_projection(
            conn,
            target="obsidian",
            entity_type="run",
            entity_id="run-1",
            idempotency_key="run_imported:run-1",
            payload={"run_id": "run-1"},
        )
        conn.commit()
    finally:
        conn.close()
    code = main(
        ["project", "--db", str(db_path), "--vault", str(vault), "--outbox"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["processed"] == 1
    assert len(_opportunity_notes(vault)) == 2
    conn = connect(db_path)
    try:
        assert list_projection_tasks(conn)[0].status == "done"
    finally:
        conn.close()


def test_the_command_builds_the_vault_once_for_the_whole_drain(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The vault is a projection of the whole state, so one build is enough."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    conn = connect(db_path)
    try:
        for key in ("run_imported:run-1", "run_imported:run-2"):
            enqueue_projection(
                conn,
                target="obsidian",
                entity_type="run",
                entity_id=key.split(":")[1],
                idempotency_key=key,
                payload={"run_id": key.split(":")[1]},
            )
        conn.commit()
    finally:
        conn.close()
    builds: list[int] = []
    build = Projector.project

    def counted(self, as_of=None):
        builds.append(1)
        return build(self, as_of=as_of)

    monkeypatch.setattr(Projector, "project", counted)
    code = main(
        ["project", "--db", str(db_path), "--vault", str(vault), "--outbox"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["processed"] == 2
    assert len(builds) == 1
    assert len(_opportunity_notes(vault)) == 2


def test_the_command_builds_the_vault_when_no_task_is_pending(
    tmp_path: Path, capsys
) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    code = main(
        ["project", "--db", str(db_path), "--vault", str(vault), "--outbox"]
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True
    assert summary["processed"] == 0
    assert summary["written"] > 0
    assert len(_opportunity_notes(vault)) == 2


# --- the vault layout -------------------------------------------------------


def _canonical_database(tmp_path: Path, lifecycle: str = "PROMISING") -> Path:
    """A database whose opportunities table holds one row of the contract."""
    path = _database(tmp_path)
    conn = connect(path)
    conn.executescript(CANONICAL_DDL)
    conn.execute(
        "INSERT INTO opportunities (opportunity_id, title, potential, lifecycle,"
        " created_at, updated_at) VALUES ('opp_canonical', 'A canonical idea',"
        " 91.0, ?, '2026-10-01T00:00:00Z', '2026-10-19T00:00:00Z')",
        (lifecycle,),
    )
    conn.commit()
    conn.close()
    return path


def test_a_potential_files_the_note_under_its_band(tmp_path: Path) -> None:
    db_path = _canonical_database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    notes = _opportunity_notes(vault)
    assert len(notes) == 1
    assert notes[0].parent.name == BAND_EXCEPTIONAL


def test_a_missing_potential_files_the_note_under_the_unscored_band(
    tmp_path: Path,
) -> None:
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    assert {note.parent.name for note in _opportunity_notes(vault)} == {
        BAND_UNSCORED
    }


def test_a_rejected_lifecycle_files_the_note_under_the_archive(
    tmp_path: Path,
) -> None:
    db_path = _canonical_database(tmp_path, lifecycle="REJECTED")
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    notes = list((vault / "99-Archive" / "Rejected").glob("*.md"))
    assert len(notes) == 1
    assert notes[0].read_text(encoding="utf-8").count('id: "opp_canonical"') == 1


def test_a_note_path_never_leaves_the_vault_root(tmp_path: Path) -> None:
    layout = VaultLayout(tmp_path / "vault")
    with pytest.raises(ValueError):
        layout.resolve("../escape.md")
    with pytest.raises(ValueError):
        layout.resolve("/etc/passwd")


def test_the_vault_has_the_seven_folders_and_the_dashboard_readme(
    tmp_path: Path,
) -> None:
    """A vault built from an empty database still has the whole layout."""
    path = tmp_path / "store.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    vault = tmp_path / "vault"
    _projector(path, vault).project()
    for name in TOP_LEVEL_FOLDERS:
        assert (vault / name).is_dir()
    assert (vault / "00-Dashboard" / "README.md").is_file()


def test_the_vault_holds_the_index_of_the_daily_briefs(tmp_path: Path) -> None:
    """The index of the daily briefs is part of the tree of the spec."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    index = vault / "00-Dashboard" / "Daily Briefs" / "README.md"
    assert index.is_file()
    text = index.read_text(encoding="utf-8")
    # The only day the data holds activity on.
    assert "2026-10-18" in text
    assert "2026-10-19" not in text


def test_a_link_to_an_opportunity_resolves_to_the_note_that_was_written(
    tmp_path: Path,
) -> None:
    """The link names the note, and the note is the file the projection wrote."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    dashboard = (vault / "00-Dashboard" / "Top Opportunities.md").read_text(
        encoding="utf-8"
    )
    targets = re.findall(r"\[\[([^\[\]]+)\]\]", dashboard)
    assert targets
    written = {note.relative_to(vault).as_posix() for note in _opportunity_notes(vault)}
    for target in targets:
        assert f"{target}.md" in written


def test_every_link_in_the_vault_resolves_to_a_note_on_disk(tmp_path: Path) -> None:
    """A link that names a note that is not there is a broken link."""
    db_path = _database(tmp_path)
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    notes = {path.relative_to(vault).as_posix() for path in vault.rglob("*.md")}
    links = 0
    for path in sorted(vault.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"\[\[([^\[\]]+)\]\]", text):
            links += 1
            assert f"{target}.md" in notes, f"{path.name} links to a missing note"
    assert links


def test_two_markets_cannot_share_one_file(tmp_path: Path) -> None:
    db_path = _database(tmp_path)
    conn = connect(db_path)
    conn.execute("UPDATE pain_points SET domain = 'C++' WHERE pain_id = 'pp_1'")
    conn.execute("UPDATE pain_points SET domain = 'C#' WHERE pain_id = 'pp_2'")
    conn.commit()
    conn.close()
    vault = tmp_path / "vault"
    _projector(db_path, vault).project()
    markets = sorted((vault / "02-Research" / "Markets").glob("*.md"))
    assert len(markets) == 2
