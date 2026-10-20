"""Turn one stored raw capture into parsed documents and chunks.

The parser is deterministic: the same raw bytes and the same fetch record
always produce the same document ids, the same text, and the same text file
path. Every document text is written to a content-addressed file under
``data/processed/text`` so identical text is stored once.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from pydantic import BaseModel

from product_hunter.fetch import FetchRecord

PARSER_VERSION = "phase1-0.2"

TEXT_DIR = Path("data") / "processed" / "text"

_HN_ITEM_URL = "https://news.ycombinator.com/item?id={id}"
_BLOG_DROP = ("script", "style", "noscript", "nav", "footer")


class DocumentError(Exception):
    """A capture could not become a usable document."""

    reason_code = "document_error"

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(detail or reason_code)


class Document(BaseModel):
    """One normalized document extracted from a raw capture."""

    doc_id: str
    fetch_id: str
    source_id: str
    url: str
    text: str
    text_sha256: str
    text_path: str
    parser_version: str = PARSER_VERSION
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    raw_path: str
    raw_sha256: str
    content_type: str = ""


class DocumentChunk(BaseModel):
    """A slice of a document text, with offsets into the full text."""

    doc_id: str
    chunk_index: int
    char_start: int
    char_end: int
    text: str


@dataclass(frozen=True)
class _Parsed:
    """The fields of one document before the text file is written."""

    identity: str
    url: str
    title: str | None
    author: str | None
    published_at: str | None
    text: str


def parse_documents(
    fetch: FetchRecord, raw: bytes, *, repo_root: Path
) -> list[Document]:
    """Parse one raw capture into documents, or fail with a reason code."""
    digest = hashlib.sha256(raw).hexdigest()
    if fetch.raw_sha256 is not None and digest != fetch.raw_sha256:
        raise DocumentError(
            "raw_hash_mismatch",
            f"expected sha256 {fetch.raw_sha256!r}, got {digest}",
        )

    if fetch.source_id == "hn_algolia":
        parsed = _parse_hn(raw)
    elif fetch.source_id == "github_issues":
        parsed = _parse_github(raw)
    elif fetch.source_id == "blog_http":
        parsed = _parse_blog(fetch, raw)
    else:
        raise DocumentError(
            "unknown_source", f"no parser for source {fetch.source_id!r}"
        )

    if not parsed:
        raise DocumentError(
            "empty_document", f"no documents parsed from {fetch.source_id!r}"
        )

    root = Path(repo_root)
    return [_build(fetch, root, item) for item in parsed]


def load_fetch_documents(fetch: FetchRecord, repo_root: Path) -> list[Document]:
    """Read the stored raw bytes for a fetch and parse them."""
    if not fetch.raw_path:
        raise DocumentError(
            "raw_missing", f"fetch {fetch.fetch_id!r} has no raw_path"
        )
    path = Path(repo_root) / fetch.raw_path
    if not path.is_file():
        raise DocumentError("raw_missing", f"raw file not found: {fetch.raw_path}")
    return parse_documents(fetch, path.read_bytes(), repo_root=Path(repo_root))


def chunk_documents(
    documents: Iterable[Document], max_chars: int, overlap: int
) -> list[DocumentChunk]:
    """Split documents into overlapping chunks of at most ``max_chars``."""
    if max_chars <= 0:
        raise ValueError(f"max_chars must be positive, got {max_chars}")
    overlap = max(0, overlap)

    chunks: list[DocumentChunk] = []
    for document in documents:
        text = document.text
        length = len(text)
        start = 0
        index = 0
        while start < length:
            end = _chunk_end(text, start, max_chars)
            if end <= start:
                break
            piece = text[start:end]
            if piece.strip():
                chunks.append(
                    DocumentChunk(
                        doc_id=document.doc_id,
                        chunk_index=index,
                        char_start=start,
                        char_end=end,
                        text=piece,
                    )
                )
                index += 1
            if end >= length:
                break
            next_start = end - overlap
            # A chunk no longer than the overlap cannot step back without
            # looping, so the next chunk starts where this one ended.
            start = next_start if next_start > start else end
    return chunks


# --- parsing -------------------------------------------------------------


def _parse_hn(raw: bytes) -> list[_Parsed]:
    payload = _load_json(raw)
    parsed: list[_Parsed] = []
    for item in _hn_items(payload):
        body = _strip_html(_first_text(item, "comment_text", "story_text", "text"))
        title = _first_text(item, "story_title", "title")
        # The title is only part of the text when the item carries no body.
        text = body or title
        if not text:
            continue
        ident = item.get("objectID")
        if ident is None:
            ident = item.get("id")
        if ident is None:
            continue
        ident = str(ident)
        parsed.append(
            _Parsed(
                identity=ident,
                url=_HN_ITEM_URL.format(id=ident),
                title=title or None,
                author=_first_text(item, "author") or None,
                published_at=_first_text(item, "created_at") or None,
                text=text,
            )
        )
    return parsed


def _parse_github(raw: bytes) -> list[_Parsed]:
    payload = _load_json(raw)
    entries = payload if isinstance(payload, list) else [payload]
    parsed: list[_Parsed] = []
    for entry in entries:
        if not isinstance(entry, dict) or "pull_request" in entry:
            continue
        ident = entry.get("id")
        html_url = entry.get("html_url")
        if ident is None and not html_url:
            continue
        title = (entry.get("title") or "").strip()
        body = (entry.get("body") or "").strip()
        text = f"{title}\n\n{body}" if body else title
        if not text:
            continue
        user = entry.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        parsed.append(
            _Parsed(
                identity=str(ident) if ident is not None else str(html_url),
                url=str(html_url or ""),
                title=title or None,
                author=author or None,
                published_at=entry.get("created_at") or None,
                text=text,
            )
        )
    return parsed


def _parse_blog(fetch: FetchRecord, raw: bytes) -> list[_Parsed]:
    html = raw.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_BLOG_DROP):
        tag.decompose()
    text = _collapse_blank_lines(soup.get_text("\n"))
    if not text.strip():
        raise DocumentError("empty_document", "blog page has no text")
    title = ""
    if soup.title is not None:
        title = soup.title.get_text(" ", strip=True)
    if not title:
        heading = soup.find("h1")
        if heading is not None:
            title = heading.get_text(" ", strip=True)
    return [
        _Parsed(
            identity="0",
            url=fetch.final_url,
            title=title or None,
            author=None,
            published_at=None,
            text=text,
        )
    ]


def _build(
    fetch: FetchRecord,
    repo_root: Path,
    parsed: _Parsed,
) -> Document:
    # A document without raw provenance cannot be traced back to a capture.
    if not fetch.raw_path or not fetch.raw_sha256:
        raise DocumentError(
            "missing_raw_provenance",
            f"fetch {fetch.fetch_id!r} has no raw_path or raw_sha256",
        )
    text_sha256, text_path = _write_text(repo_root, parsed.text)
    return Document(
        doc_id=_doc_id(fetch, parsed.identity),
        fetch_id=fetch.fetch_id,
        source_id=fetch.source_id,
        url=parsed.url,
        title=parsed.title,
        author=parsed.author,
        published_at=parsed.published_at,
        text=parsed.text,
        text_sha256=text_sha256,
        text_path=text_path,
        raw_path=fetch.raw_path,
        raw_sha256=fetch.raw_sha256,
        parser_version=PARSER_VERSION,
        content_type=fetch.content_type,
    )


def _doc_id(fetch: FetchRecord, identity: str) -> str:
    basis = f"{fetch.fetch_id}|{_SOURCE_TAG[fetch.source_id]}|{identity}|{PARSER_VERSION}"
    return "doc_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


_SOURCE_TAG = {
    "hn_algolia": "hn",
    "github_issues": "gh",
    "blog_http": "blog",
}


def _write_text(repo_root: Path, text: str) -> tuple[str, str]:
    """Write the text to a content-addressed file; identical text is stored once."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    relative = TEXT_DIR / f"{digest}.txt"
    target = Path(repo_root) / relative
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, temp_path = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
        try:
            with os.fdopen(handle_fd, "wb") as handle:
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
    return digest, relative.as_posix()


def _hn_items(payload: object) -> Iterator[dict]:
    """Yield every item node of an Algolia search response or item tree."""
    if isinstance(payload, list):
        for node in payload:
            yield from _hn_item_tree(node)
        return
    if not isinstance(payload, dict):
        return
    hits = payload.get("hits")
    if isinstance(hits, list):
        for hit in hits:
            yield from _hn_item_tree(hit)
        return
    yield from _hn_item_tree(payload)


def _hn_item_tree(node: object) -> Iterator[dict]:
    if not isinstance(node, dict):
        return
    yield node
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            yield from _hn_item_tree(child)


def _load_json(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentError("parse_error", f"invalid JSON: {exc}") from exc


def _first_text(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _strip_html(value: str) -> str:
    """Drop markup and decode entities; inline tags become a single space."""
    if not value:
        return ""
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def _collapse_blank_lines(text: str) -> str:
    """Trim each line and collapse runs of blank lines into one blank line."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line and lines and not lines[-1]:
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def _chunk_end(text: str, start: int, max_chars: int) -> int:
    """End offset of the next chunk: a paragraph break when one fits."""
    limit = min(start + max_chars, len(text))
    if limit >= len(text):
        return len(text)
    window = text[start:limit]
    break_at = window.find("\n\n")
    if break_at != -1:
        return start + break_at + 2
    break_at = window.find("\n")
    if break_at != -1:
        return start + break_at + 1
    return limit
