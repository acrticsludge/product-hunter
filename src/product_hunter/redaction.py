"""Masking of the secrets that reach a reader.

A handler can raise an exception whose message carries a credential, and the
control plane puts error text in front of the operator. The rule lives here so
the worker and the control plane mask the same way.
"""

from __future__ import annotations

import re

# A labelled secret: the label names the setting and the value is what follows
# it. The label stays, so the reader still knows which secret was there.
# The value may itself be a bearer token with its own space, so that form is
# masked whole instead of leaving the token behind a masked word. A bare
# "bearer" followed by a word is NOT masked on its own: it is a common word,
# and a real Authorization header carries the label that names it.
LABELLED_SECRET = re.compile(
    r"(?i)\b(bearer|basic|api[_-]?key|apikey|access[_-]?key|token|secret"
    r"|password|passwd|authorization|cookie|session[_-]?id|private[_-]?key)\b"
    r'"?\s*[=:]\s*'
    r"(\"[^\"]*\"|'[^']*'|bearer\s+\S+|\S+)"
)
# A credential with no label at all: a provider key or a JSON web token.
BARE_CREDENTIAL = re.compile(
    r"(?i)\b(sk_live_[A-Za-z0-9_-]{6,}|sk-[A-Za-z0-9_-]{6,}|pk_[A-Za-z0-9_-]{6,}"
    r"|ghp_[A-Za-z0-9]{6,}|gho_[A-Za-z0-9]{6,}|ghu_[A-Za-z0-9]{6,}"
    r"|ghs_[A-Za-z0-9]{6,}|github_pat_[A-Za-z0-9_]{6,}|xox[baprs]-[A-Za-z0-9-]{6,}"
    r"|AIza[A-Za-z0-9_-]{6,}|AKIA[A-Z0-9]{6,}|ASIA[A-Z0-9]{6,}"
    r"|ya29\.[A-Za-z0-9_-]{6,}|eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"
    r"\.[A-Za-z0-9_-]{6,})\b"
)

# The longest error text that reaches a reader.
MAX_ERROR_CHARS = 300


def redact(text: str) -> str:
    """The text with every labelled secret and bare credential masked."""
    text = LABELLED_SECRET.sub(_mask_labelled, text)
    return BARE_CREDENTIAL.sub(_mask_bare, text)


def safe_error(exc: BaseException) -> str:
    """The failure text, masked and cut. The type always stays."""
    message = redact(str(exc))
    if len(message) > MAX_ERROR_CHARS:
        message = message[:MAX_ERROR_CHARS] + "..."
    return f"{type(exc).__name__}: {message}"


def _mask_labelled(match: re.Match[str]) -> str:
    """The masked form of one labelled secret."""
    return f"{match.group(1)}=[redacted]"


def _mask_bare(match: re.Match[str]) -> str:
    """The masked form of a bare credential."""
    return "[redacted]"