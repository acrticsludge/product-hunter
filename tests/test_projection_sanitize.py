"""The sanitizer keeps scraped text from acting inside a vault.

The rules are the ones of section 8 of the spec: the characters that give
text meaning are escaped, and a URL that is not http or https loses its
scheme. Ordinary prose comes through untouched.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from product_hunter.projection.sanitize import (
    content_hash,
    neutralize,
    safe_url,
    slug,
    yaml_scalar,
)


def test_plain_prose_is_left_alone() -> None:
    text = "The team pastes the invoice totals by hand every month."
    assert neutralize(text) == text


def test_a_number_is_left_alone() -> None:
    assert neutralize("147 mentions, 12 documents, 0.75 share") == (
        "147 mentions, 12 documents, 0.75 share"
    )


def test_an_http_url_is_left_alone() -> None:
    url = "https://news.ycombinator.com/item?id=1"
    assert neutralize(url) == url


def test_html_is_escaped() -> None:
    escaped = neutralize("a <b>bold</b> & a <script>alert(1)</script>")
    assert "<" not in escaped
    assert ">" not in escaped
    assert "&lt;script&gt;" in escaped
    assert "&amp;" in escaped


def test_a_wikilink_stays_text() -> None:
    escaped = neutralize("see [[Some Note]] for the details")
    assert escaped == "see \\[[Some Note]] for the details"
    assert "[[Some Note]]" not in escaped.replace("\\[[", "")


def test_an_embed_stays_text() -> None:
    escaped = neutralize("![[embed.png]]")
    assert escaped == "!\\[[embed.png]]"
    assert "![[embed.png]]" not in escaped.replace("\\[[", "")


def test_a_code_fence_stays_text() -> None:
    escaped = neutralize("```dataview\nTABLE file.name\n```")
    assert "```" not in escaped
    assert "\\`\\`\\`" in escaped


def test_a_templater_expression_stays_text() -> None:
    escaped = neutralize("{{tp.date.now()}}")
    assert "{{" not in escaped
    assert "\\{\\{tp.date.now()\\}\\}" in escaped


def test_a_dataview_inline_block_stays_text() -> None:
    escaped = neutralize("%%dataview%%")
    assert "%%" not in escaped
    assert "\\%\\%dataview\\%\\%" in escaped


def test_a_comment_stays_text() -> None:
    escaped = neutralize("%% a hidden note %%")
    assert "%% a hidden note %%" not in escaped


def test_a_leading_hash_does_not_make_a_heading() -> None:
    escaped = neutralize("# a heading from a page")
    assert escaped.startswith("\\#")


def test_a_leading_pipe_does_not_break_a_table() -> None:
    escaped = neutralize("| a | b |")
    assert escaped.startswith("\\|")


def test_a_leading_quote_does_not_open_a_blockquote() -> None:
    escaped = neutralize("> [!note] a callout")
    assert not escaped.startswith(">")
    assert "&gt;" in escaped


def test_a_leading_rule_does_not_close_the_frontmatter() -> None:
    escaped = neutralize("---\nid: something")
    assert escaped.startswith("\\---")


def test_a_leading_equals_does_not_start_a_dataview_query() -> None:
    escaped = neutralize("= this.file.name")
    assert escaped.startswith("\\=")
    assert not escaped.startswith("=")
    # an equals inside a line is left alone
    text = "the total is = 100 and the url is https://example.test/?a=1"
    assert neutralize(text) == text
    assert neutralize("| a | b=c |") == "\\| a | b=c |"


def test_a_tilde_fence_does_not_open_a_code_fence() -> None:
    escaped = neutralize("~~~dataview\nTABLE file.name\n~~~")
    assert escaped.startswith("\\~~~")
    assert "~~~" not in escaped.replace("\\~~~", "")


def test_a_marker_after_three_spaces_is_escaped() -> None:
    """CommonMark reads a marker after up to three spaces of indentation."""
    assert neutralize("   # a heading") == "   \\# a heading"
    assert neutralize("   | a | b |") == "   \\| a | b |"
    assert neutralize("   ---") == "   \\---"
    assert neutralize("   = this.file.name") == "   \\= this.file.name"
    assert neutralize("   ~~~") == "   \\~~~"


def test_four_spaces_of_indentation_is_a_code_block_and_is_left_alone() -> None:
    assert neutralize("    # a heading") == "    # a heading"
    assert neutralize("    ~~~") == "    ~~~"


def test_a_markdown_link_cannot_carry_a_hostile_address() -> None:
    escaped = neutralize("[click me](javascript:alert(1))")
    assert "](" not in escaped
    assert "javascript:" not in escaped
    assert "blocked:javascript" in escaped


def test_a_hostile_scheme_is_blocked() -> None:
    for url in (
        "javascript:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
    ):
        assert neutralize(url) == f"blocked:{url.split(':')[0]}"


def test_a_control_character_is_dropped() -> None:
    assert neutralize("a\x00b\x07c") == "abc"


def test_safe_url_keeps_http_and_https() -> None:
    assert safe_url("https://example.com/x") == "https://example.com/x"
    assert safe_url("http://example.com/x") == "http://example.com/x"


def test_safe_url_blocks_another_scheme() -> None:
    assert safe_url("javascript:alert(1)") == "blocked:javascript"
    assert safe_url("data:text/html,x") == "blocked:data"
    assert safe_url("file:///etc/passwd") == "blocked:file"


def test_safe_url_does_not_raise_on_a_broken_url() -> None:
    assert safe_url("http://[::1").startswith("blocked:")


def test_safe_url_of_nothing_is_nothing() -> None:
    assert safe_url("") == ""


def test_yaml_scalar_is_a_quoted_single_line_string() -> None:
    scalar = yaml_scalar("a title: with a colon\nand a newline")
    assert "\n" not in scalar
    assert scalar.startswith('"')
    assert scalar.endswith('"')
    assert json.loads(scalar) == "a title: with a colon\nand a newline"


def test_a_frontmatter_value_cannot_break_out() -> None:
    scalar = yaml_scalar("---\nid: other\n---\n")
    assert "\n" not in scalar
    assert "---" in json.loads(scalar)


def test_slug_is_lowercase_and_dash_separated() -> None:
    assert slug("The Team Pastes Totals!") == "the-team-pastes-totals"


def test_slug_drops_what_it_cannot_keep() -> None:
    assert slug("café ☕") == "caf"


def test_slug_is_at_most_sixty_characters() -> None:
    assert len(slug("a" * 200)) == 60


def test_slug_of_nothing_is_untitled() -> None:
    assert slug("!!!") == "untitled"
    assert slug("") == "untitled"


def test_content_hash_is_the_sha256_of_the_body() -> None:
    body = "# a note\n"
    assert content_hash(body) == hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert len(content_hash(body)) == 64


@pytest.mark.parametrize(
    "text",
    [
        "plain",
        "a title: with a colon",
        "a newline\nand a line",
    ],
)
def test_ordinary_text_survives_the_round_trip(text: str) -> None:
    assert json.loads(yaml_scalar(neutralize(text))) == neutralize(text)
