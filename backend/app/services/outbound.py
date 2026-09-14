"""Sending an approved draft, and recording that it happened.

Three locks have to be open before anything leaves: the transport must be configured
(``send_enabled``), safe mode must permit the recipient, and a human must have moved
the draft to APPROVED. This module checks the third; ``SafeTransport`` checks the
second; configuration is the first. None of them is skippable from here.

A sent message is recorded as a real OUTBOUND ``Interaction`` carrying the provider's
own ids, so when the next sync pulls the same message out of the Sent folder it
deduplicates instead of creating a second copy — and so the dealer's reply threads onto
it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import Channel, Direction, DraftStatus
from app.ingestion.base import MessageTransport, SendRefused, get_transport
from app.ingestion.messages import OutboundMessage
from app.models import Contact, DraftMessage, EmailSource, Interaction
from app.models.base import utcnow
from app.services import state_engine


@dataclass
class SendOutcome:
    draft: DraftMessage
    interaction: Interaction
    provider_message_id: str
    provider_thread_id: str | None


def _recipient(db: Session, draft: DraftMessage) -> str:
    if draft.contact_id:
        contact = db.get(Contact, draft.contact_id)
        if contact and contact.email:
            return contact.email
    primary = db.scalars(
        select(Contact)
        .where(Contact.dealer_id == draft.dealer_id, Contact.email.is_not(None))
        .order_by(Contact.is_primary.desc(), Contact.id)
        .limit(1)
    ).first()
    if primary is None or not primary.email:
        raise SendRefused(
            "No email address on file for this dealer. Add a contact before sending."
        )
    return primary.email


def _thread_context(db: Session, draft: DraftMessage) -> tuple[str | None, str | None, tuple]:
    """Gmail thread id, the RFC-822 id to reply to, and the references chain."""
    if not draft.in_reply_to_interaction_id:
        return None, None, ()
    interaction = db.get(Interaction, draft.in_reply_to_interaction_id)
    if interaction is None:
        return None, None, ()
    source = db.get(EmailSource, interaction.id)
    references = tuple(
        ref for ref in (source.references or "").split() if ref
    ) if source else ()
    rfc822 = source.rfc822_message_id if source else None
    if rfc822:
        references = (*references, rfc822)
    return interaction.source_thread_identifier, rfc822, references


def send_draft(
    db: Session,
    draft: DraftMessage,
    *,
    transport: MessageTransport | None = None,
) -> SendOutcome:
    """Send an approved draft. Raises SendRefused with the reason if anything blocks."""
    # Order matters: a sent draft is no longer APPROVED, so checking approval first
    # would report the vaguer reason for the more specific situation.
    if draft.sent_at is not None:
        raise SendRefused("This draft has already been sent.")
    if draft.status != DraftStatus.APPROVED:
        raise SendRefused(
            f"This draft is {draft.status}. Only an approved draft can be sent — "
            f"review it and approve it explicitly first."
        )

    transport = transport or get_transport()
    recipient = _recipient(db, draft)
    thread_id, in_reply_to, references = _thread_context(db, draft)

    outbound = OutboundMessage(
        to_addresses=(recipient,),
        subject=draft.subject or "(no subject)",
        body_text=draft.body,
        in_reply_to_rfc822_id=in_reply_to,
        references=references,
        thread_identifier=thread_id,
    )

    receipt = transport.send(outbound)

    interaction = Interaction(
        dealer_id=draft.dealer_id,
        contact_id=draft.contact_id,
        channel=Channel.EMAIL,
        direction=Direction.OUTBOUND,
        occurred_at=receipt.sent_at,
        subject=outbound.subject,
        raw_content=draft.body,
        normalized_content=draft.body,
        actor_kind="HUMAN",
        source_system=receipt.source_system,
        source_identifier=receipt.source_identifier,
        source_thread_identifier=receipt.thread_identifier,
        created_at=utcnow(),
    )
    db.add(interaction)
    db.flush()

    db.add(
        EmailSource(
            interaction_id=interaction.id,
            provider=receipt.source_system,
            provider_message_id=receipt.source_identifier,
            provider_thread_id=receipt.thread_identifier,
            rfc822_message_id=receipt.rfc822_message_id,
            in_reply_to=in_reply_to,
            references=" ".join(references) or None,
            to_emails=", ".join(receipt.to_addresses),
            sent_at=receipt.sent_at,
            label_ids=", ".join(receipt.label_ids) or None,
        )
    )

    draft.status = DraftStatus.SENT
    draft.sent_at = receipt.sent_at
    draft.sent_interaction_id = interaction.id
    db.flush()

    state_engine.refresh_one(db, draft.dealer_id)

    return SendOutcome(
        draft=draft,
        interaction=interaction,
        provider_message_id=receipt.source_identifier,
        provider_thread_id=receipt.thread_identifier,
    )
