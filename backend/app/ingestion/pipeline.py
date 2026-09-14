"""The ingestion pipeline.

One function, ``ingest``, takes a provider-neutral ``RawMessage`` and does the whole
sequence: dedupe, normalize, classify, resolve, persist, extract, transition. Every
transport funnels through here, which is the point — a replayed .eml and a live Gmail
message exercise identical code, so a replay test is evidence about production rather
than about the test harness.

Idempotency is the contract. Running the same message through twice must leave the
database exactly as it was after the first run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enrichment import candidates, extract_rules
from app.enums import ActorKind, Channel, Direction
from app.ingestion import classify as classify_module
from app.ingestion import normalize as normalize_module
from app.ingestion import resolve as resolve_module
from app.ingestion.messages import RawMessage, address_of
from app.models import BuyerProfile, EmailSource, Interaction, Offer
from app.models.base import utcnow
from app.services import state_engine
from app.services.context import get_profile


@dataclass
class IngestResult:
    message_id: str
    created: bool = False
    duplicate_of: int | None = None
    interaction: Interaction | None = None
    offer: Offer | None = None
    dealer_id: int | None = None
    contact_id: int | None = None
    direction: str | None = None
    actor_kind: str | None = None
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def duplicate(self) -> bool:
        return self.duplicate_of is not None


def _existing(db: Session, message: RawMessage) -> Interaction | None:
    """Both dedupe guards: provider identity and content identity."""
    by_source = db.scalars(
        select(Interaction).where(
            Interaction.source_system == message.source_system,
            Interaction.source_identifier == message.source_identifier,
        )
    ).first()
    if by_source is not None:
        return by_source

    by_content = db.scalars(
        select(Interaction).where(Interaction.content_hash == message.content_fingerprint)
    ).first()
    if by_content is not None:
        return by_content

    # The same message forwarded or re-imported arrives with a new provider id but
    # the original RFC-822 Message-ID, which is the only stable identity mail has.
    if message.rfc822_message_id:
        by_rfc822 = db.scalars(
            select(Interaction)
            .join(EmailSource, EmailSource.interaction_id == Interaction.id)
            .where(EmailSource.rfc822_message_id == message.rfc822_message_id)
        ).first()
        if by_rfc822 is not None:
            return by_rfc822
    return None


def _buyer_texts(db: Session, limit: int = 5) -> list[str]:
    """The buyer's own outbound messages, for restatement detection."""
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.direction == Direction.OUTBOUND)
        .order_by(Interaction.occurred_at)
        .limit(limit)
    ).all()
    return [row.normalized_content or "" for row in rows if row.normalized_content]


def _prior_dealer_texts(db: Session, dealer_id: int | None, limit: int = 8) -> list[str]:
    if dealer_id is None:
        return []
    rows = db.scalars(
        select(Interaction)
        .where(
            Interaction.dealer_id == dealer_id,
            Interaction.direction == Direction.INBOUND,
        )
        .order_by(Interaction.occurred_at.desc())
        .limit(limit)
    ).all()
    return [row.normalized_content or "" for row in rows if row.normalized_content]


def ingest(
    db: Session,
    message: RawMessage,
    *,
    profile: BuyerProfile | None = None,
    extract: bool = True,
    refresh_state: bool = True,
    now: datetime | None = None,
) -> IngestResult:
    """Ingest one message. Safe to call repeatedly with the same input.

    ``now`` is the moment to evaluate state at. Replay passes the message's own
    timestamp so the negotiation is reconstructed as it stood then; live ingest leaves
    it None and gets wall clock.
    """
    result = IngestResult(message_id=message.source_identifier)

    duplicate = _existing(db, message)
    if duplicate is not None:
        result.duplicate_of = duplicate.id
        result.interaction = duplicate
        result.dealer_id = duplicate.dealer_id
        result.reasons.append("Already ingested.")
        return result

    profile = profile if profile is not None else get_profile(db)
    body = normalize_module.normalize(message.body_text, message.body_html)

    # Resolve before classifying: the dealer's earlier messages are the reference for
    # spotting a repeated template, and they cannot be looked up without the dealer.
    resolution = resolve_module.resolve(
        db, message, body.text, profile=profile, create_contacts=False
    )
    result.direction = resolution.direction

    classification: classify_module.Classification | None = None
    if resolution.direction == Direction.INBOUND:
        classification = classify_module.classify(
            message,
            body.text,
            buyer_texts=_buyer_texts(db),
            prior_dealer_texts=_prior_dealer_texts(
                db, resolution.dealer.id if resolution.dealer else None
            ),
        )
        result.actor_kind = classification.actor_kind
    else:
        result.actor_kind = ActorKind.HUMAN

    contact = resolution.contact
    if resolution.dealer is not None and resolution.direction == Direction.INBOUND:
        contact = resolve_module.ensure_contact(
            db,
            resolution.dealer,
            address=address_of(message.from_address),
            name=message.from_name,
            normalized_text=body.text or (body.signature or ""),
            actor_kind=result.actor_kind or ActorKind.UNKNOWN,
        )
        if (
            contact is not None
            and classification is not None
            and classification.is_automated
            and contact.automation_evidence is None
        ):
            contact.automation_evidence = classification.reason

    needs_review = resolution.needs_review
    if resolution.needs_review:
        result.reasons.append(resolution.reason)

    interaction = Interaction(
        dealer_id=resolution.dealer.id if resolution.dealer else None,
        contact_id=contact.id if contact else None,
        channel=Channel.EMAIL,
        direction=resolution.direction,
        occurred_at=message.sent_at,
        subject=message.subject,
        raw_content=message.best_body,
        normalized_content=body.text,
        quoted_content="\n\n".join(part for part in (body.quoted, body.signature) if part)
        or None,
        actor_kind=result.actor_kind or ActorKind.UNKNOWN,
        classification_reason=classification.reason if classification else None,
        source_system=message.source_system,
        source_identifier=message.source_identifier,
        source_thread_identifier=message.thread_identifier,
        content_hash=message.content_fingerprint,
        needs_review=needs_review,
        created_at=utcnow(),
    )
    db.add(interaction)
    db.flush()

    db.add(
        EmailSource(
            interaction_id=interaction.id,
            provider=message.source_system,
            provider_message_id=message.source_identifier,
            provider_thread_id=message.thread_identifier,
            rfc822_message_id=message.rfc822_message_id,
            in_reply_to=message.in_reply_to,
            references=" ".join(message.references) or None,
            from_name=message.from_name,
            from_email=address_of(message.from_address),
            to_emails=", ".join(message.to_addresses) or None,
            cc_emails=", ".join(message.cc_addresses) or None,
            sent_at=message.sent_at,
            snippet=message.snippet or (body.text or "")[:200],
            label_ids=", ".join(message.label_ids) or None,
            has_attachments=bool(message.attachments),
            mime_summary=message.mime_summary,
            history_id=message.history_id,
        )
    )
    db.flush()

    result.created = True
    result.interaction = interaction
    result.dealer_id = interaction.dealer_id
    result.contact_id = interaction.contact_id

    if extract and resolution.dealer is not None and resolution.direction == Direction.INBOUND:
        candidate = extract_rules.extract(body.text)
        interaction.is_quote_bearing = candidate.has_pricing
        commit = candidates.commit(
            db,
            dealer=resolution.dealer,
            candidate=candidate,
            interaction=interaction,
            profile=profile,
        )
        result.offer = commit.offer
        if commit.review_reasons:
            result.reasons.extend(commit.review_reasons)
            interaction.needs_review = True
        db.flush()

    result.needs_review = interaction.needs_review

    if refresh_state and interaction.dealer_id is not None:
        state_engine.refresh_one(db, interaction.dealer_id, now=now)

    return result


def ingest_many(
    db: Session, messages: list[RawMessage], *, refresh_state: bool = True, **kwargs
) -> list[IngestResult]:
    """Chronological ingest of a batch.

    Order matters: classification compares against what has already arrived, so a
    batch fed out of order would miss repeated templates.
    """
    profile = get_profile(db)
    ordered = sorted(messages, key=lambda m: (m.sent_at, m.source_identifier))
    return [
        ingest(db, message, profile=profile, refresh_state=refresh_state, **kwargs)
        for message in ordered
    ]
