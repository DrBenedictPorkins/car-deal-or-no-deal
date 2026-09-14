"""Provider-neutral message DTOs.

Everything downstream of ingestion sees a ``RawMessage`` and nothing else. Gmail,
an .eml file on disk, and a replayed historical message all produce the same shape,
which is what makes the three test transports interchangeable: the negotiation logic
cannot tell them apart, so a replay test exercises the same code paths as live Gmail.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parseaddr


def address_of(value: str | None) -> str | None:
    """Bare lowercase address from "Name <a@b>", "a@b", or None."""
    if not value:
        return None
    _, addr = parseaddr(value)
    addr = (addr or value).strip().lower()
    return addr or None


def display_name_of(value: str | None) -> str | None:
    if not value:
        return None
    name, addr = parseaddr(value)
    name = name.strip().strip('"')
    if name:
        return name
    # "chris.benton@westport.example" → "Chris Benton" is a guess, not a fact, so
    # only the local part is offered and the caller decides whether to trust it.
    return addr.split("@")[0] if addr else None


def domain_of(value: str | None) -> str | None:
    addr = address_of(value)
    return addr.split("@")[-1] if addr and "@" in addr else None


@dataclass(frozen=True)
class Attachment:
    filename: str
    mime_type: str
    size: int
    content: bytes | None = None
    provider_attachment_id: str | None = None


@dataclass(frozen=True)
class RawMessage:
    """One inbound message, exactly as some transport handed it over."""

    source_system: str
    source_identifier: str
    sent_at: datetime

    thread_identifier: str | None = None
    rfc822_message_id: str | None = None
    in_reply_to: str | None = None
    references: tuple[str, ...] = ()

    from_address: str | None = None
    from_name: str | None = None
    to_addresses: tuple[str, ...] = ()
    cc_addresses: tuple[str, ...] = ()

    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    snippet: str | None = None

    label_ids: tuple[str, ...] = ()
    history_id: str | None = None
    attachments: tuple[Attachment, ...] = ()
    raw_bytes: bytes | None = None
    mime_summary: str | None = None

    @property
    def best_body(self) -> str:
        """Plain text if the sender supplied it, otherwise the HTML part."""
        if self.body_text and self.body_text.strip():
            return self.body_text
        return self.body_html or ""

    @property
    def content_fingerprint(self) -> str:
        """Identity of the *content*, independent of provider ids.

        Two copies of the same message arriving under different Gmail ids — which
        happens with forwarding and with re-imports — must collapse to one.
        """
        basis = "\n".join(
            [
                (self.rfc822_message_id or "").strip().lower(),
                (address_of(self.from_address) or ""),
                (self.subject or "").strip().lower(),
                self.sent_at.replace(microsecond=0).isoformat(),
                (self.best_body or "").strip(),
            ]
        )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutboundMessage:
    """A message the user has approved for sending."""

    to_addresses: tuple[str, ...]
    subject: str
    body_text: str
    cc_addresses: tuple[str, ...] = ()
    in_reply_to_rfc822_id: str | None = None
    references: tuple[str, ...] = ()
    thread_identifier: str | None = None
    from_address: str | None = None


@dataclass(frozen=True)
class SentMessage:
    """Receipt for something that actually left the machine."""

    source_system: str
    source_identifier: str
    thread_identifier: str | None
    rfc822_message_id: str | None
    sent_at: datetime
    to_addresses: tuple[str, ...]
    label_ids: tuple[str, ...] = field(default=())
