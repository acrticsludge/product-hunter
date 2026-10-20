from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from product_hunter.documents import (
    PARSER_VERSION,
    DocumentError,
    chunk_documents,
    load_fetch_documents,
    parse_documents,
)
from product_hunter.fetch import FetchRecord

FIXTURES = Path(__file__).parent / "fixtures"


def make_fetch(
    source_id: str,
    raw: bytes,
    *,
    fetch_id: str = "fetch_test",
    content_type: str = "application/json",
) -> FetchRecord:
    sha = hashlib.sha256(raw).hexdigest()
    return FetchRecord(
        fetch_id=fetch_id,
        source_id=source_id,
        requested_url="https://example.invalid/x",
        final_url="https://example.invalid/x",
        status=200,
        content_type=content_type,
        raw_sha256=sha,
        raw_path=f"data/raw/{source_id}/{sha}.json",
        bytes=len(raw),
        fetched_at="2026-10-20T12:00:00+00:00",
    )


def hn_raw() -> bytes:
    return (FIXTURES / "hn_search.json").read_bytes()


def github_raw() -> bytes:
    return (FIXTURES / "github_issues.json").read_bytes()


def blog_raw() -> bytes:
    return (FIXTURES / "blog.html").read_bytes()


def test_hn_search_hits_become_documents(tmp_path):
    raw = hn_raw()
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )

    assert len(documents) == 5
    pain = documents[0]
    assert pain.author == "dana_dev"
    assert pain.published_at == "2026-03-04T09:15:00Z"
    assert pain.url == "https://news.ycombinator.com/item?id=9000001"
    assert pain.title == "How we keep track of customer invoices"
    assert pain.text.startswith("Every month I export the invoices to CSV")
    assert pain.source_id == "hn_algolia"
    assert pain.parser_version == PARSER_VERSION
    assert pain.content_type == "application/json"
    # The four evaluation shapes the later step needs.
    assert "paste the totals into a spreadsheet by hand" in documents[0].text
    assert "none of these apps are any good" in documents[1].text
    assert documents[2].text.startswith("Is there a way")
    assert documents[3].text.startswith("It would be great if the export")


def test_hn_text_is_html_stripped(tmp_path):
    raw = hn_raw()
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )

    text = documents[4].text
    assert "<p>" not in text
    assert "&quot;" not in text
    assert '"does not export"' in text
    assert text == (
        'Our bookkeeping tool "does not export" anything. The PDF it produces '
        "is unreadable, so I retype every number into the ledger by hand and "
        "hope I do not miss a row."
    )


def test_hn_item_children_are_parsed_recursively(tmp_path):
    payload = {
        "id": 1,
        "type": "story",
        "title": "Root story",
        "author": "pg",
        "created_at": "2006-10-09T18:21:51.000Z",
        "children": [
            {
                "id": 15,
                "type": "comment",
                "author": "sama",
                "created_at": "2006-10-09T19:51:01.000Z",
                "text": "Is there anywhere to eat on Sandhill Road?",
                "children": [
                    {
                        "id": 17,
                        "type": "comment",
                        "author": "pg",
                        "created_at": "2006-10-09T19:52:45.000Z",
                        "text": "the rising star of venture capital eating lunch",
                        "children": [],
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode("utf-8")
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )

    assert len(documents) == 3
    by_id = {doc.url.rsplit("=", 1)[1]: doc for doc in documents}
    assert set(by_id) == {"1", "15", "17"}
    assert by_id["15"].text == "Is there anywhere to eat on Sandhill Road?"
    assert by_id["17"].author == "pg"
    assert by_id["1"].text == "Root story"


def test_github_issue_list_skips_pull_requests(tmp_path):
    raw = github_raw()
    documents = parse_documents(
        make_fetch("github_issues", raw), raw, repo_root=tmp_path
    )

    assert len(documents) == 2
    assert all("pull" not in doc.url for doc in documents)
    issue = documents[0]
    assert issue.url == "https://github.com/example/ledger/issues/6146"
    assert issue.author == "ledger_user"
    assert issue.published_at == "2026-08-31T19:17:12Z"
    assert issue.text == (
        "CSV export truncates at ten thousand rows\n\n"
        "Since the March release the CSV export truncates at ten thousand rows."
        "\n\nI have to split the export by hand and merge it in a spreadsheet, "
        "which takes most of a morning every single month."
    )


def test_github_issue_without_body_keeps_the_title_only(tmp_path):
    raw = github_raw()
    documents = parse_documents(
        make_fetch("github_issues", raw), raw, repo_root=tmp_path
    )

    without_body = documents[1]
    assert without_body.url.endswith("/issues/6148")
    assert without_body.text == "Dark mode is unreadable on the reports page"


def test_github_single_object_is_accepted(tmp_path):
    entry = json.loads(github_raw())[0]
    raw = json.dumps(entry).encode("utf-8")
    documents = parse_documents(
        make_fetch("github_issues", raw), raw, repo_root=tmp_path
    )

    assert len(documents) == 1
    assert documents[0].text.startswith("CSV export truncates at ten thousand rows")


def test_blog_html_drops_script_style_nav_footer_and_noscript(tmp_path):
    raw = blog_raw()
    documents = parse_documents(
        make_fetch("blog_http", raw, content_type="text/html"),
        raw,
        repo_root=tmp_path,
    )

    assert len(documents) == 1
    document = documents[0]
    text = document.text
    assert "do-not-include-this" not in text
    assert "sans-serif" not in text
    assert "Pricing" not in text
    assert "You need JavaScript" not in text
    assert "All rights reserved" not in text
    assert "console.log" not in text
    assert "Every quarter I rebuild the same report by hand" in text
    assert "Most of these tools are the same" in text
    assert document.title == "Small business bookkeeping is still broken | Ledger Notes"


def test_doc_ids_are_deterministic_across_repeated_parses(tmp_path):
    raw = hn_raw()
    first = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path / "a"
    )
    second = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path / "b"
    )

    assert [doc.doc_id for doc in first] == [doc.doc_id for doc in second]
    assert all(doc.doc_id.startswith("doc_") for doc in first)
    assert all(len(doc.doc_id) == len("doc_") + 16 for doc in first)
    assert all(
        all(char in "0123456789abcdef" for char in doc.doc_id[4:]) for doc in first
    )


def test_doc_id_changes_with_the_fetch_record(tmp_path):
    raw = hn_raw()
    first = parse_documents(
        make_fetch("hn_algolia", raw, fetch_id="fetch_one"),
        raw,
        repo_root=tmp_path,
    )
    second = parse_documents(
        make_fetch("hn_algolia", raw, fetch_id="fetch_two"),
        raw,
        repo_root=tmp_path,
    )

    assert first[0].doc_id != second[0].doc_id
    assert first[0].text == second[0].text


def test_raw_hash_mismatch_fails_closed(tmp_path):
    raw = hn_raw()
    fetch = make_fetch("hn_algolia", raw).model_copy(
        update={"raw_sha256": "0" * 64}
    )

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(fetch, raw, repo_root=tmp_path)

    assert excinfo.value.reason_code == "raw_hash_mismatch"
    assert not (tmp_path / "data" / "processed").exists()


def test_unknown_source_fails_closed(tmp_path):
    raw = hn_raw()

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(
            make_fetch("reddit", raw), raw, repo_root=tmp_path
        )

    assert excinfo.value.reason_code == "unknown_source"


def test_malformed_json_fails_with_parse_error(tmp_path):
    raw = b"{not json"

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(make_fetch("hn_algolia", raw), raw, repo_root=tmp_path)

    assert excinfo.value.reason_code == "parse_error"


def test_empty_input_fails_with_empty_document(tmp_path):
    raw = json.dumps({"hits": []}).encode("utf-8")

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(make_fetch("hn_algolia", raw), raw, repo_root=tmp_path)

    assert excinfo.value.reason_code == "empty_document"


def test_blog_without_text_fails_with_empty_document(tmp_path):
    raw = b"<html><body><script>var x = 1;</script></body></html>"

    with pytest.raises(DocumentError) as excinfo:
        parse_documents(
            make_fetch("blog_http", raw, content_type="text/html"),
            raw,
            repo_root=tmp_path,
        )

    assert excinfo.value.reason_code == "empty_document"


def test_text_file_is_content_addressed(tmp_path):
    raw = hn_raw()
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )

    for document in documents:
        assert document.text_path == (
            f"data/processed/text/{document.text_sha256}.txt"
        )
        stored = tmp_path / document.text_path
        assert stored.read_text(encoding="utf-8") == document.text
        assert (
            hashlib.sha256(document.text.encode("utf-8")).hexdigest()
            == document.text_sha256
        )


def test_identical_text_is_stored_once(tmp_path):
    payload = [
        {
            "id": 1,
            "title": "Same title",
            "html_url": "https://github.com/example/ledger/issues/1",
            "user": {"login": "a"},
            "created_at": "2026-09-01T00:00:00Z",
            "body": "Same body",
        },
        {
            "id": 2,
            "title": "Same title",
            "html_url": "https://github.com/example/ledger/issues/2",
            "user": {"login": "b"},
            "created_at": "2026-09-02T00:00:00Z",
            "body": "Same body",
        },
    ]
    raw = json.dumps(payload).encode("utf-8")
    documents = parse_documents(
        make_fetch("github_issues", raw), raw, repo_root=tmp_path
    )

    assert documents[0].text_path == documents[1].text_path
    assert documents[0].doc_id != documents[1].doc_id
    stored = list((tmp_path / "data" / "processed" / "text").iterdir())
    assert len(stored) == 1


def test_load_fetch_documents_reads_the_stored_raw_file(tmp_path):
    raw = hn_raw()
    fetch = make_fetch("hn_algolia", raw)
    target = tmp_path / fetch.raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)

    documents = load_fetch_documents(fetch, tmp_path)

    assert len(documents) == 5
    assert documents[0].raw_path == fetch.raw_path
    assert documents[0].raw_sha256 == fetch.raw_sha256


def test_load_fetch_documents_rejects_a_missing_raw_file(tmp_path):
    raw = hn_raw()
    fetch = make_fetch("hn_algolia", raw)

    with pytest.raises(DocumentError) as excinfo:
        load_fetch_documents(fetch, tmp_path)

    assert excinfo.value.reason_code == "raw_missing"


def test_load_fetch_documents_verifies_the_hash(tmp_path):
    raw = hn_raw()
    fetch = make_fetch("hn_algolia", raw)
    target = tmp_path / fetch.raw_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"tampered")

    with pytest.raises(DocumentError) as excinfo:
        load_fetch_documents(fetch, tmp_path)

    assert excinfo.value.reason_code == "raw_hash_mismatch"


def test_chunk_offsets_are_exact_slices(tmp_path):
    raw = blog_raw()
    documents = parse_documents(
        make_fetch("blog_http", raw, content_type="text/html"),
        raw,
        repo_root=tmp_path,
    )
    document = documents[0]

    chunks = chunk_documents(documents, 200, 0)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.doc_id == document.doc_id
        assert chunk.text == document.text[chunk.char_start : chunk.char_end]
        assert 0 <= chunk.char_start < chunk.char_end <= len(document.text)
        assert len(chunk.text) <= 200
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(document.text)


def test_chunk_overlap_repeats_the_tail_of_the_previous_chunk(tmp_path):
    raw = blog_raw()
    documents = parse_documents(
        make_fetch("blog_http", raw, content_type="text/html"),
        raw,
        repo_root=tmp_path,
    )
    document = documents[0]

    chunks = chunk_documents(documents, 200, 40)

    assert len(chunks) > 1
    overlaps = 0
    for previous, following in zip(chunks, chunks[1:]):
        assert following.char_start > previous.char_start
        gap = previous.char_end - following.char_start
        assert 0 <= gap <= 40
        if gap:
            overlaps += 1
            # The tail of the previous chunk opens the next one.
            assert following.text[:gap] == previous.text[-gap:]
    assert overlaps > 0
    for chunk in chunks:
        assert len(chunk.text) <= 200
        assert chunk.text == document.text[chunk.char_start : chunk.char_end]


def test_chunk_never_exceeds_the_limit_without_a_paragraph_break(tmp_path):
    raw = hn_raw()
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )
    document = documents[0]
    assert "\n" not in document.text

    chunks = chunk_documents([document], 50, 10)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.text) <= 50
        assert chunk.text == document.text[chunk.char_start : chunk.char_end]
    assert chunks[-1].char_end == len(document.text)


def test_empty_documents_produce_no_chunks(tmp_path):
    raw = hn_raw()
    documents = parse_documents(
        make_fetch("hn_algolia", raw), raw, repo_root=tmp_path
    )
    blank = documents[0].model_copy(update={"text": ""})

    assert chunk_documents([blank], 100, 10) == []
