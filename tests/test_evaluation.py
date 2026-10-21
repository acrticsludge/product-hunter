from __future__ import annotations

import csv
import socket
from pathlib import Path

import pytest

from product_hunter.evaluation import (
    COLUMNS,
    EvalReport,
    ReviewRow,
    evaluate,
    read_review_sheet,
    render_markdown,
    write_report,
    write_review_sheet,
)
from product_hunter.extraction import ExtractedCandidate, PainValidity

HEADER = [
    "candidate_id",
    "pain_id",
    "doc_id",
    "source_id",
    "quote",
    "char_start",
    "char_end",
    "match_mode",
    "verbatim_ok",
    "model_validity",
    "model_reason",
    "model_severity",
    "model_frequency",
    "model_confidence",
    "model_domain",
    "model_normalized_key",
    "human_valid_pain",
    "human_severity",
    "human_frequency",
    "human_normalization_ok",
    "human_normalized_key",
    "notes",
]

# The header of a sheet filled before the normalization columns existed.
OLD_HEADER = [
    name
    for name in HEADER
    if name
    not in (
        "model_domain",
        "model_normalized_key",
        "human_normalization_ok",
        "human_normalized_key",
    )
]


def make_candidate(
    candidate_id: str,
    *,
    source_id: str = "hn_algolia",
    doc_id: str = "doc_1",
    validity: str = "genuine",
    reason: str = "time_cost",
    severity: int = 3,
    frequency: str = "monthly",
    confidence: float = 0.9,
    match_mode: str = "exact",
    accepted: bool = True,
) -> ExtractedCandidate:
    """A stored candidate, as the extractor would have written it."""
    quote = f"the team pastes the totals by hand every month ({candidate_id})"
    return ExtractedCandidate(
        candidate_id=candidate_id,
        pain_id=f"pp_{candidate_id}" if accepted else None,
        doc_id=doc_id,
        fetch_id="fetch_1",
        source_id=source_id,
        url="https://example.invalid/item/1",
        span_id=f"ev_{candidate_id}",
        quote=quote,
        char_start=10,
        char_end=10 + len(quote),
        match_mode=match_mode,
        model_quote=quote,
        occurrence=1,
        pain_validity=validity,
        reason=reason,
        pain_statement="the team pastes the totals by hand",
        severity=severity,
        frequency=frequency,
        confidence=confidence,
        rationale="the text says the totals are pasted by hand",
        domain="finance" if accepted else None,
        actor="the bookkeeping team" if accepted else None,
        action="paste invoice totals by hand" if accepted else None,
        object="invoice totals" if accepted else None,
        consequence="a Saturday of manual work every month" if accepted else None,
        normalized_key=(
            "finance:paste-invoice-totals-by-hand:invoice-totals" if accepted else None
        ),
        normalization_version="norm-v1",
        accepted=accepted,
        provider="fake",
        model="fake-model",
        extractor_version="phase1-0.2",
        prompt_version="extract-v2",
        raw_path="data/raw/hn_algolia/abc123.json",
        raw_sha256="a" * 64,
        text_sha256="b" * 64,
        created_at="2026-10-20T12:00:00+00:00",
    )


def sample_candidates() -> list[ExtractedCandidate]:
    """Three accepted candidates and three the model did not accept."""
    return [
        make_candidate("cand_a1", source_id="hn_algolia", doc_id="doc_1"),
        make_candidate("cand_a2", source_id="hn_algolia", doc_id="doc_2"),
        make_candidate("cand_a3", source_id="reddit", doc_id="doc_3"),
        make_candidate(
            "cand_w1",
            source_id="hn_algolia",
            doc_id="doc_4",
            validity="weak",
            reason="incidental",
            severity=1,
            accepted=False,
        ),
        make_candidate(
            "cand_n1",
            source_id="hn_algolia",
            doc_id="doc_5",
            validity="not_pain",
            reason="negative_opinion",
            severity=0,
            accepted=False,
        ),
        make_candidate(
            "cand_n2",
            source_id="reddit",
            doc_id="doc_6",
            validity="not_pain",
            reason="question",
            severity=0,
            accepted=False,
        ),
    ]


def make_row(**changes: object) -> ReviewRow:
    """A review row with the model's verdict, ready to be labelled."""
    values: dict[str, object] = {
        "candidate_id": "cand_1",
        "pain_id": None,
        "doc_id": "doc_1",
        "source_id": "hn_algolia",
        "quote": "the team pastes the totals by hand every month",
        "char_start": 10,
        "char_end": 52,
        "match_mode": "exact",
        "verbatim_ok": True,
        "model_validity": "genuine",
        "model_reason": "time_cost",
        "model_severity": 3,
        "model_frequency": "monthly",
        "model_confidence": 0.9,
        "human_valid_pain": None,
        "human_severity": None,
        "human_frequency": None,
        "notes": "",
    }
    values.update(changes)
    return ReviewRow(**values)


def filled_row(**changes: object) -> dict[str, object]:
    """One CSV row with every column filled in."""
    values: dict[str, object] = {
        "candidate_id": "cand_1",
        "pain_id": "pp_cand_1",
        "doc_id": "doc_1",
        "source_id": "hn_algolia",
        "quote": "the team pastes the totals by hand every month",
        "char_start": "10",
        "char_end": "52",
        "match_mode": "exact",
        "verbatim_ok": "true",
        "model_validity": "genuine",
        "model_reason": "time_cost",
        "model_severity": "3",
        "model_frequency": "monthly",
        "model_confidence": "0.9",
        "model_domain": "finance",
        "model_normalized_key": "finance:paste-invoice-totals-by-hand:invoice-totals",
        "human_valid_pain": "",
        "human_severity": "",
        "human_frequency": "",
        "human_normalization_ok": "",
        "human_normalized_key": "",
        "notes": "",
    }
    values.update(changes)
    return values


def write_csv(path: Path, rows: list[dict[str, object]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


# --- the review sheet ------------------------------------------------------


def test_sheet_holds_accepted_and_non_accepted_candidates(tmp_path):
    path = tmp_path / "review.csv"
    candidates = sample_candidates()

    count = write_review_sheet(candidates, path)

    assert count == 6
    rows = read_review_sheet(path)
    assert len(rows) == 6
    validities = {row.model_validity for row in rows}
    assert PainValidity.genuine in validities
    assert PainValidity.weak in validities
    assert PainValidity.not_pain in validities
    # The accepted candidates are all in the sheet.
    accepted = {c.candidate_id for c in candidates if c.accepted}
    assert accepted <= {row.candidate_id for row in rows}


def test_sheet_respects_the_limit(tmp_path):
    path = tmp_path / "review.csv"
    candidates = sample_candidates()

    count = write_review_sheet(candidates, path, limit=3)

    assert count == 3
    rows = read_review_sheet(path)
    assert len(rows) == 3
    # The accepted candidates take the room first, so all three are here.
    genuine = [row for row in rows if row.model_validity is PainValidity.genuine]
    assert len(genuine) == 3


def test_sheet_keeps_an_accepted_candidate_at_a_small_limit(tmp_path):
    """A limit of one must not drop the accepted candidate."""
    path = tmp_path / "review.csv"
    candidates = [
        make_candidate("cand_a1"),
        make_candidate(
            "cand_n1",
            validity="not_pain",
            reason="negative_opinion",
            severity=0,
            accepted=False,
        ),
    ]

    count = write_review_sheet(candidates, path, limit=1)

    assert count == 1
    rows = read_review_sheet(path)
    assert [row.candidate_id for row in rows] == ["cand_a1"]
    assert rows[0].model_validity is PainValidity.genuine


def test_sheet_caps_the_accepted_candidates_at_the_limit(tmp_path):
    """Accepted candidates over the limit are left out, not squeezed in."""
    path = tmp_path / "review.csv"
    candidates = [make_candidate("cand_a1"), make_candidate("cand_a2")]

    count = write_review_sheet(candidates, path, limit=1)

    assert count == 1
    assert [row.candidate_id for row in read_review_sheet(path)] == ["cand_a1"]


def test_sheet_never_exceeds_the_limit(tmp_path):
    path = tmp_path / "review.csv"
    candidates = sample_candidates()

    for limit in (0, 1, 2, 4, 5, 6, 20):
        count = write_review_sheet(candidates, path, limit=limit)
        assert count == min(limit, len(candidates))
        assert len(read_review_sheet(path)) == count


def test_sheet_leaves_the_human_columns_blank(tmp_path):
    path = tmp_path / "review.csv"

    write_review_sheet(sample_candidates(), path)

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert rows
    for row in rows:
        assert row["human_valid_pain"] == ""
        assert row["human_severity"] == ""
        assert row["human_frequency"] == ""
        assert row["human_normalization_ok"] == ""
        assert row["human_normalized_key"] == ""
        assert row["notes"] == ""


def test_sheet_header_is_exact(tmp_path):
    path = tmp_path / "review.csv"

    write_review_sheet(sample_candidates(), path)

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == HEADER
    assert list(COLUMNS) == HEADER


def test_sheet_order_is_deterministic(tmp_path):
    candidates = sample_candidates()
    first = tmp_path / "one.csv"
    second = tmp_path / "two.csv"

    write_review_sheet(candidates, first)
    write_review_sheet(list(reversed(candidates)), second)

    order = [row.candidate_id for row in read_review_sheet(first)]
    assert order == [
        "cand_a1",
        "cand_a2",
        "cand_a3",
        "cand_w1",
        "cand_n1",
        "cand_n2",
    ]
    assert [row.candidate_id for row in read_review_sheet(second)] == order


def test_sheet_carries_the_model_verdict(tmp_path):
    path = tmp_path / "review.csv"

    write_review_sheet(sample_candidates(), path)

    rows = {row.candidate_id: row for row in read_review_sheet(path)}
    assert rows["cand_a1"].model_validity is PainValidity.genuine
    assert rows["cand_a1"].model_reason.value == "time_cost"
    assert rows["cand_a1"].model_severity == 3
    assert rows["cand_a1"].model_frequency.value == "monthly"
    assert rows["cand_a1"].model_confidence == 0.9
    assert rows["cand_a1"].verbatim_ok is True
    assert rows["cand_a1"].pain_id == "pp_cand_a1"
    assert rows["cand_n1"].pain_id is None
    # The model key travels with the row.
    assert rows["cand_a1"].model_domain == "finance"
    assert (
        rows["cand_a1"].model_normalized_key
        == "finance:paste-invoice-totals-by-hand:invoice-totals"
    )
    assert rows["cand_n1"].model_domain is None
    assert rows["cand_n1"].model_normalized_key is None


# --- reading a filled sheet ------------------------------------------------


def test_read_review_sheet_parses_the_human_columns(tmp_path):
    path = write_csv(
        tmp_path / "filled.csv",
        [
            filled_row(
                candidate_id="cand_1",
                human_valid_pain="yes",
                human_severity="3",
                human_frequency="monthly",
            ),
            filled_row(
                candidate_id="cand_2",
                human_valid_pain="no",
                human_severity="",
                human_frequency="",
            ),
            filled_row(
                candidate_id="cand_3",
                human_valid_pain="weak",
                human_severity="2",
                human_frequency="weekly",
                notes="borderline",
            ),
        ],
    )

    rows = read_review_sheet(path)

    assert len(rows) == 3
    assert rows[0].human_valid_pain == "yes"
    assert rows[0].human_severity == 3
    assert rows[0].human_frequency == "monthly"
    assert rows[1].human_valid_pain == "no"
    assert rows[1].human_severity is None
    assert rows[1].human_frequency is None
    assert rows[2].human_valid_pain == "weak"
    assert rows[2].human_severity == 2
    assert rows[2].human_frequency == "weekly"
    assert rows[2].notes == "borderline"


def test_blank_human_labels_read_as_none(tmp_path):
    path = tmp_path / "blank.csv"
    write_review_sheet(sample_candidates(), path)

    rows = read_review_sheet(path)

    assert rows
    for row in rows:
        assert row.human_valid_pain is None
        assert row.human_severity is None
        assert row.human_frequency is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("human_valid_pain", "maybe"),
        ("human_valid_pain", "true"),
        ("human_severity", "7"),
        ("human_severity", "abc"),
        ("human_frequency", "yearly"),
        ("human_normalization_ok", "maybe"),
        ("human_normalization_ok", "true"),
        ("model_validity", "bogus"),
        ("model_reason", "bogus"),
        ("model_frequency", "yearly"),
        ("match_mode", "fuzzy"),
        ("verbatim_ok", "maybe"),
    ],
)
def test_malformed_values_are_refused(tmp_path, field, value):
    path = write_csv(tmp_path / "bad.csv", [filled_row(**{field: value})])

    with pytest.raises(ValueError) as excinfo:
        read_review_sheet(path)

    assert field in str(excinfo.value)
    assert "row 1" in str(excinfo.value)


def test_a_missing_column_is_refused(tmp_path):
    path = tmp_path / "short.csv"
    row = {key: value for key, value in filled_row().items() if key != "notes"}
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HEADER[:-1])
        writer.writeheader()
        writer.writerow(row)

    with pytest.raises(ValueError) as excinfo:
        read_review_sheet(path)

    assert "notes" in str(excinfo.value)


# --- the normalization columns ---------------------------------------------


def test_read_review_sheet_parses_the_normalization_columns(tmp_path):
    path = write_csv(
        tmp_path / "normalized.csv",
        [
            filled_row(
                candidate_id="cand_1",
                human_normalization_ok="yes",
                human_normalized_key="finance:paste:totals",
            ),
            filled_row(
                candidate_id="cand_2",
                human_normalization_ok="no",
                human_normalized_key="  finance:copy:totals  ",
            ),
        ],
    )

    rows = read_review_sheet(path)

    assert rows[0].human_normalization_ok == "yes"
    assert rows[0].human_normalized_key == "finance:paste:totals"
    assert rows[1].human_normalization_ok == "no"
    # The key is stripped, so a stray space cannot make two keys differ.
    assert rows[1].human_normalized_key == "finance:copy:totals"


def test_a_sheet_without_the_normalization_columns_still_reads(tmp_path):
    """A sheet filled before the normalization columns existed still reads."""
    path = tmp_path / "old.csv"
    row = {
        key: value
        for key, value in filled_row(human_valid_pain="yes").items()
        if key in OLD_HEADER
    }
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OLD_HEADER)
        writer.writeheader()
        writer.writerow(row)

    rows = read_review_sheet(path)

    assert len(rows) == 1
    assert rows[0].human_valid_pain == "yes"
    assert rows[0].model_domain is None
    assert rows[0].model_normalized_key is None
    assert rows[0].human_normalization_ok is None
    assert rows[0].human_normalized_key is None


def test_a_malformed_normalization_label_is_refused(tmp_path):
    path = write_csv(
        tmp_path / "bad.csv", [filled_row(human_normalization_ok="maybe")]
    )

    with pytest.raises(ValueError) as excinfo:
        read_review_sheet(path)

    assert "human_normalization_ok" in str(excinfo.value)
    assert "yes, no" in str(excinfo.value)
    assert "row 1" in str(excinfo.value)


def normalization_rows() -> list[ReviewRow]:
    """Rows with known normalization labels and keys."""
    return [
        make_row(
            candidate_id="cand_1",
            model_validity="genuine",
            human_valid_pain="yes",
            model_normalized_key="finance:paste:totals",
            human_normalization_ok="yes",
            human_normalized_key="finance:paste:totals",
        ),
        make_row(
            candidate_id="cand_2",
            model_validity="genuine",
            human_valid_pain="yes",
            model_normalized_key="finance:copy:totals",
            human_normalization_ok="no",
            human_normalized_key="finance:copy:totals",
        ),
        make_row(
            candidate_id="cand_3",
            model_validity="genuine",
            human_valid_pain="yes",
            model_normalized_key="finance:paste:totals",
            human_normalization_ok="no",
            human_normalized_key="finance:paste-invoice-totals",
        ),
        make_row(
            candidate_id="cand_4",
            model_validity="genuine",
            human_valid_pain="yes",
            model_normalized_key="hr:rekey:staff",
            human_normalization_ok="",
            human_normalized_key="hr:rekey:staff",
        ),
        make_row(
            candidate_id="cand_5",
            model_validity="genuine",
            human_valid_pain="no",
            human_normalization_ok="yes",
        ),
        make_row(
            candidate_id="cand_6",
            model_validity="not_pain",
            model_reason="informational",
            model_severity=0,
            human_valid_pain="yes",
            human_normalization_ok="yes",
        ),
    ]


def test_evaluate_counts_the_normalization_labels():
    report = evaluate(normalization_rows())

    # Only the genuine pain points the human confirmed carry a label.
    assert report.normalization_n == 3
    assert report.normalization_ok == 1
    assert report.normalization_rate == pytest.approx(1 / 3)


def test_evaluate_counts_the_key_agreement():
    report = evaluate(normalization_rows())

    assert report.normalized_key_n == 4
    assert report.normalized_key_exact == 3
    assert report.normalized_key_agreement == pytest.approx(3 / 4)


def test_blank_normalization_labels_leave_the_rates_at_none():
    report = evaluate([make_row(candidate_id="cand_1", human_valid_pain="yes")])

    assert report.normalization_n == 0
    assert report.normalization_ok == 0
    assert report.normalization_rate is None
    assert report.normalized_key_n == 0
    assert report.normalized_key_exact == 0
    assert report.normalized_key_agreement is None


def test_a_row_without_a_model_key_is_left_out_of_the_key_count():
    """A weak or not_pain row has no model key, so it cannot disagree."""
    rows = [
        make_row(
            candidate_id="cand_1",
            model_validity="genuine",
            human_valid_pain="yes",
            model_normalized_key="finance:paste:totals",
            human_normalized_key="finance:paste:totals",
        ),
        make_row(
            candidate_id="cand_2",
            model_validity="weak",
            model_reason="incidental",
            model_severity=1,
            human_valid_pain="no",
            model_normalized_key=None,
            human_normalized_key="finance:paste:totals",
        ),
    ]

    report = evaluate(rows)

    # The weak row carries a human key but no model key, so it is not
    # counted and it is not a disagreement.
    assert report.normalized_key_n == 1
    assert report.normalized_key_exact == 1
    assert report.normalized_key_agreement == pytest.approx(1.0)


# --- the report ------------------------------------------------------------


def reviewed_rows() -> list[ReviewRow]:
    """Ten rows with labels, so every metric has a known value."""
    return [
        make_row(
            candidate_id="cand_1",
            source_id="hn_algolia",
            model_validity="genuine",
            model_reason="time_cost",
            model_severity=3,
            human_valid_pain="yes",
            human_severity=3,
        ),
        make_row(
            candidate_id="cand_2",
            source_id="hn_algolia",
            model_validity="genuine",
            model_reason="time_cost",
            model_severity=3,
            match_mode="canonical",
            human_valid_pain="yes",
            human_severity=4,
        ),
        make_row(
            candidate_id="cand_3",
            source_id="hn_algolia",
            model_validity="genuine",
            model_reason="recurring_manual_work",
            model_severity=3,
            human_valid_pain="yes",
            human_severity=5,
        ),
        make_row(
            candidate_id="cand_4",
            source_id="hn_algolia",
            model_validity="genuine",
            model_reason="negative_opinion",
            model_severity=2,
            human_valid_pain="no",
        ),
        make_row(
            candidate_id="cand_5",
            source_id="hn_algolia",
            model_validity="genuine",
            model_reason="question",
            model_severity=2,
            match_mode="canonical",
            human_valid_pain="weak",
        ),
        make_row(
            candidate_id="cand_6",
            source_id="reddit",
            model_validity="not_pain",
            model_reason="informational",
            model_severity=0,
            human_valid_pain="yes",
        ),
        make_row(
            candidate_id="cand_7",
            source_id="reddit",
            model_validity="weak",
            model_reason="incidental",
            model_severity=1,
            human_valid_pain="no",
        ),
        make_row(
            candidate_id="cand_8",
            source_id="reddit",
            model_validity="genuine",
            model_reason="time_cost",
            model_severity=4,
            human_valid_pain="yes",
        ),
        make_row(
            candidate_id="cand_9",
            source_id="blog_http",
            model_validity="genuine",
            model_reason="time_cost",
            model_severity=3,
            verbatim_ok=False,
        ),
        make_row(
            candidate_id="cand_10",
            source_id="blog_http",
            model_validity="not_pain",
            model_reason="incidental",
            model_severity=0,
            match_mode="canonical",
        ),
    ]


def test_evaluate_counts_the_confusion_matrix():
    report = evaluate(reviewed_rows())

    assert report.total == 10
    assert report.labeled == 8
    assert report.coverage == pytest.approx(0.8)
    assert report.model_genuine == 6
    assert report.true_positive == 4
    assert report.false_positive == 2
    assert report.false_negative == 1
    assert report.weak == 1
    assert report.precision == pytest.approx(4 / 6)
    assert report.recall == pytest.approx(4 / 5)


def test_evaluate_counts_severity_agreement():
    report = evaluate(reviewed_rows())

    assert report.severity_n == 3
    assert report.severity_exact == 1
    assert report.severity_within_one == 2


def test_evaluate_counts_the_false_positive_reasons():
    report = evaluate(reviewed_rows())

    assert report.false_positive_reasons == {"negative_opinion": 1, "question": 1}
    assert sum(report.false_positive_reasons.values()) == report.false_positive


def test_evaluate_splits_by_source():
    report = evaluate(reviewed_rows())

    assert set(report.per_source) == {"hn_algolia", "reddit", "blog_http"}
    hn = report.per_source["hn_algolia"]
    assert hn["total"] == 5
    assert hn["labeled"] == 5
    assert hn["tp"] == 3
    assert hn["fp"] == 2
    assert hn["precision"] == pytest.approx(3 / 5)
    reddit = report.per_source["reddit"]
    assert reddit["total"] == 3
    assert reddit["labeled"] == 3
    assert reddit["tp"] == 1
    assert reddit["fp"] == 0
    assert reddit["precision"] == pytest.approx(1.0)
    blog = report.per_source["blog_http"]
    assert blog["total"] == 2
    assert blog["labeled"] == 0
    # No denominator, so no precision.
    assert blog["precision"] is None


def test_evaluate_counts_the_quote_metrics():
    report = evaluate(reviewed_rows())

    assert report.quote_exact == 7
    assert report.quote_canonical == 3
    # One stored row failed the verbatim check.
    assert report.quote_failed == 1
    assert report.quote_ok_rate == pytest.approx(10 / 11)


def test_evaluate_adds_the_rejected_quotes_to_the_failures():
    report = evaluate(reviewed_rows(), quote_failures=2)

    assert report.quote_failed == 3
    assert report.quote_ok_rate == pytest.approx(10 / 13)


def test_evaluate_with_no_labels_divides_by_nothing():
    rows = [make_row(candidate_id=f"cand_{n}") for n in range(3)]

    report = evaluate(rows)

    assert report.total == 3
    assert report.labeled == 0
    assert report.coverage == 0.0
    assert report.model_genuine == 0
    assert report.true_positive == 0
    assert report.false_positive == 0
    assert report.false_negative == 0
    assert report.precision is None
    assert report.recall is None
    assert report.severity_n == 0
    assert report.false_positive_reasons == {}
    assert report.normalization_n == 0
    assert report.normalization_rate is None
    assert report.normalized_key_n == 0
    assert report.normalized_key_agreement is None


def test_evaluate_with_no_rows_at_all():
    report = evaluate([])

    assert report.total == 0
    assert report.labeled == 0
    assert report.coverage == 0.0
    assert report.quote_ok_rate is None
    assert report.precision is None
    assert report.recall is None
    assert report.per_source == {}


def test_the_report_states_the_recall_limitation():
    report = evaluate(reviewed_rows())

    assert "not to the whole corpus" in report.notes
    assert "candidates the model returned" in report.notes


# --- the markdown report ---------------------------------------------------


def test_render_markdown_carries_the_metrics_and_the_limitation():
    report = evaluate(reviewed_rows())

    text = render_markdown(report)

    assert "# Extraction evaluation" in text
    assert "The sheet holds 10 candidates" in text
    assert "## Metrics" in text
    assert "## Severity" in text
    assert "## Normalization" in text
    assert "## False-positive reasons" in text
    assert "## Per source" in text
    assert "## Limitations" in text
    # The metrics are in the table.
    assert "| Precision | 0.667 |" in text
    assert "| Recall | 0.800 |" in text
    assert "| Candidates in the sheet | 10 |" in text
    assert "| True positives | 4 |" in text
    assert "| False positives | 2 |" in text
    assert "| Coverage | 80.0% |" in text
    # The severity table.
    assert "| Compared | 3 |" in text
    assert "| Exact match | 1 |" in text
    assert "| Within one point | 2 |" in text
    # The false-positive reasons.
    assert "| negative_opinion | 1 |" in text
    assert "| question | 1 |" in text
    # The per-source table.
    assert "| hn_algolia | 5 | 5 | 3 | 2 | 0.600 |" in text
    # The recall limitation.
    assert "not to the whole corpus" in text
    assert report.notes in text


def test_render_markdown_handles_an_empty_report():
    report = evaluate([])

    text = render_markdown(report)

    assert "| Precision | n/a |" in text
    assert "| Recall | n/a |" in text
    assert "No false positives were recorded." in text
    assert "No source was reviewed." in text
    # An unlabelled sheet gets no made-up normalization rate.
    assert "not labelled" in text
    assert "| Normalization rate | n/a |" in text
    assert "| Key agreement | n/a |" in text


def test_render_markdown_shows_the_normalization_metrics():
    report = evaluate(normalization_rows())

    text = render_markdown(report)

    assert "| Rows compared | 3 |" in text
    assert "| Normalized correctly | 1 |" in text
    assert "| Normalization rate | 33.3% |" in text
    assert "| Keys compared | 4 |" in text
    assert "| Keys identical | 3 |" in text
    assert "| Key agreement | 75.0% |" in text
    assert "not labelled" not in text


def test_write_report_creates_the_parent_and_writes_the_markdown(tmp_path):
    report = evaluate(reviewed_rows())
    path = tmp_path / "reports" / "phase-1-acceptance.md"

    write_report(report, path)

    assert path.is_file()
    written = path.read_text(encoding="utf-8")
    assert written == render_markdown(report)
    assert "## Limitations" in written


# --- boundaries ------------------------------------------------------------


def test_evaluation_never_opens_a_socket(monkeypatch, tmp_path):
    def explode(*args, **kwargs):
        raise AssertionError("the evaluation step must not open a socket")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    path = tmp_path / "review.csv"
    candidates = sample_candidates()
    write_review_sheet(candidates, path)
    rows = read_review_sheet(path)
    report = evaluate(rows)
    render_markdown(report)
    assert path.is_file()


def test_the_report_model_takes_the_numbers_as_given():
    report = EvalReport(
        total=1,
        labeled=1,
        coverage=1.0,
        quote_exact=1,
        quote_canonical=0,
        quote_failed=0,
        quote_ok_rate=1.0,
        model_genuine=1,
        true_positive=1,
        false_positive=0,
        false_negative=0,
        weak=0,
        precision=1.0,
        recall=1.0,
        severity_n=0,
        severity_exact=0,
        severity_within_one=0,
        normalization_n=2,
        normalization_ok=1,
        normalization_rate=0.5,
        normalized_key_n=2,
        normalized_key_exact=1,
        normalized_key_agreement=0.5,
        false_positive_reasons={},
        per_source={},
        notes="a note",
    )

    assert report.total == 1
    assert report.false_positive_reasons == {}
    assert report.normalization_rate == 0.5
    assert report.normalized_key_agreement == 0.5
