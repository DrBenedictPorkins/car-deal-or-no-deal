"""Thin Gmail API wrapper.

Converts Gmail's payload shape into ``RawMessage`` and nothing more. Kept separate from
the source and transport so the mapping — which is where the fiddly MIME and base64url
handling lives — can be tested against recorded payloads without any network.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from app.ingestion.messages import Attachment, RawMessage, address_of, display_name_of


def decode_b64url(data: str | None) -> bytes:
    if not data:
        return b""
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def encode_b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode()


def _headers(payload: dict) -> dict[str, str]:
    return {h["name"].lower(): h.get("value", "") for h in payload.get("headers", [])}


def _walk(payload: dict) -> list[dict]:
    parts = [payload]
    for part in payload.get("parts", []) or []:
        parts.extend(_walk(part))
    return parts


def _decode_part(part: dict) -> str:
    body = part.get("body", {}) or {}
    return decode_b64url(body.get("data")).decode("utf-8", errors="replace")


def to_raw_message(payload: dict, *, source_system: str = "gmail") -> RawMessage:
    """Gmail `users.messages.get(format="full")` → RawMessage."""
    top = payload.get("payload", {}) or {}
    headers = _headers(top)

    body_text: str | None = None
    body_html: str | None = None
    attachments: list[Attachment] = []
    mime_types: list[str] = []

    for part in _walk(top):
        mime = part.get("mimeType", "")
        mime_types.append(mime)
        filename = part.get("filename") or ""
        if filename:
            body = part.get("body", {}) or {}
            attachments.append(
                Attachment(
                    filename=filename,
                    mime_type=mime,
                    size=int(body.get("size") or 0),
                    provider_attachment_id=body.get("attachmentId"),
                )
            )
            continue
        if mime == "text/plain" and body_text is None:
            body_text = _decode_part(part)
        elif mime == "text/html" and body_html is None:
            body_html = _decode_part(part)

    # internalDate is epoch milliseconds and is the time Gmail received the message —
    # more reliable than the Date header, which senders get wrong.
    internal = payload.get("internalDate")
    sent_at = (
        datetime.fromtimestamp(int(internal) / 1000, tz=UTC).replace(tzinfo=None)
        if internal
        else datetime.now(UTC).replace(tzinfo=None)
    )

    references = tuple(ref for ref in headers.get("references", "").split() if ref)

    return RawMessage(
        source_system=source_system,
        source_identifier=payload["id"],
        sent_at=sent_at,
        thread_identifier=payload.get("threadId"),
        rfc822_message_id=headers.get("message-id") or None,
        in_reply_to=headers.get("in-reply-to") or None,
        references=references,
        from_address=address_of(headers.get("from")),
        from_name=display_name_of(headers.get("from")),
        to_addresses=tuple(
            addr for addr in (address_of(a) for a in headers.get("to", "").split(",")) if addr
        ),
        cc_addresses=tuple(
            addr for addr in (address_of(a) for a in headers.get("cc", "").split(",")) if addr
        ),
        subject=headers.get("subject"),
        body_text=body_text,
        body_html=body_html,
        snippet=payload.get("snippet"),
        label_ids=tuple(payload.get("labelIds", []) or []),
        history_id=str(payload.get("historyId") or "") or None,
        attachments=tuple(attachments),
        mime_summary=", ".join(sorted(set(mime_types))) or None,
    )


def build_service(credentials):  # pragma: no cover - needs live credentials
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=credentials, cache_discovery=False)
