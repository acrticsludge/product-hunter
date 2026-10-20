from __future__ import annotations

import pytest

from product_hunter.quote import (
    QuoteError,
    QuoteNotFound,
    QuoteTooLong,
    QuoteTooShort,
    SpanMatch,
    canonicalize,
    locate_quote,
)

PAIN = (
    "Every month I export the invoices to CSV and paste the totals into a "
    "spreadsheet by hand. It takes me a whole Saturday."
)
PAIN_QUOTE = "paste the totals into a spreadsheet by hand"


def test_exact_match_locates_the_span():
    match = locate_quote(PAIN, PAIN_QUOTE)

    assert isinstance(match, SpanMatch)
    assert match.match_mode == "exact"
    assert match.quote == PAIN_QUOTE
    assert match.model_quote == PAIN_QUOTE
    assert PAIN[match.char_start : match.char_end] == PAIN_QUOTE
    assert match.char_start == PAIN.index(PAIN_QUOTE)
    assert match.char_end == match.char_start + len(PAIN_QUOTE)
    assert match.occurrence == 1


def test_curly_quote_matches_canonically():
    text = "I do not trust the tool’s numbers when the report is late."
    quote = "I do not trust the tool's numbers when the report is late."

    match = locate_quote(text, quote)

    assert match.match_mode == "canonical"
    assert match.char_start == 0
    assert match.char_end == len(text)
    # The stored quote is the original substring, curly character and all.
    assert match.quote == text
    assert "’" in match.quote
    assert match.quote != quote


def test_em_dash_matches_canonically():
    text = "The monthly close takes two days of work — and I do it by hand."
    quote = "The monthly close takes two days of work - and I do it by hand."

    match = locate_quote(text, quote)

    assert match.match_mode == "canonical"
    assert match.quote == text
    assert "—" in match.quote


def test_non_breaking_space_matches_canonically():
    text = "The outstanding\u00a0amount never matches the bank statement."
    quote = "The outstanding amount never matches the bank statement."

    match = locate_quote(text, quote)

    assert match.match_mode == "canonical"
    assert match.quote == text
    assert "\u00a0" in match.quote


def test_tab_and_space_match_canonically():
    text = "The export writes one column\tand drops the rest of the rows."
    quote = "The export writes one column and drops the rest of the rows."

    match = locate_quote(text, quote)

    assert match.match_mode == "canonical"
    assert match.quote == text
    assert "\t" in match.quote


def test_canonicalize_keeps_the_length():
    text = "The tool’s report—\ttotal\u00a0is wrong"

    assert len(canonicalize(text)) == len(text)
    assert canonicalize(text) == "The tool's report- total is wrong"


def test_paraphrase_never_passes():
    with pytest.raises(QuoteNotFound) as excinfo:
        locate_quote(PAIN, "I manually put the totals into a spreadsheet")

    assert excinfo.value.reason_code == "quote_not_found"
    assert isinstance(excinfo.value, QuoteError)


def test_absent_quote_fails():
    with pytest.raises(QuoteNotFound):
        locate_quote(PAIN, "the totals are reconciled with the bank statement")


def test_short_quote_fails():
    with pytest.raises(QuoteTooShort) as excinfo:
        locate_quote(PAIN, "too short")

    assert excinfo.value.reason_code == "quote_too_short"


def test_empty_quote_fails():
    with pytest.raises(QuoteTooShort):
        locate_quote(PAIN, "   ")


def test_long_quote_fails():
    with pytest.raises(QuoteTooLong) as excinfo:
        locate_quote(PAIN, "x" * 2001)

    assert excinfo.value.reason_code == "quote_too_long"


def test_bounds_are_configurable():
    with pytest.raises(QuoteTooShort):
        locate_quote(PAIN, "paste", min_chars=20, max_chars=2000)
    with pytest.raises(QuoteTooLong):
        locate_quote(PAIN, "paste the totals", min_chars=5, max_chars=10)


def test_matching_is_case_sensitive():
    with pytest.raises(QuoteNotFound):
        locate_quote(PAIN, "Paste the totals into a spreadsheet by hand")


def test_repeated_quote_records_the_occurrence():
    text = (
        "The report is late every week. The report is late every week. "
        "The report is late every week."
    )
    quote = "The report is late every week"

    first = locate_quote(text, quote)
    assert first.occurrence == 1
    assert first.char_start == 0

    second = locate_quote(text, quote, search_start=first.char_end)
    assert second.occurrence == 2
    assert second.char_start == text.index(quote, first.char_end)
    assert second.char_start > first.char_end
    assert text[second.char_start : second.char_end] == quote


def test_single_occurrence_is_recorded_as_one():
    match = locate_quote(PAIN, PAIN_QUOTE)

    assert match.occurrence == 1


def test_search_start_falls_back_to_the_first_occurrence():
    match = locate_quote(PAIN, PAIN_QUOTE, search_start=len(PAIN))

    assert match.match_mode == "exact"
    assert match.char_start == PAIN.index(PAIN_QUOTE)
    assert match.occurrence == 1
