"""Turn scraped text into text that cannot act.

Everything a page said is data. The sanitizer escapes the characters that
give text meaning inside a vault: a heading, a table row, a blockquote, a
wikilink, a code fence, a Templater expression, a comment, and the
frontmatter delimiter. It replaces the scheme of a URL that is not http or
https, so a hostile address cannot be followed from a note, and it drops the
control characters that have no business in a file and the lone surrogates
that cannot be encoded at all.
"""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

# A URL that names a scheme and an authority. Only http and https survive.
_SCHEME_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*)://[^\s]+")

# A URL whose scheme is hostile. These carry a payload rather than an
# authority, so they are matched on the scheme alone.
_HOSTILE_SCHEME = re.compile(
    r"(?i)\b(javascript|vbscript|data|file|blob|jar|about|view-source|mhtml|ms-msdt)"
    r":[^\s]+"
)

# The characters of the C0 range and the delete character, minus the ones
# that carry text.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# A lone surrogate: half of a character whose other half was lost. It cannot
# be encoded, so it is dropped. A whole character, written as a pair, is left
# alone.
_LONE_SURROGATE = re.compile(
    "[\ud800-\udbff](?![\udc00-\udfff])|(?<![\ud800-\udbff])[\udc00-\udfff]"
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")

# The markers that give a line a meaning of its own: a heading, a table row,
# a rule, a Dataview query, and a tilde fence. A tilde fence is a fence in
# CommonMark and in Obsidian, so it is escaped like the backtick one.
_LINE_START_MARKERS = ("#", "|", "---", "= ", "~~~")

# The indentation CommonMark still reads. A line indented by four spaces or
# more is a code block, and a code block cannot act.
_MAX_INDENT = 3


def neutralize(text: str) -> str:
    """Escape the text so that it reads as text and does nothing else.

    The order matters: the ampersand goes first, so the entities the angle
    brackets turn into stay intact, and the markup escapes come last, so
    none of them undoes another.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = _LONE_SURROGATE.sub("", text)
    text = _block_hostile_urls(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("`", "\\`")
    text = text.replace("[[", "\\[[")
    text = text.replace("{{", "\\{\\{").replace("}}", "\\}\\}")
    text = text.replace("%%", "\\%\\%")
    # A markdown link is the one place where text becomes a live address, so
    # its opening parenthesis is broken.
    text = text.replace("](", "]\\(")
    return _escape_line_starts(text)


def _block_hostile_urls(text: str) -> str:
    """Replace every URL whose scheme is not http or https."""

    def replace_scheme(match: re.Match[str]) -> str:
        scheme = match.group(1).lower()
        if scheme in {"http", "https"}:
            return match.group(0)
        return f"blocked:{scheme}"

    text = _SCHEME_URL.sub(replace_scheme, text)

    def replace_hostile(match: re.Match[str]) -> str:
        return f"blocked:{match.group(1).lower()}"

    return _HOSTILE_SCHEME.sub(replace_hostile, text)


def _escape_line_starts(text: str) -> str:
    """Escape the characters that would make a line into something else.

    CommonMark reads a marker after up to three spaces of indentation, so the
    escape goes immediately in front of the marker and the indentation is
    kept as it was.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        marker = line.lstrip(" ")
        indent = len(line) - len(marker)
        if indent <= _MAX_INDENT and marker.startswith(_LINE_START_MARKERS):
            line = line[:indent] + "\\" + marker
        lines.append(line)
    return "\n".join(lines)


def safe_url(url: str) -> str:
    """The URL, or a marker that says its scheme was refused.

    A URL that is not http or https cannot be followed from a note, so its
    scheme is replaced. A hostile URL must not stop a projection, so this
    never raises.
    """
    if not url:
        return ""
    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return "blocked:invalid"
    if scheme in {"http", "https"}:
        return url
    return f"blocked:{scheme}"


def yaml_scalar(value: str) -> str:
    """A string as a YAML double-quoted scalar.

    A JSON string is a valid YAML double-quoted scalar, so the frontmatter
    takes the standard library's escaping instead of a second implementation
    of it. A lone surrogate is dropped, because the frontmatter does not go
    through the sanitizer and the write cannot encode one.
    """
    return json.dumps(_LONE_SURROGATE.sub("", value), ensure_ascii=False)


def slug(text: str) -> str:
    """A short, readable, filesystem-safe name for a title."""
    cleaned = _NON_SLUG.sub("-", text.lower()).strip("-")
    if len(cleaned) > 60:
        cleaned = cleaned[:60].rstrip("-")
    return cleaned or "untitled"


def content_hash(body: str) -> str:
    """The sha256 of a note body.

    A character that cannot be encoded is replaced, so a hash is always
    computable and describes the bytes the note is written as.
    """
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()
