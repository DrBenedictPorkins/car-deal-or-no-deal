"""The inbox: mail that has arrived but has not been claimed.

The buyer decides what is a dealership, by looking at the message and saying so.
No Gmail labels, no AI classifying a personal mailbox, no guessing from a domain
that half the CRM vendors share.

Two rules make that cheap rather than tedious:

* **Metadata only until promoted.** Sweeping a wide date range costs nothing in
  privacy, because nothing is stored but a sender, a subject and the provider's
  own snippet until the buyer points at a message.
* **Promoting one message claims its neighbours.** Its thread, and anything else
  from the same sender, attribute immediately. Seven clicks for a whole
  negotiation, not seven a day.

Ranking is deterministic and only reorders the list. Suggesting an *order* is
safe in a way that suggesting an *action* is not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import DomainKind, InboxStatus
from app.ingestion import resolve as resolve_module
from app.ingestion.base import MessageSource
from app.ingestion.messages import RawMessage, address_of, domain_of
from app.ingestion.pipeline import ingest
from app.models import (
    BuyerProfile,
    Campaign,
    Contact,
    Dealer,
    DealerDomain,
    InboxMessage,
    Interaction,
)
from app.services.context import get_profile

BULK_MARKERS = ("unsubscribe", "view in browser", "manage preferences", "privacy policy")


@dataclass
class SweepReport:
    fetched: int = 0
    added: int = 0
    already_known: int = 0
    since: datetime | None = None

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "added": self.added,
            "already_known": self.already_known,
            "since": self.since.isoformat() if self.since else None,
        }


@dataclass
class PromoteResult:
    dealer: Dealer
    created_dealer: bool
    interaction: Interaction | None = None
    also_claimed: list[int] = field(default_factory=list)
    learned_domain: str | None = None
    offer_id: int | None = None


# --------------------------------------------------------------------- ranking


def _known_domains(db: Session) -> set[str]:
    return {row.domain.lower() for row in db.scalars(select(DealerDomain)).all()}


def _known_contacts(db: Session) -> set[str]:
    return {
        (row or "").lower()
        for row in db.scalars(select(Contact.email)).all()
        if row
    }


def _known_threads(db: Session) -> set[str]:
    return {
        row
        for row in db.scalars(
            select(Interaction.source_thread_identifier).where(
                Interaction.dealer_id.is_not(None)
            )
        ).all()
        if row
    }


def _known_aliases(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for dealer in db.scalars(select(Dealer).where(Dealer.inquiry_alias.is_not(None))).all():
        alias = address_of(dealer.inquiry_alias)
        if alias:
            out[alias] = dealer.name
    return out


def score_message(
    message: InboxMessage,
    *,
    profile: BuyerProfile | None,
    domains: set[str],
    contacts: set[str],
    threads: set[str],
    aliases: dict[str, str],
) -> tuple[float, list[str]]:
    """How likely this is dealership mail. Ordering only — never an action."""
    score = 0.0
    reasons: list[str] = []
    haystack = f"{message.subject or ''} {message.snippet or ''}".lower()
    sender = (message.from_email or "").lower()
    sender_domain = domain_of(sender) or ""

    for recipient in (message.to_emails or "").lower().split(","):
        alias = address_of(recipient.strip())
        if alias and alias in aliases:
            score += 4
            reasons.append(f"Addressed to the alias you used for {aliases[alias]}")
            break

    if sender and sender in contacts:
        score += 4
        reasons.append("From a contact you already have")
    if sender_domain and sender_domain in domains:
        score += 3
        reasons.append(f"From a known dealership domain ({sender_domain})")
    if message.thread_identifier and message.thread_identifier in threads:
        score += 3
        reasons.append("Continues a thread already attached to a dealership")

    if profile is not None:
        target = " ".join(
            part
            for part in (profile.target_make, profile.target_model, profile.target_trim)
            if part
        ).lower()
        if target and all(word in haystack for word in target.split()[:2]):
            score += 2
            reasons.append(f"Mentions {target.title()}")
        make = (profile.target_make or "").lower()
        if make and sender_domain and make in sender_domain:
            score += 1
            reasons.append(f"Sender domain contains '{make}'")

    if any(marker in haystack for marker in BULK_MARKERS):
        score -= 2
        reasons.append("Looks like a bulk mailing")
    if sender.startswith(("no-reply", "noreply", "donotreply")):
        score -= 1
        reasons.append("Sent from a no-reply address")

    return score, reasons


def rescore_all(db: Session) -> int:
    """Re-rank the whole inbox. Cheap, and worth doing after any promotion."""
    profile = get_profile(db)
    domains, contacts = _known_domains(db), _known_contacts(db)
    threads, aliases = _known_threads(db), _known_aliases(db)
    rows = db.scalars(select(InboxMessage).where(InboxMessage.status == InboxStatus.NEW)).all()
    for row in rows:
        score, reasons = score_message(
            row,
            profile=profile,
            domains=domains,
            contacts=contacts,
            threads=threads,
            aliases=aliases,
        )
        row.score = score
        row.score_reasons = "\n".join(reasons)
    db.flush()
    return len(rows)


# ---------------------------------------------------------------------- sweep


def sweep(
    db: Session,
    source: MessageSource,
    *,
    since: datetime,
    campaign: Campaign | None = None,
    limit: int | None = None,
) -> SweepReport:
    """Pull message metadata from ``since`` forward. Safe to re-run."""
    report = SweepReport(since=since)
    messages = source.fetch_metadata(since=since, limit=limit)
    report.fetched = len(messages)

    for message in messages:
        existing = db.scalars(
            select(InboxMessage).where(
                InboxMessage.source_system == message.source_system,
                InboxMessage.source_identifier == message.source_identifier,
            )
        ).first()
        if existing is not None:
            report.already_known += 1
            continue
        db.add(
            InboxMessage(
                campaign_id=campaign.id if campaign else None,
                source_system=message.source_system,
                source_identifier=message.source_identifier,
                thread_identifier=message.thread_identifier,
                rfc822_message_id=message.rfc822_message_id,
                from_name=message.from_name,
                from_email=address_of(message.from_address),
                to_emails=", ".join(message.to_addresses) or None,
                subject=message.subject,
                snippet=message.snippet,
                sent_at=message.sent_at,
                label_ids=", ".join(message.label_ids) or None,
                has_attachments=bool(message.attachments),
            )
        )
        report.added += 1

    db.flush()
    rescore_all(db)
    return report


# -------------------------------------------------------------------- promote

# The dot is deliberately outside the word class: it ends the organisation name
# rather than being part of it.
_ORG_PATTERNS = (
    re.compile(
        r"(?:contacting|contacted|interest in|inquiry (?:at|with)|reaching out to)\s+"
        r"([A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*){0,4})"
    ),
    re.compile(
        r"(?:Thank you for (?:your interest in|contacting))\s+"
        r"([A-Z][\w'&-]*(?:\s+[A-Z][\w'&-]*){0,4})"
    ),
)


MAKES = (
    "chevrolet", "volkswagen", "mitsubishi", "mercedes", "cadillac", "chrysler",
    "hyundai", "infiniti", "lincoln", "porsche", "subaru", "toyota", "nissan",
    "genesis", "acura", "honda", "mazda", "volvo", "lexus", "buick", "dodge",
    "jeep", "audi", "ford", "bmw", "gmc", "ram", "kia", "mini",
)
# Longest first: "honda" must win over the "ford" hiding inside "stamford".
_MAKES_BY_LENGTH = tuple(sorted(MAKES, key=len, reverse=True))
_JOINERS = ("of", "the")


def _split_domain_label(label: str) -> list[str]:
    """Best effort at words inside a run-together domain label.

    Matching is anchored — a make is stripped from the start or the end before
    anything else — because an unanchored search finds "ford" in the middle of
    "hondaofstamford" and produces "Honda Ofstam Ford".
    """
    parts: list[str] = []
    rest = label.lower()

    for make in _MAKES_BY_LENGTH:
        if rest.startswith(make) and len(rest) > len(make):
            parts.append(make)
            rest = rest[len(make) :]
            break

    for joiner in _JOINERS:
        if rest.startswith(joiner) and len(rest) > len(joiner):
            parts.append(joiner)
            rest = rest[len(joiner) :]
            break

    if not parts:
        # No prefix matched. Try each make longest-first, checking suffix and
        # interior together — checking all suffixes before any interior would let
        # the "ford" ending "oceanhondamilford" win over the "honda" inside it.
        for make in _MAKES_BY_LENGTH:
            if rest.endswith(make) and len(rest) > len(make):
                return [rest[: -len(make)], make]
            index = rest.find(make)
            if index > 0 and index + len(make) < len(rest):
                return [rest[:index], make, rest[index + len(make) :]]

    parts.append(rest)
    return [part for part in parts if part]


def suggest_dealer_name(message: InboxMessage, body: str | None = None) -> str:
    """Best guess at the dealership's name, for the buyer to confirm or correct.

    A guess, and presented as one — the promote endpoint takes an override and
    the UI pre-fills this into an editable field.
    """
    haystack = f"{message.subject or ''}\n{body or message.snippet or ''}"
    for pattern in _ORG_PATTERNS:
        hit = pattern.search(haystack)
        if hit:
            return hit.group(1).strip(" .,!")

    domain = domain_of(message.from_email) or ""
    label = domain.split(".")[0] if domain else ""
    if not label:
        return message.from_name or "Unknown dealership"

    # A domain that already separates its words needs no guessing.
    words = re.split(r"[-_.]+", label) if re.search(r"[-_.]", label) else _split_domain_label(label)
    titled = " ".join(
        word.lower() if word.lower() in _JOINERS else word.capitalize()
        for word in words
        if word
    )
    return titled or label.title()


def _neighbours(db: Session, message: InboxMessage) -> list[InboxMessage]:
    """Other unclaimed mail that plainly belongs with this one."""
    clauses = []
    if message.thread_identifier:
        clauses.append(InboxMessage.thread_identifier == message.thread_identifier)
    if message.from_email:
        clauses.append(func.lower(InboxMessage.from_email) == message.from_email.lower())
    if not clauses:
        return []
    from sqlalchemy import or_

    return list(
        db.scalars(
            select(InboxMessage).where(
                InboxMessage.status == InboxStatus.NEW,
                InboxMessage.id != message.id,
                or_(*clauses),
            )
        ).all()
    )


def _ingest_one(
    db: Session, source: MessageSource, row: InboxMessage, profile: BuyerProfile | None
) -> Interaction | None:
    """Fetch the full message and put it through the normal pipeline."""
    full: RawMessage | None = source.fetch_one(row.source_identifier)
    if full is None:
        return None
    result = ingest(db, full, profile=profile)
    row.status = InboxStatus.PROMOTED
    row.promoted_interaction_id = result.interaction.id if result.interaction else None
    row.dealer_id = result.dealer_id
    db.flush()
    return result.interaction


def promote(
    db: Session,
    row: InboxMessage,
    source: MessageSource,
    *,
    dealer_id: int | None = None,
    dealer_name: str | None = None,
    claim_neighbours: bool = True,
) -> PromoteResult:
    """Claim a message: attach it to a dealership, creating one if needed.

    ``dealer_id`` folds into an existing dealership. Without it, resolution runs
    first — so a message from an address, domain or thread already known folds in
    automatically, and a dealership is created only when nothing matches. Clicking
    the same message twice is a no-op.
    """
    profile = get_profile(db)
    full = source.fetch_one(row.source_identifier)
    if full is None:
        raise LookupError(f"message {row.source_identifier} is no longer available")

    created = False
    dealer: Dealer | None = db.get(Dealer, dealer_id) if dealer_id else None

    if dealer is None:
        # Let the normal ladder speak first: alias, known address, domain, thread.
        existing = resolve_module.resolve(db, full, full.best_body or "", profile=profile,
                                          create_contacts=False)
        dealer = existing.dealer

    if dealer is None:
        dealer = Dealer(
            name=(dealer_name or suggest_dealer_name(row, full.best_body)).strip(),
            campaign_id=row.campaign_id,
        )
        db.add(dealer)
        db.flush()
        created = True
    elif dealer_name:
        dealer.name = dealer_name.strip()

    # The sending domain now belongs to this dealership — which is how a store's
    # management domain and its CRM's domain get learned without being typed.
    sender_domain = domain_of(full.from_address)
    learned = resolve_module.learn_domain(
        db,
        dealer,
        sender_domain,
        kind=DomainKind.PRIMARY if created else DomainKind.UNKNOWN,
        verified=True,
    )
    db.flush()

    interaction = _ingest_one(db, source, row, profile)
    if interaction is not None and learned is not None:
        learned.learned_from_interaction_id = interaction.id

    also: list[int] = []
    if claim_neighbours:
        for neighbour in _neighbours(db, row):
            claimed = _ingest_one(db, source, neighbour, profile)
            if claimed is not None:
                also.append(neighbour.id)

    db.flush()
    rescore_all(db)

    offer_id = None
    if interaction is not None:
        from app.models import Offer

        offer = db.scalars(
            select(Offer).where(Offer.interaction_id == interaction.id)
        ).first()
        offer_id = offer.id if offer else None

    return PromoteResult(
        dealer=dealer,
        created_dealer=created,
        interaction=interaction,
        also_claimed=also,
        learned_domain=learned.domain if learned else None,
        offer_id=offer_id,
    )


def ignore(db: Session, row: InboxMessage) -> InboxMessage:
    """Mark a message as not relevant. Nothing is deleted; it just stops showing."""
    row.status = InboxStatus.IGNORED
    db.flush()
    return row


def restore(db: Session, row: InboxMessage) -> InboxMessage:
    row.status = InboxStatus.NEW
    row.dealer_id = None
    row.promoted_interaction_id = None
    db.flush()
    return row


def listing(
    db: Session,
    *,
    status: str = InboxStatus.NEW,
    limit: int = 200,
) -> list[InboxMessage]:
    """The inbox, most promising first, then newest."""
    return list(
        db.scalars(
            select(InboxMessage)
            .where(InboxMessage.status == status)
            .order_by(InboxMessage.score.desc(), InboxMessage.sent_at.desc())
            .limit(limit)
        ).all()
    )


def counts(db: Session) -> dict[str, int]:
    rows = db.execute(
        select(InboxMessage.status, func.count(InboxMessage.id)).group_by(InboxMessage.status)
    ).all()
    out = {status: 0 for status in (InboxStatus.NEW, InboxStatus.PROMOTED, InboxStatus.IGNORED)}
    out.update(dict(rows))
    return out


def default_since(db: Session, campaign: Campaign | None = None) -> datetime | None:
    """The sweep anchor: when the buyer first reached out."""
    if campaign is not None and campaign.opened_at:
        return campaign.opened_at
    active = db.scalars(
        select(Campaign).where(Campaign.status == "ACTIVE").order_by(Campaign.id.desc())
    ).first()
    if active is not None and active.opened_at:
        return active.opened_at
    return None


__all__ = [
    "PromoteResult",
    "SweepReport",
    "counts",
    "default_since",
    "ignore",
    "listing",
    "promote",
    "rescore_all",
    "restore",
    "score_message",
    "suggest_dealer_name",
    "sweep",
]
