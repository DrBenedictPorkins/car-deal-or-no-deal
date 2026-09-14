"""Turning a raw message body into the text extraction should actually read.

The stakes here are higher than they look. A lead-management system that quotes the
buyer's original inquiry verbatim will, if the quoted block is not removed, look like
the dealer agreeing to every term in it — including a price the buyer named. That is a
real failure mode from the source negotiation, so stripping is done conservatively and
what was stripped is kept rather than discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape

# --------------------------------------------------------------------- HTML

_BLOCK_TAGS = r"(?:p|div|br|tr|li|h[1-6]|table|blockquote)"
_DROP_ELEMENTS = re.compile(
    r"<(script|style|head|title)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_BLOCK_OPEN = re.compile(rf"<{_BLOCK_TAGS}\b[^>]*>", re.IGNORECASE)
_BLOCK_CLOSE = re.compile(rf"</{_BLOCK_TAGS}>", re.IGNORECASE)
_CELL_BOUNDARY = re.compile(r"</t[dh]>\s*<t[dh]\b[^>]*>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]+>")
_MANY_BLANKS = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


def html_to_text(html: str) -> str:
    """Flatten HTML mail to text, preserving line and table-cell structure.

    Table cells become double-space separated rather than run together, because
    dealer quotes arrive as two-column tables and "Selling price$28,035.00" is not
    parseable while "Selling price  $28,035.00" is.
    """
    text = _DROP_ELEMENTS.sub(" ", html)
    text = _CELL_BOUNDARY.sub("  ", text)
    text = _BLOCK_CLOSE.sub("\n", text)
    text = _BLOCK_OPEN.sub("\n", text)
    text = _ANY_TAG.sub("", text)
    text = unescape(text)
    text = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = _TRAILING_SPACE.sub("\n", text)
    return _MANY_BLANKS.sub("\n\n", text).strip()


# ------------------------------------------------------------ quoted replies

# Deliberately conservative: each of these is an unambiguous start-of-quote marker.
_QUOTE_MARKERS = (
    re.compile(r"^\s*On .{4,120}\bwrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Forwarded message\s*-{2,}\s*$", re.IGNORECASE),
    re.compile(r"^\s*_{10,}\s*$"),
    re.compile(r"^\s*From:\s*.+<?[\w.+-]+@[\w.-]+>?\s*$", re.IGNORECASE),
    re.compile(r"^\s*Sent from my \w+", re.IGNORECASE),
)

_SIGNATURE_MARKER = re.compile(r"^\s*--\s*$")

# A block where most lines begin with ">" is a quote regardless of any marker.
_QUOTE_PREFIX = re.compile(r"^\s*>")


@dataclass(frozen=True)
class NormalizedBody:
    """The new content, plus everything removed from it — never thrown away."""

    text: str
    quoted: str
    signature: str

    @property
    def has_new_content(self) -> bool:
        return bool(self.text.strip())


def _first_quote_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if any(marker.match(line) for marker in _QUOTE_MARKERS):
            return index
        # Three consecutive ">" lines: a quote block with no header.
        if _QUOTE_PREFIX.match(line) and all(
            _QUOTE_PREFIX.match(candidate) or not candidate.strip()
            for candidate in lines[index : index + 3]
        ):
            return index
    return None


def _signature_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _SIGNATURE_MARKER.match(line):
            return index
    return None


def split_body(body: str) -> NormalizedBody:
    """Separate new content from quoted history and a trailing signature block."""
    if not body:
        return NormalizedBody("", "", "")

    text = body.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    quote_at = _first_quote_index(lines)
    quoted = "\n".join(lines[quote_at:]).strip() if quote_at is not None else ""
    body_lines = lines[:quote_at] if quote_at is not None else lines

    signature = ""
    sig_at = _signature_index(body_lines)
    if sig_at is not None:
        signature = "\n".join(body_lines[sig_at + 1 :]).strip()
        body_lines = body_lines[:sig_at]

    return NormalizedBody(
        text=_MANY_BLANKS.sub("\n\n", "\n".join(body_lines)).strip(),
        quoted=quoted,
        signature=signature,
    )


def normalize(body_text: str | None, body_html: str | None) -> NormalizedBody:
    """Full path: pick the best part, flatten HTML, split off quotes and signature."""
    if body_text and body_text.strip():
        source = body_text
    elif body_html:
        source = html_to_text(body_html)
    else:
        return NormalizedBody("", "", "")
    return split_body(source)
