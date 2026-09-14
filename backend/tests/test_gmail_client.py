"""Gmail payload mapping.

No network and no credentials: the Gmail API's message shape is a fixture, because the
fiddly part is the base64url and MIME walking, not the HTTP.
"""

from __future__ import annotations

import base64

from app.ingestion.gmail.client import decode_b64url, encode_b64url, to_raw_message


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


PAYLOAD = {
    "id": "18f2c9a1b2c3",
    "threadId": "18f2c9a1b2c0",
    "snippet": "Happy to put this in writing.",
    "labelIds": ["INBOX", "CATEGORY_PERSONAL"],
    "internalDate": "1788254640000",
    "historyId": "99123",
    "payload": {
        "mimeType": "multipart/alternative",
        "headers": [
            {"name": "From", "value": "Chris Benton <chris@westport.example.test>"},
            {"name": "To", "value": "buyer@example.test"},
            {"name": "Cc", "value": "manager@westport.example.test"},
            {"name": "Subject", "value": "Re: 2026 Civic pricing"},
            {"name": "Message-ID", "value": "<abc123@westport.example.test>"},
            {"name": "In-Reply-To", "value": "<original@example.test>"},
            {"name": "References", "value": "<root@example.test> <original@example.test>"},
        ],
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": b64("Selling price $28,035.00\nOut the door $30,775.10")},
            },
            {
                "mimeType": "text/html",
                "body": {"data": b64("<p>Selling price $28,035.00</p>")},
            },
            {
                "mimeType": "application/pdf",
                "filename": "buyers-order.pdf",
                "body": {"size": 20480, "attachmentId": "att-1"},
            },
        ],
    },
}


def test_base64url_round_trips_without_padding_errors():
    assert decode_b64url(encode_b64url(b"hello")) == b"hello"
    assert decode_b64url(b64("a")) == b"a"  # unpadded input, as Gmail sends it


def test_headers_and_ids_are_mapped():
    message = to_raw_message(PAYLOAD)
    assert message.source_identifier == "18f2c9a1b2c3"
    assert message.thread_identifier == "18f2c9a1b2c0"
    assert message.rfc822_message_id == "<abc123@westport.example.test>"
    assert message.in_reply_to == "<original@example.test>"
    assert message.references == ("<root@example.test>", "<original@example.test>")
    assert message.from_address == "chris@westport.example.test"
    assert message.from_name == "Chris Benton"
    assert message.to_addresses == ("buyer@example.test",)
    assert message.cc_addresses == ("manager@westport.example.test",)
    assert message.subject == "Re: 2026 Civic pricing"
    assert message.history_id == "99123"


def test_both_body_parts_are_decoded():
    message = to_raw_message(PAYLOAD)
    assert "Out the door $30,775.10" in message.body_text
    assert message.body_html.startswith("<p>")
    assert message.best_body == message.body_text


def test_attachments_are_listed_but_not_downloaded():
    """Metadata now; fetching the bytes is the document pipeline's job."""
    message = to_raw_message(PAYLOAD)
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.filename == "buyers-order.pdf"
    assert attachment.size == 20480
    assert attachment.content is None
    assert attachment.provider_attachment_id == "att-1"


def test_the_timestamp_comes_from_gmails_own_clock():
    """internalDate beats the Date header, which senders routinely get wrong."""
    message = to_raw_message(PAYLOAD)
    assert message.sent_at.year == 2026
    assert message.sent_at.tzinfo is None  # naive UTC, matching the schema


def test_a_plain_text_only_message_is_handled():
    payload = {
        "id": "x",
        "threadId": "t",
        "internalDate": "1788254640000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "From", "value": "a@b.test"}],
            "body": {"data": b64("just text")},
        },
    }
    message = to_raw_message(payload)
    assert message.body_text == "just text"
    assert message.body_html is None


def test_a_message_with_no_body_does_not_crash():
    payload = {
        "id": "x",
        "threadId": "t",
        "internalDate": "1788254640000",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {}},
    }
    message = to_raw_message(payload)
    assert message.best_body == ""


def test_the_content_fingerprint_survives_a_change_of_provider_id():
    """Which is what makes re-imported and forwarded copies deduplicate."""
    first = to_raw_message(PAYLOAD)
    second = to_raw_message({**PAYLOAD, "id": "different-id"})
    assert first.content_fingerprint == second.content_fingerprint
