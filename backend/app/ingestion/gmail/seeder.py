"""Loading fixture messages into a dedicated test Gmail account.

Uses ``users.messages.insert``, which writes a message into the mailbox without
sending it anywhere — no dealership receives anything. That is the only safe way to
stage a realistic inbox for an end-to-end test.

Guarded three ways, because this writes to a real mailbox:

* it requires ``gmail.insert``, a scope the application never requests otherwise;
* it refuses to run unless ``DEALBENCH_GMAIL_ALLOW_INSERT`` is explicitly set;
* it refuses any account whose address is not in the outbound allowlist, so pointing
  it at a personal inbox by mistake fails instead of filling it with test mail.
"""

from __future__ import annotations

import logging

from app.config import Settings, get_settings
from app.ingestion import eml
from app.ingestion.gmail import client
from app.ingestion.messages import RawMessage

log = logging.getLogger("dealbench.gmail")

INSERT_SCOPE = "https://www.googleapis.com/auth/gmail.insert"


class InsertRefused(RuntimeError):
    """Raised rather than writing to a mailbox that has not opted in."""


def guard(settings: Settings, account: str | None) -> None:
    if not settings.gmail_allow_insert:
        raise InsertRefused(
            "Inserting fixture messages writes to a real mailbox. Set "
            "DEALBENCH_GMAIL_ALLOW_INSERT=true and use a dedicated test account."
        )
    allowlist = {a.strip().lower() for a in settings.send_allowlist}
    if not allowlist:
        raise InsertRefused(
            "Refusing to insert with an empty DEALBENCH_SEND_ALLOWLIST — that list is "
            "what identifies the account as a test account."
        )
    if account and account.strip().lower() not in allowlist:
        raise InsertRefused(
            f"{account} is not in DEALBENCH_SEND_ALLOWLIST. Refusing to write fixture "
            f"messages into a mailbox that has not been named as a test account."
        )


def insert(
    service,
    messages: list[RawMessage],
    *,
    settings: Settings | None = None,
    account: str | None = None,
    label_ids: tuple[str, ...] = ("INBOX", "UNREAD"),
) -> list[str]:
    """Insert messages into the mailbox. Returns the Gmail ids created."""
    settings = settings or get_settings()
    guard(settings, account)

    created: list[str] = []
    for message in messages:
        raw = message.raw_bytes or eml.to_bytes(message)
        # The buyer's own messages belong in Sent, not the inbox — otherwise direction
        # detection is being handed the answer rather than working it out.
        labels = list(label_ids)
        if settings.gmail_account and message.from_address == settings.gmail_account:
            labels = ["SENT"]
        body = {"raw": client.encode_b64url(raw), "labelIds": labels}
        response = (
            service.users()
            .messages()
            .insert(userId="me", internalDateSource="dateHeader", body=body)
            .execute()
        )
        created.append(response["id"])
    log.info("Inserted %d fixture message(s) into the test mailbox", len(created))
    return created


def purge(service, query: str) -> int:
    """Delete everything matching a query. For resetting a test mailbox only."""
    ids: list[str] = []
    page_token = None
    while True:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute()
        )
        ids.extend(item["id"] for item in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    if ids:
        service.users().messages().batchDelete(userId="me", body={"ids": ids}).execute()
    return len(ids)
