"""Sending through Gmail.

This class does no safety checking of its own, and that is deliberate: the allowlist
lives in ``SafeTransport``, which wraps every transport. Putting the check in both
places invites the version where one of them is wrong.

What it does do is return a real receipt — Gmail's message id and thread id — so the
sent message can be matched to the reply that comes back.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.message import EmailMessage

from app.config import Settings, get_settings
from app.ingestion.gmail import auth, client
from app.ingestion.messages import OutboundMessage, SentMessage

log = logging.getLogger("dealbench.gmail")


class GmailTransport:
    name = "gmail"

    def __init__(self, settings: Settings | None = None, service=None) -> None:
        self.settings = settings or get_settings()
        self._service = service

    @property
    def service(self):  # pragma: no cover - needs live credentials
        if self._service is None:
            self._service = client.build_service(auth.get_credentials(self.settings))
        return self._service

    def _mime(self, message: OutboundMessage) -> EmailMessage:
        mail = EmailMessage()
        mail["To"] = ", ".join(message.to_addresses)
        if message.cc_addresses:
            mail["Cc"] = ", ".join(message.cc_addresses)
        if message.from_address:
            mail["From"] = message.from_address
        mail["Subject"] = message.subject
        # Threading headers matter: without them the dealer's reply starts a new
        # conversation and the negotiation timeline splits in two.
        if message.in_reply_to_rfc822_id:
            mail["In-Reply-To"] = message.in_reply_to_rfc822_id
            references = list(message.references) or [message.in_reply_to_rfc822_id]
            mail["References"] = " ".join(references)
        mail.set_content(message.body_text)
        return mail

    def send(self, message: OutboundMessage) -> SentMessage:
        body = {"raw": client.encode_b64url(self._mime(message).as_bytes())}
        if message.thread_identifier:
            body["threadId"] = message.thread_identifier

        sent = self.service.users().messages().send(userId="me", body=body).execute()
        log.info("Sent Gmail message %s to %d recipient(s)", sent.get("id"),
                 len(message.to_addresses))

        # Read the stored copy back so the receipt carries the real RFC-822 id rather
        # than one we guessed.
        stored = (
            self.service.users()
            .messages()
            .get(userId="me", id=sent["id"], format="metadata",
                 metadataHeaders=["Message-ID"])
            .execute()
        )
        raw = client.to_raw_message(stored)
        return SentMessage(
            source_system=self.name,
            source_identifier=sent["id"],
            thread_identifier=sent.get("threadId"),
            rfc822_message_id=raw.rfc822_message_id,
            sent_at=datetime.now(UTC).replace(tzinfo=None),
            to_addresses=message.to_addresses,
            label_ids=tuple(sent.get("labelIds", []) or []),
        )
