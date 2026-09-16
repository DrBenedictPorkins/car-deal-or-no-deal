"""Attaching a message to a dealer and a contact.

Deterministic evidence first, and an explicit review queue instead of a guess. The
schema's assumption A3 — that a dealer can be identified by email domain — is treated
as a hint here rather than a rule, because dealer groups share domains across rooftops
and a wrong merge silently corrupts the whole comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import ActorKind, ContactRole, Direction, DomainKind
from app.ingestion.messages import RawMessage, address_of, display_name_of, domain_of
from app.models import BuyerProfile, Contact, Dealer, DealerDomain, Interaction

# Titles dealers actually use, mapped to the roles the model knows about.
ROLE_HINTS: tuple[tuple[str, str], ...] = (
    ("general manager", ContactRole.GENERAL_MANAGER),
    ("sales manager", ContactRole.SALES_MANAGER),
    ("internet sales", ContactRole.INTERNET_SALES),
    ("internet manager", ContactRole.INTERNET_SALES),
    ("business development", ContactRole.BDC),
    ("bdc", ContactRole.BDC),
    ("client services", ContactRole.CLIENT_SERVICES),
    ("client advisor", ContactRole.CLIENT_SERVICES),
    ("finance manager", ContactRole.FINANCE_MANAGER),
    ("sales consultant", ContactRole.SALESPERSON),
    ("sales associate", ContactRole.SALESPERSON),
    ("salesperson", ContactRole.SALESPERSON),
)


@dataclass
class Resolution:
    direction: str
    dealer: Dealer | None = None
    contact: Contact | None = None
    needs_review: bool = False
    reason: str = ""
    created_contact: bool = False

    @property
    def resolved(self) -> bool:
        return self.dealer is not None


def infer_role(text: str | None) -> str:
    if not text:
        return ContactRole.UNKNOWN
    lowered = text.lower()
    for hint, role in ROLE_HINTS:
        if hint in lowered:
            return role
    return ContactRole.UNKNOWN


def _buyer_addresses(profile: BuyerProfile | None) -> set[str]:
    if profile is None or not profile.email:
        return set()
    return {address_of(profile.email) or ""} - {""}


def detect_direction(message: RawMessage, profile: BuyerProfile | None) -> str:
    """Whose message is this?

    Falls back to INBOUND when the buyer's own address is unknown, because an
    unclassified dealer message is recoverable but a dealer quote filed as the buyer's
    own outbound message corrupts "who owes a response".
    """
    buyer = _buyer_addresses(profile)
    if not buyer:
        return Direction.INBOUND
    sender = address_of(message.from_address)
    if sender and sender in buyer:
        return Direction.OUTBOUND
    return Direction.INBOUND


def _contact_by_address(db: Session, address: str | None) -> Contact | None:
    if not address:
        return None
    return db.scalars(
        select(Contact).where(func.lower(Contact.email) == address.lower())
    ).first()


def _contacts_by_address(db: Session, address: str | None) -> list[Contact]:
    if not address:
        return []
    return list(
        db.scalars(select(Contact).where(func.lower(Contact.email) == address.lower())).all()
    )


def _dealers_by_domain(db: Session, domain: str | None) -> list[Dealer]:
    """Dealerships known to use this mail domain.

    An indexed lookup against learned domains rather than a scan over every
    dealership splitting a comma-separated string.
    """
    if not domain:
        return []
    rows = db.scalars(
        select(DealerDomain).where(func.lower(DealerDomain.domain) == domain.lower())
    ).all()
    seen: dict[int, Dealer] = {}
    for row in rows:
        dealer = db.get(Dealer, row.dealer_id)
        if dealer is not None:
            seen[dealer.id] = dealer
    return list(seen.values())


def _dealer_by_alias(db: Session, message: RawMessage) -> Dealer | None:
    """Match the plus-alias the buyer used on the dealership's web form.

    Checked before anything else because it is the only signal the buyer
    controls. The dealership's sending domain is their CRM vendor's choice and
    is frequently shared across unrelated stores; the alias is not.
    """
    recipients = {
        address_of(a)
        for a in (*message.to_addresses, *message.cc_addresses)
        if address_of(a)
    }
    if not recipients:
        return None
    for dealer in db.scalars(
        select(Dealer).where(Dealer.inquiry_alias.is_not(None))
    ).all():
        alias = address_of(dealer.inquiry_alias)
        if alias and alias in recipients:
            return dealer
    return None


def learn_domain(
    db: Session,
    dealer: Dealer,
    domain: str | None,
    *,
    kind: str = DomainKind.UNKNOWN,
    interaction_id: int | None = None,
    verified: bool = False,
) -> DealerDomain | None:
    """Record a mail domain as belonging to a dealership.

    Called when a message from an unrecognized domain is folded into an existing
    dealership, which is how the domain list fills itself in — a store's
    management domain and its CRM's domain get learned the first time each one
    writes, and never have to be typed.
    """
    if not domain:
        return None
    domain = domain.strip().lower()
    existing = db.scalars(
        select(DealerDomain).where(
            DealerDomain.dealer_id == dealer.id,
            func.lower(DealerDomain.domain) == domain,
        )
    ).first()
    if existing is not None:
        if verified and not existing.verified:
            existing.verified = True
            db.flush()
        return existing
    row = DealerDomain(
        dealer_id=dealer.id,
        domain=domain,
        kind=kind,
        learned_from_interaction_id=interaction_id,
        verified=verified,
    )
    db.add(row)
    db.flush()
    return row


def _dealer_by_thread(db: Session, thread_id: str | None) -> Dealer | None:
    if not thread_id:
        return None
    prior = db.scalars(
        select(Interaction)
        .where(
            Interaction.source_thread_identifier == thread_id,
            Interaction.dealer_id.is_not(None),
        )
        .order_by(Interaction.occurred_at)
        .limit(1)
    ).first()
    return db.get(Dealer, prior.dealer_id) if prior else None


def _dealer_by_name_in_text(db: Session, text: str) -> Dealer | None:
    if not text:
        return None
    lowered = text.lower()
    hits = [d for d in db.scalars(select(Dealer)).all() if d.name.lower() in lowered]
    return hits[0] if len(hits) == 1 else None


def resolve(
    db: Session,
    message: RawMessage,
    normalized_text: str,
    *,
    profile: BuyerProfile | None = None,
    actor_kind: str = ActorKind.UNKNOWN,
    create_contacts: bool = True,
) -> Resolution:
    """Attach a message to a dealer and contact, or park it for review."""
    direction = detect_direction(message, profile)

    # The counterparty is the sender on the way in and the recipient on the way out.
    if direction == Direction.OUTBOUND:
        buyer = _buyer_addresses(profile)
        counterparties = [a for a in message.to_addresses if address_of(a) not in buyer]
        counterparty = counterparties[0] if counterparties else None
        counterparty_name = None
    else:
        counterparty = message.from_address
        counterparty_name = message.from_name

    address = address_of(counterparty)
    domain = domain_of(counterparty)

    # 0. The alias the buyer used on this dealership's form. Theirs, not the
    #    dealer's, so no CRM can muddle it.
    aliased = _dealer_by_alias(db, message)
    if aliased is not None:
        return Resolution(
            direction=direction,
            dealer=aliased,
            contact=_contact_by_address(db, address),
            reason=f"Addressed to {aliased.inquiry_alias}, the alias used for {aliased.name}.",
        )

    # 1. A known address — but only when it belongs to exactly one dealership.
    #    A shared BDC or CRM mailbox writing on behalf of several rooftops is
    #    real, and picking the first match would silently merge two negotiations.
    contacts = _contacts_by_address(db, address)
    contact_dealers = {c.dealer_id for c in contacts}
    if len(contact_dealers) == 1:
        contact = contacts[0]
        return Resolution(
            direction=direction,
            dealer=db.get(Dealer, contact.dealer_id),
            contact=contact,
            reason=f"Matched the known address {address}.",
        )

    dealer: Dealer | None = None
    reason = ""
    if len(contact_dealers) > 1:
        # Ambiguous sender: fall through to the thread, which is unambiguous.
        reason = f"{address} is used by {len(contact_dealers)} dealerships; "

    # 2. Domain, but only when it identifies exactly one dealer (assumption A3).
    domain_matches = _dealers_by_domain(db, domain)
    if len(domain_matches) == 1:
        dealer = domain_matches[0]
        reason = f"Matched the mail domain {domain}."
    elif len(domain_matches) > 1:
        return Resolution(
            direction=direction,
            needs_review=True,
            reason=(
                f"The domain {domain} belongs to {len(domain_matches)} dealers "
                f"({', '.join(d.name for d in domain_matches)}); guessing would merge "
                f"two negotiations."
            ),
        )

    # 3. An existing thread already resolved to someone. Ranked above the body
    #    text because a thread is a fact and a name in prose is a guess.
    if dealer is None:
        dealer = _dealer_by_thread(db, message.thread_identifier)
        if dealer is not None:
            reason = f"{reason}continues an existing thread with {dealer.name}."

    # 4. The dealership naming itself in the body or signature.
    if dealer is None:
        dealer = _dealer_by_name_in_text(db, normalized_text)
        if dealer is not None:
            reason = f"The message names {dealer.name}."

    if dealer is None:
        return Resolution(
            direction=direction,
            needs_review=True,
            reason=(
                f"No dealer matches {address or 'this sender'}. Assign it by hand or "
                f"add the domain to a dealer."
            ),
        )

    if not create_contacts or direction == Direction.OUTBOUND:
        return Resolution(direction=direction, dealer=dealer, reason=reason)

    contact = ensure_contact(
        db,
        dealer,
        address=address,
        name=counterparty_name or display_name_of(counterparty),
        normalized_text=normalized_text,
        actor_kind=actor_kind,
    )
    return Resolution(
        direction=direction,
        dealer=dealer,
        contact=contact,
        reason=f"{reason} New contact {address} recorded.",
        created_contact=True,
    )


def ensure_contact(
    db: Session,
    dealer: Dealer,
    *,
    address: str | None,
    name: str | None,
    normalized_text: str = "",
    actor_kind: str = ActorKind.UNKNOWN,
) -> Contact | None:
    """Find or create the person at a dealership who sent this.

    A new name at a known dealership is a normal event rather than an anomaly — the
    source negotiation had two contacts at Mount Kisco and two at Tarrytown, and one
    of the Tarrytown pair was a machine.
    """
    if not address:
        return None
    existing = _contact_by_address(db, address)
    if existing is not None:
        # Classification improves as more messages arrive; an address first seen in a
        # template and later used by a person should stop being labelled a machine.
        if existing.actor_kind != actor_kind and actor_kind != ActorKind.UNKNOWN:
            existing.actor_kind = actor_kind
            db.flush()
        return existing

    contact = Contact(
        dealer_id=dealer.id,
        name=name,
        email=address,
        # The signature block is where a title lives, so the tail of the message is
        # the part worth reading for a role.
        role=infer_role(f"{name or ''} {normalized_text[-400:]}"),
        actor_kind=actor_kind,
        is_primary=not db.scalars(
            select(Contact).where(Contact.dealer_id == dealer.id).limit(1)
        ).first(),
    )
    db.add(contact)
    db.flush()
    return contact
