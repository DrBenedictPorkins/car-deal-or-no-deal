"""RFC-822 (.eml) ↔ RawMessage.

This is the interchange format for everything that is not live Gmail: golden fixtures,
the replay corpus, and the sanitizer's output. Using real .eml rather than a bespoke
JSON shape means the fixtures are the same artefact Gmail would hand back, so a bug in
header handling shows up in replay tests rather than only in production.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from pathlib import Path

from app.ingestion.messages import Attachment, RawMessage, address_of, display_name_of


def _addresses(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(addr for _, addr in getaddresses([value]) if addr)


def _naive_utc(value: datetime) -> datetime:
    """Store everything naive-UTC, matching the rest of the schema."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def parse_bytes(
    data: bytes, *, source_system: str = "eml", source_identifier: str | None = None
) -> RawMessage:
    message = BytesParser(policy=policy.default).parsebytes(data)

    body_text: str | None = None
    body_html: str | None = None
    attachments: list[Attachment] = []
    parts: list[str] = []

    for part in message.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        parts.append(content_type)
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                Attachment(
                    filename=part.get_filename() or "attachment",
                    mime_type=content_type,
                    size=len(payload),
                    content=payload,
                )
            )
            continue
        try:
            content = part.get_content()
        except Exception:  # pragma: no cover - malformed part, keep going
            continue
        if content_type == "text/plain" and body_text is None:
            body_text = content
        elif content_type == "text/html" and body_html is None:
            body_html = content

    date_header = message.get("Date")
    sent_at = (
        _naive_utc(parsedate_to_datetime(date_header))
        if date_header
        else datetime.now(UTC).replace(tzinfo=None)
    )

    rfc822_id = (message.get("Message-ID") or "").strip() or None
    references = tuple(
        ref for ref in (message.get("References") or "").split() if ref.strip()
    )

    # Gmail supplies its own ids; a bare .eml has none, so the RFC-822 Message-ID
    # stands in. That keeps dedupe working identically across transports.
    identifier = source_identifier or rfc822_id or RawMessage(
        source_system=source_system, source_identifier="", sent_at=sent_at
    ).content_fingerprint

    thread_id = (message.get("X-Dealbench-Thread") or "").strip() or None
    if thread_id is None:
        # Fall back to the root of the reference chain, which is how mail clients
        # thread when the provider does not do it for them.
        thread_id = references[0] if references else rfc822_id

    return RawMessage(
        source_system=source_system,
        source_identifier=identifier,
        sent_at=sent_at,
        thread_identifier=thread_id,
        rfc822_message_id=rfc822_id,
        in_reply_to=(message.get("In-Reply-To") or "").strip() or None,
        references=references,
        from_address=address_of(message.get("From")),
        from_name=display_name_of(message.get("From")),
        to_addresses=_addresses(message.get("To")),
        cc_addresses=_addresses(message.get("Cc")),
        subject=message.get("Subject"),
        body_text=body_text,
        body_html=body_html,
        label_ids=tuple(
            label.strip()
            for label in (message.get("X-Dealbench-Labels") or "").split(",")
            if label.strip()
        ),
        attachments=tuple(attachments),
        raw_bytes=data,
        mime_summary=", ".join(sorted(set(parts))) or None,
    )


def parse_file(path: Path, *, source_system: str = "eml") -> RawMessage:
    return parse_bytes(path.read_bytes(), source_system=source_system)


def to_bytes(message: RawMessage) -> bytes:
    """Serialize back to .eml. Used by the sanitizer to write fixtures."""
    out = EmailMessage()
    out["From"] = (
        f"{message.from_name} <{message.from_address}>"
        if message.from_name and message.from_address
        else (message.from_address or "")
    )
    if message.to_addresses:
        out["To"] = ", ".join(message.to_addresses)
    if message.cc_addresses:
        out["Cc"] = ", ".join(message.cc_addresses)
    out["Subject"] = message.subject or ""
    out["Date"] = format_datetime(message.sent_at.replace(tzinfo=UTC))
    if message.rfc822_message_id:
        out["Message-ID"] = message.rfc822_message_id
    if message.in_reply_to:
        out["In-Reply-To"] = message.in_reply_to
    if message.references:
        out["References"] = " ".join(message.references)
    if message.thread_identifier:
        out["X-Dealbench-Thread"] = message.thread_identifier
    if message.label_ids:
        out["X-Dealbench-Labels"] = ", ".join(message.label_ids)

    out.set_content(message.body_text or "")
    if message.body_html:
        out.add_alternative(message.body_html, subtype="html")
    return out.as_bytes()


def load_directory(directory: Path, *, source_system: str = "replay") -> list[RawMessage]:
    """Every .eml under a directory, in chronological order.

    Chronological rather than filename order on purpose: replay must reconstruct the
    negotiation as it happened, and a corpus exported from a mailbox will not be named
    in time order.
    """
    messages = [
        parse_file(path, source_system=source_system)
        for path in sorted(directory.rglob("*.eml"))
    ]
    return sorted(messages, key=lambda m: (m.sent_at, m.source_identifier))
