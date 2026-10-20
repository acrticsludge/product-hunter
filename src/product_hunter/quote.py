"""Locate a model-supplied quote inside a document text.

The model returns a quote, never offsets. This module turns that quote into a
span: an exact substring match first, then a conservative canonical match
(curly quotes, dashes, and whitespace variants only). Paraphrase never passes.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

MatchMode = Literal["exact", "canonical"]

# Curly quotes and dashes fold to their ASCII twin. Every replacement is one
# character wide, so the mapping never shifts an offset.
_CHAR_MAP = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2013": "-",
        "\u2014": "-",
    }
)
_WHITESPACE = re.compile(r"\s")


class SpanMatch(BaseModel):
    """One located quote and the span it occupies in the document text."""

    quote: str
    char_start: int
    char_end: int
    match_mode: MatchMode
    model_quote: str
    occurrence: int = 1


class QuoteError(Exception):
    """Base for quote failures; every subclass carries a stable reason code."""

    reason_code = "quote_error"

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


class QuoteNotFound(QuoteError):
    reason_code = "quote_not_found"

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.reason_code, detail)


class QuoteTooShort(QuoteError):
    reason_code = "quote_too_short"

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.reason_code, detail)


class QuoteTooLong(QuoteError):
    reason_code = "quote_too_long"

    def __init__(self, detail: str = "") -> None:
        super().__init__(self.reason_code, detail)


def canonicalize(value: str) -> str:
    """Fold a string to its comparable form, one character in, one character out."""
    return _WHITESPACE.sub(" ", value.translate(_CHAR_MAP))


def locate_quote(
    text: str,
    model_quote: str,
    *,
    min_chars: int = 20,
    max_chars: int = 2000,
    search_start: int = 0,
) -> SpanMatch:
    """Find the model quote in ``text`` and return the span it occupies."""
    stripped = (model_quote or "").strip()
    if len(stripped) < min_chars:
        raise QuoteTooShort(
            f"quote is {len(stripped)} characters, the minimum is {min_chars}"
        )
    if len(stripped) > max_chars:
        raise QuoteTooLong(
            f"quote is {len(stripped)} characters, the maximum is {max_chars}"
        )

    start = text.find(stripped, search_start)
    if start == -1:
        start = text.find(stripped)
    if start != -1:
        end = start + len(stripped)
        return SpanMatch(
            quote=text[start:end],
            char_start=start,
            char_end=end,
            match_mode="exact",
            model_quote=stripped,
            occurrence=_occurrence(text, stripped, start),
        )

    canonical_text = canonicalize(text)
    canonical_quote = canonicalize(stripped)
    start = canonical_text.find(canonical_quote, search_start)
    if start == -1:
        start = canonical_text.find(canonical_quote)
    if start == -1:
        raise QuoteNotFound("quote is not in the document text")

    end = start + len(canonical_quote)
    quote = text[start:end]
    if canonicalize(quote) != canonical_quote:
        raise QuoteNotFound("canonical match failed the verbatim check")
    return SpanMatch(
        quote=quote,
        char_start=start,
        char_end=end,
        match_mode="canonical",
        model_quote=stripped,
        occurrence=_occurrence(canonical_text, canonical_quote, start),
    )


def _occurrence(haystack: str, needle: str, start: int) -> int:
    """The 1-based number of the match among the occurrences of the quote."""
    count = 1
    position = haystack.find(needle)
    while position != -1 and position < start:
        count += 1
        position = haystack.find(needle, position + 1)
    return count
